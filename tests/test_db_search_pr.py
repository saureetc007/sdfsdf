import unittest

from decisiontrace.db import Database
from decisiontrace.extractor import DecisionExtractor, SourceDoc
from decisiontrace.pr_context import find_relevant
from helpers import ScriptedProvider, dec

T1 = "We decided to use SQLite for the job queue because it needs zero setup. Redis is rejected: we do not want another service."
T2 = "I propose adopting GraphQL for the public API."


def store(db, text, payload, repo="o/r", key=None, **kw):
    doc = SourceDoc(text=text, source_key=key or "", repo=repo, **kw)
    res = DecisionExtractor(ScriptedProvider(payload)).extract(doc)
    sid, _ = db.upsert_source(doc, db.upsert_repository(repo))
    return sid, db.save_extraction(sid, res), doc


SQLITE = {"decisions": [dec("Use SQLite for the job queue", "accepted", ["We decided to use SQLite for the job queue because it needs zero setup."],
                            reasons=["needs zero setup"], alternatives=[{"name": "Redis", "rejected": True, "rejection_reason": "another service"}])]}
GQL = {"decisions": [dec("Adopt GraphQL for the public API", "proposed", ["I propose adopting GraphQL for the public API."])]}


class PersistenceAndSearch(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.sid1, n, _ = store(self.db, T1, SQLITE, key="o/r#issue:1")
        store(self.db, T2, GQL, repo="o/other", key="o/other#issue:9")

    def test_13_persistence_and_retrieval(self):
        d = self.db.search("SQLite")[0]
        self.assertEqual((d["status"], d["evidence_state"], d["repo"], d["source_key"]), ("accepted", "excerpt_verified", "o/r", "o/r#issue:1"))
        self.assertEqual(d["alternatives"][0]["name"], "Redis")
        self.assertEqual(d["evidence"][0]["state"], "verified_excerpt")
        self.assertEqual(self.db.get_source_text(self.sid1), T1)  # immutable snapshot retained for verification

    def test_13_repeated_save_does_not_duplicate(self):
        res = DecisionExtractor(ScriptedProvider(SQLITE)).extract(SourceDoc(text=T1))
        self.assertEqual(self.db.save_extraction(self.sid1, res), 0)
        self.assertEqual(len(self.db.search("")), 2)

    def test_14_search_by_decision_reasoning_alternative(self):
        self.assertEqual(len(self.db.search("job queue")), 1)
        self.assertEqual(len(self.db.search("zero setup")), 1)  # reasoning
        self.assertEqual(len(self.db.search("Redis")), 1)  # alternative
        self.assertEqual(self.db.search("kubernetes"), [])

    def test_14_filters(self):
        self.assertEqual([d["repo"] for d in self.db.search("", repo="o/other")], ["o/other"])
        self.assertEqual([d["status"] for d in self.db.search("", status="proposed")], ["proposed"])

    def test_14_hostile_query_strings_are_safe(self):
        for q in ('"; DROP TABLE decisions; --', "a OR b NOT (", "*", "'"):
            self.db.search(q)
        self.assertEqual(len(self.db.search("")), 2)

    def test_changed_source_replaces_old_decisions(self):
        doc = SourceDoc(text=T1 + " Edited.", source_key="o/r#issue:1", repo="o/r")
        sid, status = self.db.upsert_source(doc, self.db.upsert_repository("o/r"))
        self.assertEqual((sid, status), (self.sid1, "updated"))
        self.assertEqual(self.db.search("SQLite"), [])

    def test_validation_failures_recorded(self):
        bad = {"decisions": [dec("Use Mongo", "accepted", ["The team chose Mongo unanimously."])]}
        sid, _, _ = store(self.db, "unrelated text here", bad, key="k:bad")
        self.assertIn("evidence_not_found", [v["kind"] for v in self.db.validation_outcomes(sid)])
        self.assertEqual(self.db.search("Mongo")[0]["status"], "unresolved")


class PRContext(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        store(self.db, T1, SQLITE, key="o/r#issue:1")

    def test_relevant_when_multiple_distinct_terms_overlap(self):
        hits = find_relevant(self.db, "Add retry logic to the job queue worker with SQLite persistence")
        self.assertEqual(len(hits), 1)
        self.assertIn("sqlite", hits[0]["matched_terms"])
        self.assertEqual(hits[0]["decision"]["source_key"], "o/r#issue:1")

    def test_single_shared_technology_name_is_not_enough(self):
        self.assertEqual(find_relevant(self.db, "Fix typo in SQLite docs"), [])

    def test_excludes_the_prs_own_source(self):
        self.assertEqual(find_relevant(self.db, "job queue SQLite", exclude_source_key="o/r#issue:1"), [])

    def test_unverified_decisions_hidden_by_default(self):
        db = Database()
        store(db, "nothing relevant", {"decisions": [dec("Use SQLite for the job queue", "accepted", ["a quote that is not in the source"])]}, key="k")
        self.assertEqual(find_relevant(db, "SQLite job queue"), [])
        self.assertEqual(len(find_relevant(db, "SQLite job queue", include_unverified=True)), 1)


if __name__ == "__main__":
    unittest.main()
