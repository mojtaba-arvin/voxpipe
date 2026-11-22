"""PostgreSQL repository adapter and versioned migration runner.

psycopg is imported lazily so SQLite/local users do not need the PostgreSQL
driver installed merely to import the VoxPipe package.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from voxpipe.ingestion.repository import IngestionRepositoryConflict

_AUDIT_STRING_FIELDS = frozenset({"from", "to", "prior_state", "retention_expires_at"})
_AUDIT_BOOLEAN_FIELDS = frozenset({"staging_deleted", "asset_deleted", "object_deleted"})


def sanitize_audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only bounded, non-sensitive lifecycle facts in audit metadata."""
    safe: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key in _AUDIT_STRING_FIELDS and isinstance(value, str) and len(value) <= 64:
            safe[key] = value
        elif key in _AUDIT_BOOLEAN_FIELDS and isinstance(value, bool):
            safe[key] = value
    return safe


def sanitize_audit_error_code(error_code: str | None) -> str | None:
    if error_code is None or error_code == "":
        return error_code
    if len(error_code) > 64 or any(
        not (char.isascii() and (char.isupper() or char.isdigit() or char == "_"))
        for char in error_code
    ):
        return "AUDIT_ERROR_REDACTED"
    return error_code



def apply_migrations(
    dsn: str,
    migrations_dir: str | Path | None = None,
) -> tuple[int, ...]:
    """Apply ordered SQL migrations atomically and return newly applied versions.

    A transaction-scoped advisory lock serializes concurrent runners. Each SQL
    file is sent to PostgreSQL as one complete script: splitting on semicolons
    is not SQL-aware and corrupts dollar-quoted blocks and string literals.
    """
    import psycopg

    directory = Path(migrations_dir) if migrations_dir else (
        Path(__file__).resolve().parents[3] / "migrations" / "postgres"
    )
    if not directory.is_dir():
        raise ValueError("PostgreSQL migrations directory does not exist")

    applied_now: list[int] = []
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('voxpipe:postgres:migrations', 0))"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS voxpipe_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            for migration in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
                version = int(migration.name.split("_", 1)[0])
                existing = connection.execute(
                    "SELECT 1 FROM voxpipe_schema_migrations WHERE version = %s",
                    (version,),
                ).fetchone()
                if existing:
                    continue

                sql = migration.read_text(encoding="utf-8")
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO voxpipe_schema_migrations (version, name) VALUES (%s, %s)",
                    (version, migration.name),
                )
                applied_now.append(version)
    return tuple(applied_now)


class PostgresIngestionRepository:
    """Multi-node repository using PostgreSQL transactions and tenant RLS.

    The connecting role must not have BYPASSRLS. Every repository operation
    sets a transaction-local tenant context; recovery/reconciliation scans
    require an explicit tenant_id and never run cross-tenant under the app role.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    def create_or_get_session(self, session: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant_id = session["tenant_id"]
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                INSERT INTO ingestion_sessions
                    (session_id, tenant_id, idempotency_key, state, payload, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::timestamptz)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING payload
                """,
                (
                    session["session_id"],
                    tenant_id,
                    session["idempotency_key"],
                    session["state"],
                    self._dump(session),
                    session["updated_at"],
                ),
            ).fetchone()
            if row is not None:
                import uuid
                connection.execute(
                    """
                    INSERT INTO ingestion_audit_events
                        (event_id, tenant_id, session_id, asset_id, event_type, outcome, metadata)
                    VALUES (%s, %s, %s, %s, 'ingestion.created', 'succeeded', %s::jsonb)
                    """,
                    (str(uuid.uuid4()), tenant_id, session["session_id"], session.get("asset_id"),
                     json.dumps({"state": session["state"]})),
                )
                return self._payload(row["payload"]), True

            existing = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (tenant_id, session["idempotency_key"]),
            ).fetchone()
            if existing is None:
                raise IngestionRepositoryConflict("session insert conflicted without a visible row")
            return self._payload(existing["payload"]), False

    def get_session_by_idempotency(
        self,
        tenant_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND idempotency_key = %s
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
        return self._payload(row["payload"]) if row else None

    def get_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND session_id = %s
                """,
                (tenant_id, session_id),
            ).fetchone()
        return self._payload(row["payload"]) if row else None

    def update_session(
        self,
        tenant_id: str,
        session_id: str,
        expected_state: str,
        updated_session: dict[str, Any],
    ) -> dict[str, Any]:
        if updated_session.get("tenant_id") != tenant_id:
            raise IngestionRepositoryConflict("updated session tenant does not match repository scope")
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                UPDATE ingestion_sessions
                SET state = %s, payload = %s::jsonb, updated_at = %s::timestamptz
                WHERE tenant_id = %s AND session_id = %s AND state = %s
                RETURNING session_id
                """,
                (
                    updated_session["state"],
                    self._dump(updated_session),
                    updated_session["updated_at"],
                    tenant_id,
                    session_id,
                    expected_state,
                ),
            ).fetchone()
            if row is None:
                raise IngestionRepositoryConflict("session state changed concurrently")
            if expected_state != updated_session["state"]:
                import uuid
                connection.execute(
                    """
                    INSERT INTO ingestion_audit_events
                        (event_id, tenant_id, session_id, asset_id, event_type, outcome, metadata)
                    VALUES (%s, %s, %s, %s, 'ingestion.state_transition', 'succeeded', %s::jsonb)
                    """,
                    (str(uuid.uuid4()), tenant_id, session_id, updated_session.get("asset_id"),
                     json.dumps({"from": expected_state, "to": updated_session["state"]})),
                )
        return updated_session

    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        tenant_id = asset["tenant_id"]
        if not tenant_id:
            raise IngestionRepositoryConflict("asset tenant is required")
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                INSERT INTO audio_assets (asset_id, tenant_id, payload, updated_at)
                VALUES (%s, %s, %s::jsonb, %s::timestamptz)
                ON CONFLICT DO NOTHING
                RETURNING payload
                """,
                (
                    asset["asset_id"],
                    tenant_id,
                    self._dump(asset),
                    asset["updated_at"],
                ),
            ).fetchone()
            if row is not None:
                return self._payload(row["payload"])

            existing = connection.execute(
                """
                SELECT payload FROM audio_assets
                WHERE tenant_id = %s AND asset_id = %s
                """,
                (tenant_id, asset["asset_id"]),
            ).fetchone()
            if existing is None or self._payload(existing["payload"]) != asset:
                raise IngestionRepositoryConflict(
                    "asset identifier already exists with different content or tenant"
                )
            return self._payload(existing["payload"])

    def get_asset(self, tenant_id: str, asset_id: str) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload FROM audio_assets
                WHERE tenant_id = %s AND asset_id = %s
                """,
                (tenant_id, asset_id),
            ).fetchone()
        return self._payload(row["payload"]) if row else None

    def delete_asset(self, tenant_id: str, asset_id: str) -> bool:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                DELETE FROM audio_assets
                WHERE tenant_id = %s AND asset_id = %s
                RETURNING asset_id
                """,
                (tenant_id, asset_id),
            ).fetchone()
        return row is not None

    def claim_session(
        self,
        tenant_id: str,
        session_id: str,
        worker_id: str,
        now: str,
        lease_expires_at: str,
    ) -> dict[str, Any] | None:
        with self._tenant_connection(tenant_id) as connection:
            row = connection.execute(
                """
                SELECT state, payload FROM ingestion_sessions
                WHERE tenant_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (tenant_id, session_id),
            ).fetchone()
            if row is None or row["state"] in {"completed", "failed", "cancelled", "expired"}:
                return None

            session = self._payload(row["payload"])
            if session.get("tenant_id") != tenant_id:
                raise IngestionRepositoryConflict("session payload tenant does not match repository scope")
            prior_lease = session.get("lease") or {}
            prior_expiry = prior_lease.get("expires_at")
            if prior_expiry and prior_expiry > now and prior_lease.get("owner_id") != worker_id:
                return None

            session["lease"] = {
                "owner_id": worker_id,
                "expires_at": lease_expires_at,
                "heartbeat_at": now,
                "attempt": int(prior_lease.get("attempt", 0)) + 1,
            }
            session["updated_at"] = now
            connection.execute(
                """
                UPDATE ingestion_sessions
                SET payload = %s::jsonb, updated_at = %s::timestamptz
                WHERE tenant_id = %s AND session_id = %s
                """,
                (self._dump(session), now, tenant_id, session_id),
            )
            return session

    def list_recoverable_sessions(
        self,
        now: str,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tenant = self._required_tenant(tenant_id)
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s
                  AND state = ANY(%s)
                  AND NOT EXISTS (
                    SELECT 1 FROM ingestion_jobs AS job
                    WHERE job.tenant_id = ingestion_sessions.tenant_id
                      AND job.session_id = ingestion_sessions.session_id
                  )
                  AND (
                    state <> 'created'
                    OR (
                        COALESCE((payload #>> '{upload,received_size_bytes}')::bigint, 0) > 0
                        AND (payload #>> '{upload,received_size_bytes}')::bigint =
                            (payload #>> '{upload,expected_size_bytes}')::bigint
                    )
                  )
                  AND (
                    payload #>> '{lease,expires_at}' IS NULL
                    OR (payload #>> '{lease,expires_at}')::timestamptz <= %s::timestamptz
                  )
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                (tenant, ["created", "receiving", "finalizing", "queued", "processing"], now, limit),
            ).fetchall()
        return [self._payload(row["payload"]) for row in rows]

    def record_audit_event(self, tenant_id: str, session_id: str | None, asset_id: str | None,
                           event_type: str, outcome: str, error_code: str | None = None,
                           metadata: dict[str, Any] | None = None) -> None:
        import uuid
        with self._tenant_connection(tenant_id) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_audit_events
                    (event_id, tenant_id, session_id, asset_id, event_type, outcome, error_code, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (str(uuid.uuid4()), tenant_id, session_id, asset_id, event_type, outcome,
                 sanitize_audit_error_code(error_code),
                 json.dumps(sanitize_audit_metadata(metadata), ensure_ascii=False, sort_keys=True)),
            )

    def list_expired_assets(self, now: str, limit: int = 100, tenant_id: str | None = None) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tenant = self._required_tenant(tenant_id)
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM audio_assets
                WHERE tenant_id = %s AND payload->>'retention_expires_at' <= %s
                ORDER BY payload->>'retention_expires_at' ASC
                LIMIT %s
                """,
                (tenant, now, limit),
            ).fetchall()
        return [self._payload(row["payload"]) for row in rows]

    def list_expired_sessions(self, now: str, limit: int = 100, tenant_id: str | None = None) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tenant = self._required_tenant(tenant_id)
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND state IN ('created', 'receiving')
                  AND payload->>'expires_at' <= %s
                  AND COALESCE(payload #>> '{lease,expires_at}', '') <= %s
                  AND payload->>'staging_cleanup_completed_at' IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM ingestion_upload_leases AS upload
                    WHERE upload.tenant_id = ingestion_sessions.tenant_id
                      AND upload.session_id = ingestion_sessions.session_id
                      AND upload.lease_expires_at > now()
                  )
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                (tenant, now, now, limit),
            ).fetchall()
        return [self._payload(row["payload"]) for row in rows]

    def list_sessions_by_state(
        self,
        state: str,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        tenant = self._required_tenant(tenant_id)
        with self._tenant_connection(tenant) as connection:
            rows = connection.execute(
                """
                SELECT payload FROM ingestion_sessions
                WHERE tenant_id = %s AND state = %s
                  AND payload->>'staging_cleanup_completed_at' IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM ingestion_upload_leases AS upload
                    WHERE upload.tenant_id = ingestion_sessions.tenant_id
                      AND upload.session_id = ingestion_sessions.session_id
                      AND upload.lease_expires_at > now()
                  )
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                (tenant, state, limit),
            ).fetchall()
        return [self._payload(row["payload"]) for row in rows]

    def create_transcript_revision(self, transcript: dict[str, Any]) -> dict[str, Any]:
        """Append one immutable transcript revision and all its derived rows.

        Input shape:
          tenant_id, transcript_id, asset_id, revision, language, text,
          provider, model, model_version, audio_sha256, generated_at,
          speakers=[{speaker_id, label?, confidence?}],
          segments=[{segment_id, start_ms, end_ms, text, confidence?,
                     speaker_id?, words=[{word_index, start_ms, end_ms,
                     text, confidence?}]}]

        Revision numbers are caller-assigned and never updated in place.
        Identical retries return the stored revision; conflicting content fails.
        """
        import re
        import uuid

        required = (
            "tenant_id", "transcript_id", "asset_id", "revision", "language",
            "text", "provider", "model", "audio_sha256", "generated_at",
        )
        if not isinstance(transcript, dict) or any(key not in transcript for key in required):
            raise ValueError("transcript is missing required fields")
        normalized_ids: dict[str, str] = {}
        for field in ("tenant_id", "transcript_id", "asset_id"):
            try:
                normalized_ids[field] = str(uuid.UUID(str(transcript[field])))
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"{field} must be a UUID") from None
        tenant_id = normalized_ids["tenant_id"]
        transcript_id = normalized_ids["transcript_id"]
        asset_id = normalized_ids["asset_id"]
        revision = transcript["revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("revision must be a positive integer")
        for field, maximum in (("language", 35), ("provider", 128), ("model", 256)):
            value = transcript[field]
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{field} must be a non-empty bounded string")
        if not isinstance(transcript["text"], str):
            raise ValueError("transcript text must be a string")
        audio_sha256 = transcript["audio_sha256"]
        if not isinstance(audio_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", audio_sha256):
            raise ValueError("audio_sha256 must be a lowercase SHA-256 hex digest")
        model_version = transcript.get("model_version")
        if model_version is not None and (not isinstance(model_version, str) or len(model_version) > 256):
            raise ValueError("model_version must be a bounded string or null")
        generated_at = transcript["generated_at"]
        from datetime import datetime
        if not isinstance(generated_at, str) or not generated_at.strip():
            raise ValueError("generated_at must be an ISO-8601 timestamp")
        try:
            generated_at_value = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("generated_at must be an ISO-8601 timestamp") from None
        if generated_at_value.tzinfo is None or generated_at_value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        speakers = transcript.get("speakers", [])
        segments = transcript.get("segments", [])
        if not isinstance(speakers, list) or not isinstance(segments, list):
            raise ValueError("speakers and segments must be arrays")

        speaker_ids: set[str] = set()
        normalized_speakers: list[dict[str, Any]] = []
        for speaker in speakers:
            if not isinstance(speaker, dict):
                raise ValueError("each speaker must be an object")
            try:
                speaker_id = str(uuid.UUID(str(speaker["speaker_id"])))
            except (KeyError, ValueError, TypeError, AttributeError):
                raise ValueError("speaker_id must be a UUID") from None
            if speaker_id in speaker_ids:
                raise ValueError("speaker IDs must be unique within a revision")
            speaker_ids.add(speaker_id)
            confidence = speaker.get("confidence")
            if confidence is not None and (
                not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ValueError("speaker confidence must be between 0 and 1")
            label = speaker.get("label")
            if label is not None and not isinstance(label, str):
                raise ValueError("speaker label must be a string or null")
            normalized_speakers.append({
                "speaker_id": speaker_id, "label": label, "confidence": confidence,
            })

        segment_ids: set[str] = set()
        normalized_segments: list[dict[str, Any]] = []
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("each segment must be an object")
            try:
                segment_id = str(uuid.UUID(str(segment["segment_id"])))
            except (KeyError, ValueError, TypeError, AttributeError):
                raise ValueError("segment_id must be a UUID") from None
            if segment_id in segment_ids:
                raise ValueError("segment IDs must be unique within a revision")
            segment_ids.add(segment_id)
            start_ms, end_ms = segment.get("start_ms"), segment.get("end_ms")
            if (not isinstance(start_ms, int) or isinstance(start_ms, bool) or start_ms < 0
                or not isinstance(end_ms, int) or isinstance(end_ms, bool) or end_ms <= start_ms):
                raise ValueError("segment timestamps must be non-negative and end after start")
            text = segment.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("segment text must be non-empty")
            speaker_id = segment.get("speaker_id")
            if speaker_id is not None:
                try:
                    speaker_id = str(uuid.UUID(str(speaker_id)))
                except (ValueError, TypeError, AttributeError):
                    raise ValueError("segment speaker_id must be a UUID or null") from None
                if speaker_id not in speaker_ids:
                    raise ValueError("segment references an unknown speaker")
            confidence = segment.get("confidence")
            if confidence is not None and (
                not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ValueError("segment confidence must be between 0 and 1")
            words = segment.get("words", [])
            if not isinstance(words, list):
                raise ValueError("segment words must be an array")
            normalized_words: list[dict[str, Any]] = []
            word_indexes: set[int] = set()
            for word in words:
                if not isinstance(word, dict):
                    raise ValueError("each word must be an object")
                index, word_start, word_end = word.get("word_index"), word.get("start_ms"), word.get("end_ms")
                if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index in word_indexes:
                    raise ValueError("word_index must be a unique non-negative integer per segment")
                if (not isinstance(word_start, int) or isinstance(word_start, bool) or word_start < start_ms
                    or not isinstance(word_end, int) or isinstance(word_end, bool)
                    or word_end <= word_start or word_end > end_ms):
                    raise ValueError("word timestamps must fit inside their segment")
                word_text = word.get("text")
                if not isinstance(word_text, str) or not word_text.strip():
                    raise ValueError("word text must be non-empty")
                word_confidence = word.get("confidence")
                if word_confidence is not None and (
                    not isinstance(word_confidence, (int, float)) or isinstance(word_confidence, bool)
                    or not 0 <= word_confidence <= 1
                ):
                    raise ValueError("word confidence must be between 0 and 1")
                word_indexes.add(index)
                normalized_words.append({
                    "word_index": index, "start_ms": word_start, "end_ms": word_end,
                    "text": word_text, "confidence": word_confidence,
                })
            normalized_segments.append({
                "segment_id": segment_id, "start_ms": start_ms, "end_ms": end_ms,
                "text": text, "confidence": confidence, "speaker_id": speaker_id,
                "words": normalized_words,
            })

        import psycopg
        try:
            with self._tenant_connection(tenant_id) as connection:
                connection.execute(
                    """INSERT INTO transcripts
                    (tenant_id, transcript_id, asset_id, revision, language, text,
                     provider, model, model_version, audio_sha256, generated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, transcript_id, asset_id,
                     revision, transcript["language"], transcript["text"],
                     transcript["provider"], transcript["model"], model_version,
                     audio_sha256, generated_at_value),
                )
                for speaker in normalized_speakers:
                    connection.execute(
                        """INSERT INTO transcript_speakers
                        (tenant_id, transcript_id, revision, speaker_id, label, confidence)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                        (tenant_id, transcript_id, revision,
                         speaker["speaker_id"], speaker["label"], speaker["confidence"]),
                    )
                for segment in normalized_segments:
                    connection.execute(
                        """INSERT INTO transcript_segments
                        (tenant_id, transcript_id, revision, segment_id, start_ms, end_ms,
                         text, confidence, speaker_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (tenant_id, transcript["transcript_id"], revision,
                         segment["segment_id"], segment["start_ms"], segment["end_ms"],
                         segment["text"], segment["confidence"], segment["speaker_id"]),
                    )
                    for word in segment["words"]:
                        connection.execute(
                            """INSERT INTO transcript_words
                            (tenant_id, transcript_id, revision, segment_id, word_index,
                             start_ms, end_ms, text, confidence)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (tenant_id, transcript["transcript_id"], revision,
                             segment["segment_id"], word["word_index"], word["start_ms"],
                             word["end_ms"], word["text"], word["confidence"]),
                        )
        except psycopg.errors.UniqueViolation as exc:
            existing = self.get_transcript_revision(
                tenant_id, transcript_id, revision,
            )
            if existing is not None:
                existing_speakers = [
                    {
                        "speaker_id": str(row["speaker_id"]),
                        "label": row["label"],
                        "confidence": row["confidence"],
                    }
                    for row in existing["speakers"]
                ]
                expected_speakers = sorted(normalized_speakers, key=lambda row: row["speaker_id"])
                existing_segments = []
                for row in existing["segments"]:
                    existing_segments.append({
                        "segment_id": str(row["segment_id"]),
                        "start_ms": row["start_ms"], "end_ms": row["end_ms"],
                        "text": row["text"], "confidence": row["confidence"],
                        "speaker_id": str(row["speaker_id"]) if row["speaker_id"] else None,
                        "words": [
                            {
                                "word_index": word["word_index"],
                                "start_ms": word["start_ms"], "end_ms": word["end_ms"],
                                "text": word["text"], "confidence": word["confidence"],
                            }
                            for word in row["words"]
                        ],
                    })
                expected_segments = [
                    {
                        "segment_id": row["segment_id"],
                        "start_ms": row["start_ms"], "end_ms": row["end_ms"],
                        "text": row["text"], "confidence": row["confidence"],
                        "speaker_id": row["speaker_id"],
                        "words": sorted(row["words"], key=lambda word: word["word_index"]),
                    }
                    for row in sorted(
                        normalized_segments,
                        key=lambda item: (item["start_ms"], item["segment_id"]),
                    )
                ]
                if (
                    str(existing["tenant_id"]) == tenant_id
                    and str(existing["transcript_id"]) == transcript_id
                    and str(existing["asset_id"]) == asset_id
                    and existing["revision"] == revision
                    and existing["language"] == transcript["language"]
                    and existing["text"] == transcript["text"]
                    and existing["provider"] == transcript["provider"]
                    and existing["model"] == transcript["model"]
                    and existing["model_version"] == model_version
                    and existing["audio_sha256"].strip() == audio_sha256
                    and existing["generated_at"] == generated_at_value
                    and existing_speakers == expected_speakers
                    and existing_segments == expected_segments
                ):
                    return existing
            raise IngestionRepositoryConflict(
                "transcript revision already exists with different content"
            ) from exc
        return self.get_transcript_revision(tenant_id, transcript_id, revision)

    def get_transcript_revision(
        self, tenant_id: str, transcript_id: str, revision: int | None = None,
    ) -> dict[str, Any] | None:
        """Read one immutable revision, or the latest visible revision, under tenant RLS."""
        import uuid
        try:
            uuid.UUID(tenant_id)
            uuid.UUID(transcript_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("tenant_id and transcript_id must be UUIDs") from None
        if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
        ):
            raise ValueError("revision must be a positive integer")
        with self._tenant_connection(tenant_id) as connection:
            if revision is None:
                transcript_row = connection.execute(
                    """SELECT * FROM transcripts
                    WHERE tenant_id=%s AND transcript_id=%s
                    ORDER BY revision DESC LIMIT 1""",
                    (tenant_id, transcript_id),
                ).fetchone()
            else:
                transcript_row = connection.execute(
                    """SELECT * FROM transcripts
                    WHERE tenant_id=%s AND transcript_id=%s AND revision=%s""",
                    (tenant_id, transcript_id, revision),
                ).fetchone()
            if transcript_row is None:
                return None
            result = dict(transcript_row)
            result["speakers"] = [
                dict(row) for row in connection.execute(
                    """SELECT speaker_id, label, confidence FROM transcript_speakers
                    WHERE tenant_id=%s AND transcript_id=%s AND revision=%s
                    ORDER BY speaker_id""",
                    (tenant_id, transcript_id, result["revision"]),
                ).fetchall()
            ]
            segments = connection.execute(
                """SELECT segment_id, start_ms, end_ms, text, confidence, speaker_id
                FROM transcript_segments
                WHERE tenant_id=%s AND transcript_id=%s AND revision=%s
                ORDER BY start_ms, segment_id""",
                (tenant_id, transcript_id, result["revision"]),
            ).fetchall()
            result["segments"] = []
            for row in segments:
                segment = dict(row)
                segment["words"] = [
                    dict(word) for word in connection.execute(
                        """SELECT word_index, start_ms, end_ms, text, confidence
                        FROM transcript_words
                        WHERE tenant_id=%s AND transcript_id=%s AND revision=%s AND segment_id=%s
                        ORDER BY word_index""",
                        (tenant_id, transcript_id, result["revision"], segment["segment_id"]),
                    ).fetchall()
                ]
                result["segments"].append(segment)
        return result

    def search_transcript_segments(
        self,
        tenant_id: str,
        query: str,
        *,
        transcript_id: str | None = None,
        asset_id: str | None = None,
        revision: int | None = None,
        language: str | None = None,
        speaker_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search transcript segments using PostgreSQL FTS under mandatory tenant RLS.

        Returned rows retain the immutable transcript revision and source asset
        provenance. Query text and transcript contents are never logged here.
        """
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(query, str) or not query.strip() or len(query) > 256:
            raise ValueError("query must contain 1..256 characters")
        if not 1 <= limit <= 100 or not 0 <= offset <= 10_000:
            raise ValueError("pagination is outside the allowed bounds")
        if start_ms is not None and (not isinstance(start_ms, int) or isinstance(start_ms, bool) or start_ms < 0):
            raise ValueError("start_ms must be a non-negative integer")
        if end_ms is not None and (not isinstance(end_ms, int) or isinstance(end_ms, bool) or end_ms < 1):
            raise ValueError("end_ms must be a positive integer")
        if start_ms is not None and end_ms is not None and end_ms <= start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if transcript_id is not None:
            import uuid
            try:
                uuid.UUID(transcript_id)
            except (ValueError, TypeError, AttributeError):
                raise ValueError("transcript_id must be a UUID") from None
        if speaker_id is not None:
            import uuid
            try:
                uuid.UUID(speaker_id)
            except (ValueError, TypeError, AttributeError):
                raise ValueError("speaker_id must be a UUID") from None
        if asset_id is not None:
            import uuid
            try:
                uuid.UUID(asset_id)
            except (ValueError, TypeError, AttributeError):
                raise ValueError("asset_id must be a UUID") from None
        if revision is not None and (
            not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
        ):
            raise ValueError("revision must be a positive integer")
        with self._tenant_connection(tenant_id) as connection:
            rows = connection.execute(
                """
                WITH search_query AS (
                    SELECT websearch_to_tsquery('simple', %s) AS value
                )
                SELECT
                    t.transcript_id, t.revision, t.asset_id, t.language,
                    t.provider, t.model, t.model_version, t.audio_sha256,
                    s.segment_id, s.start_ms, s.end_ms, s.text, s.speaker_id,
                    ts_rank(s.search_vector, q.value) AS rank
                FROM transcript_segments AS s
                JOIN transcripts AS t
                  ON t.tenant_id = s.tenant_id
                 AND t.transcript_id = s.transcript_id
                 AND t.revision = s.revision
                CROSS JOIN search_query AS q
                WHERE s.search_vector @@ q.value
                  AND (%s::uuid IS NULL OR t.transcript_id = %s::uuid)
                  AND (%s::uuid IS NULL OR t.asset_id = %s::uuid)
                  AND (%s::integer IS NULL OR t.revision = %s::integer)
                  AND (%s::text IS NULL OR t.language = %s::text)
                  AND (%s::uuid IS NULL OR s.speaker_id = %s::uuid)
                  AND (%s::bigint IS NULL OR s.end_ms >= %s::bigint)
                  AND (%s::bigint IS NULL OR s.start_ms < %s::bigint)
                ORDER BY rank DESC, s.start_ms ASC, s.segment_id ASC
                LIMIT %s OFFSET %s
                """,
                (query, transcript_id, transcript_id, asset_id, asset_id,
                 revision, revision, language, language, speaker_id, speaker_id,
                 start_ms, start_ms, end_ms, end_ms, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def _tenant_connection(self, tenant_id: str) -> Iterator[Any]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('voxpipe.tenant_id', %s, true)",
                (tenant_id,),
            )
            yield connection

    @staticmethod
    def _required_tenant(tenant_id: str | None) -> str:
        if tenant_id is None:
            raise ValueError("tenant_id is required for PostgreSQL RLS-scoped scans")
        return tenant_id

    @staticmethod
    def _dump(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        raise TypeError("PostgreSQL JSONB payload has an unsupported representation")
