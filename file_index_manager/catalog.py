"""System catalog — persistent type metadata.

Stored as a pickled dict { type_name: TypeMeta } in `catalog.dat` next to
archive.py. The catalog is a metadata file, not a paged relation, so it is
read/written directly rather than through the buffer.

Transactional DDL (spec §1: "uncommitted ones leave no trace"). `create type`
runs inside a transaction, so a type is held in memory the moment it is added
(the rest of the creating transaction must see it) but is only written to
`catalog.dat` once that transaction commits. A type whose transaction never
commits — because of a `crash` or a clean shutdown with the transaction still
open — is therefore absent from disk on the next restart, leaving no trace.
This mirrors how record data is rolled back by WAL Undo; here the catalog is
the durable record and commit is the only thing that makes a type durable.
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
        self.types: dict = {}        # live view: committed + pending-this-run
        self._pending: set = set()   # type names created by not-yet-committed txns
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "rb") as f:
            data = f.read()
        if not data:
            return
        # Only committed types were ever written, so everything we load is
        # durable; nothing starts out pending.
        self.types = pickle.loads(data)

    def _persist(self) -> None:
        """Write only committed (non-pending) types to disk + fsync.

        Pending types stay in memory so the rest of their creating transaction
        can use them, but they never reach catalog.dat — a crash before commit
        therefore leaves no trace of them (spec §1)."""
        durable = {n: m for n, m in self.types.items() if n not in self._pending}
        with open(self.path, "wb") as f:
            pickle.dump(durable, f)
            f.flush()
            os.fsync(f.fileno())

    def has(self, name: str) -> bool:
        return name in self.types

    def get(self, name: str) -> TypeMeta:
        return self.types[name]

    def add(self, meta: TypeMeta) -> None:
        """Register a type in memory under the (open) creating transaction.

        Not durable until commit() is called for that transaction."""
        self.types[meta.name] = meta
        self._pending.add(meta.name)

    def commit(self, names) -> None:
        """The creating transaction committed: its types become durable."""
        for n in names:
            self._pending.discard(n)
        self._persist()

    def all_types(self):
        return list(self.types.values())
