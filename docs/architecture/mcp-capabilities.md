# MCP Capability Contract

Contract version: 1.2.0. Transport: stdio.

The MCP server is a thin authenticated adapter over the Go HTTP API. Tenant identity is derived from the bearer token; callers cannot supply tenant IDs.

| Tool | Scope | HTTP mapping | Retry behavior |
|---|---|---|---|
| create_ingestion | ingestion:write | POST /v1/ingestions | Reuse the same idempotency_key for safe retries. |
| get_ingestion_status | ingestion:read | GET /v1/ingestions/{session_id} | Retry transient transport and server errors with bounded backoff. |
| get_asset | ingestion:read | GET /v1/assets/{asset_id} | Retry transient transport and server errors with bounded backoff. |
| search_assets | ingestion:read | GET /v1/assets/search | Retry transient transport and server errors with bounded backoff. |
| search_transcripts | transcript:read | GET /v1/transcripts/search | Retry transient transport and server errors with bounded backoff; search is read-only. |
| search_hybrid_transcripts | transcript:read | POST /v1/retrieval/hybrid-search | Read-only; retry transient service failures with bounded backoff. |
| index_transcript | transcript:index | POST /v1/transcripts/{transcript_id}/index | Idempotent upsert by tenant, transcript revision, segment, model and model version. |

## Tool contracts

### create_ingestion

Create an authenticated batch ingestion session; upload bytes separately through the returned HTTP URL.

Required scope: ingestion:write.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "idempotency_key",
    "expected_size_bytes",
    "expected_sha256",
    "source"
  ],
  "properties": {
    "idempotency_key": {
      "type": "string",
      "minLength": 16,
      "maxLength": 128
    },
    "expected_size_bytes": {
      "type": "integer",
      "minimum": 1
    },
    "expected_sha256": {
      "type": "string",
      "pattern": "^[a-fA-F0-9]{64}$"
    },
    "content_type_hint": {
      "type": "string",
      "maxLength": 128
    },
    "source": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "kind"
      ],
      "properties": {
        "kind": {
          "type": "string",
          "enum": [
            "file_upload",
            "device_capture",
            "telephony",
            "live_stream",
            "import"
          ]
        },
        "external_reference": {
          "type": "string",
          "maxLength": 2048
        },
        "recorded_at": {
          "type": "string",
          "format": "date-time"
        },
        "declared_by": {
          "type": "string",
          "maxLength": 256
        }
      }
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "session_id",
    "state",
    "expected_size_bytes",
    "expected_sha256",
    "upload_url"
  ],
  "properties": {
    "session_id": {
      "type": "string",
      "format": "uuid"
    },
    "state": {
      "type": "string"
    },
    "expected_size_bytes": {
      "type": "integer"
    },
    "expected_sha256": {
      "type": "string"
    },
    "upload_url": {
      "type": "string"
    }
  }
}

Stable errors:
- INVALID_IDEMPOTENCY_KEY; retryable: false
- INVALID_REQUEST; retryable: false
- UPLOAD_SIZE_LIMIT; retryable: false
- INVALID_CHECKSUM; retryable: false
- INVALID_SOURCE; retryable: false
- IDEMPOTENCY_CONFLICT; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### get_ingestion_status

Get tenant-scoped ingestion session and durable job status.

Required scope: ingestion:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "session_id"
  ],
  "properties": {
    "session_id": {
      "type": "string",
      "format": "uuid"
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "session_id",
    "state",
    "created_at",
    "updated_at"
  ],
  "properties": {
    "session_id": {
      "type": "string",
      "format": "uuid"
    },
    "state": {
      "type": "string"
    },
    "job": {
      "type": "object"
    },
    "asset_id": {
      "type": "string",
      "format": "uuid"
    },
    "failure": {
      "type": "object"
    },
    "created_at": {
      "type": "string",
      "format": "date-time"
    },
    "updated_at": {
      "type": "string",
      "format": "date-time"
    }
  }
}

Stable errors:
- NOT_FOUND; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### get_asset

Get public asset metadata without tenant IDs or private storage references.

Required scope: ingestion:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "asset_id"
  ],
  "properties": {
    "asset_id": {
      "type": "string",
      "format": "uuid"
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "asset_id",
    "source",
    "media",
    "integrity",
    "state",
    "created_at",
    "updated_at"
  ],
  "properties": {
    "asset_id": {
      "type": "string",
      "format": "uuid"
    },
    "source": {
      "type": "object"
    },
    "media": {
      "type": "object"
    },
    "integrity": {
      "type": "object"
    },
    "state": {
      "type": "string"
    },
    "created_at": {
      "type": "string",
      "format": "date-time"
    },
    "updated_at": {
      "type": "string",
      "format": "date-time"
    },
    "retention_policy_id": {
      "type": "string"
    },
    "retention_expires_at": {
      "type": "string",
      "format": "date-time"
    }
  }
}

Stable errors:
- NOT_FOUND; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### search_assets

Search metadata by asset ID, source kind, MIME type, container format, or codec; not transcript or semantic search.

Required scope: ingestion:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "query"
  ],
  "properties": {
    "query": {
      "type": "string",
      "minLength": 2,
      "maxLength": 128
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 20
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "items",
    "limit"
  ],
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "asset_id": {
            "type": "string",
            "format": "uuid"
          },
          "retention_policy_id": {
            "type": "string"
          },
          "retention_expires_at": {
            "type": "string",
            "format": "date-time"
          }
        }
      }
    },
    "limit": {
      "type": "integer"
    }
  }
}

Stable errors:
- INVALID_SEARCH_QUERY; retryable: false
- INVALID_SEARCH_LIMIT; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### search_transcripts

Search tenant-scoped transcript segments using PostgreSQL full-text search and optional transcript, asset, revision, language, speaker and time filters.

Required scope: transcript:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "query"
  ],
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1,
      "maxLength": 256
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100,
      "default": 20
    },
    "offset": {
      "type": "integer",
      "minimum": 0,
      "maximum": 10000,
      "default": 0
    },
    "transcript_id": {
      "type": "string",
      "format": "uuid"
    },
    "asset_id": {
      "type": "string",
      "format": "uuid"
    },
    "revision": {
      "type": "integer",
      "minimum": 1
    },
    "language": {
      "type": "string",
      "minLength": 2,
      "maxLength": 35,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9-]{1,34}$"
    },
    "speaker_id": {
      "type": "string",
      "format": "uuid"
    },
    "start_ms": {
      "type": "integer",
      "minimum": 0
    },
    "end_ms": {
      "type": "integer",
      "minimum": 1
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "items",
    "limit",
    "offset"
  ],
  "properties": {
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "transcript_id",
          "revision",
          "asset_id",
          "language",
          "provider",
          "model",
          "segment_id",
          "start_ms",
          "end_ms",
          "text",
          "rank"
        ],
        "properties": {
          "transcript_id": {
            "type": "string",
            "format": "uuid"
          },
          "revision": {
            "type": "integer"
          },
          "asset_id": {
            "type": "string",
            "format": "uuid"
          },
          "language": {
            "type": "string"
          },
          "provider": {
            "type": "string"
          },
          "model": {
            "type": "string"
          },
          "model_version": {
            "type": "string"
          },
          "segment_id": {
            "type": "string",
            "format": "uuid"
          },
          "start_ms": {
            "type": "integer"
          },
          "end_ms": {
            "type": "integer"
          },
          "text": {
            "type": "string"
          },
          "speaker_id": {
            "type": "string",
            "format": "uuid"
          },
          "rank": {
            "type": "number"
          }
        }
      }
    },
    "limit": {
      "type": "integer"
    },
    "offset": {
      "type": "integer"
    }
  }
}

Stable errors:
- INVALID_SEARCH_QUERY; retryable: false
- INVALID_SEARCH_FILTER; retryable: false
- INVALID_SEARCH_LIMIT; retryable: false
- INVALID_SEARCH_OFFSET; retryable: false
- INVALID_TRANSCRIPT_ID; retryable: false
- INVALID_ASSET_ID; retryable: false
- INVALID_SPEAKER_ID; retryable: false
- INVALID_REVISION; retryable: false
- INVALID_LANGUAGE; retryable: false
- INVALID_START_MS; retryable: false
- INVALID_END_MS; retryable: false
- INVALID_TIME_RANGE; retryable: false
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false
- INTERNAL_ERROR; retryable: true

### search_hybrid_transcripts

Search transcript segments with authenticated lexical and local-vector retrieval, deterministic rank fusion, and source/time/speaker filters.

Required scope: transcript:read.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "query"
  ],
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1,
      "maxLength": 256
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100,
      "default": 20
    },
    "transcript_id": {
      "type": "string",
      "format": "uuid"
    },
    "asset_id": {
      "type": "string",
      "format": "uuid"
    },
    "revision": {
      "type": "integer",
      "minimum": 1
    },
    "language": {
      "type": "string",
      "minLength": 2,
      "maxLength": 35,
      "pattern": "^[A-Za-z0-9][A-Za-z0-9-]{1,34}$"
    },
    "speaker_id": {
      "type": "string",
      "format": "uuid"
    },
    "start_ms": {
      "type": "integer",
      "minimum": 0
    },
    "end_ms": {
      "type": "integer",
      "minimum": 1
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "items",
    "model",
    "model_version"
  ],
  "properties": {
    "model": {
      "type": "string"
    },
    "model_version": {
      "type": "string"
    },
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "transcript_id",
          "revision",
          "asset_id",
          "segment_id",
          "start_ms",
          "end_ms",
          "text",
          "language",
          "fused_score"
        ],
        "properties": {
          "transcript_id": {
            "type": "string",
            "format": "uuid"
          },
          "revision": {
            "type": "integer"
          },
          "asset_id": {
            "type": "string",
            "format": "uuid"
          },
          "segment_id": {
            "type": "string",
            "format": "uuid"
          },
          "start_ms": {
            "type": "integer"
          },
          "end_ms": {
            "type": "integer"
          },
          "text": {
            "type": "string"
          },
          "language": {
            "type": "string"
          },
          "speaker_id": {
            "type": "string",
            "format": "uuid"
          },
          "fused_score": {
            "type": "number"
          },
          "lexical_rank": {
            "type": "integer"
          },
          "semantic_rank": {
            "type": "integer"
          }
        }
      }
    }
  }
}

Stable errors:
- INVALID_RETRIEVAL_REQUEST; retryable: false
- RETRIEVAL_INDEX_NOT_READY; retryable: false
- RETRIEVAL_NOT_CONFIGURED; retryable: false
- RETRIEVAL_SERVICE_UNAVAILABLE; retryable: true
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false

### index_transcript

Idempotently index one immutable transcript revision with the deployment-configured local embedding model.

Required scope: transcript:index.

Input schema:

{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "transcript_id"
  ],
  "properties": {
    "transcript_id": {
      "type": "string",
      "format": "uuid"
    },
    "revision": {
      "type": "integer",
      "minimum": 1
    }
  }
}

Output schema:

{
  "type": "object",
  "required": [
    "transcript_id",
    "revision",
    "indexed_segments",
    "model",
    "model_version"
  ],
  "properties": {
    "transcript_id": {
      "type": "string",
      "format": "uuid"
    },
    "revision": {
      "type": "integer"
    },
    "indexed_segments": {
      "type": "integer",
      "minimum": 0
    },
    "model": {
      "type": "string"
    },
    "model_version": {
      "type": "string"
    }
  }
}

Stable errors:
- INVALID_RETRIEVAL_REQUEST; retryable: false
- RETRIEVAL_NOT_CONFIGURED; retryable: false
- TRANSCRIPT_NOT_FOUND; retryable: false
- RETRIEVAL_SERVICE_UNAVAILABLE; retryable: true
- UNAUTHENTICATED; retryable: false
- FORBIDDEN; retryable: false

## Security and search boundaries

- Tenant identity comes from the authenticated principal; caller-supplied tenant IDs are forbidden.
- Asset responses omit tenant identifiers and private storage/object references.
- search_assets performs metadata search only; search_transcripts performs lexical full-text search; search_hybrid_transcripts performs fused lexical/local-vector retrieval only when a pre-provisioned local model is explicitly configured.
- index_transcript idempotently indexes immutable transcript revisions under the dedicated transcript:index scope; model weights are never downloaded at runtime.
- MCP creates ingestion sessions; audio bytes are transferred separately through the authenticated HTTP upload API.
