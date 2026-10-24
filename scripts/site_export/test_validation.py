"""Regression checks for publishable validation metrics and the raw-only publication rule."""
import tempfile
import unittest
from pathlib import Path

from export import compact_validation


class ValidationExportTests(unittest.TestCase):
    def export_fixture(self, text):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            path = Path(temporary) / "summary.csv"
            path.write_text(text)
            return compact_validation(path)

    def test_invalid_after_rows_never_replace_raw_agreement(self):
        result = self.export_fixture(
            "comparison,label,phase,n,agreement,kappa\n"
            "sim_vs_honeypot,honeypot,before,549,0.956284153,0.726332406\n"
            "sim_vs_honeypot,honeypot,after,525,1,1\n"
        )
        self.assertEqual(result["phase"], "before")
        self.assertEqual(len(result["comparisons"]), 1)
        self.assertEqual(result["comparisons"][0]["n"], 549)
        self.assertAlmostEqual(result["comparisons"][0]["agreement"], 0.95628415)

    def test_revised_raw_schema_ignores_legacy_accuracy_columns(self):
        result = self.export_fixture(
            "comparison,label,phase,raw_n,raw_agreement,kappa,ours_accuracy\n"
            "sim_vs_honeypot,honeypot,raw,549,0.956284153,0.726332406,\n"
            "sim_vs_honeypot,honeypot,adjudicated,549,0.956284153,0.726332406,0.99\n"
        )
        self.assertEqual(result["phase"], "before")
        self.assertEqual(result["comparisons"][0].get("adjudicated_accuracy", {}), {})

    def test_explicit_accuracy_columns_do_not_override_raw_headline(self):
        result = self.export_fixture(
            "comparison,label,phase,raw_n,raw_agreement,kappa,adjudicated_accuracy_sim\n"
            "sim_vs_honeypot,honeypot,raw,549,0.956284153,0.726332406,\n"
            "sim_vs_honeypot,honeypot,adjudicated,549,0.956284153,0.726332406,0\n"
        )
        self.assertEqual(result["phase"], "before")
        self.assertNotIn("adjudicated_accuracy", result["comparisons"][0])

    def test_empty_accuracy_columns_keep_the_raw_report(self):
        result = self.export_fixture(
            "comparison,label,phase,n,agreement,kappa,adjudicated_accuracy_sim\n"
            "sim_vs_honeypot,honeypot,before,549,0.956284153,0.726332406,\n"
        )
        self.assertEqual(result["phase"], "before")


if __name__ == "__main__":
    unittest.main()
