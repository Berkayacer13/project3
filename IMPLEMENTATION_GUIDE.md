# CMPE 321 Project 3 — Modular DBMS Engine: Implementation Guide

A working contract for the two of us. Read this fully before writing code, and re-read sections 2 and 11 (Hard Rules + Pitfalls) before every PR. The grading is reproducibility-driven: graders run `python3 archive.py config.json input.txt` with **only** the config changed between runs, and diff the output. A single missed rule (wrong file location, raw return value, mis-formatted stats line) can silently fail a whole batch of test cases.

## Progress Tracker

Update these as we complete work. Don't rely on memory — flip the box the moment a PR lands.

### Phase 0 — Skeleton (done)
- [x] Directory layout (`common/`, four module folders, `tests/`)
- [x] Result objects in `common/results.py` (`PageResult`, `WriteResult`, `AllocResult`, `BufferResult`, `RecordResult`, `OpResult`)
- [x] DiskSpaceManager stub with counters + `log_write` slot
- [x] BufferManager stub with the 5 counters
- [x] FileIndexManager stub with catalog dict + stats counters
- [x] QueryProcessor stub with output/log/stats file paths resolved to base dir
- [x] `archive.py` matching the spec's §3 pattern verbatim
- [x] `config.json` with all 5 fields
- [x] Import + construct smoke test passes (all four layers instantiate)

### Phase 1 — Lower stack (done)
- [x] DiskSpaceManager: `create_file`, `read_page`, `write_page`, `allocate_page`, free-list head on page 0
- [x] DiskSpaceManager: I/O counters increment on every op (header reads/writes included)
- [x] DiskSpaceManager: `log_write` invoked on every write (no-op default)
- [x] DiskSpaceManager: persistence — lazy header load means a fresh instance picks up an existing `.dat` correctly
- [x] BufferManager: LRU eviction (`next(iter(_frames))`)
- [x] BufferManager: MRU eviction (`next(reversed(_frames))`)
- [x] BufferManager: dirty tracking via `mark_dirty` + `flush()` (writes back, keeps frames in pool)
- [x] BufferManager: all 5 counters update correctly (requests, hits, misses, evictions, dirty_writebacks)
- [x] L3 disk passthroughs on the buffer (`create_file`, `file_exists`, `get_page_count`) so L3 never touches DSM
- [x] Unit tests for disk + buffer: 14 tests, all passing (`python3 tests/test_phase1.py`)

**Design decisions locked in during Phase 1** (document in the report):
- Page 0 of every `.dat`/`.idx` is a DSM-internal header (`page_count` u32 + `free_list_head` u32, rest zero-padded). The buffer never sees page 0; data starts at page 1.
- `WriteResult.old_data` is `b""` — reading-before-writing would double I/O cost. Reserved for opt-in WAL through `log_write`.
- `allocate_page` physically writes a zeroed page so subsequent reads always work. The buffer's `allocate_page` inserts the zeroed frame with `dirty=False` (matches disk).
- The buffer counts `allocate_page` as `requests += 1, misses += 1`. Eviction may still happen during allocation.
- `flush()` writes back dirty frames but does NOT remove them from the pool — pages may still be needed.
- Header reads/writes count toward disk I/O (they are real disk ops). Headers are cached in memory after first load.

### Phase 2 — Upper stack (done)
- [x] Slotted page encode/decode (`file_index_manager/page.py`)
- [x] System catalog with persistence (`catalog.dat`)
- [x] `create_type` (with field validation, duplicate detection)
- [x] `insert_record` (heap, with PK duplicate detection)
- [x] `delete_record` (heap)
- [x] `search_record` via heap scan
- [x] `range_search` via heap scan
- [x] Input line parser (inline in QueryProcessor._dispatch)
- [x] QueryProcessor: dispatch + log.csv writes + output.txt writes
- [x] QueryProcessor: failure handling (no crash on duplicate/missing/wrong-type)
- [x] QueryProcessor: `stats` writes `stats_output.txt` in the spec format
- [x] QueryProcessor: `explain` writes 3-block (PLAN / RESULT / STATS)
- [x] Unit tests: 16 Phase 2 tests passing (`python3 tests/test_phase2.py`)
- [x] End-to-end smoke vs spec §9 sample matches expected output

**Design decisions locked in during Phase 2** (document in the report):
- Field names may contain underscores: the spec §14 text says alphanumeric only, but the spec's own sample uses `military_strength` and `spice_production`. We accept `[A-Za-z0-9_]` — strictly a superset of what the grader produces.
- `_dispatch` parses inline (split-and-prefix) rather than via a separate parser module. The grammar is small enough that adding an AST layer would be overhead.
- `catalog.dat` is a pickled dict opened directly by L3 (not through the buffer/DSM). Catalog is metadata, not paged data; one read at startup + one rewrite per `create type` keeps it simple.
- `explain` plan estimates: `heap_scan = page_count - 1`, `bplus_tree = 3` (placeholder until Phase 3 wires the real tree height), `hash_index = 2`.
- `output.txt` is truncated at the start of every run; `log.csv` is append-only (per spec §15 persistence).

### Phase 3 — Indexes & system commands (done)
- [x] Hash index — static, 16 buckets, FNV-1a stable hashing (`file_index_manager/hash_index.py`)
- [x] B+-tree index — persistent, equality + range, splits propagate up with new-root promotion (`file_index_manager/bplus_tree.py`)
- [x] `hash_index.range_search` falls back to heap scan (spec §7.2)
- [x] FileIndexManager wires indexes via `_get_index` (lazy + cached) and `_lookup_pk` (index probe with heap fallback)
- [x] `explain` reports the type's stored strategy and uses real B+-tree height (`idx.height() + 1`)
- [x] `stats` and `stats reset` already shipped in Phase 2; verified they exercise the new index counters
- [x] Persistence smoke: 600-key shuffled load + tear-down + re-open in `test_phase3.py`
- [x] Unit tests: 14 Phase 3 tests passing (`python3 tests/test_phase3.py`); 44/44 across all phases

**Design decisions locked in during Phase 3** (document in the report):
- **Index strategy is frozen at `create type`.** `TypeMeta.index_strategy` captures the engine's config at the moment of creation. Config swaps between runs only affect *new* types — existing types keep using whatever index was built for them. This is the implementation guide §15.7 default ("rebuild on access" was the alternative; we picked freeze-on-create for predictability and zero migration cost).
- **Hash index parameters.** N = 16 primary buckets, fixed at code-level (`hash_index.NUM_BUCKETS`). Key bytes: 4 for int (signed little-endian), 32 for str (null-padded ASCII). Bucket page header is 8 bytes (`next_overflow_pid u32, entry_count u16, 2-byte pad`); entries are `key_bytes + data_pid u32 + slot u16`. Overflow pages chain via `next_overflow_pid`.
- **Stable hashing matters.** Python's built-in `hash()` is salted across runs (would invalidate a persisted index on restart). We use **FNV-1a 32-bit** for string keys and `int(key) % N` for int keys.
- **B+-tree node format.** Struct-encoded, one node per page. Header (16 bytes): `type u8, 3-byte pad, num_keys u32, parent_pid u32, next_pid u32` — `next_pid` doubles as leftmost-child pointer for internals and next-leaf pointer for leaves. Internal entries: `key + right_child_pid u32`. Leaf entries: `key + data_pid u32 + data_slot u16`. Splits at `(capacity - 1)` not `capacity` — the shift-on-insert loop needs one scratch slot at index `num_keys` before the split triggers.
- **B+-tree delete is non-rebalancing.** Leaves can become under-full; subsequent inserts refill them. Spec doesn't require strict balance, and lookups stay correct. Saves ~200 lines of merge/redistribute logic.
- **`hash_index` + `range_search` fallback updates the heap-scan counters** (`records_scanned`, `pages_accessed`), not `index_nodes_visited` — matches what the heap-scan path naturally produces and keeps the "Index: hash_index, 0 nodes visited" stats line truthful for range workloads.
- **Index pages go through the buffer**, same as data pages (spec §4.3). Both `.idx` files have a DSM-internal header at page 0; data starts at page 1.

### Phase 4 — Experiments & deliverables
- [x] `workload_generator.py` with 4 modes (sequential, random, range, mixed)
- [x] Experiment 1: LRU vs. MRU — runner in `run_experiments.py`
- [x] Experiment 2: heap_scan vs. hash_index vs. bplus_tree — runner in `run_experiments.py`
- [x] Experiment 3: buffer pool size sensitivity (4/8/16/32/64) — runner in `run_experiments.py`
- [ ] `report.pdf` — draft in `report.md`, fill experiment tables then convert to PDF
- [x] `record.txt` with reproduction commands
- [x] `README.md`
- [x] `ai_usage.md`
- [ ] Individual contribution PDFs
- [ ] Video recorded

---

## 1. Architecture at a Glance

Four layers, strict bottom-up dependency:

```
QueryProcessor   (Layer 4 — parses input, writes output.txt, logs to log.csv)
      |
FileIndexManager (Layer 3 — records, slotted pages, catalog, indexes)
      |
BufferManager    (Layer 2 — page cache, LRU/MRU)
      |
DiskSpaceManager (Layer 1 — ONLY layer that touches the filesystem)
```

**Rules of the dependency graph (non-negotiable):**
- Layer N talks only to Layer N-1. The QueryProcessor receives references to all three lower layers, but it uses them **only to read counters** for `stats` and `explain` — never to bypass a layer for data access.
- `FileIndexManager` must **never** call `DiskSpaceManager` directly. Always through the buffer.
- `DiskSpaceManager` is the **only** component that does file I/O. Buffer manager calls disk; nobody else opens a file (except the QueryProcessor for `output.txt`, `stats_output.txt`, and `log.csv`, which are output/log files, not data files).
- Every cross-layer return value is a **Result object** (see §3). Passing raw `bytes`, raw `int`, or `None` across a layer boundary breaks the contract.

---

## 2. Hard Rules (Things That Will Cost Us Points If Violated)

These are the warnings the PDF emphasizes. Internalize all of them.

1. **archive.py structure is fixed.** The exact pattern in §3 of the PDF must be preserved. We may add helpers but not change the wiring order, constructor signatures, the `qp.process(line)` loop, or the final `buffer.flush()`.
2. **Constructor signatures are fixed:**
   - `DiskSpaceManager(config: dict)`
   - `BufferManager(config: dict, disk: DiskSpaceManager)`
   - `FileIndexManager(config: dict, buffer: BufferManager)`
   - `QueryProcessor(config: dict, file_idx, buffer, disk)`
3. **All cross-layer returns are Result objects.** No exceptions.
4. **Only the Python standard library.** No `pip install`. Allowed: `struct`, `os`, `json`, `time`, `sys`, `dataclasses`, `typing`, `collections`, `pickle`, `hashlib`, `bisect`, `argparse`, `csv`, etc.
5. **All output files live next to `archive.py`.** Not in cwd, not in a temp folder, not in a path read from config. Compute paths relative to `__file__`'s directory.
6. **Persistence is required.** Data files, system catalog, indexes, and `log.csv` must survive process exit and be reloaded on next launch. `log.csv` is append-only.
7. **No crashes on failure conditions.** Duplicate type, duplicate PK, missing type/record, range search on non-int field → log `failure`, write nothing to `output.txt`, continue.
8. **`int(time.time())` for log timestamps.** Not `time.time()` (float), not ISO strings.
9. **`log_write` stub on DiskSpaceManager** must exist and be called on every page write, even if it's a no-op. We'll use it for WAL-style auditing.
10. **Count every I/O operation.** Every `read_page` and `write_page` increments a counter on DiskSpaceManager. Buffer hits do **not** increment disk I/O.
11. **Buffer manager tracks 5 counters:** total requests, hits, misses, evictions, dirty writebacks.
12. **Index data goes through the buffer.** B+-tree nodes and hash buckets are pages too — fetch and modify them via the buffer, never directly.
13. **`stats` overwrites `stats_output.txt`** (snapshot). `log.csv` appends.
14. **`stats reset` zeroes all counters across all layers.**
15. **Up to 10 records per page** (cap from `max_records_per_page`, even if the math allows more).
16. **Records are fixed-length.** Strings are null-padded to the chosen width.
17. **Page size, buffer size, replacement policy, index strategy** all come from `config.json`. Hard-coding these defeats the whole point of the project.
18. **Alphanumeric ASCII only** for type names, field names, and string values. No spaces, no Unicode, no special chars. We can assume the grader honors this — but we should still not crash on malformed input.
19. **`primary-key-order` is 1-indexed.** A value of `1` means field index 0.
20. **`hash_index` falls back to `heap_scan` for `range_search`.** The fallback must produce identical results to a true heap scan and update the heap-scan counters, not the hash counters.
21. **Search of a deleted/missing record writes NOTHING to `output.txt`** and logs `failure`. The empty line is also wrong — write nothing.
22. **`stats_output.txt` format is fixed.** Match the column alignment and labels exactly:
    ```
    === STATISTICS ===
    Disk I/O:     45 reads, 12 writes
    Buffer Pool:  128 requests, 91 hits, 37 misses (71.1% hit rate)
    Evictions:    29 (14 dirty writebacks)
    Index:        bplus_tree, 23 nodes visited
    Records:      82 scanned, 15 returned
    ```
    Hit rate is one decimal place. `Index:` line reflects the **active** strategy from config.
23. **`explain` runs the query.** It prints the plan first, then the result, then per-query actual stats — all to `output.txt`.
24. **Buffer pool sizes tested: 4, 8, 16, 32, 64.** Make sure the code works with a pool of 4 (eviction will happen frequently).
25. **Document and justify field byte widths, page header layout, record format** in the report. We will be asked about these.
26. **The submission ZIP must include:** `archive.py`, four module folders, `workload_generator.py`, `config.json`, `README.md`, `report.pdf`, `ai_usage.md`, `record.txt`, individual contribution PDFs.

---

## 3. Result Objects — The Most Important Decision

The PDF calls this the "most important architectural requirement." We will define them once, in a shared module, and never bypass them.

Place all Result types in `common/results.py` (a fifth top-level folder is fine — it's not a module per the rubric, it's a shared library). Import them everywhere.

**Proposed Result types** (we can adjust during week 1, but freeze before week 2):

```python
from dataclasses import dataclass, field
from typing import Optional, Any, List

@dataclass
class PageResult:                # DiskSpaceManager: read_page returns this
    data: bytes
    page_id: int
    file_id: str
    io_performed: bool           # always True for disk reads; False is reserved for the buffer's wrapping
    status: str = "success"

@dataclass
class WriteResult:               # DiskSpaceManager: write_page returns this
    success: bool
    page_id: int
    file_id: str
    old_data: bytes
    new_data: bytes

@dataclass
class AllocResult:               # DiskSpaceManager: allocate_page
    page_id: int
    file_id: str
    success: bool

@dataclass
class BufferResult:              # BufferManager: get_page returns this
    data: bytes                  # actual page bytes (so callers can read/modify)
    page_id: int
    file_id: str
    cache_hit: bool
    evicted_page_id: Optional[int]
    dirty_writeback: bool
    io_performed: bool           # True iff a disk read happened

@dataclass
class RecordResult:              # FileIndexManager: search/range
    records: List[List[Any]]     # rows are lists of field values, in declared order
    pages_accessed: int
    index_nodes_visited: int     # 0 for heap_scan
    status: str                  # "success" or "failure"
    message: str = ""            # human-readable error for failures

@dataclass
class OpResult:                  # FileIndexManager: insert/delete/create_type
    success: bool
    message: str = ""
    pages_touched: int = 0
    index_nodes_visited: int = 0
```

Two rules to enforce during code review:
- **No raw `bytes` returns across layers.** If a buffer call gives back raw bytes, the wrapper got bypassed.
- **No raw exceptions across layers.** Failures become `status="failure"` on the Result, not Python exceptions. The QueryProcessor catches structural bugs at the top level (one `try/except` around `qp.process(line)`) to satisfy the "no crash" rule, but layer-to-layer failures are normal control flow.

---

## 4. Concrete Design Decisions (Freeze These Early)

These are choices we have to make. I'm proposing defaults; if you disagree, say so before we write code.

| Decision | Proposed | Rationale |
| --- | --- | --- |
| Integer width | **4 bytes**, signed, `struct` format `<i` (little-endian) | Standard. Supports values up to ~2.1B. |
| String width | **32 bytes**, null-padded, ASCII | The PDF caps field-name length at 20+ and string field values are alphanumeric — 32 gives headroom and keeps records small. |
| Page header layout | `<I I H 2x` = 12 bytes: page_id (uint32), record_count (uint32), slot_bitmap (uint16, 10 bits used), 2 bytes padding | Slotted-page unpacked. Bitmap of 16 bits handles up to 10 slots comfortably. |
| Endianness | Little-endian everywhere (`<` prefix) | Consistency. |
| File naming | `<type_name>.dat` for relation files; `<type_name>.idx` for index; `catalog.dat` for system catalog | Keep it obvious. |
| Catalog format | Pickle of a dict `{ type_name: TypeMeta }`, rewritten on every `create type` | Catalog changes are rare. Persistence simple. |
| Free space tracking | Per-file free list: a header page (page 0 of each `.dat`) stores `next_alloc_page_id` and a bitmap of pages with free slots | Lets `insert` find a page in O(1). |
| B+-tree fanout | Derived from page size — compute at runtime from int width + page-id width | Keep it data-driven so changing page size doesn't break it. |
| Hash index | Static hashing, `N` buckets fixed at type creation, `N = 1024` default. Each bucket is a chain of overflow pages. | Project says "static" — don't implement dynamic hashing. |
| String hashing | Python's built-in `hash()` is salted across runs — use a stable hash like FNV-1a or SHA-1 mod N | Stability across runs is required because indexes persist. **This is a real footgun.** |

**Worked example for our defaults (6-field type, 3 str + 3 int):**
- `record_size = 3 × 32 + 3 × 4 = 108 bytes`
- `usable_space = 4096 − 12 (header) = 4084 bytes`
- `4084 // 108 = 37 records` would fit, but **we cap at 10** per the spec.
- 10 × 108 = 1080 bytes used; ~3000 bytes wasted per page. That's fine — the cap is a spec requirement, not an optimization.

---

## 5. File Structure (What We Commit)

```
project3/
├── archive.py
├── config.json
├── workload_generator.py
├── README.md
├── report.pdf
├── ai_usage.md
├── record.txt
├── <StudentID>_Contribution.pdf  (one per teammate)
├── common/
│   ├── __init__.py
│   └── results.py
├── disk_space_manager/
│   ├── __init__.py            (exports DiskSpaceManager)
│   ├── manager.py
│   └── page_io.py
├── buffer_manager/
│   ├── __init__.py            (exports BufferManager)
│   ├── manager.py
│   ├── lru.py
│   └── mru.py
├── file_index_manager/
│   ├── __init__.py            (exports FileIndexManager)
│   ├── manager.py
│   ├── catalog.py
│   ├── page.py                (slotted page encode/decode)
│   ├── heap_scan.py
│   ├── hash_index.py
│   └── bplus_tree.py
└── query_processor/
    ├── __init__.py            (exports QueryProcessor)
    ├── processor.py
    ├── parser.py
    └── logger.py
```

The grader only inspects what each `__init__.py` exports. The internal subdivision is for us.

---

## 6. Per-Layer Implementation Notes

### Layer 1 — DiskSpaceManager

**Public surface (what BufferManager calls):**
- `read_page(file_id, page_id) -> PageResult`
- `write_page(file_id, page_id, data) -> WriteResult`
- `allocate_page(file_id) -> AllocResult`
- `create_file(file_id)` / `file_exists(file_id) -> bool`
- `get_io_counts() -> (reads, writes)`
- `reset_counts()`
- `set_log_writer(fn)` — registers the `log_write` stub
- `flush_all()` — called from `buffer.flush()` indirectly

**Invariants:**
- Every `read_page` increments `reads`. Every `write_page` increments `writes`.
- Every `write_page` calls `self.log_write(file_id, page_id, old_bytes, new_bytes)` after writing.
- Files are opened with `open(path, 'r+b')` per operation, seeked, then closed — or kept open in a dict if perf matters. Simplest first: open on demand.
- Page 0 of each `.dat` is a header page with the per-file metadata (next free page, page count). Pages 1..N hold data.
- `seek(page_id * page_size)` then `read(page_size)`. **Never** load the whole file.

**Free space tracking choice:** per-file bitmap stored on page 0. Document this in the report.

### Layer 2 — BufferManager

**Public surface:**
- `get_page(file_id, page_id) -> BufferResult`
- `mark_dirty(file_id, page_id)` — or `pin/unpin` if we pick that model
- `flush()` — write all dirty pages, called at end of `archive.py`
- `get_stats() -> dict` with the 5 counters
- `reset_stats()`

**Data structures:**
- `frames: dict[(file_id, page_id), Frame]` where Frame = `{data: bytearray, dirty: bool, last_used_seq: int}`
- For LRU/MRU we'll use a monotonically increasing sequence number bumped on every access. On eviction, scan frames and pick min (LRU) or max (MRU) sequence. O(n) per miss, but n ≤ 64 — fine.
- An `OrderedDict` works too and is O(1), but the sequence-number approach makes LRU/MRU truly identical code paths with one comparator swap.

**Counters (track every call to get_page):**
- `requests` += 1 per call
- `hits` += 1 if in pool, else `misses` += 1
- On miss with full pool: `evictions` += 1; if evicted frame was dirty, `dirty_writebacks` += 1 and we write it back via disk.

**Replacement policy switch:** read `config["replacement_policy"]` in `__init__`. Don't read it on every call.

### Layer 3 — FileIndexManager

**Public surface (what QueryProcessor calls):**
- `create_type(name, fields, pk_index) -> OpResult`
- `insert_record(type_name, values) -> OpResult`
- `delete_record(type_name, pk_value) -> OpResult`
- `search_record(type_name, pk_value) -> RecordResult` (1 record max)
- `range_search(type_name, field_name, low, high) -> RecordResult`
- `get_index_stats() -> dict` with `{strategy, nodes_visited, records_scanned, records_returned}`

**Catalog:** a dict on disk in `catalog.dat`, persisted via pickle. Rewrite on every `create type`. Schema:
```python
TypeMeta = {
    "name": str,
    "fields": [(name, type), ...],
    "pk_index": int,           # 0-indexed internal
    "record_size": int,
    "field_offsets": [int],    # byte offset of each field in a record
    "index_strategy": str,     # captured at type creation time
}
```

**Slotted page format (unpacked):**
```
[ page header: 12 bytes ]
[ slot 0 record: record_size bytes ]
[ slot 1 record: record_size bytes ]
...
[ slot 9 record: record_size bytes ]
[ padding to page_size ]
```
"Unpacked" means slots have fixed positions; deletion just clears the bitmap bit, doesn't compact.

**Insert algorithm:**
1. Read per-file header page (via buffer). Find first page with a free slot via the bitmap.
2. Read that page (via buffer), find a clear bit in the slot bitmap, write the record bytes there, set the bit, mark dirty.
3. If full, allocate a new page (via disk through buffer), repeat.
4. If indexed, update the index.

**Index strategies — make them swappable via a base class:**
```python
class Index:
    def build(self, type_meta): ...
    def insert(self, key, page_id, slot): ...
    def delete(self, key): ...
    def lookup(self, key) -> list[(page_id, slot)]: ...
    def range(self, lo, hi) -> list[(page_id, slot)]: ...
    def nodes_visited(self) -> int: ...
```
`heap_scan` returns an empty `Index` that says "no index" — the manager checks `isinstance` or a flag and falls back to scanning. `hash_index.range()` is the fallback trigger: it returns `None` or raises `NotImplementedError` and the manager catches that to switch to heap scan, **incrementing the heap-scan path's counters, not the index's**.

### Layer 4 — QueryProcessor

**Public surface:** `process(line)` and that's it.

**Responsibilities:**
- Parse one input line into `(op, args)`.
- Dispatch to FileIndexManager.
- Write results to `output.txt`.
- Append to `log.csv` (timestamp, original line, status).
- Handle `stats`, `stats reset`, and `explain` specially.
- Catch all exceptions inside `process()` and convert them to `failure` log entries. We do not crash.

**Files opened by QueryProcessor (the exception to "only disk manager opens files"):**
- `output.txt` — opened in append mode, flushed after each line. Open once in `__init__`.
- `log.csv` — opened in append mode. Open once in `__init__`.
- `stats_output.txt` — opened in **write** mode each time `stats` runs (overwrites).

All three paths are computed as `os.path.join(os.path.dirname(os.path.abspath(__file__))...)` relative to where `archive.py` lives — but since the QP lives in a submodule, plumb the directory through the config or set it in `archive.py` before constructing the QP. Cleanest: `archive.py` resolves the base directory and passes it via `config["_base_dir"]`.

**Parser must accept the exact spec format.** Whitespace-split, validate, and convert int args.

**Output formatting:** for search/range, print fields space-separated in declared order, one record per line, then a single `\n`. No trailing whitespace.

---

## 7. Persistence — The Quiet Killer

The spec says "If the engine is stopped and re-invoked with the same data directory, it must recover its state from disk." Build for this from day 1; bolting it on later is painful.

- `DiskSpaceManager.__init__` should scan the working directory for existing `.dat` files and rebuild any in-memory state (open file handles, page counts).
- `FileIndexManager.__init__` should load `catalog.dat` if it exists. For each type with an index, read the index file from disk (the index pages are persistent too — they're regular pages routed through the buffer).
- `QueryProcessor.__init__` should open `log.csv` in append mode, not write mode.

**Test this:** run the engine with a `create type` + a few `create record`s, exit, then re-run with only `search record` — it must find them.

---

## 8. Logging — `log.csv`

- Append-only. Open once in `__init__`, close on `flush`/destructor.
- Format: `timestamp,operation_string,status\n`
- `timestamp = int(time.time())` — integer, not float.
- `operation_string` is the original input line, **un-escaped** but commas inside the input would break CSV. The spec's example shows commas in the operation string are not escaped, which means the format is CSV-shaped but not strictly CSV-parsable. **Match the spec's example output exactly** — don't quote the operation field. If we want to be safe, use Python's `csv` module with `quoting=csv.QUOTE_MINIMAL`, but verify the sample output matches first.
- `status` is literally `success` or `failure`.

`explain` and `stats` commands: I think these should still be logged (the spec isn't explicit). Default: log them as `success`. If the grader expects them unlogged, easy fix.

---

## 9. Output Format — Strict

- `output.txt`:
  - `search record` (success) → one line with space-separated field values, in declared order.
  - `search record` (failure / not found / no type) → **nothing**.
  - `range_search` → one line per matching record, in heap order (or index-traversal order for B+-tree). The spec doesn't require a specific ordering, so document our choice.
  - `range_search` with zero matches → **nothing**.
  - `create record`, `delete record`, `create type` → **nothing**.
  - `explain` → the 3-block format shown in §9.2 of the PDF. Exactly. Match the labels and capitalization.
- `stats_output.txt`: fixed 6-line format, see Hard Rule 22.
- `log.csv`: see §8.

**Trailing newlines:** the spec doesn't say. Use `\n` after each line. Don't add extra blank lines between operations.

---

## 10. Workload Generator

A separate concern — owned by whoever writes the experiments.

```bash
python3 workload_generator.py --mode {sequential,random,range,mixed} --records N --queries Q > workload.txt
```

Requirements:
- Output a `create type` line first, with ≥4 fields and ≥2 int fields. (Spec is more permissive than the engine, which requires ≥6 fields — so the generator should still emit 6 fields to be consistent with what the engine accepts.)
- Then `N` `create record` lines with valid alphanumeric values.
- Then `Q` query lines, generated according to mode.
- Use a fixed `random.seed(...)` (e.g., 42) so experiments are reproducible. Document this.

---

## 11. Pitfalls to Avoid (Lessons From Similar Projects)

1. **Python's `hash()` is salted across runs.** If we use it for the hash index, lookups will fail after restart. Use FNV-1a, SHA-1 mod N, or `zlib.crc32` mod N.
2. **`bytes` vs `bytearray`.** Pages we modify must be `bytearray`. If the buffer returns immutable `bytes` and the file manager tries to mutate, we crash. Decide once and stick to it. Recommendation: buffer holds `bytearray`, results contain a reference to the same buffer, mutations propagate, `mark_dirty` is required after any write.
3. **Eviction of a dirty page that is still being modified.** Avoid by either (a) implementing pin counts, or (b) following a strict "read page → modify → mark dirty → release" discipline with no nested page access. Option (b) is simpler — only nest when you must (e.g., B+-tree splits).
4. **B+-tree node splits during insert.** Document the algorithm. Use a recursive insert that returns the new sibling info up the tree. Test with small fanouts.
5. **`hash_index` range fallback.** Easy to forget to count the heap-scan I/Os against the right counters. The fallback should call the same `_heap_scan_range` helper that `heap_scan` does, so counters update consistently.
6. **`delete_record` from indexes.** Don't just clear the slot bitmap — also remove from the index. Forgetting this breaks search after delete.
7. **`stats reset` must reset all four layers.** QP iterates through and resets each. Don't forget the FileIndexManager's "records_scanned / records_returned" counter.
8. **`buffer.flush()` at end of archive.py** must write back every dirty page. After flush, the on-disk state must equal the in-memory state.
9. **Counting I/O for the buffer flush at the end.** The PDF doesn't say whether flush writes count toward the `writes` counter. Default: yes, they do — they're real disk writes. Document this.
10. **The first run of a brand-new database** with no `catalog.dat` must not crash. Initialize empty state.
11. **Concurrency:** there is none. Single-threaded.
12. **Don't write files to a path derived from config.** All output lives next to `archive.py`. Resolve via `os.path.dirname(os.path.abspath(archive_py_path))`.

---

## 12. Work Split (Proposal)

Two people, ~3 weeks. Adjust based on what each of us prefers.

**Person A — Lower stack (week 1):**
- Result objects in `common/results.py` (joint pairing session).
- DiskSpaceManager: complete with persistence, free-list, I/O counters, `log_write` stub.
- BufferManager: LRU and MRU, all 5 counters, dirty tracking, flush.
- Unit tests for both (small driver scripts).

**Person B — Upper stack (week 1):**
- Slotted page encode/decode (in `file_index_manager/page.py`).
- System catalog with persistence.
- `heap_scan` end-to-end (create type, insert, delete, search, range).
- Input parser.
- Basic QueryProcessor without `stats`/`explain` yet.

**Joint (week 2):**
- Hash index (Person A) + B+-tree (Person B), running in parallel.
- `explain` and `stats` and `stats reset`.
- Failure-condition handling and logging.
- Persistence end-to-end smoke test.

**Joint (week 3):**
- `workload_generator.py`.
- Experiments 1–3, generate tables for `report.pdf`.
- `record.txt` with exact commands to reproduce.
- Video script + recording.
- README, ai_usage.md, individual contribution PDFs.

**Code review rule:** every PR is reviewed by the other person before merge. Specific things to check:
- No raw bytes / ints crossing a layer boundary.
- No direct disk access from Layer 3.
- I/O counters increment on every disk op.
- Persistence test still passes.

---

## 13. Testing Strategy

The grader runs `archive.py` against an `input.txt` and diffs `output.txt`. Replicate that locally.

**Test artifacts to build:**
- `tests/inputs/basic.txt` — the spec's §9.1 sample. We already know the expected output.
- `tests/inputs/persist_part1.txt` — `create type` + `create record`s + exit.
- `tests/inputs/persist_part2.txt` — `search record` (run after part 1, must succeed).
- `tests/inputs/failure_cases.txt` — duplicate type, duplicate PK, missing record, range on str field. Verify no crash and all log as `failure`.
- `tests/inputs/all_indexes/` — same input run with each index strategy. Output should be identical.
- `tests/inputs/lru_vs_mru.txt` — sequential and random workloads for the report.

**Smoke test script** (we should write this once and run it before every commit):
```bash
rm -f *.dat *.idx catalog.dat output.txt log.csv stats_output.txt
python3 archive.py config.json tests/inputs/basic.txt
diff output.txt tests/expected/basic_output.txt
```

---

## 14. Submission Checklist

Before we hit submit:

- [ ] `archive.py` matches the §3 pattern verbatim (constructor order, `qp.process(line)`, `buffer.flush()`).
- [ ] Four module folders, each with `__init__.py` exporting the named class.
- [ ] `config.json` with all five fields.
- [ ] `workload_generator.py` accepts the four modes.
- [ ] `README.md` with setup/run instructions.
- [ ] `report.pdf` documents: field byte widths, page header layout, record format, free-space tracking choice, three experiment tables, justifications.
- [ ] `ai_usage.md` — honest disclosure of which tools we used and for what.
- [ ] `record.txt` — exact commands to reproduce each experiment.
- [ ] `<StudentID>_Contribution.pdf` for each of us (Times New Roman 12pt, 1.5 spacing, 1–2 pages).
- [ ] Smoke test passes with `index_strategy = heap_scan`, `hash_index`, and `bplus_tree`.
- [ ] Smoke test passes with `replacement_policy = LRU` and `MRU`.
- [ ] Smoke test passes with `buffer_pool_size = 4` and `64`.
- [ ] Persistence test passes (kill + restart).
- [ ] `output.txt`, `log.csv`, `stats_output.txt` all land **next to archive.py**.
- [ ] All Result objects in `common/results.py`; no raw types crossing layers.
- [ ] Failure cases produce `failure` log entries and no crash.
- [ ] Video links recorded in the final PDF.
- [ ] GitHub repo set to private and access granted to graders per course instructions.

---

## 15. Open Questions for Us to Decide

Things the spec is ambiguous about — let's agree before coding.

1. **`explain` log entry:** should it log as `success` or be skipped? (Default: log as `success`.)
2. **`stats` and `stats reset` log entries:** same question.
3. **`range_search` result ordering:** heap order? sorted by key? (Default: implementation-defined, document in report.)
4. **`buffer.flush()` final writes:** count as I/O? (Default: yes.)
5. **Empty type (no records) `range_search`:** write nothing, log as `success`? (Default: yes, success with zero results.)
6. **`create type` with 0 records and then `stats`:** Records line shows `0 scanned, 0 returned`? (Default: yes.)
7. **Index strategy at type creation vs. runtime:** spec implies it's read at runtime from config. If the user `create type`s with `bplus_tree` then restarts with `hash_index`, what happens? (Default: rebuild the index for that type from the heap file on first access. Document.)

Resolve these in a Slack/issue thread before we start coding.

---

## 16. References to the PDF

Quick map for when one of us cites "the spec":

| Topic | PDF Section |
| --- | --- |
| File structure | §2 |
| `archive.py` exact pattern | §3 |
| DiskSpaceManager responsibilities | §4.1 |
| BufferManager responsibilities | §4.2 |
| FileIndexManager record/page rules | §4.3 |
| QueryProcessor role | §4.4 |
| Result objects | §5 |
| Config parameters | §6 |
| Operation formats | §7.1–7.3 |
| Failure conditions | §7.4 |
| log.csv format | §8 |
| Sample I/O | §9 |
| stats_output.txt format | §9.3 |
| Workload generator | §10 |
| Experiments to run | §12 |
| Output file locations | §13 |
| Standard library only | §14 |
| Persistence | §15 |
