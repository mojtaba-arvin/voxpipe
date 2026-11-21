import unittest
from voxpipe.structuring.evaluation import evaluate_claims
class EvaluationTests(unittest.TestCase):
 def test_precision_recall_f1(self):
  report=evaluate_claims({"entities":[{"value":{"name":"A"}},{"value":{"name":"C"}}]},{"entities":[{"value":{"name":"A"}},{"value":{"name":"B"}}]})
  score=report["per_type"]["entities"]
  self.assertEqual((score.true_positive,score.false_positive,score.false_negative),(1,1,1))
  self.assertEqual((score.precision,score.recall,score.f1),(.5,.5,.5))
 def test_missing_predictions(self):
  report=evaluate_claims({},{"events":[{"value":{"kind":"call"}}]})
  self.assertEqual(report["micro"].recall,0)
  self.assertEqual(report["micro"].f1,0)
