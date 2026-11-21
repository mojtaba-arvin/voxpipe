import unittest
from types import SimpleNamespace
from voxpipe.speech.vad import SileroVADAdapter, SpeechRegion, evaluate_vad
from voxpipe.speech.language_id import SpeechBrainLanguageIdentifier, evaluate_language_id
from voxpipe.speech.diarization import ECAPADiarizationAdapter, SpeakerTurn, evaluate_diarization

DIGEST="a"*64

class FakeVAD:
    def is_speech(self,frame,sample_rate): return frame[0] == 1

class SpeechIntelligenceTests(unittest.TestCase):
    def test_vad_boundary_matching_uses_best_overlapping_region(self):
        from voxpipe.speech.vad import SpeechRegion, evaluate_vad
        metrics = evaluate_vad([SpeechRegion(0, 1000)],
            [SpeechRegion(0, 200), SpeechRegion(200, 1000)], duration_ms=1000)
        self.assertEqual(metrics["matched_regions"], 1)
        self.assertEqual(metrics["unmatched_hypothesis_regions"], 1)
        self.assertEqual(metrics["mean_boundary_error_ms"], 100.0)

    def test_silero_vad_converts_sample_offsets_and_merges(self):
        def timestamps(wave,model,**kwargs):
            return [{"start":0,"end":1600},{"start":1500,"end":3200}]
        result=SileroVADAdapter(model=object(),timestamp_fn=timestamps).detect([0.1]*3200,sample_rate=16000,audio_sha256=DIGEST)
        self.assertEqual(result.regions,(SpeechRegion(0,200),))
        self.assertEqual(result.duration_ms,200)
    def test_webrtc_vad_and_vad_metrics(self):
        pcm=(b"\x01\x00"*160)+(b"\x00\x00"*160)
        from voxpipe.speech.vad import WebRTCVADAdapter
        result=WebRTCVADAdapter(vad=FakeVAD(),frame_duration_ms=10).detect(pcm,sample_rate=16000,audio_sha256=DIGEST)
        self.assertEqual(result.regions,(SpeechRegion(0,10),))
        score=evaluate_vad([SpeechRegion(0,100)],[SpeechRegion(10,90)],duration_ms=100,frame_ms=10)
        self.assertGreater(score["f1"],0.7)
        self.assertEqual(score["mean_boundary_error_ms"],10)
    def test_language_id_abstains_below_threshold_and_reports_metrics(self):
        class Model:
            def classify(self,samples,sample_rate): return [0.8,0.2],["__label__fa","__label__en"]
        adapter=SpeechBrainLanguageIdentifier(model=Model(),threshold=0.85)
        pred=adapter.identify([0.0]*16000,sample_rate=16000)
        self.assertEqual(pred.language,"und")
        self.assertEqual(pred.confidence,0.8)
        metrics=evaluate_language_id(["fa","en"],[
            SimpleNamespace(language="fa",confidence=.9),
            SimpleNamespace(language="fa",confidence=.6)])
        self.assertEqual(metrics["accuracy"],.5)
        self.assertIn("en",metrics["confusion_matrix"])

    def test_speechbrain_label_inventory_is_multiclass_not_top_prediction_only(self):
        from voxpipe.speech.language_id import SpeechBrainLanguageIdentifier
        encoder = SimpleNamespace(ind2lab={0: "fa: Persian", 1: "en: English", 2: "de: German"})
        model = SimpleNamespace(hparams=SimpleNamespace(label_encoder=encoder))
        labels = SpeechBrainLanguageIdentifier._class_labels(model, 3)
        self.assertEqual(labels, ["fa: Persian", "en: English", "de: German"])
        with self.assertRaises(ValueError):
            SpeechBrainLanguageIdentifier._class_labels(model, 4)
    def test_ecapa_clustering_and_der_jer(self):
        class Embedder:
            def embed(self,samples,sample_rate): return [1.0,0.0] if samples[0]>0 else [0.0,1.0]
        audio=[1.0]*32000+[-1.0]*32000
        adapter=ECAPADiarizationAdapter(embedder=Embedder(),window_ms=1000,similarity_threshold=.8)
        result=adapter.diarize(audio,audio_sha256=DIGEST,speech_regions=[SpeechRegion(0,4000)])
        self.assertEqual([x.speaker_id for x in result.turns],["speaker_1","speaker_2"])
        reference=[SpeakerTurn(0,2000,"ref_a",1),SpeakerTurn(2000,4000,"ref_b",1)]
        score=evaluate_diarization(reference,result.turns,duration_ms=4000,collar_ms=0,frame_ms=100)
        self.assertEqual(score["der"],0)
        self.assertEqual(score["jer"],0)

if __name__=="__main__": unittest.main()
