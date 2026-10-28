"""The repo's shape: skills are well-formed, standard-library only, documented."""
import ast
import json
import os
import re
import sys
import unittest

from _util import ROOT, SKILLS

STDLIB = set(getattr(sys, "stdlib_module_names", ())) or {
    "argparse", "csv", "datetime", "email", "gzip", "http", "io", "json", "math", "os", "random", "re",
    "statistics", "subprocess", "sys", "time", "urllib", "xml", "importlib"}


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    mm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return mm.group(1) if mm else None, text


class TestRepo(unittest.TestCase):
    def skills(self):
        return sorted(d for d in os.listdir(SKILLS) if d.startswith("trader-"))

    def test_skill_md(self):
        for d in self.skills():
            fm, text = frontmatter(os.path.join(SKILLS, d, "SKILL.md"))
            self.assertIsNotNone(fm, d)
            self.assertIn("name: %s\n" % d, fm + "\n", d)
            self.assertIn("description:", fm, d)
            desc = fm.split("description:", 1)[1]
            self.assertLess(len(desc), 1024, d)
            self.assertIn("${CLAUDE_SKILL_DIR}", text, d)
            self.assertIn("not financial advice", text.lower(), d)

    def test_stdlib_only(self):
        for d in self.skills():
            for f in os.listdir(os.path.join(SKILLS, d)):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(SKILLS, d, f), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [n.name for n in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names = [node.module]
                    for n in names:
                        top = n.split(".")[0]
                        self.assertTrue(top in STDLIB or top == "indmkt", "%s/%s imports %s" % (d, f, n))

    def test_no_order_placement(self):
        bad = re.compile(r"place_(stock_)?order|kiteconnect|smartapi|/orders\b", re.I)
        for d in self.skills():
            for f in os.listdir(os.path.join(SKILLS, d)):
                if f.endswith(".py"):
                    with open(os.path.join(SKILLS, d, f), encoding="utf-8") as fh:
                        self.assertIsNone(bad.search(fh.read()), "%s/%s" % (d, f))

    def test_readme_lists_every_skill(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for d in self.skills():
            self.assertIn("/%s" % d, readme)

    def test_manifests_and_config(self):
        for f in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "config.example.json"):
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                json.load(fh)
        with open(os.path.join(ROOT, "config.example.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        sys.path.insert(0, os.path.join(ROOT, "core"))
        import indmkt
        for k in cfg:
            self.assertIn(k, indmkt.DEFAULT_CONFIG, k)


if __name__ == "__main__":
    unittest.main()
