"""Keep authored schedules, freshness expectations and public cadence aligned."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def daily_slots(expression):
    minute, hours, day, month, weekday = expression.split()
    if (day, month, weekday) != ('*', '*', '*'):
        raise ValueError('Expected every-day cadence')
    minute = int(minute)
    parsed_hours = [int(hour) for hour in hours.split(',')]
    if not 0 <= minute < 60 or any(not 0 <= hour < 24 for hour in parsed_hours):
        raise ValueError('Invalid UTC clock time')
    return sorted(hour * 60 + minute for hour in parsed_hours)


def workflow_schedule(path):
    text = path.read_text()
    slots = re.findall(r"^    - cron: '([^']+)'\n      timezone: (\S+)\s*$", text, re.M)
    if len(slots) != 1:
        raise ValueError('Expected one explicitly zoned schedule entry')
    return slots[0]


class ScanScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / 'data/scan-config.json').read_text())

    def test_source_and_publication_match_configured_utc_slots(self):
        self.assertEqual(self.config['timezone'], 'UTC')
        for workflow, key, times_key in [('.github/workflows/scan.yml', 'scan_cron', 'scan_times_local'),
                                         ('automation/publish.yml', 'publish_cron', 'publish_times_local')]:
            with self.subTest(workflow=workflow):
                expression, timezone = workflow_schedule(ROOT / workflow)
                self.assertEqual((expression, timezone), (self.config[key], self.config['timezone']))
                times = [f'{slot // 60:02d}:{slot % 60:02d}' for slot in daily_slots(expression)]
                self.assertEqual(times, self.config[times_key])

    def test_both_schedules_have_three_equal_eight_hour_intervals(self):
        for key in ['scan_cron', 'publish_cron']:
            with self.subTest(key=key):
                slots = daily_slots(self.config[key])
                self.assertEqual(len(slots), 3)
                self.assertEqual(len(set(slots)), 3)
                gaps = [b - a for a, b in zip(slots, slots[1:] + [slots[0] + 1440])]
                self.assertEqual(gaps, [480, 480, 480])

    def test_publication_leaves_scan_and_quality_buffer(self):
        scans = daily_slots(self.config['scan_cron'])
        publications = daily_slots(self.config['publish_cron'])
        text = (ROOT / '.github/workflows/scan.yml').read_text()
        scan_job = text.split('\n  scan:\n', 1)[1].split('\n  dispatch_quality:', 1)[0]
        scan_timeout = int(re.search(r'^    timeout-minutes: (\d+)$', scan_job, re.M).group(1))
        delays = [(publish - scan) % 1440 for scan, publish in zip(scans, publications)]
        self.assertEqual(delays, [90, 90, 90])
        self.assertTrue(all(delay >= scan_timeout + 30 for delay in delays))
        publication = (ROOT / 'automation/publish.yml').read_text()
        gate = publication.index('name: Validate source data and website behavior')
        deployment = publication.index('name: Build website from the verified catalog')
        self.assertLess(gate, deployment)
        for check in ['python scripts/catalog.py validate', 'python scripts/catalog.py build --check',
                      'python -m unittest discover -s tests -v', 'node --test tests/web.test.mjs']:
            self.assertIn(check, publication[gate:deployment])

    def test_freshness_limit_is_one_interval_plus_two_hours(self):
        self.assertEqual(self.config['max_scan_age_hours'], 8 + 2)
        self.assertEqual(self.config['max_consecutive_partial'], 4)
        self.assertEqual(self.config['recent_lookback_days'], 14)
        self.assertEqual(self.config['recent_max_results'], 500)
        self.assertEqual(self.config['harvest_limit'], 500)
        freshness = (ROOT / '.github/workflows/freshness.yml').read_text()
        self.assertIn("cron: '37 */3 * * *'", freshness)

    def test_public_copy_has_no_stale_twice_daily_claim(self):
        paths = ['README.md', 'README.zh-CN.md', 'CONTRIBUTING.md', 'docs/maintenance.md',
                 'docs/methodology.md', 'docs/website.md', 'scripts/search_pages.py',
                 'scripts/agent_pages.py', 'scripts/prepare_publication.py', 'web/app.js']
        stale = re.compile(r'twice[ -]daily|twice a day|both daily|both scheduled|每日两次|每日计划执行两次', re.I)
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(stale.search((ROOT / path).read_text()))
        for time in self.config['scan_times_local']:
            self.assertIn(time, (ROOT / 'README.md').read_text())
            self.assertIn(time, (ROOT / 'README.zh-CN.md').read_text())
        for time in self.config['publish_times_local']:
            self.assertIn(time, (ROOT / 'docs/maintenance.md').read_text())


if __name__ == '__main__':
    unittest.main()
