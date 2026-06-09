#!/usr/bin/env python3
"""Isolated end-to-end runner for the Project 4 crash-recovery test cases.

For each case_*/ directory it:
  1. Creates a fresh temp working dir and copies the engine code + archive.py
     into it (so base_dir is isolated and the repo is never polluted).
  2. Copies the case's config.json in.
  3. Runs `python3 archive.py config.json input_X.txt` for every input file in
     lexicographic order (input files may end in `crash` → os._exit(1), which
     produces a non-zero exit code that we tolerate for non-final files).
  4. Runs the verify file last (must exit 0).
  5. Diffs the resulting output.txt against expected_output.txt byte-for-byte.

Usage: python3 tests/run_cases.py [case_name ...]
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR = os.path.join(REPO, "tests", "test_cases")
CODE = [
    "archive.py",
    "common",
    "disk_space_manager",
    "buffer_manager",
    "file_index_manager",
    "query_processor",
    "recovery_manager",
]


def _copy_engine(dst):
    for item in CODE:
        src = os.path.join(REPO, item)
        d = os.path.join(dst, item)
        if os.path.isdir(src):
            shutil.copytree(src, d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, d)


def run_case(name):
    case_dir = os.path.join(CASES_DIR, name)
    if not os.path.isdir(case_dir):
        print(f"  [skip] {name}: not a directory")
        return None
    inputs = sorted(
        f for f in os.listdir(case_dir)
        if f.startswith("input_") and f.endswith(".txt")
    )
    verify = os.path.join(case_dir, "verify.txt")
    expected_path = os.path.join(case_dir, "expected_output.txt")
    config = os.path.join(case_dir, "config.json")

    work = tempfile.mkdtemp(prefix=f"p4_{name}_")
    try:
        _copy_engine(work)
        shutil.copy2(config, os.path.join(work, "config.json"))
        archive = os.path.join(work, "archive.py")
        cfg = os.path.join(work, "config.json")

        run_list = [os.path.join(case_dir, i) for i in inputs] + [verify]
        for idx, inp in enumerate(run_list):
            is_last = idx == len(run_list) - 1
            proc = subprocess.run(
                [sys.executable, archive, cfg, inp],
                cwd=work, capture_output=True, text=True,
            )
            if is_last and proc.returncode != 0:
                print(f"  [FAIL] {name}: verify run exited {proc.returncode}")
                print(proc.stderr[-2000:])
                return False
            if proc.returncode not in (0, 1):
                # 1 is the expected crash exit; anything else is a real error.
                print(f"  [warn] {name}: {os.path.basename(inp)} "
                      f"exited {proc.returncode}")
                if proc.stderr.strip():
                    print(proc.stderr[-2000:])

        out_path = os.path.join(work, "output.txt")
        actual = open(out_path).read() if os.path.exists(out_path) else ""
        expected = open(expected_path).read()
        if actual == expected:
            print(f"  [PASS] {name}")
            return True
        print(f"  [FAIL] {name}: output mismatch")
        _show_diff(expected, actual)
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _show_diff(expected, actual):
    import difflib
    el = expected.splitlines()
    al = actual.splitlines()
    diff = list(difflib.unified_diff(el, al, "expected", "actual", lineterm=""))
    for line in diff[:80]:
        print("    " + line)
    if len(diff) > 80:
        print(f"    ... ({len(diff) - 80} more diff lines)")


def main():
    names = sys.argv[1:]
    if not names:
        names = sorted(
            d for d in os.listdir(CASES_DIR)
            if os.path.isdir(os.path.join(CASES_DIR, d)) and d.startswith("case_")
        )
    results = {}
    for name in names:
        results[name] = run_case(name)
    ok = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n{ok}/{total} cases passed")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
