import copy
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import catalog
import build


def osworld():
    row = next(r for r in catalog.read_jsonl(catalog.DATA / "catalog.jsonl") if r["id"] == "arxiv:2404.07972")
    return {**copy.deepcopy(row), "relations": []}


def evidence(key, value):
    return lambda row: row["evidence"][0].__setitem__(key, value)


def field(key, value):
    return lambda row: row.__setitem__(key, value)


MALFORMED = {
    "url": field("url", None), "kind": field("kind", ["paper"]), "review_status": field("review_status", ["curated"]), "scope": field("scope", ["core"]),
    "published_at": field("published_at", 20240411), "last_verified_at": field("last_verified_at", None),
    "identifiers": field("identifiers", None), "licenses": field("licenses", []), "details": field("details", None),
    "environments": field("environments", [["web"]]), "relations": field("relations", [None]),
    "evidence": evidence("retrieved_at", None),
}


class MalformedRecordTests(unittest.TestCase):
    def test_fixture_is_valid(self):
        self.assertEqual(build.validate_records([osworld()]), [])

    def test_wrong_field_types_are_reported_not_raised(self):
        cases = {**MALFORMED, "evidence sha256": evidence("sha256", None), "evidence supports": evidence("supports", None),
            "evidence items": field("evidence", [None]), "relation target": field("relations", [{"type": "uses", "target_id": ["x"], "evidence_url": "https://example.com"}]),
            "preferred arXiv": lambda row: row["identifiers"].__setitem__("preferred_arxiv", 2404), "licenses null": field("licenses", None),
            "details list": field("details", []), "id": field("id", ["arxiv:2404.07972"])}
        for name, mutate in cases.items():
            with self.subTest(name):
                row = osworld()
                mutate(row)
                errors = build.validate_records([row])
                self.assertEqual(len(errors), 1, errors)
                self.assertIn("malformed fields", errors[0])
        for key, mutate in MALFORMED.items():
            row = osworld()
            mutate(row)
            self.assertEqual(build.validate_records([row]), [f"arxiv:2404.07972: malformed fields {key}"])

    def test_malformed_record_does_not_hide_other_errors(self):
        malformed, bad = osworld(), osworld()
        malformed.update(id="arxiv:2404.00001", url=None)
        # Keep the synthetic identity independent of OSWorld's publication.
        malformed["details"].pop("publication", None)
        malformed["identifiers"].pop("doi", None)
        bad["scope"] = "unknown"
        self.assertEqual(build.validate_records([malformed, bad]), ["arxiv:2404.00001: malformed fields url", "arxiv:2404.07972: invalid scope"])

    def test_missing_verification_date_is_not_a_date_format_error(self):
        row = osworld()
        del row["last_verified_at"]
        self.assertEqual(build.validate_records([row]), ["arxiv:2404.07972: missing fields last_verified_at"])
        row["published_at"] = "2024-13-01"
        self.assertIn("arxiv:2404.07972: invalid published_at", build.validate_records([row]))

    def test_validate_reports_schema_errors_after_record_checks(self):
        row = osworld()
        row["url"] = None
        with tempfile.TemporaryDirectory() as directory:
            catalog.write_jsonl(Path(directory) / "catalog.jsonl", [row])
            with mock.patch.object(build, "DATA", Path(directory)), self.assertRaises(ValueError) as raised:
                build.validate(types.SimpleNamespace())
        self.assertIn("arxiv:2404.07972: malformed fields url", str(raised.exception))
        self.assertIn("arxiv:2404.07972: None is not of type 'string'", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
