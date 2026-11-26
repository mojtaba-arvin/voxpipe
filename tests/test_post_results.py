from __future__ import annotations

import unittest

from eval.post_results import build_summary


def metrics32():
    metric={"tp":10,"fp":1,"fn":2,"precision":0.909091,"recall":0.833333,"f1":0.869565}
    return {
        "schema_version":1,"source_kind":"synthetic_template","label_kind":"deterministic_rule_silver",
        "reference_kind":"template_generated_reference_not_human_gold","seed":1,"dataset_count":120,
        "split_counts":{"train":80,"validation":20,"test":20},
        "split_ratios":{"train":0.666667,"validation":0.166667,"test":0.166667},
        "fields":{"date":metric,"amount":metric,"order_id":metric,"customer_name":metric},
        "overall_micro":metric,"overall_macro":{"precision":0.9,"recall":0.8,"f1":0.85}
    }


def metrics48():
    return {
        "schema_version":1,"source_kind":"synthetic_template","qrels_kind":"heuristic_lexical_silver",
        "ranking_method":"deterministic_jaccard_lexical_baseline","seed":1,"query_count":120,
        "test_query_count":20,"corpus_count":5,"split_counts":{"train":80,"validation":20,"test":20},
        "split_ratios":{"train":0.666667,"validation":0.166667,"test":0.166667},
        "jaccard_threshold":0.3,"relevance_policy":"binary relevant","recall_at_5":1.0,
        "mrr":0.8,"ndcg_at_5":0.9,"warnings":["silver"]
    }


def speech_report():
    return {
        "schema_version":1,"status":"success","run_id":123,"commit_sha":"a"*40,
        "workflow_run_url":"https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        "read_speech":{"status":"success","artifact_name":"persian-read-speech-evaluation","metrics":{"wer":0.1}},
        "synthetic_overlap":{"status":"success","artifact_name":"synthetic-overlap-annotations","metrics":{"overlap_count":50}},
        "natural_conversation_evidence":False,"medical_readiness_claim":False,"warnings":["not natural audio"]
    }


def artifacts():
    return [
        {"name":"extraction-silver-baseline","id":11,"size_in_bytes":100,"expired":False},
        {"name":"retrieval-silver-baseline","id":12,"size_in_bytes":100,"expired":False},
        {"name":"speech-evaluation-group","id":13,"size_in_bytes":100,"expired":False},
    ]


class PostResultsTests(unittest.TestCase):
    def test_completion_requires_all_three_valid_groups_and_artifacts(self) -> None:
        summary=build_summary(
            extraction_job_status="success",retrieval_job_status="success",speech_group_status="success",
            extraction_metrics=metrics32(),retrieval_metrics=metrics48(),speech_report=speech_report(),
            artifact_catalog=artifacts(),repository="mojtaba-arvin/voxpipe",run_id=123,run_attempt=1,
            commit_sha="a"*40,run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        )
        self.assertTrue(summary["silver_baseline_complete"])
        self.assertEqual(summary["completion_label"],"silver-baseline-complete")
        self.assertEqual(summary["groups"]["speech"]["artifact"]["id"],13)

    def test_skipped_speech_group_keeps_completion_label_absent(self) -> None:
        summary=build_summary(
            extraction_job_status="success",retrieval_job_status="success",speech_group_status="skipped",
            extraction_metrics=metrics32(),retrieval_metrics=metrics48(),speech_report=None,
            artifact_catalog=artifacts()[:2],repository="mojtaba-arvin/voxpipe",run_id=123,run_attempt=1,
            commit_sha="a"*40,run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        )
        self.assertFalse(summary["silver_baseline_complete"])
        self.assertIsNone(summary["completion_label"])
        self.assertEqual(summary["groups"]["speech"]["status"],"skipped")

    def test_success_without_exact_artifact_reference_is_failure(self) -> None:
        summary=build_summary(
            extraction_job_status="success",retrieval_job_status="success",speech_group_status="success",
            extraction_metrics=metrics32(),retrieval_metrics=metrics48(),speech_report=speech_report(),
            artifact_catalog=artifacts()[:2],repository="mojtaba-arvin/voxpipe",run_id=123,run_attempt=1,
            commit_sha="a"*40,run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        )
        self.assertEqual(summary["groups"]["speech"]["status"],"failure")
        self.assertFalse(summary["silver_baseline_complete"])


if __name__ == "__main__":
    unittest.main()
