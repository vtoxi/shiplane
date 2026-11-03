"""The Sites page grouping logic (web/js/grouping.js), run with Node. Skipped when Node is not installed.

    python tests/test_ui_grouping.py
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_grouping_logic():
    if not shutil.which('node'):
        print('node not installed: skipped')
        return
    r = subprocess.run(['node', os.path.join(ROOT, 'tests', 'check_grouping.mjs')], capture_output=True, text=True)
    assert r.returncode == 0 and 'grouping ok' in r.stdout, r.stdout + r.stderr


if __name__ == '__main__':
    run_tests(dict(globals()))
