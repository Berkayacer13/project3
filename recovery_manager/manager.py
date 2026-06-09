"""Recovery Manager — Write-Ahead Logging + simplified ARIES recovery.

Exported class: RecoveryManager(config, disk).

Cross-cutting responsibilities (spec §4):
  * Generate monotonically increasing, restart-surviving LSNs.
  * Maintain the in-memory Transaction Table (TT) and Dirty Page Table (DPT).
  * Maintain a log buffer + flushedLSN; write update/commit/end/begin_chkpt/
    end_chkpt records to the dedicated WAL file (never through the buffer pool).
  * Enforce WAL #1 via flush_log_up_to(lsn) (called by the BufferManager before
    any dirty-page write) and WAL #2 via commit (fsync the commit record).
  * Run periodic fuzzy checkpoints and the three-phase recovery on startup.

Log record format (spec §4.3) — one Python dict per record, length-prefixed and
pickled in wal.log:
    lsn, prev_lsn, xid, type ∈ {update, commit, end, begin_chkpt, end_chkpt}
    update only: file_id, page_id, offset, before, after
    end_chkpt only: txn_table, dpt (snapshots)

`offset` is a *physical* byte offset into the 4096-byte page. The first 8 bytes
of every page are the BufferManager-owned pageLSN, so update offsets are always
>= 8 and never touch the pageLSN field directly.
"""

import os
import pickle
import struct

# Record type tags (kept as short strings for readability in the log).
T_UPDATE = "update"
T_COMMIT = "commit"
T_END = "end"
T_BEGIN_CHKPT = "begin_chkpt"
T_END_CHKPT = "end_chkpt"

_LEN_FMT = "<I"
_LEN_SIZE = struct.calcsize(_LEN_FMT)


def _serialize(rec: dict) -> bytes:
    body = pickle.dumps(rec, protocol=4)
    return struct.pack(_LEN_FMT, len(body)) + body


def _deserialize_all(blob: bytes) -> list:
    """Parse every complete record; stop at a truncated tail (crash artefact)."""
    out = []
    i, n = 0, len(blob)
    while i + _LEN_SIZE <= n:
        (ln,) = struct.unpack_from(_LEN_FMT, blob, i)
        i += _LEN_SIZE
        if i + ln > n:
            break  # half-written final record — ignore it
        try:
            out.append(pickle.loads(blob[i:i + ln]))
        except Exception:
            break
        i += ln
    return out


class RecoveryManager:
    def __init__(self, config: dict, disk):
        self.config = config
        self.disk = disk
        self.base_dir = config["_base_dir"]

        self.log_buffer_size = int(config.get("log_buffer_size", 8) or 8)
        self.checkpoint_interval = int(config.get("checkpoint_interval", 50) or 0)

        # In-memory tables.
        self.txn_table: dict = {}   # xid -> {"status": str, "last_lsn": int|None}
        self.dpt: dict = {}         # (file_id, page_id) -> recLSN
        self.log_buffer: list = []  # [(lsn, serialized_bytes)] not yet on disk

        # Set after the BufferManager is constructed (recovery needs it).
        self.buffer = None

        # Transient flags / context.
        self.current_xid = None     # XID the BufferManager logs under, per op
        self.recovering = False     # True while replaying — suppresses logging
        self._op_count = 0

        # Load the existing WAL so LSNs/XIDs continue (never reset — spec §12).
        self._all_records = _deserialize_all(self.disk.log_read_all())
        max_lsn = max((r["lsn"] for r in self._all_records), default=0)
        max_xid = max(
            (r["xid"] for r in self._all_records
             if isinstance(r.get("xid"), int) and r["xid"] >= 0),
            default=0,
        )
        self.next_lsn = max_lsn + 1
        self.next_xid = max_xid + 1
        self.flushed_lsn = max_lsn  # everything already in the file is durable

        # Master record: begin_chkpt LSN of the most recent checkpoint.
        self.master_path = os.path.join(self.base_dir, "master.rec")
        self.master_lsn = self._read_master()

    def set_buffer(self, buffer) -> None:
        self.buffer = buffer

    # ------------------------------------------------------------------
    # Master record
    # ------------------------------------------------------------------

    def _read_master(self):
        if not os.path.exists(self.master_path):
            return None
        try:
            with open(self.master_path) as f:
                txt = f.read().strip()
            return int(txt) if txt else None
        except (OSError, ValueError):
            return None

    def _write_master(self, lsn: int) -> None:
        with open(self.master_path, "w") as f:
            f.write(str(lsn))
            f.flush()
            os.fsync(f.fileno())
        self.master_lsn = lsn

    # ------------------------------------------------------------------
    # Log buffer / flush
    # ------------------------------------------------------------------

    def _emit(self, rec: dict) -> int:
        lsn = self.next_lsn
        self.next_lsn += 1
        rec["lsn"] = lsn
        self.log_buffer.append((lsn, _serialize(rec)))
        if len(self.log_buffer) >= self.log_buffer_size:
            self._flush_upto(lsn)
        return lsn

    def _flush_upto(self, target_lsn: int) -> None:
        """Append every buffered record with lsn <= target to disk, then fsync."""
        if not self.log_buffer:
            return
        wrote = False
        remaining = []
        for lsn, data in self.log_buffer:
            if lsn <= target_lsn:
                self.disk.log_append(data)
                if lsn > self.flushed_lsn:
                    self.flushed_lsn = lsn
                wrote = True
            else:
                remaining.append((lsn, data))
        self.log_buffer = remaining
        if wrote:
            self.disk.log_fsync()

    def flush_log_up_to(self, lsn: int) -> None:
        """WAL #1 hook for the BufferManager: force log >= lsn before a page write."""
        if lsn is None:
            return
        if lsn > self.flushed_lsn:
            self._flush_upto(lsn)

    # ------------------------------------------------------------------
    # Transaction control (called by the QueryProcessor)
    # ------------------------------------------------------------------

    def begin_txn(self) -> int:
        xid = self.next_xid
        self.next_xid += 1
        self.txn_table[xid] = {"status": "active", "last_lsn": None}
        return xid

    def set_current_xid(self, xid) -> None:
        self.current_xid = xid

    def commit_txn(self, xid: int) -> None:
        """WAL #2: commit record durable (fsync) before returning, then end."""
        prev = self.txn_table.get(xid, {}).get("last_lsn")
        commit_lsn = self._emit({"type": T_COMMIT, "xid": xid, "prev_lsn": prev})
        if xid in self.txn_table:
            self.txn_table[xid]["status"] = "committed"
            self.txn_table[xid]["last_lsn"] = commit_lsn
        self.flush_log_up_to(commit_lsn)  # durable + fsync'd
        end_lsn = self._emit({"type": T_END, "xid": xid, "prev_lsn": commit_lsn})
        self.flush_log_up_to(end_lsn)
        self.txn_table.pop(xid, None)
        self._maybe_checkpoint()

    # ------------------------------------------------------------------
    # Update logging (called by the BufferManager on FileIndexManager's behalf)
    # ------------------------------------------------------------------

    def log_update(self, file_id, page_id, offset, before, after) -> int:
        xid = self.current_xid
        prev = self.txn_table.get(xid, {}).get("last_lsn")
        lsn = self._emit({
            "type": T_UPDATE, "xid": xid, "prev_lsn": prev,
            "file_id": file_id, "page_id": page_id, "offset": offset,
            "before": bytes(before), "after": bytes(after),
        })
        ent = self.txn_table.setdefault(xid, {"status": "active", "last_lsn": None})
        ent["status"] = "active"
        ent["last_lsn"] = lsn
        key = (file_id, page_id)
        if key not in self.dpt:
            self.dpt[key] = lsn  # recLSN: first update since the page was clean
        self._maybe_checkpoint()
        return lsn

    def note_clean(self, file_id, page_id) -> None:
        """BufferManager notifies us a page is clean on disk → drop from DPT."""
        self.dpt.pop((file_id, page_id), None)

    # ------------------------------------------------------------------
    # Fuzzy checkpoint
    # ------------------------------------------------------------------

    def _maybe_checkpoint(self) -> None:
        if self.recovering or self.checkpoint_interval <= 0:
            return
        self._op_count += 1
        if self._op_count % self.checkpoint_interval == 0:
            self.checkpoint()

    def checkpoint(self) -> None:
        """Fuzzy checkpoint: snapshot TT+DPT, do NOT flush dirty data pages."""
        begin_lsn = self._emit({"type": T_BEGIN_CHKPT, "xid": -1, "prev_lsn": None})
        tt_snap = {x: dict(v) for x, v in self.txn_table.items()}
        self._emit({
            "type": T_END_CHKPT, "xid": -1, "prev_lsn": None,
            "txn_table": tt_snap, "dpt": dict(self.dpt),
        })
        self._flush_upto(self.next_lsn - 1)
        self._write_master(begin_lsn)

    # ------------------------------------------------------------------
    # Three-phase recovery (run once at startup, before any input)
    # ------------------------------------------------------------------

    def recover(self) -> None:
        records = self._all_records
        if not records:
            return
        by_lsn = {r["lsn"]: r for r in records}

        self.recovering = True
        tt, dpt = self._analysis(records)
        self._redo(records, dpt)
        self._undo(tt, by_lsn)

        # Persist everything we touched, respecting WAL #1 on the data pages.
        self._flush_upto(self.next_lsn - 1)
        if self.buffer is not None:
            self.buffer.flush()
        self.recovering = False

        # Recovery is complete: tables are clean. Checkpoint the fresh state so
        # the next restart has a short log to scan.
        self.txn_table = {}
        self.dpt = {}
        self._all_records = []
        self._op_count = 0
        self.checkpoint()

    def _analysis(self, records):
        """Rebuild TT and DPT; survivors with status 'active' are the losers."""
        start = self.master_lsn if self.master_lsn is not None else 0
        tt: dict = {}
        dpt: dict = {}
        for r in records:
            if r["lsn"] < start:
                continue
            t = r["type"]
            if t == T_END_CHKPT:
                tt = {x: dict(v) for x, v in r.get("txn_table", {}).items()}
                dpt = dict(r.get("dpt", {}))
            elif t == T_BEGIN_CHKPT:
                continue
            elif t == T_UPDATE:
                xid = r["xid"]
                tt[xid] = {"status": "active", "last_lsn": r["lsn"]}
                key = (r["file_id"], r["page_id"])
                if key not in dpt:
                    dpt[key] = r["lsn"]
            elif t == T_COMMIT:
                ent = tt.setdefault(r["xid"], {"status": "active", "last_lsn": None})
                ent["status"] = "committed"
                ent["last_lsn"] = r["lsn"]
            elif t == T_END:
                tt.pop(r["xid"], None)
        return tt, dpt

    def _redo(self, records, dpt):
        """Repeat history: reapply every qualifying update's after-image."""
        if not dpt or self.buffer is None:
            return
        min_rec = min(dpt.values())
        for r in records:
            if r["type"] != T_UPDATE or r["lsn"] < min_rec:
                continue
            key = (r["file_id"], r["page_id"])
            if key not in dpt or r["lsn"] < dpt[key]:
                continue
            self.disk.ensure_page(r["file_id"], r["page_id"])
            cur = self.buffer.get_page_lsn(r["file_id"], r["page_id"])
            if cur >= r["lsn"]:
                continue  # already on the page (pageLSN proves it)
            self.buffer.recovery_apply(
                r["file_id"], r["page_id"], r["offset"], r["after"], r["lsn"]
            )

    def _undo(self, tt, by_lsn):
        """Roll back losers; write end records as each chain is exhausted."""
        losers = {x: v for x, v in tt.items() if v["status"] == "active"}
        to_undo = {v["last_lsn"] for v in losers.values() if v["last_lsn"] is not None}

        while to_undo:
            lsn = max(to_undo)
            to_undo.discard(lsn)
            r = by_lsn.get(lsn)
            if r is None:
                continue
            if r["type"] == T_UPDATE and self.buffer is not None:
                self.disk.ensure_page(r["file_id"], r["page_id"])
                self.buffer.recovery_apply(
                    r["file_id"], r["page_id"], r["offset"], r["before"],
                    r["prev_lsn"] if r["prev_lsn"] is not None else 0,
                )
            prev = r.get("prev_lsn")
            if prev is not None:
                to_undo.add(prev)
            else:
                self._emit({"type": T_END, "xid": r["xid"], "prev_lsn": None})

        # Committed-but-unended txns (committed just before the crash): finish.
        for xid, v in tt.items():
            if v["status"] == "committed":
                self._emit({"type": T_END, "xid": xid, "prev_lsn": v["last_lsn"]})
