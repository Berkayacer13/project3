"""Layer 3 — FileIndexManager.

Understands records, types (relations), slotted pages, and indexes. Uses the
BufferManager for ALL page access — never calls DiskSpaceManager directly.
"""

from typing import Any, List

from common import OpResult, RecordResult
from buffer_manager import BufferManager


class FileIndexManager:
    def __init__(self, config: dict, buffer: BufferManager):
        self.config = config
        self.buffer = buffer

        self.page_size: int = config["page_size"]
        self.max_records_per_page: int = config["max_records_per_page"]
        self.index_strategy: str = config["index_strategy"]  # heap_scan/hash_index/bplus_tree

        # Cumulative since last stats reset.
        self.records_scanned: int = 0
        self.records_returned: int = 0
        self.index_nodes_visited: int = 0

        # Catalog of registered types — loaded from disk on init (persistence).
        self.catalog: dict = {}

    # ----- DDL -----

    def create_type(
        self,
        type_name: str,
        fields: List[tuple],  # [(name, type_str), ...]
        pk_index: int,        # 0-indexed internally
    ) -> OpResult:
        raise NotImplementedError

    # ----- DML -----

    def insert_record(self, type_name: str, values: List[Any]) -> OpResult:
        raise NotImplementedError

    def delete_record(self, type_name: str, pk_value: Any) -> OpResult:
        raise NotImplementedError

    def search_record(self, type_name: str, pk_value: Any) -> RecordResult:
        raise NotImplementedError

    def range_search(
        self, type_name: str, field_name: str, low: int, high: int
    ) -> RecordResult:
        raise NotImplementedError

    # ----- Stats -----

    def get_index_stats(self) -> dict:
        return {
            "strategy": self.index_strategy,
            "nodes_visited": self.index_nodes_visited,
            "records_scanned": self.records_scanned,
            "records_returned": self.records_returned,
        }

    def reset_stats(self) -> None:
        self.records_scanned = 0
        self.records_returned = 0
        self.index_nodes_visited = 0
