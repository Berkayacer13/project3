"""Layer 1 — DiskSpaceManager.

The only component that performs actual file I/O. Reads and writes fixed-size
pages to/from binary files on disk. No knowledge of records, indexes, or
queries — only raw pages.
"""

from common import PageResult, WriteResult, AllocResult


class DiskSpaceManager:
    def __init__(self, config: dict):
        self.config = config
        self.page_size: int = config["page_size"]
        self.base_dir: str = config["_base_dir"]

        self.reads: int = 0
        self.writes: int = 0

        # log_write stub — required by spec. Invoked on every write.
        # Signature: (file_id: str, page_id: int, old: bytes, new: bytes) -> None
        self.log_write = lambda file_id, page_id, old, new: None

    # ----- I/O surface (called by BufferManager) -----

    def read_page(self, file_id: str, page_id: int) -> PageResult:
        raise NotImplementedError

    def write_page(self, file_id: str, page_id: int, data: bytes) -> WriteResult:
        raise NotImplementedError

    def allocate_page(self, file_id: str) -> AllocResult:
        raise NotImplementedError

    # ----- File management -----

    def create_file(self, file_id: str) -> bool:
        raise NotImplementedError

    def file_exists(self, file_id: str) -> bool:
        raise NotImplementedError

    # ----- Stats -----

    def get_io_counts(self) -> dict:
        return {"reads": self.reads, "writes": self.writes}

    def reset_counts(self) -> None:
        self.reads = 0
        self.writes = 0

    def set_log_writer(self, fn) -> None:
        self.log_write = fn
