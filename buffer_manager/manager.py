"""Layer 2 — BufferManager.

In-memory page cache between FileIndexManager and DiskSpaceManager. Tracks
hits, misses, evictions, and dirty writebacks. Selects LRU or MRU eviction
based on config["replacement_policy"].
"""

from common import BufferResult
from disk_space_manager import DiskSpaceManager


class BufferManager:
    def __init__(self, config: dict, disk: DiskSpaceManager):
        self.config = config
        self.disk = disk

        self.pool_size: int = config["buffer_pool_size"]
        self.policy: str = config["replacement_policy"]  # "LRU" or "MRU"

        # Counters (spec §4.2)
        self.requests: int = 0
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0
        self.dirty_writebacks: int = 0

    # ----- Public surface (called by FileIndexManager) -----

    def get_page(self, file_id: str, page_id: int) -> BufferResult:
        raise NotImplementedError

    def mark_dirty(self, file_id: str, page_id: int) -> None:
        raise NotImplementedError

    def allocate_page(self, file_id: str) -> BufferResult:
        """Allocate a new page (delegates to disk) and pin it in the pool."""
        raise NotImplementedError

    def flush(self) -> None:
        """Write back every dirty frame. Called at the end of archive.py."""
        raise NotImplementedError

    # ----- Stats -----

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
