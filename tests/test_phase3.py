"""Phase 3 tests — hash_index + bplus_tree correctness, fallback, persistence.

Run from the project root:
    python3 tests/test_phase3.py

Every test runs against a fresh tmp dir; engines are torn down between runs
to exercise the on-disk persistence story.
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

# An int-keyed type so we exercise both key flavors of each index.
ORDERS_FIELDS = [
    ("id", "int"),
    ("customer", "str"),
    ("amount", "int"),
    ("status", "str"),
    ("region", "str"),
    ("priority", "int"),
]


def _stack(tmp, strategy, pool_size=8, policy="LRU"):
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
    tmp = tempfile.mkdtemp(prefix="dbms_p3_")
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


# ---------- shared behaviors (run against both strategies) ----------

def _str_round_trip(tmp, strategy):
    _, _, fim, _ = _stack(tmp, strategy)
    assert fim.create_type("house", HOUSE_FIELDS, pk_index=0).success
    assert fim.insert_record(
        "house", ["Atreides", "Caladan", "Duke", 8000, 5000, 150]
    ).success
    assert fim.insert_record(
        "house", ["Harkonnen", "GiediPrime", "Baron", 12000, 3000, 200]
    ).success
    r = fim.search_record("house", "Atreides")
    assert r.status == "success", r.message
    assert r.records == [["Atreides", "Caladan", "Duke", 8000, 5000, 150]]


def _int_round_trip(tmp, strategy):
    _, _, fim, _ = _stack(tmp, strategy)
    assert fim.create_type("orders", ORDERS_FIELDS, pk_index=0).success
    for i in [42, 7, 100, 3, 999, 17, 250]:
        assert fim.insert_record(
            "orders", [i, "cust", i * 10, "open", "EU", 1]
        ).success, f"insert {i} failed"
    r = fim.search_record("orders", 100)
    assert r.status == "success" and r.records[0][0] == 100, r.message


def _dup_pk_rejected(tmp, strategy):
    _, _, fim, _ = _stack(tmp, strategy)
    fim.create_type("orders", ORDERS_FIELDS, pk_index=0)
    fim.insert_record("orders", [1, "a", 10, "open", "EU", 1])
    r = fim.insert_record("orders", [1, "b", 20, "open", "EU", 1])
    assert not r.success


def _delete_then_search_miss(tmp, strategy):
    _, _, fim, _ = _stack(tmp, strategy)
    fim.create_type("orders", ORDERS_FIELDS, pk_index=0)
    fim.insert_record("orders", [42, "cust", 100, "open", "EU", 1])
    assert fim.delete_record("orders", 42).success
    assert fim.search_record("orders", 42).status == "failure"


def _persistence(tmp, strategy):
    _, buf, fim, _ = _stack(tmp, strategy)
    fim.create_type("orders", ORDERS_FIELDS, pk_index=0)
    for i in [5, 1, 9, 3, 7, 2, 8, 4, 6]:
        fim.insert_record("orders", [i, f"c{i}", i * 10, "open", "EU", 1])
    buf.flush()
    # Tear down; rebuild from disk.
    _, _, fim2, _ = _stack(tmp, strategy)
    for i in [5, 1, 9, 3, 7, 2, 8, 4, 6]:
        r = fim2.search_record("orders", i)
        assert r.status == "success" and r.records[0][0] == i, f"missing {i}"


# ---------- hash_index specific ----------

def test_hash_string_keys(tmp): _str_round_trip(tmp, "hash_index")
def test_hash_int_keys(tmp):    _int_round_trip(tmp, "hash_index")
def test_hash_dup_rejected(tmp): _dup_pk_rejected(tmp, "hash_index")
def test_hash_delete(tmp):       _delete_then_search_miss(tmp, "hash_index")
def test_hash_persistence(tmp):  _persistence(tmp, "hash_index")


def test_hash_range_falls_back_to_heap(tmp):
    _, _, fim, _ = _stack(tmp, "hash_index")
    fim.create_type("orders", ORDERS_FIELDS, pk_index=0)
    for i, amt in [(1, 10), (2, 50), (3, 30), (4, 90), (5, 70)]:
        fim.insert_record("orders", [i, "c", amt, "o", "EU", 1])
    # range_search on int field that is the PK *or* a non-PK int field —
    # both should fall back to heap scan (hash index has no range support).
    r = fim.range_search("orders", "amount", 25, 75)
    assert r.status == "success"
    amounts = sorted(row[2] for row in r.records)
    assert amounts == [30, 50, 70], amounts
    # And: it must report records_scanned > 0 (heap path was actually used).
    assert r.records_scanned >= 5
    # nodes_visited should NOT have ticked (hash index untouched on range).
    stats = fim.get_index_stats()
    nodes_before = stats["nodes_visited"]
    fim.range_search("orders", "id", 1, 3)
    assert fim.get_index_stats()["nodes_visited"] == nodes_before


# ---------- bplus_tree specific ----------

def test_bplus_string_keys(tmp): _str_round_trip(tmp, "bplus_tree")
def test_bplus_int_keys(tmp):    _int_round_trip(tmp, "bplus_tree")
def test_bplus_dup_rejected(tmp): _dup_pk_rejected(tmp, "bplus_tree")
def test_bplus_delete(tmp):      _delete_then_search_miss(tmp, "bplus_tree")
def test_bplus_persistence(tmp): _persistence(tmp, "bplus_tree")


def test_bplus_range_uses_index(tmp):
    _, _, fim, _ = _stack(tmp, "bplus_tree")
    fim.create_type("orders", ORDERS_FIELDS, pk_index=0)
    for i in [5, 1, 9, 3, 7, 2, 8, 4, 6, 10, 11, 12]:
        fim.insert_record("orders", [i, "c", i * 10, "open", "EU", 1])
    # Range over the PK — should use B+-tree range_search.
    nodes_before = fim.get_index_stats()["nodes_visited"]
    r = fim.range_search("orders", "id", 4, 8)
    assert r.status == "success"
    keys = sorted(row[0] for row in r.records)
    assert keys == [4, 5, 6, 7, 8], keys
    # Tree was definitely traversed.
    assert fim.get_index_stats()["nodes_visited"] > nodes_before


def test_bplus_range_non_pk_falls_back(tmp):
    """Range over a non-PK int field can't use the PK B+-tree → heap scan."""
    _, _, fim, _ = _stack(tmp, "bplus_tree")
    fim.create_type("orders", ORDERS_FIELDS, pk_index=0)
    for i in [1, 2, 3, 4, 5]:
        fim.insert_record("orders", [i, "c", i * 100, "open", "EU", 1])
    r = fim.range_search("orders", "amount", 150, 450)
    assert r.status == "success"
    amounts = sorted(row[2] for row in r.records)
    assert amounts == [200, 300, 400], amounts
    # records_scanned should match the heap-scan path (all 5).
    assert r.records_scanned == 5


def test_bplus_split_correctness(tmp):
    """Force enough inserts to trigger a leaf split, then verify lookup of
    every key still works (i.e. splits don't corrupt the index)."""
    _, _, fim, _ = _stack(tmp, "bplus_tree")
    fim.create_type("orders", ORDERS_FIELDS, pk_index=0)
    # The B+-tree fanout in our impl is on the order of hundreds for 4-byte
    # int keys; 600 inserts comfortably forces internal-node splits.
    keys = list(range(600))
    import random
    random.Random(42).shuffle(keys)
    for k in keys:
        assert fim.insert_record(
            "orders", [k, "c", k, "open", "EU", 1]
        ).success, f"insert {k} failed"
    # Spot check + boundary keys.
    for k in (0, 1, 100, 299, 300, 599):
        r = fim.search_record("orders", k)
        assert r.status == "success" and r.records[0][0] == k, f"missing {k}"
    # Range covers everything; sorted ascending by PK.
    r = fim.range_search("orders", "id", 0, 599)
    assert len(r.records) == 600
    assert [row[0] for row in r.records] == list(range(600))


# ---------- driver ----------

TESTS = [
    ("hash_string_keys", test_hash_string_keys),
    ("hash_int_keys", test_hash_int_keys),
    ("hash_dup_rejected", test_hash_dup_rejected),
    ("hash_delete", test_hash_delete),
    ("hash_persistence", test_hash_persistence),
    ("hash_range_falls_back_to_heap", test_hash_range_falls_back_to_heap),
    ("bplus_string_keys", test_bplus_string_keys),
    ("bplus_int_keys", test_bplus_int_keys),
    ("bplus_dup_rejected", test_bplus_dup_rejected),
    ("bplus_delete", test_bplus_delete),
    ("bplus_persistence", test_bplus_persistence),
    ("bplus_range_uses_index", test_bplus_range_uses_index),
    ("bplus_range_non_pk_falls_back", test_bplus_range_non_pk_falls_back),
    ("bplus_split_correctness", test_bplus_split_correctness),
]


def main():
    print(f"Running {len(TESTS)} Phase 3 tests...")
    passed = 0
    for name, fn in TESTS:
        if _run(name, fn):
            passed += 1
    print(f"\n{passed}/{len(TESTS)} passed")
    sys.exit(0 if passed == len(TESTS) else 1)


if __name__ == "__main__":
    main()
