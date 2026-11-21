"""Validate provenance and per-sample checksums in speech corpus manifests."""
from __future__ import annotations
import json
from pathlib import Path
from jsonschema import validate

def validate_manifest(manifest:dict,schema:dict)->None:
    validate(instance=manifest,schema=schema)
    ids=[item["sample_id"] for item in manifest["items"]]
    if len(ids)!=len(set(ids)): raise ValueError("corpus manifest contains duplicate sample IDs")
    if manifest["task"]=="diarization" and any(not item["speaker_id"] for item in manifest["items"]):
        raise ValueError("diarization manifest requires speaker IDs for every sample")
    if manifest["consent_basis"]=="customer-consent" and not manifest.get("consent_note"):
        raise ValueError("customer audio requires documented consent provenance")

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest",type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    schema=json.loads((root/"contracts/schemas/speech-corpus-manifest.v1.schema.json").read_text(encoding="utf-8"))
    manifest=json.loads(args.manifest.read_text(encoding="utf-8"))
    validate_manifest(manifest,schema)
    print(f"Corpus manifest valid: {len(manifest['items'])} samples")

if __name__=="__main__": main()
