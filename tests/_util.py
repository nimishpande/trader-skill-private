"""Test helpers: run a skill script in a subprocess against the synthetic bundle."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
SKILLS = os.path.join(ROOT, ".claude", "skills")
BUNDLE = os.path.join(TESTS, "fixtures", "nse-demo.json.gz")
SCRIPTS = {
    "scan": "trader-scanner/scan.py", "news": "trader-news/news.py", "entry": "trader-entry/entry.py",
    "exit": "trader-exit/exit.py", "tm": "trader-timemachine/timemachine.py", "budget": "trader-budget/size.py",
    "alerts": "trader-alerts/alerts.py", "journal": "trader-journal/journal.py", "suggest": "trader-suggest/suggest.py",
}
sys.path.insert(0, os.path.join(ROOT, "core"))
import indmkt  # noqa: E402,F401


class SkillCase(unittest.TestCase):
    """Each test gets a fresh trader/ folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="trader-test-")
        self.tdir = os.path.join(self.tmp, "trader")
        os.makedirs(self.tdir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_skill(self, key, *args, now=None, offline=True):
        cmd = [sys.executable, os.path.join(SKILLS, SCRIPTS[key])] + list(args) + ["--trader-dir", self.tdir]
        if now:
            cmd += ["--now", now]
        if offline:
            cmd += ["--offline", BUNDLE]
        env = dict(os.environ)
        env.pop("TRADER_DIR", None)
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           cwd=self.tmp, env=env, timeout=120)
        return r.returncode, r.stdout

    def read(self, name):
        with open(os.path.join(self.tdir, name), encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, name, doc):
        with open(os.path.join(self.tdir, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
