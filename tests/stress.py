#!/usr/bin/env python3
"""Stress scenarios the four provided cases don't exercise:

  * tiny buffer pool + small page capacity → forces dirty-page eviction, so a
    loser transaction's updates physically reach disk before the crash and MUST
    be rolled back by Undo (the core ARIES guarantee).
  * small checkpoint_interval → fuzzy checkpoints land mid-transaction; recovery
    must cross them correctly.
  * several consecutive crash/restart cycles on the same data dir.

Each scenario asserts on the verify output (committed survive, losers vanish).
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = ["archive.py", "common", "disk_space_manager", "buffer_manager",
        "file_index_manager", "query_processor", "recovery_manager"]


def make_workdir():
    work = tempfile.mkdtemp(prefix="p4_stress_")
    for item in CODE:
        src = os.path.join(REPO, item)
        dst = os.path.join(work, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dst)
    return work


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def run(work, config, input_text, name):
    inp = os.path.join(work, name)
    write(inp, input_text)
    cfg = os.path.join(work, "config.json")
    write(cfg, config)
    proc = subprocess.run(
        [sys.executable, os.path.join(work, "archive.py"), cfg, inp],
        cwd=work, capture_output=True, text=True,
    )
    return proc


def read_output(work):
    p = os.path.join(work, "output.txt")
    return open(p).read() if os.path.exists(p) else ""


CONFIG = """{
  "page_size": 4096,
  "max_records_per_page": 2,
  "buffer_pool_size": 2,
  "replacement_policy": "LRU",
  "index_strategy": "%s",
  "checkpoint_interval": %d,
  "log_buffer_size": 2
}"""


def scenario_evicted_loser(strategy):
    """A loser's dirty pages get evicted to disk, then crash → must roll back."""
    work = make_workdir()
    try:
        cfg = CONFIG % (strategy, 2)
        setup = (
            "tx_begin T1\n"
            "tx_op T1 create type t 2 1 k str v int\n"
            "tx_op T1 create record t a 1\n"
            "tx_op T1 create record t b 2\n"
            "tx_op T1 create record t c 3\n"
            "tx_commit T1\n"
            "tx_begin T2\n"
            "tx_op T2 create record t d 4\n"
            "tx_op T2 create record t e 5\n"
            "tx_op T2 create record t f 6\n"
            "tx_op T2 create record t g 7\n"
            "crash\n"
        )
        run(work, cfg, setup, "input_a.txt")
        verify = (
            "tx_begin V\n"
            "tx_op V search record t a\n"
            "tx_op V search record t b\n"
            "tx_op V search record t c\n"
            "tx_op V search record t d\n"
            "tx_op V search record t e\n"
            "tx_op V search record t f\n"
            "tx_op V search record t g\n"
            "tx_commit V\n"
        )
        p = run(work, cfg, verify, "verify.txt")
        out = read_output(work)
        expected = "a 1\nb 2\nc 3\n"
        ok = (out == expected and p.returncode == 0)
        print(f"  [{'PASS' if ok else 'FAIL'}] evicted_loser ({strategy})")
        if not ok:
            print(f"    rc={p.returncode}\n    expected={expected!r}\n    actual  ={out!r}")
            if p.stderr.strip():
                print("    stderr:", p.stderr[-800:])
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def scenario_checkpoint_crossing(strategy):
    """Fuzzy checkpoints fire every 2 ops; commit before crash must survive."""
    work = make_workdir()
    try:
        cfg = CONFIG % (strategy, 2)
        a = (
            "tx_begin T1\n"
            "tx_op T1 create type t 2 1 k str v int\n"
            "tx_op T1 create record t a 1\n"
            "tx_op T1 create record t b 2\n"
            "tx_commit T1\n"
            "tx_begin T2\n"
            "tx_op T2 create record t c 3\n"
            "tx_op T2 create record t d 4\n"
            "tx_commit T2\n"
            "tx_begin T3\n"
            "tx_op T3 create record t e 5\n"
            "crash\n"
        )
        run(work, cfg, a, "input_a.txt")
        # second cycle: another commit + another loser, then crash again
        b = (
            "tx_begin T4\n"
            "tx_op T4 create record t f 6\n"
            "tx_commit T4\n"
            "tx_begin T5\n"
            "tx_op T5 delete record t a\n"
            "crash\n"
        )
        run(work, cfg, b, "input_b.txt")
        verify = (
            "tx_begin V\n"
            "tx_op V search record t a\n"
            "tx_op V search record t b\n"
            "tx_op V search record t c\n"
            "tx_op V search record t d\n"
            "tx_op V search record t e\n"
            "tx_op V search record t f\n"
            "tx_commit V\n"
        )
        p = run(work, cfg, verify, "verify.txt")
        out = read_output(work)
        # a survives (T5 delete uncommitted → rolled back), b,c,d,f committed,
        # e was a loser (T3) → gone.
        expected = "a 1\nb 2\nc 3\nd 4\nf 6\n"
        ok = (out == expected and p.returncode == 0)
        print(f"  [{'PASS' if ok else 'FAIL'}] checkpoint_crossing ({strategy})")
        if not ok:
            print(f"    rc={p.returncode}\n    expected={expected!r}\n    actual  ={out!r}")
            if p.stderr.strip():
                print("    stderr:", p.stderr[-800:])
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def scenario_recommit_after_recovery(strategy):
    """After recovery rolls back a loser, the same key can be re-inserted and
    committed in a later run, and survives. The loser also created the type, so
    that `create type` is rolled back too (spec §1) and must be re-issued."""
    work = make_workdir()
    try:
        cfg = CONFIG % (strategy, 50)
        run(work, cfg,
            "tx_begin T1\n"
            "tx_op T1 create type t 2 1 k str v int\n"
            "tx_op T1 create record t x 9\n"
            "crash\n", "input_a.txt")
        run(work, cfg,
            "tx_begin T2\n"
            "tx_op T2 create type t 2 1 k str v int\n"
            "tx_op T2 create record t x 42\n"
            "tx_commit T2\n", "input_b.txt")
        p = run(work, cfg,
            "tx_begin V\n"
            "tx_op V search record t x\n"
            "tx_commit V\n", "verify.txt")
        out = read_output(work)
        expected = "x 42\n"
        ok = (out == expected and p.returncode == 0)
        print(f"  [{'PASS' if ok else 'FAIL'}] recommit_after_recovery ({strategy})")
        if not ok:
            print(f"    rc={p.returncode}\n    expected={expected!r}\n    actual  ={out!r}")
            if p.stderr.strip():
                print("    stderr:", p.stderr[-800:])
        return ok
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    results = []
    for strat in ("bplus_tree", "hash_index", "heap_scan"):
        results.append(scenario_evicted_loser(strat))
        results.append(scenario_checkpoint_crossing(strat))
        results.append(scenario_recommit_after_recovery(strat))
    ok = sum(1 for r in results if r)
    print(f"\n{ok}/{len(results)} stress scenarios passed")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
