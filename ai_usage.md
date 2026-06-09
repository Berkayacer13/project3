# AI Usage Disclosure

## Tool Used

AI was used as a learning aid during Project 4 (Write-Ahead Logging and Crash Recovery). All implementation was written and integrated by the team — **Enes** and **Berkay** — with the AI used to clarify concepts, explain the ARIES recovery algorithm, and discuss design trade-offs before and while coding.

## How It Was Used

### Understanding WAL and the Recovery Manager
Before writing any code, Enes and Berkay used Claude Code to clarify the Write-Ahead Logging model: what the Log Sequence Number (LSN), Transaction Table (TT), and Dirty Page Table (DPT) represent, why the log file must bypass the buffer pool, and what the `pageLSN` in each page header is for. We asked the AI to explain the two WAL invariants — atomicity (WAL #1: a dirty page is not written until its log record is on disk) and durability (WAL #2: commit fsyncs the log) — in plain terms. The team then implemented `recovery_manager/manager.py` and wired the invariants into the BufferManager and the commit path ourselves.

### Log records and format
We asked the AI to confirm which fields each record type needs (`lsn`, `prev_lsn`, `xid`, `type`, and the update-only `page_id`/`offset`/`before`/`after`) and how `update`, `commit`, `end`, `begin_chkpt`, and `end_chkpt` differ. Berkay implemented the on-disk serialization (length-prefixed records that tolerate a truncated tail after a crash) and the in-memory log buffer plus the `flushedLSN` counter; Enes implemented LSN generation that survives restarts by continuing from the log rather than resetting to zero.

### Three-phase recovery (Analysis / Redo / Undo)
This was the hardest part of the project, so we used Claude Code mainly to clarify the algorithm: how Analysis rebuilds the TT/DPT from the most recent checkpoint, why Redo must "repeat history" and what its three skip conditions mean, and how Undo walks each loser's `lastLSN` chain in descending order, applying before-images and writing an `end` record when a chain is exhausted. We also asked clarifying questions about edge cases (committed-but-unended transactions, and the one-shot assumption that no crash happens during recovery). Enes and Berkay coded all three phases and debugged them step by step against the provided test cases.

### Checkpointing
We asked the AI to explain what makes a checkpoint *fuzzy* (snapshot the tables, do not flush dirty data pages) and the role of the master record. The team implemented the `begin_chkpt`/`end_chkpt` records, the TT/DPT snapshot, the periodic interval trigger, and the persistent `master.rec`.

### Integration with the existing layers
Because recovery is a cross-cutting concern, we discussed with the AI where each call belongs (FileIndexManager → `log_update`, BufferManager → `flush_log_up_to`, RecoveryManager → DiskSpaceManager for the raw log file). The team then implemented: `pageLSN` tracking in `buffer_manager/manager.py`; the per-slot occupancy page format in `file_index_manager/page.py` (needed so that Undo of an interleaved transaction does not clobber another transaction's committed record); the explicit transaction commands `tx_begin` / `tx_op` / `tx_commit` and the `crash` command (`os._exit(1)`) in `query_processor/processor.py`; and the real WAL log file (append + `os.fsync`) in `disk_space_manager/manager.py`.

### Debugging and testing
We used the AI to reason about a recovery bug in which interleaved transactions sharing a page header caused committed records to disappear after Undo. The team identified the root cause (a shared `bitmap`/`record_count` field) and decided on the two fixes — per-slot occupancy flags and rebuilding indexes from the recovered data — and implemented them. We then ran the four provided test cases and additional edge cases (all-loser rollback, open transaction at clean EOF, multi-crash chains, and interleaving under heap, hash, and B+-tree strategies) to confirm correctness.

## Human Contributions

- Enes and Berkay wrote and integrated all Recovery Manager code; the AI was used to explain concepts, not to generate the implementation.
- Architecture decisions: `pageLSN` ownership in the BufferManager, the per-slot page layout, rebuilding indexes from recovered data after the three phases, and the documented DDL-rollback limitation.
- Every AI explanation was checked against the project specification; no code was accepted without the team understanding why it works.
- Testing: all four provided cases were verified byte-for-byte, and additional crash/restart edge cases were tested manually across all three index strategies.

## Statement

All code and documentation in this submission was written, reviewed, tested, and understood by the team. AI was used as a learning aid to understand Write-Ahead Logging and the ARIES recovery algorithm and to clarify design choices — not to produce code blindly. We can explain every part of our code, including the Recovery Manager.
