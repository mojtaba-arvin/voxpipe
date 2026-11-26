from __future__ import annotations

import unittest

from eval.issue47.aggregate_speech import build_report


def qwen_report():
    return {
        "schema_version": 1, "status": "passed",
        "dataset_id": "cmu5xazgy00finq074vaqo1b3", "dataset_version": "cv-corpus-27.0-2026-09-11",
        "source_kind": "scripted_read_speech", "locale": "fa", "task": "ASR", "license": "CC0-1.0",
        "model_id": "Qwen/Qwen3-ASR-0.6B-hf", "model_revision": "40946090cac63fc01d723a83174e16515073d7c0",
        "model_license": "Apache-2.0", "device": "cpu", "compute_type": "float32",
        "sample_count": 50, "audio_duration_seconds": 10.0, "inference_seconds": 20.0,
        "real_time_factor": 2.0, "word_errors": 1, "character_errors": 2,
        "reference_words": 100, "reference_characters": 500, "wer": 0.01, "cer": 0.004,
        "peak_rss_bytes": 1000000, "torch_version": "2.15.0+cpu", "transformers_version": "5.20.1",
        "python_version": "3.13", "transcript_text_uploaded": False, "audio_uploaded": False,
        "speaker_ids_used": False, "warnings": ["read speech only"]
    }


def alignment_report():
    return {
        "schema_version": 1, "status": "completed",
        "model_id": "jonatasgrosman/wav2vec2-large-xlsr-53-persian",
        "model_revision": "234714078a1398a9db88194c5a40fefe6f376dc1",
        "model_license": "Apache-2.0", "whisperx_version": "3.8.6",
        "device": "cpu", "model_dtype": "float32", "sample_rate": 16000, "batch_size": 1,
        "clip_count": 50, "aligned_clip_count": 50, "failed_clip_count": 0,
        "word_count": 100, "aligned_word_count": 95, "alignment_coverage": 0.95,
        "alignment_intermediate_uploaded": False, "audio_uploaded": False,
        "transcript_text_uploaded": False, "speaker_ids_used": False,
        "diarization_status": "not_evaluated_read_speech", "speaker_segment_count": 0,
        "warnings": ["read speech only"]
    }


def overlap_manifest():
    return {
        "schema_version": 1, "status": "passed", "source_kind": "synthetic_template", "seed": 20261125,
        "conversation_count": 50, "overlap_count": 50, "sample_rate": 22050,
        "leading_silence_seconds": 0.25, "background_noise_snr_db": 30.0,
        "noise_seed_policy": "base_seed_plus_conversation_index",
        "tts_runtime": "sherpa-onnx", "tts_runtime_version": "1.13.8",
        "tts_runtime_license": "Apache-2.0",
        "voice_models": {"customer":"fa_IR-amir-medium-int8","agent":"fa_IR-ganji-medium-int8"},
        "voice_model_license": "CC0-1.0",
        "voice_asset_sha256": {"customer":"b79dbf0b6b9629a36fd541bfda83817ec12e2697557d87b918638445971eac46","agent":"2ae4658c3a69ef4f92e09d0014f4af31ca6c95e9ae417de3c34544b5e3a98120"},
        "voice_asset_download_bytes": {"customer":100,"agent":100},
        "mean_overlap_seconds": 0.5, "total_mixed_audio_seconds": 10.0,
        "annotation_file": "overlap_annotations.jsonl", "annotation_sha256": "a"*64,
        "audio_files_generated": 50, "audio_files_persisted": False,
        "audio_artifact_uploaded": False, "transcript_text_uploaded": False, "warnings":["synthetic"]
    }


class SpeechAggregateTests(unittest.TestCase):
    def test_complete_read_and_synthetic_branches_are_required_for_success(self) -> None:
        report = build_report(
            read_speech_job_status="success", synthetic_job_status="success",
            qwen_report=qwen_report(), alignment_report=alignment_report(),
            speaker_report={"schema_version":1,"diarization_status":"not_evaluated_read_speech","speaker_segments":[]},
            overlap_manifest=overlap_manifest(), run_id=123, commit_sha="a"*40,
            run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        )
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["read_speech"]["metrics"]["wer"], 0.01)
        self.assertEqual(report["synthetic_overlap"]["metrics"]["overlap_count"], 50)
        self.assertFalse(report["natural_conversation_evidence"])

    def test_skipped_and_partial_branches_never_become_success(self) -> None:
        report = build_report(
            read_speech_job_status="skipped", synthetic_job_status="success",
            qwen_report=None, alignment_report=None, speaker_report=None,
            overlap_manifest=overlap_manifest(), run_id=123, commit_sha="a"*40,
            run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        )
        self.assertEqual(report["status"], "failure")
        self.assertEqual(report["read_speech"]["status"], "skipped")

    def test_alignment_partial_is_rejected_as_complete_group(self) -> None:
        alignment = alignment_report()
        alignment["status"] = "partial"
        report = build_report(
            read_speech_job_status="success", synthetic_job_status="success",
            qwen_report=qwen_report(), alignment_report=alignment,
            speaker_report={"schema_version":1,"diarization_status":"not_evaluated_read_speech","speaker_segments":[]},
            overlap_manifest=overlap_manifest(), run_id=123, commit_sha="a"*40,
            run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        )
        self.assertEqual(report["status"], "failure")
        self.assertEqual(report["read_speech"]["status"], "failure")


if __name__ == "__main__":
    unittest.main()
