import unittest

from decisiontrace.schema import Decision, SchemaError, parse_model_output
from decisiontrace.verifier import check_excerpt, verify_decisions

SRC = "Line one.\nWe decided to use\n  SQLite   because it's simple.\nBob said \u201cship it\u201d today."


class Verifier(unittest.TestCase):
    def test_8_exact_match_with_offsets(self):
        c = check_excerpt(SRC, "Line one.\nWe decided to use")
        self.assertEqual((c.state, SRC[c.start:c.end]), ("verified_excerpt", "Line one.\nWe decided to use"))

    def test_8b_whitespace_and_quote_style_insensitive(self):
        self.assertEqual(check_excerpt(SRC, "We decided to use SQLite because it's simple.").state, "verified_excerpt")
        self.assertEqual(check_excerpt(SRC, 'Bob said "ship it" today.').state, "verified_excerpt")

    def test_9_absent_excerpt_rejected(self):
        self.assertEqual(check_excerpt(SRC, "We chose PostgreSQL after a long review.").state, "not_found")

    def test_case_sensitive(self):
        self.assertEqual(check_excerpt(SRC, "we decided to use sqlite because it's simple.").state, "not_found")

    def test_too_short_excerpt_not_accepted(self):
        self.assertEqual(check_excerpt(SRC, "use").state, "too_short")

    def test_states(self):
        d1, d2, d3, d4 = (Decision("a", "proposed", evidence=e) for e in ([], ["We decided to use SQLite because it's simple."], ["this is not in the source at all"], ["We decided to use SQLite because it's simple.", "this is not in the source at all"]))
        verify_decisions(SRC, [d1, d2, d3, d4])
        self.assertEqual([d.evidence_state for d in (d1, d2, d3, d4)], ["missing_evidence", "excerpt_verified", "unsupported", "partially_verified"])
        self.assertIn("proposal_without_verified_evidence", d1.flags)

    def test_label_does_not_claim_logical_support(self):
        d = Decision("a", "proposed", evidence=["We decided to use SQLite because it's simple."])
        verify_decisions(SRC, [d])
        self.assertIn("text match only", d.label)


class SchemaValidation(unittest.TestCase):
    def test_bad_status(self):
        with self.assertRaises(SchemaError):
            parse_model_output('{"decisions":[{"decision":"x","status":"done"}]}')

    def test_not_json(self):
        with self.assertRaises(SchemaError):
            parse_model_output("Sure! Here are the decisions: ...")

    def test_code_fence_tolerated(self):
        self.assertEqual(parse_model_output('```json\n{"decisions": []}\n```'), [])

    def test_wrong_types(self):
        for bad in ('{"decisions": "x"}', '{"decisions":[{"decision":"x","status":"accepted","reasons":"because"}]}', "[]"):
            with self.assertRaises(SchemaError):
                parse_model_output(bad)


if __name__ == "__main__":
    unittest.main()
