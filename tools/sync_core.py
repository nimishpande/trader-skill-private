"""Copy core/indmkt.py into every trader-* skill folder.

Each skill in .claude/skills carries its own copy so a single folder works on its
own (copy just trader-scanner into ~/.claude/skills and it still runs). Edit core/indmkt.py,
then run:  python3 tools/sync_core.py
tests/test_core.py fails if any copy drifts from the source.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "core", "indmkt.py")
SKILLS = os.path.join(ROOT, ".claude", "skills")


def skill_dirs():
    return sorted(os.path.join(SKILLS, d) for d in os.listdir(SKILLS)
                  if d.startswith("trader-") and os.path.isdir(os.path.join(SKILLS, d)))


def main():
    n = 0
    for d in skill_dirs():
        shutil.copyfile(SRC, os.path.join(d, "indmkt.py"))
        n += 1
    sys.stdout.write("Copied core/indmkt.py into %d skill folders.\n" % n)


if __name__ == "__main__":
    main()
