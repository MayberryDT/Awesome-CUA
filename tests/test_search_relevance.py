"""Catalog search ranks records named after the query before records that only mention it."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Loads the real catalog the way index.json does and prints the ranked names for each query.
SCRIPT = r"""
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const [core, catalog, topicsFile, queriesJSON] = process.argv.slice(1);
const {filtered, parseState, recordSearchText, normalize, nameRelevance} = await import(pathToFileURL(core).href);
const topics = Object.fromEntries(Object.entries(JSON.parse(readFileSync(topicsFile, 'utf8'))).map(([k, v]) => [k, [v.en, v.zh]]));
const rows = readFileSync(catalog, 'utf8').trim().split('\n').map(JSON.parse);
rows.forEach(r => r.search_text = recordSearchText(r, topics));
const out = {};
for (const [q, kind] of JSON.parse(queriesJSON)) {
  out[q + '|' + kind] = filtered(rows, {...parseState(''), q, kind}).map(r => ({name: r.name, curated: r.review_status === 'curated', tier: nameRelevance(r, normalize(q))}));
}
console.log(JSON.stringify(out));
"""
QUERIES = [["android", ""], ["mobile agent", ""], ["web agent", ""], ["os world", ""], ["ui tars", ""], ["ui-tars", ""], ["screenspot", ""], ["grounding", "model"], ["webarena", "paper"]]


@unittest.skipUnless(shutil.which("node"), "Node is required to run the catalog search module")
class SearchRelevanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(["node", "--input-type=module", "-e", SCRIPT, str(ROOT / "web/catalog-core.js"), str(ROOT / "data/catalog.jsonl"),
                                 str(ROOT / "data/topics.json"), json.dumps(QUERIES)], capture_output=True, text=True, check=True)
        cls.results = json.loads(result.stdout)

    def ranked(self, q, kind=""):
        return self.results[q + "|" + kind]

    def test_curated_records_named_after_the_query_come_first(self):
        for q in ("android", "mobile agent", "ui-tars", "screenspot"):
            tiers = [row["tier"] for row in self.ranked(q) if row["curated"]]
            # Exact names, then names starting with the query, then names containing every query word, then summary-only matches.
            self.assertEqual(tiers, sorted(tiers), q)
            self.assertLess(tiers[0], 3, q)

    def test_first_page_is_led_by_name_matches(self):
        first = [row["name"] for row in self.ranked("android")[:24]]
        self.assertEqual(first[0], "AndroidWorld")
        for name in ("AndroidArena", "AndroidControl", "Android in the Wild"):
            self.assertIn(name, first)
        self.assertNotIn("Adaptive Milestone Reward for GUI Agents", first)
        self.assertEqual(self.ranked("mobile agent")[0]["name"], "Mobile-Agent")
        self.assertTrue(all(row["tier"] < 3 for row in self.ranked("ui-tars")[:6]))

    def test_exact_name_outranks_featured_and_alphabetical_order(self):
        self.assertEqual(self.ranked("screenspot")[0]["name"], "ScreenSpot")
        self.assertEqual(self.ranked("webarena", "paper")[0]["name"], "WebArena: A Realistic Web Environment for Building Autonomous Agents")

    def test_compound_names_typed_with_spaces_are_found(self):
        self.assertEqual(self.ranked("os world")[0]["name"], "OSWorld")
        self.assertEqual(self.ranked("ui tars")[0]["name"], "UI-TARS")
        # Joining words of one title must not match across a word boundary inside another word ("GUITAR: Structured…").
        self.assertNotIn("GUITAR: Structured Failure Diagnosis of GUI Agents via State Transitions", [row["name"] for row in self.ranked("ui tars")])

    def test_query_without_name_matches_keeps_featured_order(self):
        names = [row["name"] for row in self.ranked("grounding", "model")]
        self.assertEqual(names[:2], ["UI-TARS", "UI-Venus"])


if __name__ == "__main__":
    unittest.main()
