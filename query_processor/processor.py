"""Layer 4 — QueryProcessor.

Parses input lines, dispatches to FileIndexManager, writes results to
output.txt, and appends every operation to log.csv. Handles `stats`, `stats
reset`, and `explain` (`explain` stub in Phase 2 — fleshed out in Phase 3).

The QP holds references to every lower layer so it can READ COUNTERS for
stats output. It does NOT bypass layers for data access.

Output rules (spec §9):
- search record (success) → space-separated record on one line
- search record (failure / not found) → nothing
- range_search → one line per matching record; zero matches → nothing
- create / delete → nothing on output.txt regardless of success
- All operations append a row to log.csv with status success or failure
"""

import os
import time

from buffer_manager import BufferManager
from disk_space_manager import DiskSpaceManager
from file_index_manager import FileIndexManager


class QueryProcessor:
    def __init__(
        self,
        config: dict,
        file_idx: FileIndexManager,
        buffer: BufferManager,
        disk: DiskSpaceManager,
        recovery=None,
    ):
        self.config = config
        self.file_idx = file_idx
        self.buffer = buffer
        self.disk = disk
        self.recovery = recovery

        # Project 4: name (chosen by the input file, e.g. T1) -> internal XID.
        # Multiple transactions may be open at once; ops interleave line by line.
        self.open_txns: dict = {}

        self.base_dir: str = config["_base_dir"]
        self.output_path = os.path.join(self.base_dir, "output.txt")
        self.log_path = os.path.join(self.base_dir, "log.csv")
        self.stats_output_path = os.path.join(self.base_dir, "stats_output.txt")

        # output.txt starts fresh each run. log.csv is append-only across runs.
        open(self.output_path, "w").close()
        open(self.log_path, "a").close()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process(self, line: str) -> None:
        try:
            self._dispatch(line)
        except Exception:
            # Spec §7.4: the system must not crash. Anything unexpected
            # is logged as a failure and we move on.
            self._log(line, "failure")

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return

        head = parts[0]
        if head == "tx_begin":
            self._do_tx_begin(line, parts[1:])
        elif head == "tx_op":
            self._do_tx_op(line, parts[1:])
        elif head == "tx_commit":
            self._do_tx_commit(line, parts[1:])
        elif head == "crash":
            self._do_crash()
        elif head == "explain":
            inner = line[len("explain"):].strip()
            if inner:
                self._do_explain(line, inner)
            else:
                self._log(line, "failure")
        elif head == "stats":
            if len(parts) >= 2 and parts[1] == "reset":
                self._do_stats_reset(line)
            else:
                self._do_stats(line)
        elif head in ("create", "delete", "search", "range_search"):
            # A data operation outside any open transaction is invalid (spec §7):
            # log it as a failure with no effect on the database.
            self._log(line, "failure")
        else:
            self._log(line, "failure")

    # ------------------------------------------------------------------
    # Transaction control (Project 4 §7)
    # ------------------------------------------------------------------

    def _do_tx_begin(self, line, args):
        if len(args) != 1 or self.recovery is None:
            self._log(line, "failure")
            return
        name = args[0]
        if name in self.open_txns:
            self._log(line, "failure")  # already open
            return
        self.open_txns[name] = self.recovery.begin_txn()
        self._log(line, "success")

    def _do_tx_op(self, line, args):
        # args = [name, <inner command tokens...>]
        if len(args) < 2 or args[0] not in self.open_txns:
            self._log(line, "failure")
            return
        xid = self.open_txns[args[0]]
        self.recovery.set_current_xid(xid)
        try:
            status = self._run_op(args[1:])
        finally:
            self.recovery.set_current_xid(None)
        self._log(line, status)

    def _do_tx_commit(self, line, args):
        if len(args) != 1 or args[0] not in self.open_txns:
            self._log(line, "failure")
            return
        xid = self.open_txns.pop(args[0])
        self.recovery.commit_txn(xid)
        self._log(line, "success")

    def _do_crash(self):
        # Simulate power failure: terminate mid-flight. No flush, no destructors,
        # no cleanup (spec §7 — must be os._exit, not sys.exit / exception).
        os._exit(1)

    # ------------------------------------------------------------------
    # Data operations (only reachable inside a tx_op; logged under its XID)
    # ------------------------------------------------------------------

    def _run_op(self, parts) -> str:
        head = parts[0]
        if head == "create" and len(parts) >= 2 and parts[1] == "type":
            return self._op_create_type(parts[2:])
        if head == "create" and len(parts) >= 2 and parts[1] == "record":
            return self._op_create_record(parts[2:])
        if head == "delete" and len(parts) >= 2 and parts[1] == "record":
            return self._op_delete_record(parts[2:])
        if head == "search" and len(parts) >= 2 and parts[1] == "record":
            return self._op_search_record(parts[2:])
        if head == "range_search":
            return self._op_range_search(parts[1:])
        return "failure"

    def _op_create_type(self, args) -> str:
        # <name> <num_fields> <pk_order> <field1_name> <field1_type> ...
        if len(args) < 3:
            return "failure"
        type_name = args[0]
        try:
            num_fields = int(args[1])
            pk_order = int(args[2])
        except ValueError:
            return "failure"
        field_args = args[3:]
        if len(field_args) != 2 * num_fields:
            return "failure"
        fields = [(field_args[2 * i], field_args[2 * i + 1]) for i in range(num_fields)]
        res = self.file_idx.create_type(type_name, fields, pk_order - 1)
        return "success" if res.success else "failure"

    def _op_create_record(self, args) -> str:
        if len(args) < 2:
            return "failure"
        res = self.file_idx.insert_record(args[0], args[1:])
        return "success" if res.success else "failure"

    def _op_delete_record(self, args) -> str:
        if len(args) != 2:
            return "failure"
        res = self.file_idx.delete_record(args[0], args[1])
        return "success" if res.success else "failure"

    def _op_search_record(self, args) -> str:
        if len(args) != 2:
            return "failure"
        res = self.file_idx.search_record(args[0], args[1])
        if res.status == "success" and res.records:
            self._write_output(self._format_record(res.records[0]))
            return "success"
        return "failure"  # no match → nothing on output.txt

    def _op_range_search(self, args) -> str:
        if len(args) != 4:
            return "failure"
        type_name, field_name, low_s, high_s = args
        try:
            low, high = int(low_s), int(high_s)
        except ValueError:
            return "failure"
        res = self.file_idx.range_search(type_name, field_name, low, high)
        if res.status != "success":
            return "failure"
        for rec in res.records:
            self._write_output(self._format_record(rec))
        return "success"

    def _do_stats(self, line):
        d_reads = self.disk.reads
        d_writes = self.disk.writes
        b_req = self.buffer.requests
        b_hits = self.buffer.hits
        b_miss = self.buffer.misses
        b_evict = self.buffer.evictions
        b_wb = self.buffer.dirty_writebacks
        hit_rate = (100.0 * b_hits / b_req) if b_req > 0 else 0.0
        idx = self.file_idx.get_index_stats()

        lines = [
            "=== STATISTICS ===",
            f"Disk I/O:     {d_reads} reads, {d_writes} writes",
            f"Buffer Pool:  {b_req} requests, {b_hits} hits, {b_miss} misses ({hit_rate:.1f}% hit rate)",
            f"Evictions:    {b_evict} ({b_wb} dirty writebacks)",
            f"Index:        {idx['strategy']}, {idx['nodes_visited']} nodes visited",
            f"Records:      {idx['records_scanned']} scanned, {idx['records_returned']} returned",
        ]
        with open(self.stats_output_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        self._log(line, "success")

    def _do_stats_reset(self, line):
        self.disk.reset_counts()
        self.buffer.reset_stats()
        self.file_idx.reset_stats()
        self._log(line, "success")

    # ------------------------------------------------------------------
    # explain
    # ------------------------------------------------------------------

    def _do_explain(self, line, inner_line):
        """Run the inner DML, capture per-query deltas, emit the 3-block.

        Block layout (spec §9.2):
            ---PLAN---
            Query: <inner_line>
            Strategy: <strategy>
            Estimated I/O: <int>
            ---RESULT---
            <each matching record>
            ---STATS---
            Actual I/O: R reads, W writes
            Buffer Hits: H
            Buffer Misses: M
            Pages Scanned: H+M
        """
        parts = inner_line.split()
        if not parts:
            self._log(line, "failure")
            return
        head = parts[0]
        # Determine the type referenced for the plan / estimate.
        if head in ("create", "delete", "search") and len(parts) >= 3:
            type_name = parts[2]
        elif head == "range_search" and len(parts) >= 2:
            type_name = parts[1]
        else:
            self._log(line, "failure")
            return

        # The type's stored strategy is authoritative — config swaps don't
        # rebuild old indexes. Fall back to config's strategy for types we
        # don't recognize (shouldn't happen for explain on a known type).
        if self.file_idx.has_type(type_name):
            strategy = self.file_idx.get_type(type_name).index_strategy
        else:
            strategy = self.file_idx.index_strategy
        if head == "range_search" and strategy == "hash_index":
            strategy = "heap_scan"  # spec §7.2 fallback

        page_count = self.file_idx.page_count(type_name)
        if strategy == "heap_scan":
            est_io = max(0, page_count - 1)
        elif strategy == "bplus_tree":
            # height descent + one leaf read; for range, add a small constant
            # for leaf-chain traversal. Cheap heuristic that's honest with
            # the actual access pattern.
            meta = self.file_idx.get_type(type_name)
            idx = self.file_idx._get_index(meta)  # cached; created at create_type
            try:
                est_io = idx.height() + 1
            except AttributeError:
                est_io = 3
        elif strategy == "hash_index":
            est_io = 2  # one bucket page (+1 if overflow chain — heuristic)
        else:
            est_io = 0

        # Snapshot, run, delta.
        snap = self._snapshot()
        # We want to capture output rows separately, not write them inline.
        captured_rows = []
        inner_status = self._explain_inner(parts, captured_rows)
        delta = self._delta(snap, self._snapshot())

        block = [
            "---PLAN---",
            f"Query: {inner_line}",
            f"Strategy: {strategy}",
            f"Estimated I/O: {est_io}",
            "---RESULT---",
        ]
        for row in captured_rows:
            block.append(self._format_record(row))
        block += [
            "---STATS---",
            f"Actual I/O: {delta['reads']} reads, {delta['writes']} writes",
            f"Buffer Hits: {delta['hits']}",
            f"Buffer Misses: {delta['misses']}",
            f"Pages Scanned: {delta['hits'] + delta['misses']}",
        ]
        self._write_output("\n".join(block))
        self._log(line, inner_status)

    def _explain_inner(self, parts, out_rows) -> str:
        """Execute the inner DML; collect result rows for explain; return status."""
        head = parts[0]
        if head == "create" and len(parts) >= 2 and parts[1] == "record":
            r = self.file_idx.insert_record(parts[2], parts[3:])
            return "success" if r.success else "failure"
        if head == "delete" and len(parts) >= 2 and parts[1] == "record":
            if len(parts) != 4:
                return "failure"
            r = self.file_idx.delete_record(parts[2], parts[3])
            return "success" if r.success else "failure"
        if head == "search" and len(parts) >= 2 and parts[1] == "record":
            if len(parts) != 4:
                return "failure"
            r = self.file_idx.search_record(parts[2], parts[3])
            if r.status == "success" and r.records:
                out_rows.append(r.records[0])
                return "success"
            return "failure"
        if head == "range_search":
            if len(parts) != 5:
                return "failure"
            try:
                low, high = int(parts[3]), int(parts[4])
            except ValueError:
                return "failure"
            r = self.file_idx.range_search(parts[1], parts[2], low, high)
            if r.status == "success":
                out_rows.extend(r.records)
                return "success"
            return "failure"
        return "failure"

    def _snapshot(self) -> dict:
        return {
            "reads": self.disk.reads,
            "writes": self.disk.writes,
            "hits": self.buffer.hits,
            "misses": self.buffer.misses,
        }

    @staticmethod
    def _delta(a, b):
        return {k: b[k] - a[k] for k in a}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_record(values) -> str:
        return " ".join(str(v) for v in values)

    def _write_output(self, text: str) -> None:
        with open(self.output_path, "a") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")

    def _log(self, line: str, status: str) -> None:
        with open(self.log_path, "a") as f:
            f.write(f"{int(time.time())},{line},{status}\n")
