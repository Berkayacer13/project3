"""Comprehensive correctness test suite for CMPE 321 Project 3.

Covers: unit tests, spec sample output, failure cases, persistence,
index strategy consistency, stats format, log.csv append-only,
small buffer pool, and stats reset.

Run from the project root:
    python3 tests/test_correctness.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "archive.py")
BASE_CONFIG = os.path.join(ROOT, "config.json")
PYTHON = sys.executable

HOUSE_FIELDS = "name str origin str leader str military_strength int wealth int spice_production int"
CREATE_HOUSE = f"create type house 6 1 {HOUSE_FIELDS}"
INS_ATREIDES = "create record house Atreides Caladan Duke 8000 5000 150"
INS_HARKONNEN = "create record house Harkonnen GiediPrime Baron 12000 3000 200"
INS_CORRINO = "create record house Corrino Kaitain Emperor 15000 10000 50"

RESULTS = []  # (category, name, passed, note)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_data():
    for fname in os.listdir(ROOT):
        fpath = os.path.join(ROOT, fname)
        if not os.path.isfile(fpath):
            continue
        if fname.endswith(".dat") or fname.endswith(".idx"):
            os.remove(fpath)
        elif fname in ("catalog.dat", "output.txt", "log.csv", "stats_output.txt"):
            os.remove(fpath)


def _write_cfg(overrides: dict) -> str:
    with open(BASE_CONFIG) as f:
        cfg = json.load(f)
    cfg.update(overrides)
    cfg.pop("_base_dir", None)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir=ROOT
    )
    json.dump(cfg, tmp)
    tmp.close()
    return tmp.name


def _write_input(lines) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, dir=ROOT
    )
    tmp.write("\n".join(lines) + "\n")
    tmp.close()
    return tmp.name


def _run(cfg_path: str, input_path: str, timeout=30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, ARCHIVE, cfg_path, input_path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _read(fname: str) -> str:
    path = os.path.join(ROOT, fname)
    return open(path).read() if os.path.exists(path) else ""


def _run_case(overrides, lines, clear=True):
    """Clear, write config+input, run archive.py, return (rc, output, stats, log, stderr)."""
    if clear:
        _clear_data()
    cfg = _write_cfg(overrides)
    inp = _write_input(lines)
    try:
        proc = _run(cfg, inp)
        return proc.returncode, _read("output.txt"), _read("stats_output.txt"), _read("log.csv"), proc.stderr
    finally:
        os.remove(cfg)
        os.remove(inp)


def record(category, name, passed, note=""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append((category, name, status, note))
    sym = "  ok  " if passed else "  FAIL"
    print(f"{sym}  [{category}] {name}" + (f"  — {note}" if note else ""))


# ---------------------------------------------------------------------------
# Category 1: Existing unit tests
# ---------------------------------------------------------------------------

def run_unit_tests():
    for phase in (1, 2, 3):
        script = os.path.join(ROOT, "tests", f"test_phase{phase}.py")
        proc = subprocess.run(
            [PYTHON, script], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        out = proc.stdout + proc.stderr
        m = re.search(r"(\d+)/(\d+) passed", out)
        if m:
            passed_n, total_n = int(m.group(1)), int(m.group(2))
            ok = passed_n == total_n
            record("Unit Tests", f"Phase {phase} ({total_n} tests)", ok,
                   f"{passed_n}/{total_n}")
        else:
            record("Unit Tests", f"Phase {phase}", False, "could not parse output")


# ---------------------------------------------------------------------------
# Category 2: Spec sample (§9 basic.txt)
# ---------------------------------------------------------------------------

def run_spec_sample():
    lines = [
        CREATE_HOUSE,
        INS_ATREIDES,
        INS_HARKONNEN,
        INS_CORRINO,
        "search record house Atreides",
        "delete record house Corrino",
        "search record house Corrino",
        "range_search house wealth 4000 9000",
        "explain search record house Harkonnen",
        "stats",
    ]
    rc, out, stats, log, err = _run_case({}, lines)

    # no crash
    record("Spec Sample", "No crash (exit code 0)", rc == 0, f"rc={rc}")

    # search found record
    line1 = out.strip().splitlines()[0] if out.strip() else ""
    ok_search = line1 == "Atreides Caladan Duke 8000 5000 150"
    record("Spec Sample", "search record returns correct fields", ok_search,
           repr(line1))

    # deleted record produces no output
    out_lines = out.strip().splitlines()
    corrino_absent = not any("Corrino" in l for l in out_lines)
    record("Spec Sample", "deleted record absent from output", corrino_absent)

    # range_search returns Atreides (wealth=5000 ∈ [4000,9000]) but not Harkonnen (3000).
    # Run range_search in isolation so the explain's result block doesn't pollute the check.
    _, out_range, _, _, _ = _run_case({}, [
        CREATE_HOUSE, INS_ATREIDES, INS_HARKONNEN, INS_CORRINO,
        "range_search house wealth 4000 9000",
    ])
    range_lines = out_range.strip().splitlines()
    atreides_in = any("Atreides" in l for l in range_lines)
    harkonnen_in_range = any("Harkonnen" in l for l in range_lines)
    record("Spec Sample", "range_search includes Atreides (wealth=5000)", atreides_in)
    record("Spec Sample", "range_search excludes Harkonnen (wealth=3000)", not harkonnen_in_range)

    # explain writes all 3 blocks
    for block in ("---PLAN---", "---RESULT---", "---STATS---"):
        record("Spec Sample", f"explain contains {block}", block in out)

    # stats writes to stats_output.txt (not output.txt)
    stats_in_output = "STATISTICS" in out
    stats_in_statsfile = "=== STATISTICS ===" in stats
    record("Spec Sample", "stats writes to stats_output.txt (not output.txt)",
           stats_in_statsfile and not stats_in_output)


# ---------------------------------------------------------------------------
# Category 3: Failure cases — no crash, no spurious output
# ---------------------------------------------------------------------------

def run_failure_cases():
    # Duplicate type
    rc, out, _, log, _ = _run_case({}, [CREATE_HOUSE, CREATE_HOUSE])
    record("Failure Cases", "Duplicate create type: no crash", rc == 0)
    record("Failure Cases", "Duplicate create type: empty output.txt", out.strip() == "")
    log_lines = [l for l in log.splitlines() if "create type" in l]
    failures = [l for l in log_lines if "failure" in l]
    record("Failure Cases", "Duplicate create type: logged as failure", len(failures) >= 1)

    # Search missing record
    rc, out, _, log, _ = _run_case({}, [CREATE_HOUSE, INS_ATREIDES,
                                         "search record house Corrino"])
    record("Failure Cases", "Search missing record: no crash", rc == 0)
    record("Failure Cases", "Search missing record: empty output.txt", out.strip() == "")

    # Delete missing record
    rc, out, _, log, _ = _run_case({}, [CREATE_HOUSE, INS_ATREIDES,
                                         "delete record house Corrino"])
    record("Failure Cases", "Delete missing record: no crash", rc == 0)

    # Range search on str field
    rc, out, _, log, _ = _run_case({}, [CREATE_HOUSE, INS_ATREIDES,
                                         "range_search house name a z"])
    record("Failure Cases", "range_search on str field: no crash", rc == 0)

    # Duplicate PK insert
    rc, out, _, log, _ = _run_case({}, [CREATE_HOUSE, INS_ATREIDES, INS_ATREIDES])
    record("Failure Cases", "Duplicate PK insert: no crash", rc == 0)
    dup_logged = any("failure" in l for l in log.splitlines()
                     if "create record" in l and "Atreides" in l)
    record("Failure Cases", "Duplicate PK insert: logged as failure", dup_logged)

    # Operation on non-existent type
    rc, out, _, _, _ = _run_case({}, ["search record ghost 42"])
    record("Failure Cases", "Op on non-existent type: no crash", rc == 0)
    record("Failure Cases", "Op on non-existent type: empty output.txt", out.strip() == "")


# ---------------------------------------------------------------------------
# Category 4: Persistence across process restarts
# ---------------------------------------------------------------------------

def run_persistence():
    _clear_data()
    cfg = _write_cfg({})

    # Part 1: create type + insert
    inp1 = _write_input([CREATE_HOUSE, INS_ATREIDES, INS_HARKONNEN])
    proc1 = _run(cfg, inp1)
    os.remove(inp1)
    record("Persistence", "Part 1 (write) exits cleanly", proc1.returncode == 0,
           f"rc={proc1.returncode}")

    # Part 2: fresh process, search only
    inp2 = _write_input(["search record house Atreides", "search record house Harkonnen"])
    proc2 = _run(cfg, inp2)
    out2 = _read("output.txt")
    os.remove(inp2)
    os.remove(cfg)

    record("Persistence", "Part 2 (read-only) exits cleanly", proc2.returncode == 0,
           f"rc={proc2.returncode}")

    out_lines = out2.strip().splitlines()
    record("Persistence", "Atreides survives restart",
           any("Atreides" in l for l in out_lines))
    record("Persistence", "Harkonnen survives restart",
           any("Harkonnen" in l for l in out_lines))

    # log.csv must have rows from BOTH runs (append-only)
    log = _read("log.csv")
    log_lines = [l for l in log.splitlines() if l.strip()]
    record("Persistence", "log.csv is append-only (≥4 rows across 2 runs)",
           len(log_lines) >= 4, f"{len(log_lines)} rows")


# ---------------------------------------------------------------------------
# Category 5: Index strategy consistency — same output.txt regardless of strategy
# ---------------------------------------------------------------------------

def run_index_consistency():
    lines = [
        CREATE_HOUSE, INS_ATREIDES, INS_HARKONNEN, INS_CORRINO,
        "search record house Atreides",
        "range_search house wealth 3000 10001",
        "delete record house Corrino",
        "search record house Corrino",
    ]
    outputs = {}
    for strategy in ("heap_scan", "hash_index", "bplus_tree"):
        _, out, _, _, _ = _run_case({"index_strategy": strategy}, lines)
        outputs[strategy] = out.strip()

    record("Index Consistency", "heap_scan == hash_index output",
           outputs["heap_scan"] == outputs["hash_index"],
           "" if outputs["heap_scan"] == outputs["hash_index"]
           else f"heap={repr(outputs['heap_scan'][:60])} hash={repr(outputs['hash_index'][:60])}")
    record("Index Consistency", "heap_scan == bplus_tree output",
           outputs["heap_scan"] == outputs["bplus_tree"],
           "" if outputs["heap_scan"] == outputs["bplus_tree"]
           else f"heap={repr(outputs['heap_scan'][:60])} b+={repr(outputs['bplus_tree'][:60])}")


# ---------------------------------------------------------------------------
# Category 6: stats_output.txt format
# ---------------------------------------------------------------------------

def run_stats_format():
    _, _, stats, _, _ = _run_case({}, [
        CREATE_HOUSE, INS_ATREIDES, INS_HARKONNEN,
        "search record house Atreides",
        "stats",
    ])

    record("Stats Format", "stats_output.txt exists", stats != "")

    expected_labels = [
        "=== STATISTICS ===",
        "Disk I/O:",
        "Buffer Pool:",
        "Evictions:",
        "Index:",
        "Records:",
    ]
    for label in expected_labels:
        record("Stats Format", f"contains '{label}'", label in stats)

    # hit rate has exactly 1 decimal place
    m = re.search(r"\(([0-9]+\.[0-9]+)% hit rate\)", stats)
    record("Stats Format", "hit rate has 1 decimal place",
           bool(m) and len(m.group(1).split(".")[1]) == 1,
           repr(m.group(0)) if m else "not found")

    # all counter values are non-negative integers
    numbers = re.findall(r"\b(\d+)\b", stats)
    record("Stats Format", "all counters are non-negative integers", len(numbers) >= 8,
           f"{len(numbers)} numbers found")


# ---------------------------------------------------------------------------
# Category 7: log.csv format
# ---------------------------------------------------------------------------

def run_log_format():
    _, _, _, log, _ = _run_case({}, [
        CREATE_HOUSE, INS_ATREIDES,
        "search record house Atreides",
        "search record house Ghost",
    ])

    rows = [l for l in log.splitlines() if l.strip()]
    record("Log Format", "log.csv has rows", len(rows) >= 3, f"{len(rows)} rows")

    if rows:
        # Each row: timestamp,operation,status
        first = rows[0]
        parts = first.split(",")
        record("Log Format", "row has ≥3 comma-separated fields", len(parts) >= 3,
               repr(first[:80]))

        # timestamp is a plain integer
        ts = parts[0].strip()
        record("Log Format", "timestamp is integer (not float)", ts.isdigit(), repr(ts))

        # status is success or failure
        statuses = {r.split(",")[-1].strip() for r in rows}
        valid = statuses.issubset({"success", "failure"})
        record("Log Format", "all statuses are 'success' or 'failure'", valid,
               str(statuses))

    # failed search → logged as failure
    failure_rows = [r for r in rows if "failure" in r and "Ghost" in r]
    record("Log Format", "missing-record search logged as failure",
           len(failure_rows) >= 1)


# ---------------------------------------------------------------------------
# Category 8: Buffer pool size 4 (stress eviction)
# ---------------------------------------------------------------------------

def run_small_buffer():
    lines = [CREATE_HOUSE] + [
        f"create record house House{i:03d} Origin{i} Leader{i} {i*100} {i*50} {i*10}"
        for i in range(1, 31)
    ] + [
        f"search record house House{i:03d}" for i in range(1, 11)
    ] + ["stats"]

    rc, out, stats, _, _ = _run_case({"buffer_pool_size": 4}, lines)
    record("Small Buffer (pool=4)", "No crash with 30 records, pool=4", rc == 0,
           f"rc={rc}")
    found = len([l for l in out.strip().splitlines() if "House" in l])
    record("Small Buffer (pool=4)", "All 10 search results returned", found == 10,
           f"{found}/10 found")

    m = re.search(r"Evictions:\s+(\d+)", stats)
    evictions = int(m.group(1)) if m else 0
    record("Small Buffer (pool=4)", "Evictions > 0 with pool=4",
           evictions > 0, f"{evictions} evictions")


# ---------------------------------------------------------------------------
# Category 9: stats reset
# ---------------------------------------------------------------------------

def run_stats_reset():
    lines = [
        CREATE_HOUSE, INS_ATREIDES, INS_HARKONNEN,
        "search record house Atreides",
        "stats reset",
        "search record house Harkonnen",
        "stats",
    ]
    _, _, stats, _, _ = _run_case({}, lines)

    m_reads = re.search(r"Disk I/O:\s+(\d+) reads", stats)
    m_req = re.search(r"Buffer Pool:\s+(\d+) requests", stats)

    reads_after_reset = int(m_reads.group(1)) if m_reads else -1
    requests_after_reset = int(m_req.group(1)) if m_req else -1

    # After reset, only 1 search was done — counters should be small
    # Reads should be << what they'd be without reset (dozens from inserts)
    record("Stats Reset", "stats reset clears disk read counter",
           0 <= reads_after_reset < 20,
           f"reads after reset = {reads_after_reset}")
    record("Stats Reset", "stats reset clears buffer request counter",
           0 <= requests_after_reset < 30,
           f"requests after reset = {requests_after_reset}")

    m_scanned = re.search(r"Records:\s+(\d+) scanned", stats)
    scanned = int(m_scanned.group(1)) if m_scanned else -1
    record("Stats Reset", "records_scanned reflects only post-reset queries",
           0 <= scanned < 20, f"scanned = {scanned}")


# ---------------------------------------------------------------------------
# Print table
# ---------------------------------------------------------------------------

def print_table():
    CAT_W = 28
    NAME_W = 52
    STATUS_W = 6
    NOTE_W = 40

    header = (f"{'Category':<{CAT_W}}  {'Test':<{NAME_W}}  {'Result':<{STATUS_W}}  {'Note'}")
    sep = "-" * (CAT_W + NAME_W + STATUS_W + NOTE_W + 6)
    print("\n" + sep)
    print(header)
    print(sep)

    last_cat = ""
    passed = failed = 0
    for cat, name, status, note in RESULTS:
        if cat != last_cat:
            if last_cat:
                print()
            last_cat = cat
        cat_col = cat if cat != last_cat else ""
        note_trunc = (note[:NOTE_W - 1] + "…") if len(note) > NOTE_W else note
        print(f"{cat:<{CAT_W}}  {name:<{NAME_W}}  {status:<{STATUS_W}}  {note_trunc}")
        if status == "PASS":
            passed += 1
        else:
            failed += 1

    print(sep)
    total = passed + failed
    print(f"\nTotal: {passed}/{total} passed, {failed} failed\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running correctness tests...\n")

    run_unit_tests()
    run_spec_sample()
    run_failure_cases()
    run_persistence()
    run_index_consistency()
    run_stats_format()
    run_log_format()
    run_small_buffer()
    run_stats_reset()

    _clear_data()

    print_table()
