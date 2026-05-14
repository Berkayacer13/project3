"""Phase 1 tests — DiskSpaceManager + BufferManager.

Run from the project root:
    python3 tests/test_phase1.py
"""

import os
import shutil
import sys
import tempfile

# Make the project root importable when run as a script.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from buffer_manager import BufferManager
from disk_space_manager import DiskSpaceManager


def _make(tmp, pool_size=4, policy="LRU"):
    config = {
        "page_size": 4096,
        "max_records_per_page": 10,
        "buffer_pool_size": pool_size,
        "replacement_policy": policy,
        "index_strategy": "heap_scan",
        "_base_dir": tmp,
    }
    disk = DiskSpaceManager(config)
    buf = BufferManager(config, disk)
    return disk, buf


def _run(name, fn):
    tmp = tempfile.mkdtemp(prefix="dbms_test_")
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


# ---------- DiskSpaceManager ----------

def test_create_file_writes_header(tmp):
    disk, buf = _make(tmp)
    assert disk.create_file("t.dat") is True
    assert disk.create_file("t.dat") is False  # second create fails
    assert disk.file_exists("t.dat")
    # Header page exists, page count is 1, free list is empty.
    assert disk.get_page_count("t.dat") == 1
    # File on disk is exactly page_size bytes.
    sz = os.path.getsize(os.path.join(tmp, "t.dat"))
    assert sz == disk.page_size, f"expected one page, got {sz}"


def test_allocate_extends_file(tmp):
    disk, _ = _make(tmp)
    disk.create_file("t.dat")
    a = disk.allocate_page("t.dat")
    b = disk.allocate_page("t.dat")
    assert a.success and b.success
    assert a.page_id == 1
    assert b.page_id == 2
    assert disk.get_page_count("t.dat") == 3
    sz = os.path.getsize(os.path.join(tmp, "t.dat"))
    assert sz == 3 * disk.page_size


def test_write_then_read(tmp):
    disk, _ = _make(tmp)
    disk.create_file("t.dat")
    a = disk.allocate_page("t.dat")
    payload = b"X" + bytes(disk.page_size - 1)
    w = disk.write_page("t.dat", a.page_id, payload)
    assert w.success
    r = disk.read_page("t.dat", a.page_id)
    assert r.status == "success"
    assert r.data == payload


def test_page_zero_is_protected(tmp):
    disk, _ = _make(tmp)
    disk.create_file("t.dat")
    # Reading or writing page 0 should not crash; both fail gracefully.
    r = disk.read_page("t.dat", 0)
    assert r.status == "failure"
    w = disk.write_page("t.dat", 0, bytes(disk.page_size))
    assert not w.success


def test_io_counters_increment(tmp):
    disk, _ = _make(tmp)
    disk.create_file("t.dat")     # write: 1 (header)
    a = disk.allocate_page("t.dat")  # read: 1 (header load), writes: 2 (new page + header flush)
    disk.write_page("t.dat", a.page_id, bytes(disk.page_size))  # write: 1
    disk.read_page("t.dat", a.page_id)  # read: 1
    counts = disk.get_io_counts()
    # Don't pin to exact numbers — just sanity: more than zero of each.
    assert counts["reads"] >= 1, counts
    assert counts["writes"] >= 3, counts


def test_log_write_stub_called_on_every_write(tmp):
    disk, _ = _make(tmp)
    calls = []
    disk.set_log_writer(lambda fid, pid, old, new: calls.append((fid, pid, len(new))))
    disk.create_file("t.dat")              # +1 call (header init)
    a = disk.allocate_page("t.dat")        # +2 calls (new page write + header flush)
    disk.write_page("t.dat", a.page_id, bytes(disk.page_size))  # +1 call
    assert len(calls) == 4, calls


# ---------- BufferManager ----------

def test_buffer_hit_and_miss(tmp):
    disk, buf = _make(tmp, pool_size=4)
    buf.create_file("t.dat")
    a = buf.allocate_page("t.dat")             # requests=1, misses=1
    r1 = buf.get_page("t.dat", a.page_id)      # requests=2, hits=1
    assert r1.cache_hit is True
    assert r1.io_performed is False
    assert buf.requests == 2
    assert buf.hits == 1
    assert buf.misses == 1


def test_buffer_eviction_writes_back_dirty(tmp):
    disk, buf = _make(tmp, pool_size=2)
    buf.create_file("t.dat")
    a = buf.allocate_page("t.dat")
    a.data[:5] = b"hello"
    buf.mark_dirty("t.dat", a.page_id)
    _b = buf.allocate_page("t.dat")
    _c = buf.allocate_page("t.dat")  # pool was full → evict LRU (= a)
    assert buf.evictions >= 1
    assert buf.dirty_writebacks >= 1
    # 'a' must now be on disk; fetching it back is a miss with correct bytes.
    r = buf.get_page("t.dat", a.page_id)
    assert r.cache_hit is False
    assert bytes(r.data[:5]) == b"hello"


def test_lru_evicts_oldest(tmp):
    disk, buf = _make(tmp, pool_size=2, policy="LRU")
    buf.create_file("t.dat")
    p1 = buf.allocate_page("t.dat")
    p2 = buf.allocate_page("t.dat")
    buf.get_page("t.dat", p1.page_id)         # touch p1 → p2 is now LRU
    p3 = buf.allocate_page("t.dat")           # evicts p2
    assert ("t.dat", p1.page_id) in buf._frames
    assert ("t.dat", p2.page_id) not in buf._frames
    assert ("t.dat", p3.page_id) in buf._frames


def test_mru_evicts_newest(tmp):
    disk, buf = _make(tmp, pool_size=2, policy="MRU")
    buf.create_file("t.dat")
    p1 = buf.allocate_page("t.dat")
    p2 = buf.allocate_page("t.dat")
    buf.get_page("t.dat", p1.page_id)         # touch p1 → p1 is now MRU
    p3 = buf.allocate_page("t.dat")           # evicts p1 (most recently used)
    assert ("t.dat", p1.page_id) not in buf._frames
    assert ("t.dat", p2.page_id) in buf._frames
    assert ("t.dat", p3.page_id) in buf._frames


def test_flush_writes_dirty_keeps_in_pool(tmp):
    disk, buf = _make(tmp, pool_size=4)
    buf.create_file("t.dat")
    a = buf.allocate_page("t.dat")
    a.data[:3] = b"abc"
    buf.mark_dirty("t.dat", a.page_id)
    buf.flush()
    # Frame still pinned in pool, but no longer dirty.
    assert ("t.dat", a.page_id) in buf._frames
    assert buf._frames[("t.dat", a.page_id)].dirty is False
    # And the bytes are on disk.
    disk2 = DiskSpaceManager({
        "page_size": disk.page_size, "_base_dir": tmp,
        "max_records_per_page": 10, "buffer_pool_size": 4,
        "replacement_policy": "LRU", "index_strategy": "heap_scan",
    })
    r = disk2.read_page("t.dat", a.page_id)
    assert bytes(r.data[:3]) == b"abc"


def test_persistence_across_instances(tmp):
    d1, b1 = _make(tmp)
    b1.create_file("t.dat")
    a = b1.allocate_page("t.dat")
    a.data[:5] = b"hello"
    b1.mark_dirty("t.dat", a.page_id)
    b1.flush()
    # New instance pointing at the same directory must see the data.
    d2, b2 = _make(tmp)
    assert b2.file_exists("t.dat")
    r = b2.get_page("t.dat", a.page_id)
    assert bytes(r.data[:5]) == b"hello"


def test_layer3_never_touches_disk(tmp):
    """Sanity: BufferManager exposes everything L3 might need so it never
    has to reach into DiskSpaceManager. This is a sentinel test — if it
    starts failing because L3 needs a new disk-passthrough, add it here.
    """
    _, buf = _make(tmp)
    for name in ("get_page", "mark_dirty", "allocate_page", "flush",
                 "create_file", "file_exists", "get_page_count"):
        assert hasattr(buf, name), f"BufferManager missing L3-facing method: {name}"


def test_reset_stats(tmp):
    disk, buf = _make(tmp)
    buf.create_file("t.dat")
    buf.allocate_page("t.dat")
    buf.reset_stats()
    disk.reset_counts()
    assert buf.get_stats() == {"requests": 0, "hits": 0, "misses": 0,
                                "evictions": 0, "dirty_writebacks": 0}
    assert disk.get_io_counts() == {"reads": 0, "writes": 0}


# ---------- driver ----------

TESTS = [
    ("create_file_writes_header", test_create_file_writes_header),
    ("allocate_extends_file", test_allocate_extends_file),
    ("write_then_read", test_write_then_read),
    ("page_zero_is_protected", test_page_zero_is_protected),
    ("io_counters_increment", test_io_counters_increment),
    ("log_write_stub_called_on_every_write", test_log_write_stub_called_on_every_write),
    ("buffer_hit_and_miss", test_buffer_hit_and_miss),
    ("buffer_eviction_writes_back_dirty", test_buffer_eviction_writes_back_dirty),
    ("lru_evicts_oldest", test_lru_evicts_oldest),
    ("mru_evicts_newest", test_mru_evicts_newest),
    ("flush_writes_dirty_keeps_in_pool", test_flush_writes_dirty_keeps_in_pool),
    ("persistence_across_instances", test_persistence_across_instances),
    ("layer3_never_touches_disk", test_layer3_never_touches_disk),
    ("reset_stats", test_reset_stats),
]


def main():
    print(f"Running {len(TESTS)} Phase 1 tests...")
    passed = 0
    for name, fn in TESTS:
        if _run(name, fn):
            passed += 1
    print(f"\n{passed}/{len(TESTS)} passed")
    sys.exit(0 if passed == len(TESTS) else 1)


if __name__ == "__main__":
    main()
