"""Layer 1 — DiskSpaceManager.

The only component that performs actual file I/O. Reads and writes fixed-size
pages to/from binary files on disk. No knowledge of records, indexes, or
queries — only raw pages.

File layout per relation:
    page 0      DSM-internal header (page_count, free_list_head)
    page 1..N   data pages, accessed via BufferManager

Header layout (little-endian, 8 bytes used, rest of page zero-padded):
    offset 0: page_count    uint32   total pages including this header
    offset 4: free_list_head uint32  page id of first free page, 0 if none

Headers are cached in memory (loaded lazily on first touch) and rewritten on
every change. Cache loads count as one disk read; flushes count as one disk
write — every I/O is counted per spec §4.1.
"""

import os
import struct

from common import AllocResult, PageResult, WriteResult


_HEADER_FMT = "<II"  # page_count, free_list_head
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


class DiskSpaceManager:
    def __init__(self, config: dict):
        self.config = config
        self.page_size: int = config["page_size"]
        self.base_dir: str = config["_base_dir"]

        self.reads: int = 0
        self.writes: int = 0

        # Required by spec §4.1: log_write stub invoked on every write.
        # Signature: (file_id: str, page_id: int, old: bytes, new: bytes) -> None
        self.log_write = lambda file_id, page_id, old, new: None

        # In-memory cache of file headers. Loaded lazily.
        self._headers: dict = {}

    # ---------- path helpers ----------

    def _path(self, file_id: str) -> str:
        return os.path.join(self.base_dir, file_id)

    # ---------- file management ----------

    def file_exists(self, file_id: str) -> bool:
        return os.path.exists(self._path(file_id))

    def create_file(self, file_id: str) -> bool:
        """Create a new relation/index file with an initialized header page.

        Returns False if the file already exists.
        """
        path = self._path(file_id)
        if os.path.exists(path):
            return False
        header_page = bytearray(self.page_size)
        struct.pack_into(_HEADER_FMT, header_page, 0, 1, 0)  # page_count=1, free_head=0
        with open(path, "wb") as f:
            f.write(header_page)
        self.writes += 1
        self.log_write(file_id, 0, b"", bytes(header_page))
        self._headers[file_id] = [1, 0]
        return True

    def list_files(self) -> list:
        """Return all *.dat / *.idx / catalog.dat files next to archive.py."""
        out = []
        for name in os.listdir(self.base_dir):
            if name.endswith(".dat") or name.endswith(".idx"):
                out.append(name)
        return sorted(out)

    # ---------- header cache ----------

    def _load_header(self, file_id: str) -> list:
        if file_id in self._headers:
            return self._headers[file_id]
        path = self._path(file_id)
        with open(path, "rb") as f:
            buf = f.read(self.page_size)
        self.reads += 1
        if len(buf) < _HEADER_SIZE:
            raise IOError(f"file {file_id} too small to contain a header")
        page_count, free_head = struct.unpack_from(_HEADER_FMT, buf, 0)
        self._headers[file_id] = [page_count, free_head]
        return self._headers[file_id]

    def _flush_header(self, file_id: str) -> None:
        header = self._headers[file_id]
        page_bytes = bytearray(self.page_size)
        struct.pack_into(_HEADER_FMT, page_bytes, 0, header[0], header[1])
        with open(self._path(file_id), "r+b") as f:
            f.seek(0)
            f.write(page_bytes)
        self.writes += 1
        self.log_write(file_id, 0, b"", bytes(page_bytes))

    def get_page_count(self, file_id: str) -> int:
        """Total pages allocated in the file, including the header page."""
        return self._load_header(file_id)[0]

    # ---------- page I/O (called by BufferManager) ----------

    def read_page(self, file_id: str, page_id: int) -> PageResult:
        if page_id <= 0:
            return PageResult(
                data=b"",
                page_id=page_id,
                file_id=file_id,
                io_performed=False,
                status="failure",
                message="page 0 is the DSM-internal header, not directly readable",
            )
        path = self._path(file_id)
        if not os.path.exists(path):
            return PageResult(
                data=b"",
                page_id=page_id,
                file_id=file_id,
                io_performed=False,
                status="failure",
                message=f"file {file_id} does not exist",
            )
        with open(path, "rb") as f:
            f.seek(page_id * self.page_size)
            data = f.read(self.page_size)
        if len(data) != self.page_size:
            return PageResult(
                data=b"",
                page_id=page_id,
                file_id=file_id,
                io_performed=False,
                status="failure",
                message=f"page {page_id} not present in {file_id}",
            )
        self.reads += 1
        return PageResult(
            data=data,
            page_id=page_id,
            file_id=file_id,
            io_performed=True,
        )

    def write_page(self, file_id: str, page_id: int, data: bytes) -> WriteResult:
        if page_id <= 0:
            return WriteResult(
                success=False,
                page_id=page_id,
                file_id=file_id,
                old_data=b"",
                new_data=b"",
                message="page 0 is reserved for the DSM header",
            )
        if len(data) != self.page_size:
            return WriteResult(
                success=False,
                page_id=page_id,
                file_id=file_id,
                old_data=b"",
                new_data=bytes(data),
                message=f"data length {len(data)} != page_size {self.page_size}",
            )
        path = self._path(file_id)
        if not os.path.exists(path):
            return WriteResult(
                success=False,
                page_id=page_id,
                file_id=file_id,
                old_data=b"",
                new_data=bytes(data),
                message=f"file {file_id} does not exist",
            )
        with open(path, "r+b") as f:
            f.seek(page_id * self.page_size)
            f.write(data)
        self.writes += 1
        # old_data is intentionally empty: reading-before-writing would double
        # the I/O count for every write. Reserved for opt-in WAL.
        self.log_write(file_id, page_id, b"", bytes(data))
        return WriteResult(
            success=True,
            page_id=page_id,
            file_id=file_id,
            old_data=b"",
            new_data=bytes(data),
        )

    def allocate_page(self, file_id: str) -> AllocResult:
        if not self.file_exists(file_id):
            return AllocResult(
                success=False,
                page_id=-1,
                file_id=file_id,
                message=f"file {file_id} does not exist",
            )
        header = self._load_header(file_id)
        new_page_id = header[0]  # page_count is also the next free id
        zeroed = bytes(self.page_size)
        with open(self._path(file_id), "r+b") as f:
            f.seek(new_page_id * self.page_size)
            f.write(zeroed)
        self.writes += 1
        self.log_write(file_id, new_page_id, b"", zeroed)
        header[0] += 1
        self._flush_header(file_id)
        return AllocResult(success=True, page_id=new_page_id, file_id=file_id)

    # ---------- stats ----------

    def get_io_counts(self) -> dict:
        return {"reads": self.reads, "writes": self.writes}

    def reset_counts(self) -> None:
        self.reads = 0
        self.writes = 0

    def set_log_writer(self, fn) -> None:
        self.log_write = fn
