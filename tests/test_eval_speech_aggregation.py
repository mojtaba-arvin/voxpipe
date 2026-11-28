from __future__ import annotations

import unittest
from unittest.mock import patch

from eval.issue47.aggregate_speech import build_report


def archive_report():
    return {
        "schema_version": 1, "status": "downloaded",
        "dataset_id": "cmu5xazgy00finq074vaqo1b3",
        "dataset_name": "Common Voice Scripted Speech 27.0 - Persian",
        "dataset_version": "cv-corpus-27.0-2026-09-11",
        "release_date": "2026-09-17", "locale": "fa", "task": "ASR",
        "license": "CC0-1.0", "source_kind": "scripted_read_speech",
        "source_url": "https://mozilladatacollective.com/datasets/cmu5xazgy00finq074vaqo1b3",
        "recorded_hours": 431.44, "validated_hours": 373.15, "clip_count": 395173,
        "speaker_count": 4668, "dataset_archive_size_bytes": 11236660956,
        "dataset_archive_checksum": "f71fd362846433376b529d3f2ce4258a4075d9da8fe2b0f4b12465c12672a65d",
        "downloaded_at": "2026-11-26T14:00:00Z", "sdk_version": "0.8.1",
        "archive_name": "common-voice-scripted-speech-27-0-persia-9d86e98b.tar.gz",
        "archive_size_bytes": 11236660956, "archive_sha256": "f71fd362846433376b529d3f2ce4258a4075d9da8fe2b0f4b12465c12672a65d",
        "audio_file_count": 395173,
        "metadata_file_count": 10, "download_seconds": 360.0,
        "warnings": ["full archive downloaded; 50 clips selected for evaluation"],
    }


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
        "peak_rss_bytes": 1000000, "torch_version": "2.13.0+cpu", "transformers_version": "5.20.1",
        "python_version": "3.13", "transcript_text_uploaded": False, "audio_uploaded": False,
        "speaker_ids_used": False, "warnings": ["read speech only"],
        "language_id_model_id": "speechbrain/lang-id-voxlingua107-ecapa",
        "language_id_model_revision": "0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9",
        "language_id_model_license": "Apache-2.0",
        "language_id_confidence_threshold": 0.0,
        "language_id_failures": {"arabic": 0, "other_or_undetermined": 0},
        "language_strategy": "independent_voxlingua107_gate_then_forced_persian",
        "requested_sample_count": 50, "selected_sample_count": 50,
        "duration_shortfall_count": 0, "candidate_scan_limit": 100,
        "candidate_rows_scanned": 52, "duration_rejected_count": 2,
        "duration_probe_failures": 0,
        "selection_policy": "first_requested_validated_tsv_rows_with_audio_duration_at_least_1s",
        "min_audio_duration_seconds": 1.0, "rejected_sample_count": 0,
        "transcription_failures": {"empty_output": 0},
        "language_id_supported_languages": ["ar", "fa"],
        "language_id_threshold_calibration": {
            "dataset_id": "google/fleurs",
            "dataset_revision": "6989e5bd2dc37546a93ae5d857bc04325e6a3738",
            "validation_run_id": 46144284846,
            "min_selective_accuracy": 0.99,
            "min_coverage": 0.5,
            "selected_threshold": 0.0,
            "dataset_license": "CC-BY-4.0",
        },
        "language_id_threshold_policy": "no_confidence_floor_top_label_fa_with_zero_and_tie_abstention",
        "language_id_held_out_control": {
            "dataset_id": "google/fleurs",
            "dataset_revision": "6989e5bd2dc37546a93ae5d857bc04325e6a3738",
            "dataset_license": "CC-BY-4.0",
            "test_run_id": 46144284846,
            "test_head_sha": "5f9855c0b01a57e8a6b4405afdc0a630a2ac77be",
            "sample_count": 400,
            "accuracy": 0.94,
            "macro_f1": 0.7727272727272727,
            "coverage": 0.94,
            "selective_accuracy": 1.0,
            "abstention_rate": 0.06,
            "persian_samples": 100,
            "persian_correct": 100,
            "arabic_samples": 100,
            "arabic_correct": 100,
            "persian_arabic_cross_confusions": 0,
            "negative_control_languages": ["en", "de"],
            "negative_control_false_accepts": 0,
            "negative_control_abstentions": 24,
        },
        "forced_language": "Persian", "max_new_tokens": 256, "do_sample": False,
        "batch_size": 1, "language_id_sample_rate": 16000,
        "speechbrain_version": "1.1.2", "torchaudio_version": "2.11.0+cpu",
        "huggingface_hub_version": "1.31.0"
    }


def alignment_report():
    return {
        "schema_version": 1, "status": "completed",
        "model_id": "jonatasgrosman/wav2vec2-large-xlsr-53-persian",
        "model_revision": "234714078a1398a9db88194c5a40fefe6f376dc1",
        "model_license": "Apache-2.0", "whisperx_version": "3.8.6",
        "device": "cpu", "model_dtype": "float32", "sample_rate": 16000, "batch_size": 1,
        "clip_count": 50, "aligned_clip_count": 50, "failed_clip_count": 0,
        "word_count": 100, "aligned_word_count": 100, "unaligned_word_count": 0,
        "timestamp_validation_failure_count": 0, "missing_word_timestamp_count": 0,
        "alignment_coverage": 1.0,
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
            archive_report=archive_report(),
            run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
        )
        self.assertEqual(report["status"], "success", report["warnings"])
        self.assertEqual(report["read_speech"]["metrics"]["wer"], 0.01)
        self.assertEqual(report["read_speech"]["metrics"]["alignment_coverage"], 1.0)
        self.assertEqual(report["read_speech"]["metrics"]["unaligned_word_count"], 0)
        self.assertEqual(report["read_speech"]["metrics"]["timestamp_validation_failure_count"], 0)
        self.assertEqual(report["read_speech"]["metrics"]["missing_word_timestamp_count"], 0)
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

    def test_full_archive_provenance_mismatch_rejects_read_speech(self) -> None:
        qwen = qwen_report()
        qwen["dataset_version"] = "unexpected-release"
        with patch("eval.issue47.aggregate_speech._validate_document"):
            report = build_report(
                read_speech_job_status="success", synthetic_job_status="success",
                qwen_report=qwen, alignment_report=alignment_report(),
                speaker_report={"schema_version":1,"diarization_status":"not_evaluated_read_speech","speaker_segments":[]},
                overlap_manifest=overlap_manifest(), run_id=123, commit_sha="a"*40,
                archive_report=archive_report(),
                run_url="https://github.com/mojtaba-arvin/voxpipe/actions/runs/123",
            )
        self.assertEqual(report["status"], "failure")
        self.assertEqual(report["read_speech"]["status"], "failure")
        self.assertTrue(any("dataset provenance mismatch" in warning for warning in report["warnings"]))

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
