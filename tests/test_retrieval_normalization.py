"""Persian retrieval normalization is deterministic and preserves search syntax."""
import unittest

from voxpipe.retrieval.normalization import normalize_persian_query


class PersianQueryNormalizationTests(unittest.TestCase):
    def test_folds_arabic_persian_letter_and_digit_variants(self):
        self.assertEqual(normalize_persian_query("كِتاب يَك ۱۲۳ ١٢٣"), "کتاب یک 123 123")

    def test_zwnj_is_a_token_boundary_and_whitespace_is_collapsed(self):
        self.assertEqual(normalize_persian_query("می‌روم   به\\tخانه"), "می روم به خانه")

    def test_preserves_phrase_and_negation_search_operators(self):
        self.assertEqual(normalize_persian_query('"جلسه فروش" -لغو'), '"جلسه فروش" -لغو')

    def test_mixed_latin_text_is_casefolded(self):
        self.assertEqual(normalize_persian_query("API Meeting"), "api meeting")

    def test_empty_and_non_string_inputs(self):
        self.assertEqual(normalize_persian_query("  "), "")
        with self.assertRaises(TypeError):
            normalize_persian_query(None)


if __name__ == "__main__":
    unittest.main()
