# Evaluation corpus contract

## Purpose

The extraction generator and retrieval evaluator consume related but distinct records. A conversation is a source object containing ordered utterances; a retrieval passage is an independently addressable text unit used for indexing and relevance evaluation. Neither schema is a substitute for the other.

## Files

- `eval/schemas/conversation.schema.json`: conversation and utterance records produced by Group A.
- `eval/schemas/corpus.schema.json`: retrieval passages consumed by Group B.
- `eval/_placeholder/corpus.jsonl`: five synthetic Persian passages for deterministic development and CI.
- `eval/_shared/corpus_loader.py`: validates the corpus before returning records.

## CORPUS_PATH

Group B reads `CORPUS_PATH` when it is set to a non-empty path. If it is unset or empty, the loader uses the committed five-record placeholder. The loader does not download data or infer consent, license, or provenance. It rejects malformed JSONL, schema violations, duplicate passage IDs, empty corpora, and files larger than 20 MiB.

The placeholder records are marked `source_kind=synthetic_template`; metrics from them are plumbing/silver-baseline evidence only. A supplied corpus must explicitly identify each record's source kind and split. Read-speech remains read-speech, and consented natural conversation requires separate documented consent and privacy review.

## Mapping conversations to passages

A producer may map one conversation to one passage or create multiple passages from utterance groups. Each passage should carry a stable `passage_id`, the source `conversation_id`, and the contributing `utterance_id` values where available. The mapping must preserve language, split, and source kind. Passage IDs must remain stable across repeated evaluation runs.

Do not commit real names, phone numbers, addresses, consent forms, or customer transcripts. Do not represent synthetic or heuristic annotations as human gold. Do not claim natural-conversation coverage from read-speech or synthetic audio.
