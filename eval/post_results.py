"""Build the cross-group silver evaluation summary and exact artifact references."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "eval" / "artifacts" / "post-results"
SUMMARY_SCHEMA = ROOT / "eval" / "post_results.schema.json"


def _normalise_status(value: str) -> str:
    value = value.strip().lower()
    if value == "success":
        return "success"
    if value == "skipped":
        return "skipped"
    return "failure"


def _validate_document(value: dict[str, Any], schema_path: Path, label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError(f"{label} failed schema validation: {errors[0].message}")


def fetch_artifact_catalog(repository: str, run_id: int, token: str) -> tuple[list[dict[str, Any]], str | None]:
    url = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        return payload.get("artifacts", []), None
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return [], f"Unable to retrieve exact artifact metadata from GitHub: {exc}"


def _artifact_reference(
    catalog: list[dict[str, Any]], name: str, repository: str, run_id: int
) -> dict[str, Any] | None:
    artifact = next(
        (
            item for item in catalog
            if item.get("name") == name and not item.get("expired", False)
        ),
        None,
    )
    if artifact is None:
        return None
    artifact_id = artifact.get("id")
    if not isinstance(artifact_id, int):
        return None
    return {
        "name": name,
        "id": artifact_id,
        "url": f"https://github.com/{repository}/actions/runs/{run_id}/artifacts/{artifact_id}",
        "size_bytes": int(artifact.get("size_in_bytes") or 0),
    }


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def build_summary(
    *,
    extraction_job_status: str,
    retrieval_job_status: str,
    speech_group_status: str,
    extraction_metrics: dict[str, Any] | None,
    retrieval_metrics: dict[str, Any] | None,
    speech_report: dict[str, Any] | None,
    artifact_catalog: list[dict[str, Any]],
    repository: str,
    run_id: int,
    run_attempt: int,
    commit_sha: str,
    run_url: str,
    additional_warnings: list[str] | None = None,
) -> dict[str, Any]:
    extraction_status = _normalise_status(extraction_job_status)
    retrieval_status = _normalise_status(retrieval_job_status)
    speech_status = _normalise_status(speech_group_status)
    warnings = list(additional_warnings or [])

    extraction_artifact = _artifact_reference(artifact_catalog, "extraction-silver-baseline", repository, run_id)
    retrieval_artifact = _artifact_reference(artifact_catalog, "retrieval-silver-baseline", repository, run_id)
    speech_artifact = _artifact_reference(artifact_catalog, "speech-evaluation-group", repository, run_id)

    extraction_values = None
    if extraction_metrics is not None:
        try:
            _validate_document(extraction_metrics, ROOT / "eval" / "issue32" / "metrics.schema.json", "extraction metrics")
            extraction_values = {
                "dataset_count": extraction_metrics["dataset_count"],
                "overall_micro": extraction_metrics["overall_micro"],
                "overall_macro": extraction_metrics["overall_macro"],
            }
        except (KeyError, TypeError, ValueError) as exc:
            extraction_status = "failure"
            warnings.append(f"Extraction group evidence rejected: {exc}")
    if extraction_status == "success" and (extraction_values is None or extraction_artifact is None):
        extraction_status = "failure"
        warnings.append("Extraction group reported success without validated metrics and an exact artifact reference.")

    retrieval_values = None
    if retrieval_metrics is not None:
        try:
            _validate_document(retrieval_metrics, ROOT / "eval" / "issue48" / "metrics.schema.json", "retrieval metrics")
            retrieval_values = {
                "query_count": retrieval_metrics["query_count"],
                "test_query_count": retrieval_metrics["test_query_count"],
                "corpus_count": retrieval_metrics["corpus_count"],
                "recall_at_5": retrieval_metrics["recall_at_5"],
                "mrr": retrieval_metrics["mrr"],
                "ndcg_at_5": retrieval_metrics["ndcg_at_5"],
            }
        except (KeyError, TypeError, ValueError) as exc:
            retrieval_status = "failure"
            warnings.append(f"Retrieval group evidence rejected: {exc}")
    if retrieval_status == "success" and (retrieval_values is None or retrieval_artifact is None):
        retrieval_status = "failure"
        warnings.append("Retrieval group reported success without validated metrics and an exact artifact reference.")

    speech_values = None
    if speech_report is not None:
        try:
            _validate_document(speech_report, ROOT / "eval" / "issue47" / "report_47.schema.json", "speech aggregate")
            speech_values = {
                "report_status": speech_report["status"],
                "read_speech_status": speech_report["read_speech"]["status"],
                "synthetic_overlap_status": speech_report["synthetic_overlap"]["status"],
                "read_speech_metrics": speech_report["read_speech"]["metrics"],
                "synthetic_overlap_metrics": speech_report["synthetic_overlap"]["metrics"],
                "natural_conversation_evidence": False,
            }
            if speech_status == "success" and speech_report.get("status") != "success":
                speech_status = "failure"
                warnings.append("Speech group job output claimed success but report_47.json did not.")
        except (KeyError, TypeError, ValueError) as exc:
            speech_status = "failure"
            warnings.append(f"Speech group evidence rejected: {exc}")
    if speech_status == "success" and (speech_values is None or speech_artifact is None):
        speech_status = "failure"
        warnings.append("Speech group reported success without a validated report and an exact artifact reference.")

    groups = {
        "extraction": {"status": extraction_status, "artifact": extraction_artifact, "metrics": extraction_values},
        "retrieval": {"status": retrieval_status, "artifact": retrieval_artifact, "metrics": retrieval_values},
        "speech": {"status": speech_status, "artifact": speech_artifact, "metrics": speech_values},
    }
    complete = all(group["status"] == "success" for group in groups.values())
    if not complete:
        warnings.append("The silver-baseline-complete label is absent because one or more evaluation groups did not pass with a validated artifact.")
    warnings.extend([
        "These automated results are silver-baseline pipeline evidence, not human gold or production-quality evidence.",
        "Read-speech and synthetic TTS do not establish natural conversational performance.",
    ])
    summary = {
        "schema_version": 1,
        "run": {
            "id": run_id,
            "attempt": run_attempt,
            "commit_sha": commit_sha,
            "url": run_url,
        },
        "groups": groups,
        "silver_baseline_complete": complete,
        "completion_label": "silver-baseline-complete" if complete else None,
        "warnings": warnings,
    }
    _validate_document(summary, SUMMARY_SCHEMA, "cross-group evaluation summary")
    return summary


def render_issue_comment(issue_number: int, summary: dict[str, Any]) -> str:
    group_by_issue = {32: "extraction", 48: "retrieval", 47: "speech"}
    group_name = group_by_issue[issue_number]
    group = summary["groups"][group_name]
    lines = [
        f"## VoxPipe silver evaluation summary — issue #{issue_number}",
        "",
        f"- Group status: **{group['status']}**",
        f"- Workflow run: [#{summary['run']['id']}]({summary['run']['url']})",
    ]
    metrics = group["metrics"]
    if metrics:
        if group_name == "extraction":
            lines.extend([
                f"- Conversations: {metrics['dataset_count']}",
                f"- Micro F1: {metrics['overall_micro']['f1']:.4f}",
                f"- Macro F1: {metrics['overall_macro']['f1']:.4f}",
            ])
        elif group_name == "retrieval":
            lines.extend([
                f"- Queries: {metrics['query_count']} (test: {metrics['test_query_count']})",
                f"- Recall@5 / MRR / nDCG@5: {metrics['recall_at_5']:.4f} / {metrics['mrr']:.4f} / {metrics['ndcg_at_5']:.4f}",
            ])
        else:
            read = metrics["read_speech_metrics"]
            synthetic = metrics["synthetic_overlap_metrics"]
            lines.extend([
                f"- Read-speech branch: {metrics['read_speech_status']}",
                f"- Synthetic overlap branch: {metrics['synthetic_overlap_status']}",
                f"- Persian WER / CER: {read['wer']:.6f} / {read['cer']:.6f}" if read else "- Persian WER / CER: unavailable",
                f"- Alignment coverage: {read['alignment_coverage']:.6f}" if read else "- Alignment coverage: unavailable",
                f"- Synthetic conversations / overlaps: {synthetic['conversation_count']} / {synthetic['overlap_count']}" if synthetic else "- Synthetic overlap metrics: unavailable",
            ])
    artifact = group["artifact"]
    lines.append(f"- Artifact: [{artifact['name']}]({artifact['url']})" if artifact else "- Artifact: not published")
    lines.extend(["", "This is automated **silver** evidence only; it is not human gold or a production-quality claim."])
    lines.extend(["", "### Cross-group status"])
    for name, item in summary["groups"].items():
        lines.append(f"- {name}: {item['status']}")
    lines.append(f"- Silver baseline complete: {'yes' if summary['silver_baseline_complete'] else 'no'}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-status", required=True)
    parser.add_argument("--retrieval-status", required=True)
    parser.add_argument("--speech-status", required=True)
    parser.add_argument("--extraction-dir", type=Path, required=True)
    parser.add_argument("--retrieval-dir", type=Path, required=True)
    parser.add_argument("--speech-dir", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    catalog, catalog_warning = fetch_artifact_catalog(
        args.repository, args.run_id, os.environ.get("GITHUB_TOKEN", "")
    )
    warnings = [catalog_warning] if catalog_warning else []
    summary = build_summary(
        extraction_job_status=args.extraction_status,
        retrieval_job_status=args.retrieval_status,
        speech_group_status=args.speech_status,
        extraction_metrics=_load_json(args.extraction_dir / "metrics_32.json"),
        retrieval_metrics=_load_json(args.retrieval_dir / "metrics_48.json"),
        speech_report=_load_json(args.speech_dir / "report_47.json"),
        artifact_catalog=catalog,
        repository=args.repository,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        commit_sha=args.commit_sha,
        run_url=args.run_url,
        additional_warnings=warnings,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for issue_number in (32, 47, 48):
        (args.output_dir / f"issue-{issue_number}.md").write_text(
            render_issue_comment(issue_number, summary), encoding="utf-8"
        )
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as stream:
            stream.write(f"all_success={'true' if summary['silver_baseline_complete'] else 'false'}\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
