"""Layer 2 — BufferManager.

In-memory page cache between FileIndexManager and DiskSpaceManager.

Pool layout: an OrderedDict keyed by (file_id, page_id) with the most-
recently-used frame at the end. Insertions and accesses move-to-end. Eviction
picks `next(iter)` for LRU or `next(reversed)` for MRU — O(1) for both.

Counters (spec §4.2): requests, hits, misses, evictions, dirty_writebacks.
- `get_page`: requests += 1; hit/miss recorded.
- `allocate_page`: requests += 1, misses += 1 (alloc is never a cache hit).
- Eviction increments evictions; dirty writeback increments dirty_writebacks
  and goes through `disk.write_page` (which also counts toward disk writes).

Layer 3 must never call DiskSpaceManager directly — `create_file` and
`file_exists` are passed through here.
"""

from collections import OrderedDict
from dataclasses import dataclass

from common import BufferResult
from disk_space_manager import DiskSpaceManager


@dataclass
class _Frame:
    file_id: str
    page_id: int
    data: bytearray
    dirty: bool = False


class BufferManager:
    def __init__(self, config: dict, disk: DiskSpaceManager):
        self.config = config
        self.disk = disk

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

        # MRU end is the rightmost item.
        self._frames: "OrderedDict[tuple, _Frame]" = OrderedDict()

    # ---------- internal helpers ----------

    def _touch(self, key) -> None:
        self._frames.move_to_end(key, last=True)

    def _pick_victim_key(self):
        if self.policy == "LRU":
            return next(iter(self._frames))
        return next(reversed(self._frames))  # MRU

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
            self.disk.write_page(victim.file_id, victim.page_id, bytes(victim.data))
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
                data=frame.data,
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
        frame = _Frame(
            file_id=file_id,
            page_id=page_id,
            data=bytearray(page_result.data),
            dirty=False,
        )
        self._frames[key] = frame  # inserted at end → MRU
        return BufferResult(
            data=frame.data,
            page_id=page_id,
            file_id=file_id,
            cache_hit=False,
            evicted_page_id=evicted_pid,
            evicted_file_id=evicted_fid,
            dirty_writeback=wb,
            io_performed=True,
        )

    def mark_dirty(self, file_id: str, page_id: int) -> None:
        key = (file_id, page_id)
        if key not in self._frames:
            raise KeyError(f"mark_dirty: page ({file_id}, {page_id}) not in pool")
        self._frames[key].dirty = True

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
        zeroed = bytearray(self.disk.page_size)
        frame = _Frame(
            file_id=file_id,
            page_id=alloc.page_id,
            data=zeroed,
            dirty=False,  # disk already holds zeroes; matches buffer
        )
        self._frames[(file_id, alloc.page_id)] = frame
        return BufferResult(
            data=zeroed,
            page_id=alloc.page_id,
            file_id=file_id,
            cache_hit=False,
            evicted_page_id=evicted_pid,
            evicted_file_id=evicted_fid,
            dirty_writeback=wb,
            io_performed=False,
        )

    # ---------- passthrough to disk so L3 never touches it ----------

    def create_file(self, file_id: str) -> bool:
        return self.disk.create_file(file_id)

    def file_exists(self, file_id: str) -> bool:
        return self.disk.file_exists(file_id)

    def get_page_count(self, file_id: str) -> int:
        return self.disk.get_page_count(file_id)

    # ---------- flush ----------

    def flush(self) -> None:
        """Write back every dirty frame. Frames remain in the pool."""
        for frame in self._frames.values():
            if frame.dirty:
                self.disk.write_page(
                    frame.file_id, frame.page_id, bytes(frame.data)
                )
                frame.dirty = False

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
