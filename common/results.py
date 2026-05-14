"""Result objects used for all inter-layer communication.

Every cross-layer call MUST return one of these. Raw bytes / ints crossing a
layer boundary breaks the modular contract described in the spec.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class PageResult:
    """Returned by DiskSpaceManager when a page is read."""
    data: bytes
    page_id: int
    file_id: str
    io_performed: bool
    status: str = "success"
    message: str = ""


@dataclass
class WriteResult:
    """Returned by DiskSpaceManager when a page is written."""
    success: bool
    page_id: int
    file_id: str
    old_data: bytes
    new_data: bytes
    message: str = ""


@dataclass
class AllocResult:
    """Returned by DiskSpaceManager when a new page is allocated."""
    success: bool
    page_id: int
    file_id: str
    message: str = ""


@dataclass
class BufferResult:
    """Returned by BufferManager.get_page.

    `data` is a bytearray reference into the buffer pool — callers may mutate
    it, but must call mark_dirty(file_id, page_id) afterward.
    """
    data: bytearray
    page_id: int
    file_id: str
    cache_hit: bool
    evicted_page_id: Optional[int]
    evicted_file_id: Optional[str]
    dirty_writeback: bool
    io_performed: bool
    status: str = "success"


@dataclass
class RecordResult:
    """Returned by FileIndexManager for search / range operations."""
    records: List[List[Any]] = field(default_factory=list)
    pages_accessed: int = 0
    index_nodes_visited: int = 0
    records_scanned: int = 0
    status: str = "success"
    message: str = ""


@dataclass
class OpResult:
    """Returned by FileIndexManager for create_type / insert / delete."""
    success: bool
    message: str = ""
    pages_touched: int = 0
    index_nodes_visited: int = 0
