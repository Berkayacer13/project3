"""Layer 2 — BufferManager (Project 4: WAL-aware).

In-memory page cache between FileIndexManager and DiskSpaceManager.

Pool layout: an OrderedDict keyed by (file_id, page_id) with the most-
recently-used frame at the end. Insertions and accesses move-to-end. Eviction
picks `next(iter)` for LRU or `next(reversed)` for MRU — O(1) for both.

WAL integration
---------------
The first 8 bytes of every 4096-byte page are reserved for a BufferManager-owned
**pageLSN**; callers receive `memoryview(frame.data)[8:]`, so the data/index page
formats keep using offset 0 for *their* own headers, untouched. Because every
page mutation flows through `mark_dirty`, this is the single choke point where we:

  * diff the page body against its last-logged image,
  * call `recovery.log_update(...)` for the changed byte range (WAL — log before
    the change can ever reach disk),
  * stamp the returned LSN into the pageLSN field.

Before writing any dirty page to disk (eviction or explicit flush) we honour
WAL #1 by calling `recovery.flush_log_up_to(frame.page_lsn)`, and we notify the
RecoveryManager (`note_clean`) so the Dirty Page Table can drop the page.

Counters (spec §4.2): requests, hits, misses, evictions, dirty_writebacks.
"""

from collections import OrderedDict
from dataclasses import dataclass

from common import BufferResult
from disk_space_manager import DiskSpaceManager

PAGE_LSN_SIZE = 8  # bytes [0:8] of every page hold the pageLSN (little-endian u64)


@dataclass
class _Frame:
    file_id: str
    page_id: int
    data: bytearray            # full physical page (pageLSN in [0:8])
    dirty: bool = False
    page_lsn: int = 0          # mirror of data[0:8]
    logged_image: bytes = b""  # page state as of the last logged update


class BufferManager:
    def __init__(self, config: dict, disk: DiskSpaceManager):
        self.config = config
        self.disk = disk
        self.page_size: int = config["page_size"]

        self.pool_size: int = config["buffer_pool_size"]
        self.policy: str = config["replacement_policy"]
        if self.policy not in ("LRU", "MRU"):
            raise ValueError(f"unsupported replacement_policy: {self.policy}")

        # Counters
        self.requests: int = 0
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0
        self.dirty_writebacks: int = 0

        # Set by archive.py after the RecoveryManager exists.
        self.recovery = None

        # MRU end is the rightmost item.
        self._frames: "OrderedDict[tuple, _Frame]" = OrderedDict()

    def set_recovery(self, recovery) -> None:
        self.recovery = recovery

    # ---------- pageLSN helpers ----------

    @staticmethod
    def _read_lsn(data: bytearray) -> int:
        return int.from_bytes(bytes(data[:PAGE_LSN_SIZE]), "little")

    @staticmethod
    def _write_lsn(data: bytearray, lsn: int) -> None:
        data[:PAGE_LSN_SIZE] = int(lsn).to_bytes(PAGE_LSN_SIZE, "little")

    @staticmethod
    def _window(frame: "_Frame"):
        """The caller-visible page: physical bytes [8:], a mutable view."""
        return memoryview(frame.data)[PAGE_LSN_SIZE:]

    # ---------- internal helpers ----------

    def _touch(self, key) -> None:
        self._frames.move_to_end(key, last=True)

    def _pick_victim_key(self):
        if self.policy == "LRU":
            return next(iter(self._frames))
        return next(reversed(self._frames))  # MRU

    def _write_back(self, frame: "_Frame") -> None:
        """Persist one dirty frame, enforcing WAL #1 and updating the DPT."""
        if self.recovery is not None:
            self.recovery.flush_log_up_to(frame.page_lsn)
        self.disk.write_page(frame.file_id, frame.page_id, bytes(frame.data))
        frame.dirty = False
        if self.recovery is not None:
            self.recovery.note_clean(frame.file_id, frame.page_id)

    def _evict_if_full(self):
        """Evict one frame if the pool is at capacity.

        Returns (evicted_page_id, evicted_file_id, dirty_writeback_happened).
        """
        if len(self._frames) < self.pool_size:
            return None, None, False
        victim_key = self._pick_victim_key()
        victim = self._frames.pop(victim_key)
        self.evictions += 1
        wb = False
        if victim.dirty:
            self.dirty_writebacks += 1
            self._write_back(victim)
            wb = True
        return victim.page_id, victim.file_id, wb

    # ---------- public surface (called by FileIndexManager) ----------

    def get_page(self, file_id: str, page_id: int) -> BufferResult:
        self.requests += 1
        key = (file_id, page_id)
        if key in self._frames:
            self.hits += 1
            self._touch(key)
            frame = self._frames[key]
            return BufferResult(
                data=self._window(frame),
                page_id=page_id,
                file_id=file_id,
                cache_hit=True,
                evicted_page_id=None,
                evicted_file_id=None,
                dirty_writeback=False,
                io_performed=False,
            )

        self.misses += 1
        evicted_pid, evicted_fid, wb = self._evict_if_full()
        page_result = self.disk.read_page(file_id, page_id)
        if page_result.status != "success":
            return BufferResult(
                data=bytearray(),
                page_id=page_id,
                file_id=file_id,
                cache_hit=False,
                evicted_page_id=evicted_pid,
                evicted_file_id=evicted_fid,
                dirty_writeback=wb,
                io_performed=False,
                status="failure",
            )
        data = bytearray(page_result.data)
        frame = _Frame(
            file_id=file_id,
            page_id=page_id,
            data=data,
            dirty=False,
            page_lsn=self._read_lsn(data),
            logged_image=bytes(data),
        )
        self._frames[key] = frame  # inserted at end → MRU
        return BufferResult(
            data=self._window(frame),
            page_id=page_id,
            file_id=file_id,
            cache_hit=False,
            evicted_page_id=evicted_pid,
            evicted_file_id=evicted_fid,
            dirty_writeback=wb,
            io_performed=True,
        )

    def mark_dirty(self, file_id: str, page_id: int, log: bool = True) -> None:
        key = (file_id, page_id)
        if key not in self._frames:
            raise KeyError(f"mark_dirty: page ({file_id}, {page_id}) not in pool")
        frame = self._frames[key]
        frame.dirty = True
        if not log:
            return
        rm = self.recovery
        if rm is None or rm.recovering or rm.current_xid is None:
            return
        # Diff the page body [8:] against the last-logged image.
        n = self.page_size
        a = frame.logged_image
        b = frame.data
        if a[PAGE_LSN_SIZE:n] == b[PAGE_LSN_SIZE:n]:
            return  # nothing changed in the body
        i = PAGE_LSN_SIZE
        while a[i] == b[i]:
            i += 1
        j = n - 1
        while a[j] == b[j]:
            j -= 1
        before = bytes(a[i:j + 1])
        after = bytes(b[i:j + 1])
        lsn = rm.log_update(file_id, page_id, i, before, after)
        self._write_lsn(frame.data, lsn)
        frame.page_lsn = lsn
        frame.logged_image = bytes(frame.data)

    def allocate_page(self, file_id: str) -> BufferResult:
        alloc = self.disk.allocate_page(file_id)
        if not alloc.success:
            return BufferResult(
                data=bytearray(),
                page_id=alloc.page_id,
                file_id=file_id,
                cache_hit=False,
                evicted_page_id=None,
                evicted_file_id=None,
                dirty_writeback=False,
                io_performed=False,
                status="failure",
            )
        self.requests += 1
        self.misses += 1
        evicted_pid, evicted_fid, wb = self._evict_if_full()
        data = bytearray(self.disk.page_size)
        frame = _Frame(
            file_id=file_id,
            page_id=alloc.page_id,
            data=data,
            dirty=False,  # disk already holds zeroes; matches buffer
            page_lsn=0,
            logged_image=bytes(data),
        )
        self._frames[(file_id, alloc.page_id)] = frame
        return BufferResult(
            data=self._window(frame),
            page_id=alloc.page_id,
            file_id=file_id,
            cache_hit=False,
            evicted_page_id=evicted_pid,
            evicted_file_id=evicted_fid,
            dirty_writeback=wb,
            io_performed=False,
        )

    # ---------- recovery support (called by RecoveryManager) ----------

    def get_page_lsn(self, file_id: str, page_id: int) -> int:
        """Load the page if needed and return its on-disk pageLSN (-1 if absent)."""
        res = self.get_page(file_id, page_id)
        if res.status != "success":
            return -1
        return self._frames[(file_id, page_id)].page_lsn

    def recovery_apply(self, file_id, page_id, offset, image, new_lsn) -> None:
        """Apply a redo after-image / undo before-image and set the pageLSN.

        `offset` is a physical page offset (>= 8). No new log record is written
        (this is replay, not a fresh update)."""
        res = self.get_page(file_id, page_id)
        if res.status != "success":
            return
        frame = self._frames[(file_id, page_id)]
        frame.data[offset:offset + len(image)] = image
        self._write_lsn(frame.data, new_lsn)
        frame.page_lsn = new_lsn
        frame.logged_image = bytes(frame.data)
        frame.dirty = True

    # ---------- passthrough to disk so L3 never touches it ----------

    def create_file(self, file_id: str) -> bool:
        return self.disk.create_file(file_id)

    def file_exists(self, file_id: str) -> bool:
        return self.disk.file_exists(file_id)

    def get_page_count(self, file_id: str) -> int:
        return self.disk.get_page_count(file_id)

    def drop_file(self, file_id: str) -> None:
        """Discard all cached frames for a file WITHOUT writing them back.

        Used when rebuilding an index file from scratch at recovery time."""
        for key in [k for k in self._frames if k[0] == file_id]:
            del self._frames[key]

    def delete_file(self, file_id: str) -> None:
        """Drop cached frames and remove the file from disk."""
        self.drop_file(file_id)
        self.disk.delete_file(file_id)

    # ---------- flush ----------

    def flush(self) -> None:
        """Write back every dirty frame (WAL #1 respected). Frames remain pooled."""
        for frame in self._frames.values():
            if frame.dirty:
                self._write_back(frame)

    # ---------- stats ----------

    def get_stats(self) -> dict:
        return {
            "requests": self.requests,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "dirty_writebacks": self.dirty_writebacks,
        }

    def reset_stats(self) -> None:
        self.requests = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.dirty_writebacks = 0
