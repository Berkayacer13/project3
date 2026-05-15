"""Phase 2 tests — heap-scan DML + QueryProcessor end-to-end.

Run from the project root:
    python3 tests/test_phase2.py
"""

import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from buffer_manager import BufferManager
from disk_space_manager import DiskSpaceManager
from file_index_manager import FileIndexManager
from query_processor import QueryProcessor


HOUSE_FIELDS = [
    ("name", "str"),
    ("origin", "str"),
    ("leader", "str"),
    ("military_strength", "int"),
    ("wealth", "int"),
    ("spice_production", "int"),
]


def _stack(tmp, pool_size=8, policy="LRU", strategy="heap_scan"):
    config = {
        "page_size": 4096,
        "max_records_per_page": 10,
        "buffer_pool_size": pool_size,
        "replacement_policy": policy,
        "index_strategy": strategy,
        "_base_dir": tmp,
    }
    disk = DiskSpaceManager(config)
    buf = BufferManager(config, disk)
    fim = FileIndexManager(config, buf)
    qp = QueryProcessor(config, fim, buf, disk)
    return disk, buf, fim, qp


def _run(name, fn):
    tmp = tempfile.mkdtemp(prefix="dbms_p2_")
    try:
        fn(tmp)
        print(f"  ok   {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL {name}: {e}")
        return False
    except Exception as e:
        print(f"  ERR  {name}: {type(e).__name__}: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- DDL ----------

def test_create_type_then_duplicate(tmp):
    _, _, fim, _ = _stack(tmp)
    r1 = fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    assert r1.success, r1.message
    r2 = fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    assert not r2.success and "already exists" in r2.message


def test_create_type_rejects_too_few_fields(tmp):
    _, _, fim, _ = _stack(tmp)
    r = fim.create_type("tooSmall", HOUSE_FIELDS[:5], pk_index=0)
    assert not r.success and "6 fields" in r.message


def test_create_type_rejects_bad_field_type(tmp):
    _, _, fim, _ = _stack(tmp)
    bad = HOUSE_FIELDS[:5] + [("misc", "float")]
    r = fim.create_type("house", bad, pk_index=0)
    assert not r.success and "unknown field type" in r.message


# ---------- Insert / Search / Delete ----------

def test_insert_and_search_round_trip(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    r = fim.insert_record(
        "house", ["Atreides", "Caladan", "Duke", 8000, 5000, 150]
    )
    assert r.success, r.message
    s = fim.search_record("house", "Atreides")
    assert s.status == "success"
    assert s.records == [["Atreides", "Caladan", "Duke", 8000, 5000, 150]]


def test_insert_rejects_duplicate_pk(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    fim.insert_record("house", ["Atreides", "Caladan", "Duke", 8000, 5000, 150])
    r = fim.insert_record("house", ["Atreides", "X", "Y", 1, 2, 3])
    assert not r.success and "duplicate" in r.message


def test_search_missing_returns_failure(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    s = fim.search_record("house", "Nowhere")
    assert s.status == "failure" and s.records == []


def test_delete_then_search_misses(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    fim.insert_record("house", ["Atreides", "Caladan", "Duke", 8000, 5000, 150])
    d = fim.delete_record("house", "Atreides")
    assert d.success
    s = fim.search_record("house", "Atreides")
    assert s.status == "failure"


def test_delete_missing_is_failure(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    d = fim.delete_record("house", "Nope")
    assert not d.success


def test_insert_on_missing_type_fails(tmp):
    _, _, fim, _ = _stack(tmp)
    r = fim.insert_record("ghost", ["a", "b", "c", 1, 2, 3])
    assert not r.success


# ---------- Range search ----------

def test_range_search_int_field(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    fim.insert_record("house", ["Atreides", "Caladan", "Duke", 8000, 5000, 150])
    fim.insert_record("house", ["Harkonnen", "GiediPrime", "Baron", 12000, 3000, 200])
    fim.insert_record("house", ["Corrino", "Kaitain", "Emperor", 15000, 10000, 50])
    r = fim.range_search("house", "wealth", 4000, 9000)
    assert r.status == "success"
    names = sorted(rec[0] for rec in r.records)
    assert names == ["Atreides"]


def test_range_search_on_str_field_fails(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    r = fim.range_search("house", "name", 0, 999)
    assert r.status == "failure"


def test_range_search_empty_result_is_success(tmp):
    _, _, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    r = fim.range_search("house", "wealth", 1000, 2000)
    assert r.status == "success"
    assert r.records == []


# ---------- Page-fill behaviour ----------

def test_inserts_span_multiple_pages(tmp):
    _, buf, fim, _ = _stack(tmp)
    fim.create_type("house", HOUSE_FIELDS, pk_index=0)
    # Insert 25 records — pages cap at 10, so we should end up with 3 data pages.
    for i in range(25):
        r = fim.insert_record(
            "house",
            [f"H{i:03d}", "orig", "lead", i, i * 10, i * 100],
        )
        assert r.success, (i, r.message)
    page_count = buf.get_page_count("house.dat")
    # 1 header + 3 data pages = 4
    assert page_count == 4, page_count

    # All 25 should be findable.
    for i in range(25):
        s = fim.search_record("house", f"H{i:03d}")
        assert s.status == "success", i


# ---------- Persistence ----------

def test_persistence_across_runs(tmp):
    _, _, fim1, _ = _stack(tmp)
    fim1.create_type("house", HOUSE_FIELDS, pk_index=0)
    fim1.insert_record("house", ["Atreides", "Caladan", "Duke", 8000, 5000, 150])
    fim1.buffer.flush()
    # Fresh stack pointing at the same dir
    _, _, fim2, _ = _stack(tmp)
    assert fim2.catalog.has("house")
    s = fim2.search_record("house", "Atreides")
    assert s.status == "success"
    assert s.records[0][0] == "Atreides"


# ---------- QueryProcessor end-to-end (spec §9 sample) ----------

SAMPLE_INPUT = """create type house 6 1 name str origin str leader str military_strength int wealth int spice_production int
create record house Atreides Caladan Duke 8000 5000 150
create record house Harkonnen GiediPrime Baron 12000 3000 200
create record house Corrino Kaitain Emperor 15000 10000 50
search record house Atreides
delete record house Corrino
search record house Corrino
range_search house wealth 4000 9000
"""

# Expected output: 1 record for Atreides search, 1 record for range_search,
# nothing for the (deleted) Corrino search.
EXPECTED_OUTPUT_LINES = [
    "Atreides Caladan Duke 8000 5000 150",
    "Atreides Caladan Duke 8000 5000 150",
]


def test_qp_end_to_end_sample(tmp):
    _, buf, _, qp = _stack(tmp)
    for line in SAMPLE_INPUT.strip().splitlines():
        qp.process(line.strip())
    buf.flush()

    with open(os.path.join(tmp, "output.txt")) as f:
        lines = [l.rstrip("\n") for l in f if l.strip()]
    assert lines == EXPECTED_OUTPUT_LINES, lines

    # Log file should have one row per input line, with the deleted-then-
    # searched Corrino logged as failure.
    with open(os.path.join(tmp, "log.csv")) as f:
        rows = [l.rstrip("\n") for l in f if l.strip()]
    assert len(rows) == 8, rows
    statuses = [r.rsplit(",", 1)[-1] for r in rows]
    # create type, 3x create record, search Atreides, delete Corrino,
    # search Corrino (FAIL), range_search
    assert statuses == [
        "success", "success", "success", "success",
        "success", "success", "failure", "success",
    ], statuses


def test_qp_does_not_crash_on_malformed_input(tmp):
    _, buf, _, qp = _stack(tmp)
    bad = [
        "",                              # empty (should be skipped by archive.py but just in case)
        "create type",                   # too few args
        "create type foo notanint 1 a int b int c int d int e int f int",
        "create record",                 # missing args
        "search",                        # missing args
        "range_search foo bar baz qux",  # non-int range
        "garbage command",
    ]
    for line in bad:
        qp.process(line)  # must not raise
    # All failures should be logged (except the empty line which yields no row).
    with open(os.path.join(tmp, "log.csv")) as f:
        rows = [l for l in f if l.strip()]
    # Empty line is processed by QP (it splits to []) — but no log row is written.
    assert len(rows) == len(bad) - 1, rows


# ---------- run all ----------

TESTS = [
    ("create_type_then_duplicate", test_create_type_then_duplicate),
    ("create_type_rejects_too_few_fields", test_create_type_rejects_too_few_fields),
    ("create_type_rejects_bad_field_type", test_create_type_rejects_bad_field_type),
    ("insert_and_search_round_trip", test_insert_and_search_round_trip),
    ("insert_rejects_duplicate_pk", test_insert_rejects_duplicate_pk),
    ("search_missing_returns_failure", test_search_missing_returns_failure),
    ("delete_then_search_misses", test_delete_then_search_misses),
    ("delete_missing_is_failure", test_delete_missing_is_failure),
    ("insert_on_missing_type_fails", test_insert_on_missing_type_fails),
    ("range_search_int_field", test_range_search_int_field),
    ("range_search_on_str_field_fails", test_range_search_on_str_field_fails),
    ("range_search_empty_result_is_success", test_range_search_empty_result_is_success),
    ("inserts_span_multiple_pages", test_inserts_span_multiple_pages),
    ("persistence_across_runs", test_persistence_across_runs),
    ("qp_end_to_end_sample", test_qp_end_to_end_sample),
    ("qp_does_not_crash_on_malformed_input", test_qp_does_not_crash_on_malformed_input),
]


def main():
    print(f"Running {len(TESTS)} Phase 2 tests...")
    passed = 0
    for name, fn in TESTS:
        if _run(name, fn):
            passed += 1
    print(f"\n{passed}/{len(TESTS)} passed")
    sys.exit(0 if passed == len(TESTS) else 1)


if __name__ == "__main__":
    main()
