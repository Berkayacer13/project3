#!/usr/bin/env python3
"""Comprehensive edge-case suite for Project 4 crash recovery.

Each scenario lists input files (run in lexicographic order) + a verify file,
and the exact expected output.txt. We assert byte-for-byte (that is what the
grader checks). Run across all three index strategies.
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
    work = tempfile.mkdtemp(prefix="p4_edge_")
    for item in CODE:
        src = os.path.join(REPO, item)
        dst = os.path.join(work, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dst)
    return work


def cfg(strategy, mrpp=10, pool=16, ckpt=50, logbuf=8):
    return ('{"page_size":4096,"max_records_per_page":%d,"buffer_pool_size":%d,'
            '"replacement_policy":"LRU","index_strategy":"%s",'
            '"checkpoint_interval":%d,"log_buffer_size":%d}'
            % (mrpp, pool, strategy, ckpt, logbuf))


def run_scenario(config, files, verify):
    """files: list of (name, text). verify: text. Returns (rc, output)."""
    work = make_workdir()
    try:
        cfgp = os.path.join(work, "config.json")
        open(cfgp, "w").write(config)
        archive = os.path.join(work, "archive.py")
        for name, text in files:
            p = os.path.join(work, name)
            open(p, "w").write(text)
            subprocess.run([sys.executable, archive, cfgp, p],
                           cwd=work, capture_output=True, text=True)
        vp = os.path.join(work, "verify.txt")
        open(vp, "w").write(verify)
        proc = subprocess.run([sys.executable, archive, cfgp, vp],
                              cwd=work, capture_output=True, text=True)
        out_path = os.path.join(work, "output.txt")
        out = open(out_path).read() if os.path.exists(out_path) else ""
        return proc.returncode, out, proc.stderr
    finally:
        shutil.rmtree(work, ignore_errors=True)


SCENARIOS = []


def scenario(name):
    def deco(fn):
        SCENARIOS.append((name, fn))
        return fn
    return deco


TYPE_T = "tx_op {tx} create type t 2 1 k str v int\n"


@scenario("durability_commit_then_crash")
def s1(strat):
    files = [("input_a.txt",
              "tx_begin T1\n"
              "tx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\n"
              "tx_commit T1\n"
              "crash\n")]
    verify = "tx_begin V\ntx_op V search record t a\ntx_commit V\n"
    return cfg(strat), files, verify, "a 1\n"


@scenario("loser_insert_rolled_back")
def s2(strat):
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_commit T1\n"
              "tx_begin T2\ntx_op T2 create record t b 2\ncrash\n")]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_commit V\n")
    return cfg(strat), files, verify, "a 1\n"


@scenario("loser_delete_of_committed_record_undone")
def s3(strat):
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_op T1 create record t b 2\n"
              "tx_commit T1\n"
              "tx_begin T2\ntx_op T2 delete record t a\ncrash\n")]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_commit V\n")
    return cfg(strat), files, verify, "a 1\nb 2\n"


@scenario("samepage_interleave_one_loser")
def s4(strat):
    # a (committed) and b (loser) land on the SAME page; undo of b must not
    # clobber a.
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_commit T1\n"
              "tx_begin T2\ntx_op T2 create record t b 2\ncrash\n")]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_commit V\n")
    return cfg(strat, mrpp=10), files, verify, "a 1\n"


@scenario("interleave_commit_and_loser_same_page")
def s5(strat):
    # T1 commits b, T2 (loser) inserts c on the same page as a/b; a,b survive.
    files = [("input_a.txt",
              "tx_begin SETUP\ntx_op SETUP create type t 2 1 k str v int\n"
              "tx_op SETUP create record t a 1\ntx_commit SETUP\n"
              "tx_begin T1\ntx_begin T2\n"
              "tx_op T1 create record t b 2\n"
              "tx_op T2 create record t c 3\n"
              "tx_commit T1\ncrash\n")]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_op V search record t c\n"
              "tx_commit V\n")
    return cfg(strat, mrpp=10), files, verify, "a 1\nb 2\n"


@scenario("empty_transaction_commit")
def s6(strat):
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_commit T1\n"
              "tx_begin T2\ntx_commit T2\n"
              "tx_begin T3\ntx_op T3 create record t b 2\ntx_commit T3\ncrash\n")]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_commit V\n")
    return cfg(strat), files, verify, "a 1\nb 2\n"


@scenario("op_outside_txn_is_noop")
def s7(strat):
    # A raw data op with no surrounding tx is invalid → no effect.
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_commit T1\n"
              "create record t b 2\n"            # invalid, ignored
              "tx_op T9 create record t c 3\n"   # unknown tx, ignored
              "crash\n")]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_op V search record t c\n"
              "tx_commit V\n")
    return cfg(strat), files, verify, "a 1\n"


@scenario("multi_crash_cycles")
def s8(strat):
    files = [
        ("input_a.txt",
         "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
         "tx_op T1 create record t a 1\ntx_commit T1\ncrash\n"),
        ("input_b.txt",
         "tx_begin T2\ntx_op T2 create record t b 2\ntx_commit T2\n"
         "tx_begin T3\ntx_op T3 create record t c 3\ncrash\n"),
        ("input_c.txt",
         "tx_begin T4\ntx_op T4 create record t d 4\ntx_commit T4\ncrash\n"),
    ]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_op V search record t c\n"
              "tx_op V search record t d\ntx_commit V\n")
    return cfg(strat), files, verify, "a 1\nb 2\nd 4\n"


@scenario("tiny_checkpoint_interval_crash")
def s9(strat):
    # checkpoint every op; crash mid-stream.
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_op T1 create record t b 2\n"
              "tx_commit T1\n"
              "tx_begin T2\ntx_op T2 create record t c 3\n"
              "tx_op T2 create record t d 4\ncrash\n")]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_op V search record t c\n"
              "tx_op V search record t d\ntx_commit V\n")
    return cfg(strat, ckpt=1, logbuf=1), files, verify, "a 1\nb 2\n"


@scenario("eviction_forces_loser_to_disk")
def s10(strat):
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_op T1 create record t b 2\n"
              "tx_op T1 create record t c 3\ntx_commit T1\n"
              "tx_begin T2\n"
              "tx_op T2 create record t d 4\ntx_op T2 create record t e 5\n"
              "tx_op T2 create record t f 6\ntx_op T2 create record t g 7\n"
              "crash\n")]
    verify = ("tx_begin V\n" + "".join(
        f"tx_op V search record t {k}\n" for k in "abcdefg") + "tx_commit V\n")
    return cfg(strat, mrpp=2, pool=2, ckpt=2, logbuf=2), files, verify, \
        "a 1\nb 2\nc 3\n"


@scenario("negative_and_boundary_ints_range")
def s11(strat):
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type n 2 1 k str v int\n"
              "tx_op T1 create record n a -5\ntx_op T1 create record n b 0\n"
              "tx_op T1 create record n c 5\ntx_op T1 create record n d 10\n"
              "tx_commit T1\ncrash\n")]
    verify = ("tx_begin V\ntx_op V range_search n v -5 5\n"
              "tx_op V range_search n v 0 0\ntx_commit V\n")
    # range -5..5 → a(-5),b(0),c(5) sorted by v asc; 0..0 → b
    return cfg(strat), files, verify, "a -5\nb 0\nc 5\nb 0\n"


@scenario("delete_then_reinsert_same_pk_committed")
def s12(strat):
    files = [("input_a.txt",
              "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
              "tx_op T1 create record t a 1\ntx_commit T1\n"
              "tx_begin T2\ntx_op T2 delete record t a\n"
              "tx_op T2 create record t a 2\ntx_commit T2\ncrash\n")]
    verify = "tx_begin V\ntx_op V search record t a\ntx_commit V\n"
    return cfg(strat), files, verify, "a 2\n"


@scenario("loser_create_type_then_reinsert_committed")
def s13(strat):
    # Loser creates type foo + record; after crash, a committed txn re-creates
    # foo (same schema) and inserts. Output must reflect the committed data.
    files = [
        ("input_a.txt",
         "tx_begin T1\ntx_op T1 create type foo 2 1 k str v int\n"
         "tx_op T1 create record foo a 1\ncrash\n"),
        ("input_b.txt",
         "tx_begin T2\ntx_op T2 create type foo 2 1 k str v int\n"
         "tx_op T2 create record foo a 99\ntx_commit T2\n"),
    ]
    verify = "tx_begin V\ntx_op V search record foo a\ntx_commit V\n"
    return cfg(strat), files, verify, "a 99\n"


@scenario("loser_create_type_then_diff_schema_committed")
def s14(strat):
    files = [
        ("input_a.txt",
         "tx_begin T1\ntx_op T1 create type foo 2 1 k str v int\n"
         "tx_op T1 create record foo a 1\ncrash\n"),
        ("input_b.txt",
         "tx_begin T2\ntx_op T2 create type foo 3 1 k str v int w int\n"
         "tx_op T2 create record foo a 1 9\ntx_commit T2\n"),
    ]
    verify = "tx_begin V\ntx_op V search record foo a\ntx_commit V\n"
    return cfg(strat), files, verify, "a 1 9\n"


@scenario("spec_section7_example")
def s16(strat):
    # Verbatim from spec §7: Dune survives (T1 committed), Foundation does not
    # (T2 was a loser).
    files = [("input_a.txt",
              "tx_begin T1\n"
              "tx_op T1 create type book 4 1 title str author str year int copies int\n"
              "tx_op T1 create record book Dune Herbert 1965 100\n"
              "tx_begin T2\n"
              "tx_op T2 create record book Foundation Asimov 1951 50\n"
              "tx_commit T1\ncrash\n")]
    verify = ("tx_begin V\ntx_op V search record book Dune\n"
              "tx_op V search record book Foundation\ntx_commit V\n")
    return cfg(strat), files, verify, "Dune Herbert 1965 100\n"


@scenario("loser_create_type_then_queries_only")
def s17(strat):
    # A loser created the type; a later run only queries it. The type left no
    # trace, so queries find nothing — and the engine must not error.
    files = [
        ("input_a.txt",
         "tx_begin T1\ntx_op T1 create type foo 2 1 k str v int\n"
         "tx_op T1 create record foo a 1\ncrash\n"),
        ("input_b.txt", "tx_begin T2\ntx_op T2 search record foo a\ntx_commit T2\n"),
    ]
    verify = "tx_begin V\ntx_op V search record foo a\ntx_commit V\n"
    return cfg(strat), files, verify, ""


@scenario("clean_shutdown_committed_type_persists")
def s18(strat):
    # No crash anywhere: a committed type + record from one file must be visible
    # to a later file (deferred catalog persistence must still persist on commit).
    files = [
        ("input_a.txt",
         "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
         "tx_op T1 create record t a 1\ntx_commit T1\n"),
        ("input_b.txt",
         "tx_begin T2\ntx_op T2 create record t b 2\ntx_commit T2\n"),
    ]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_commit V\n")
    return cfg(strat), files, verify, "a 1\nb 2\n"


@scenario("open_txn_clean_shutdown_rolled_back")
def s19(strat):
    # File a ends normally (no crash) with T2 still open. An uncommitted txn must
    # leave no trace even without a crash: T2's record is gone after restart.
    files = [
        ("input_a.txt",
         "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
         "tx_op T1 create record t a 1\ntx_commit T1\n"
         "tx_begin T2\ntx_op T2 create record t b 2\n"),  # T2 never commits
        ("input_b.txt",
         "tx_begin T3\ntx_op T3 create record t c 3\ntx_commit T3\n"),
    ]
    verify = ("tx_begin V\ntx_op V search record t a\n"
              "tx_op V search record t b\ntx_op V search record t c\n"
              "tx_commit V\n")
    return cfg(strat), files, verify, "a 1\nc 3\n"


@scenario("crash_with_no_transactions")
def s15(strat):
    files = [
        ("input_a.txt",
         "tx_begin T1\ntx_op T1 create type t 2 1 k str v int\n"
         "tx_op T1 create record t a 1\ntx_commit T1\n"),
        ("input_b.txt", "crash\n"),
    ]
    verify = "tx_begin V\ntx_op V search record t a\ntx_commit V\n"
    return cfg(strat), files, verify, "a 1\n"


def main():
    strategies = ("bplus_tree", "hash_index", "heap_scan")
    failures = []
    total = 0
    for name, fn in SCENARIOS:
        for strat in strategies:
            total += 1
            config, files, verify, expected = fn(strat)
            rc, out, err = run_scenario(config, files, verify)
            ok = (rc == 0 and out == expected)
            tag = "PASS" if ok else "FAIL"
            print(f"  [{tag}] {name} ({strat})")
            if not ok:
                failures.append((name, strat))
                print(f"        rc={rc}")
                print(f"        expected={expected!r}")
                print(f"        actual  ={out!r}")
                if err.strip():
                    print("        stderr:", err.strip().splitlines()[-1][:200])
    print(f"\n{total - len(failures)}/{total} edge checks passed")
    if failures:
        print("FAILURES:")
        for n, s in failures:
            print(f"  - {n} ({s})")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
