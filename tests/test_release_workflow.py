import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from test_data_release_package import make_release_fixture

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
CATALOG = ROOT / ".github" / "workflows" / "catalog.yml"
EXPORTS = {"data/catalog.jsonl": "catalog.jsonl", "data/coverage.json": "coverage.json", "dist/catalog.json": "catalog.json", "dist/catalog.sqlite": "catalog.sqlite",
           "dist/site/downloads/topics.json": "topics.json", "dist/site/downloads/benchmarks-profiles.json": "benchmarks-profiles.json",
           "dist/site/downloads/datasets-profiles.json": "datasets-profiles.json"}


def jobs(text):
    found, name = {}, None
    for line in text.split("\njobs:\n", 1)[1].splitlines():
        header = re.fullmatch(r"  ([A-Za-z_][\w-]*):\s*", line)
        if header:
            name = header.group(1)
            found[name] = []
        elif name:
            found[name].append(line)
    return found


def step_script(lines, name):
    body = lines[lines.index(f"      - name: {name}") + 1:]
    body = body[:next((i for i, line in enumerate(body) if line.startswith("      - ")), len(body))]
    run = next(i for i, line in enumerate(body) if line.strip().startswith("run:"))
    if body[run].strip() == "run: |":
        return textwrap.dedent("\n".join(body[run + 1:])).strip("\n") + "\n"
    return body[run].split("run: ", 1)[1] + "\n"


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = RELEASE.read_text()
        self.jobs = jobs(self.text)

    def test_runs_for_version_tags_and_manual_dispatch_only(self):
        triggers = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  (\w+):", triggers), ["push", "workflow_dispatch"])
        self.assertIn("  push:\n    tags: ['v*']\n", triggers)

    def test_only_the_release_job_can_write_and_it_runs_no_repository_code(self):
        self.assertIn("\npermissions: {}\n", self.text)
        self.assertEqual(sorted(self.jobs), ["build", "release"])
        self.assertIn("    permissions:\n      contents: read", "\n".join(self.jobs["build"]))
        release = "\n".join(self.jobs["release"])
        self.assertIn("    permissions:\n      contents: write", release)
        self.assertEqual(self.text.count("contents: write"), 1)
        self.assertIn("    needs: build\n    if: startsWith(github.ref, 'refs/tags/v')\n", release)
        self.assertNotIn("actions/checkout@", release)
        self.assertNotRegex(release, r"\b(python|node|pip)\b")

    def test_never_creates_or_moves_a_tag(self):
        self.assertNotRegex(self.text, r"git (tag|push)|/git/refs|--target")
        self.assertRegex(step_script(self.jobs["release"], "Publish the GitHub release"), r"^gh release create \"\$GITHUB_REF_NAME\" .*--verify-tag ")

    def test_build_job_validates_then_builds_with_the_ci_toolchain(self):
        build = self.jobs["build"]
        commands = [re.sub(r"^(- )?run: ", "", line.strip()) for line in build if re.match(r" +(- )?run: (?!\|)|          python scripts/", line)]
        self.assertEqual(commands[:5], ["python -m pip install --require-hashes -r requirements-dev.txt", "python scripts/catalog.py validate",
                                        "python scripts/catalog.py build --check", "python scripts/catalog.py build", "python scripts/build_site.py"])
        catalog = CATALOG.read_text()
        for line in ("      - uses: actions/setup-python@", "          python-version: "):
            self.assertIn(next(l for l in catalog.splitlines() if l.startswith(line)), build)
        checkout = build.index(next(l for l in build if "actions/checkout@" in l))
        self.assertEqual(build[checkout + 1:checkout + 3], ["        with:", "          persist-credentials: false"])


class ReleaseStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.work = self.base / "work"
        (self.base / "bin").mkdir()
        os.symlink(sys.executable, self.base / "bin" / "python")
        # The fake gh records its arguments and the credentials it can see.
        (self.base / "bin" / "gh").write_text('#!/bin/sh\nprintf \'%s\\n\' "$GH_TOKEN" "$@" > "$CALLS"\n')
        (self.base / "bin" / "gh").chmod(0o755)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org", "GITHUB_REPOSITORY": "reacher-z/Awesome-CUA",
                    "GITHUB_REF_NAME": "v2026.10.17", "GITHUB_REF": "refs/tags/v2026.10.17", "GH_TOKEN": "ghs_exampletoken", "CALLS": str(self.base / "calls"),
                    "PATH": f"{self.base / 'bin'}{os.pathsep}{os.environ['PATH']}"}
        make_release_fixture(self.work, with_scripts=True)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "Snapshot")
        self.commit = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.work, env=self.env, check=True, capture_output=True, text=True).stdout

    def run_step(self, job, name):
        script = step_script(jobs(RELEASE.read_text())[job], name)
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=self.work, env=self.env, capture_output=True, text=True)

    def test_collects_every_export_with_checksums_and_notes_naming_counts_and_commit(self):
        result = self.run_step("build", "Collect release assets, checksums and notes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assets = self.work / "release/assets"
        self.assertEqual(sorted(p.name for p in assets.iterdir()), sorted([*EXPORTS.values(), "awesomecua-data.zip", "release-manifest.json", "SHA256SUMS"]))
        for source, name in EXPORTS.items():
            self.assertEqual((assets / name).read_bytes(), (self.work / source).read_bytes())
        sums = (assets / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(sorted(line.split("  ", 1)[1] for line in sums), sorted([*EXPORTS.values(), "awesomecua-data.zip", "release-manifest.json"]))
        check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=assets, capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        notes = (self.work / "release/notes.md").read_text()
        self.assertIn(f"[`{self.commit}`](https://github.com/reacher-z/Awesome-CUA/tree/{self.commit})", notes)
        self.assertIn("as of 2026-10-17T00:00:00Z", notes)
        for row in ("| Active records | 3 |", "| Curated | 2 |", "| Metadata only | 1 |", "| Kind: dataset | 1 |", "| Kind: paper | 2 |",
                    "| Benchmark profiles | 0 |", "| Dataset profiles | 1 |", "| Topics | 2 |"):
            self.assertIn(row, notes)
        self.assertIn("sha256sum -c SHA256SUMS", notes)
        manifest = json.loads((assets / "release-manifest.json").read_text())
        self.assertEqual(manifest["source_commit"], self.commit)
        self.assertEqual(manifest["source_ref"], self.env["GITHUB_REF"])
        self.assertIn("awesomecua-data.zip", notes)

    def test_collect_can_repeat_without_hashing_sha256sums_itself(self):
        for _ in range(2):
            result=self.run_step("build", "Collect release assets, checksums and notes")
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            check=subprocess.run(["sha256sum","-c","SHA256SUMS"],cwd=self.work/"release/assets",capture_output=True,text=True)
            self.assertEqual(check.returncode,0,check.stdout+check.stderr)
        sums=(self.work/"release/assets/SHA256SUMS").read_text()
        self.assertNotIn("  SHA256SUMS",sums)

    def test_unexpected_residual_asset_blocks_collection(self):
        assets=self.work/"release/assets";assets.mkdir(parents=True);(assets/"extra.bin").write_bytes(b"not an approved export")
        result=self.run_step("build", "Collect release assets, checksums and notes")
        self.assertNotEqual(result.returncode,0)
        self.assertIn("Unexpected or unsafe release asset",result.stderr)

    def test_a_missing_export_fails_the_build_instead_of_releasing_without_it(self):
        (self.work / "dist/site/downloads/datasets-profiles.json").unlink()
        self.assertNotEqual(self.run_step("build", "Collect release assets, checksums and notes").returncode, 0)

    def test_publish_attaches_every_asset_to_the_existing_tag(self):
        self.assertEqual(self.run_step("build", "Collect release assets, checksums and notes").returncode, 0)
        result = self.run_step("release", "Publish the GitHub release")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        token, *args = (self.base / "calls").read_text().splitlines()
        self.assertEqual(token, "ghs_exampletoken")
        self.assertEqual(args[:3], ["release", "create", "v2026.10.17"])
        self.assertEqual(sorted(args[3:-7]), sorted(f"release/assets/{name}" for name in [*EXPORTS.values(), "awesomecua-data.zip", "release-manifest.json", "SHA256SUMS"]))
        self.assertEqual(args[-7:], ["--verify-tag", "--title", "Awesome CUA catalog v2026.10.17", "--notes-file", "release/notes.md", "-R", "reacher-z/Awesome-CUA"])


if __name__ == "__main__":
    unittest.main()
