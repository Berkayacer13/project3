# AI Usage Disclosure

## Tool Used

**Claude Code** (model: `claude-sonnet-4-6` by Anthropic) was used as the primary AI assistant throughout all four phases of this project.

## How It Was Used

### Phase 0 — Skeleton
Claude Code was used to scaffold the directory layout, stub out the four module classes with correct constructor signatures, and create the `common/results.py` Result dataclasses. The implementation guide (`IMPLEMENTATION_GUIDE.md`) was authored collaboratively to document all design decisions before coding began.

### Phase 1 — DiskSpaceManager + BufferManager
Claude Code generated the initial implementations of `disk_space_manager/manager.py` and `buffer_manager/manager.py`, including the page-0 header layout, LRU/MRU eviction logic via `OrderedDict`, dirty-page tracking, and all I/O counter increments. Unit tests in `tests/test_phase1.py` were also generated with AI assistance.

### Phase 2 — FileIndexManager + QueryProcessor (heap scan)
Claude Code implemented the slotted-page encode/decode (`file_index_manager/page.py`), the system catalog with pickle persistence, heap-scan insert/delete/search/range_search, and the full QueryProcessor dispatch loop including `stats`, `stats reset`, and `explain`. Unit tests in `tests/test_phase2.py` were generated with AI assistance.

### Phase 3 — Indexes
Claude Code implemented the static hash index (`file_index_manager/hash_index.py`) using FNV-1a stable hashing and the B+-tree index (`file_index_manager/bplus_tree.py`) with persistent struct-encoded nodes, recursive splits, and non-rebalancing deletes. The `FileIndexManager` wiring and `explain`/`stats` integration were also done with AI assistance. Unit tests in `tests/test_phase3.py` were generated with AI assistance.

### Phase 4 — Experiments and Deliverables
Claude Code generated `workload_generator.py`, `run_experiments.py`, `README.md`, `record.txt`, and `report.md`. Experiment analysis and the report text were drafted with AI assistance.

## Human Contributions

- Architecture decisions: layer separation rules, Result object design, page format (12-byte header, 10-record cap), index strategy selection.
- All key design decisions recorded in `IMPLEMENTATION_GUIDE.md` before implementation.
- Code review: every AI-generated module was reviewed against the spec and the implementation guide before acceptance.
- Testing: all 44 tests were run and verified to pass; edge cases and failure conditions were tested manually.
- Report: experiment results were interpreted and the written analysis reflects the team's understanding.

## Statement

All code and documentation in this submission was reviewed, tested, and accepted by the team. AI-generated code was not accepted blindly — it was checked against the project specification and corrected where needed.
