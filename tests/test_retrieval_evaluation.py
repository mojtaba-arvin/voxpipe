import math
import unittest

from voxpipe.retrieval.evaluation import evaluate_retrieval


class RetrievalEvaluationTests(unittest.TestCase):
    def test_recall_mrr_and_ndcg_are_macro_averaged(self):
        result = evaluate_retrieval(
            {"q1": {"a", "b"}, "q2": {"x"}},
            {"q1": ["a", "z", "b"], "q2": ["z", "x"]},
            ks=(1, 2, 3),
        )
        self.assertAlmostEqual(result["recall_at_k"]["1"], .25)
        self.assertAlmostEqual(result["recall_at_k"]["2"], .75)
        self.assertAlmostEqual(result["mrr"], .75)
        q1 = (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
        q2 = 1 / math.log2(3)
        self.assertAlmostEqual(result["ndcg"], (q1 + q2) / 2)

    def test_empty_relevance_and_duplicate_results_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_retrieval({}, {})
        with self.assertRaises(ValueError):
            evaluate_retrieval({"q": {"a"}}, {"q": ["a", "a"]})


if __name__ == "__main__":
    unittest.main()
