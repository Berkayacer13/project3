"""System catalog — persistent type metadata.

Stored as a pickled dict { type_name: TypeMeta } in `catalog.dat` next to
archive.py. The catalog is a metadata file, not a paged relation, so it is
read/written directly rather than through the buffer.
"""

import os
import pickle
from dataclasses import dataclass, field
from typing import List, Tuple

CATALOG_FILE = "catalog.dat"


@dataclass
class TypeMeta:
    name: str
    fields: List[Tuple[str, str]]
    pk_index: int
    record_size: int
    field_offsets: List[int]
    file_id: str
    index_strategy: str = "heap_scan"
    index_file_id: str = ""

    def field_index(self, field_name: str) -> int:
        for i, (n, _) in enumerate(self.fields):
            if n == field_name:
                return i
        return -1

    def field_type(self, field_name: str) -> str:
        for n, t in self.fields:
            if n == field_name:
                return t
        return ""


class Catalog:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.path = os.path.join(base_dir, CATALOG_FILE)
        self.types: dict = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "rb") as f:
            data = f.read()
        if not data:
            return
        self.types = pickle.loads(data)

    def _save(self) -> None:
        # fsync so a committed `create type` survives a crash. (DDL is not rolled
        # back by recovery, so an uncommitted type may leak — documented.)
        with open(self.path, "wb") as f:
            pickle.dump(self.types, f)
            f.flush()
            os.fsync(f.fileno())

    def has(self, name: str) -> bool:
        return name in self.types

    def get(self, name: str) -> TypeMeta:
        return self.types[name]

    def add(self, meta: TypeMeta) -> None:
        self.types[meta.name] = meta
        self._save()

    def update(self, meta: TypeMeta) -> None:
        self.types[meta.name] = meta
        self._save()

    def all_types(self):
        return list(self.types.values())
