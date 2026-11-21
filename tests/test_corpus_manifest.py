import unittest
from scripts.validate_corpus_manifest import validate_manifest
import json
from pathlib import Path

SCHEMA=json.loads((Path(__file__).resolve().parents[1]/"contracts/schemas/speech-corpus-manifest.v1.schema.json").read_text())
BASE={"schema_version":"1.0.0","task":"asr","dataset_id":"google/fleurs","dataset_revision":"6989e5bd2dc37546a93ae5d857bc04325e6a3738",
"config":"fa_ir","split":"test","license":"cc-by-4.0","attribution":"FLEURS CC BY 4.0","consent_basis":"public-dataset-license",
"source_file_sha256":"a"*64,"generated_at":"2026-11-21T00:00:00+00:00",
"items":[{"sample_id":"1","audio_sha256":"b"*64,"reference_sha256":"c"*64,"language":"fa-IR","duration_ms":1000,"speaker_id":None,"session_id":None}]}
class CorpusManifestTests(unittest.TestCase):
 def test_valid_manifest(self): validate_manifest(BASE,SCHEMA)
 def test_duplicate_samples_rejected(self):
  value={**BASE,"items":BASE["items"]*2}
  with self.assertRaises(ValueError): validate_manifest(value,SCHEMA)
 def test_diarization_requires_speaker_identity(self):
  item={**BASE,"task":"diarization"}
  with self.assertRaises(ValueError): validate_manifest(item,SCHEMA)
 def test_invalid_checksum_rejected(self):
  item={**BASE,"source_file_sha256":"not-a-checksum"}
  with self.assertRaises(Exception): validate_manifest(item,SCHEMA)
if __name__=="__main__": unittest.main()
