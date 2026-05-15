"""B+-tree index on the primary key.

One node per page, fetched via the BufferManager. Layout:

    page 0     DSM-internal header
    page 1     metadata page: root_pid (u32) at offset 0
    page 2+    node pages (internal + leaf)

Node header (16 bytes, little-endian):
    offset 0   node_type (u8)        0 = internal, 1 = leaf
    offset 1   pad (3 bytes)
    offset 4   num_keys (u32)
    offset 8   parent_pid (u32)      0 if root
    offset 12  next_pid (u32)
                 - internal: leftmost_child page id
                 - leaf:     next-leaf page id, 0 if last

Entries (after the header):
    Internal nodes: ``num_keys`` × (key, right_child_pid)
                    where right_child_pid is the subtree containing keys >= key.
                    The leftmost child for the smallest keys lives in
                    ``next_pid`` (header position).
    Leaf nodes:     ``num_keys`` × (key, data_pid, data_slot)

Delete is simplified: we shift entries left and decrement num_keys but do not
merge/redistribute underflowing nodes. The spec does not require strict
balance, and lookups still return correct answers (an under-full leaf is just
"sparser") so this is acceptable. Future inserts to that leaf refill it.
"""

import struct


NODE_HEADER_FMT = "<B3xIII"  # type u8, 3-byte pad, num_keys u32, parent u32, next u32
NODE_HEADER_SIZE = 16

INTERNAL_TYPE = 0
LEAF_TYPE = 1

INT_KEY_FMT = "<i"
INT_KEY_SIZE = 4
STR_KEY_SIZE = 32

LEAF_TAIL_FMT = "<IH"      # data_pid u32, slot u16
LEAF_TAIL_SIZE = 6
INTERNAL_TAIL_FMT = "<I"   # right_child_pid u32
INTERNAL_TAIL_SIZE = 4

META_PAGE_ID = 1
META_FMT = "<I"


class BPlusTree:
    """Persistent B+-tree on the primary key of a single type."""

    def __init__(self, meta, buffer, page_size, on_visit=None):
        self.meta = meta
        self.buffer = buffer
        self.page_size = page_size
        self.key_type = meta.fields[meta.pk_index][1]
        self.key_size = INT_KEY_SIZE if self.key_type == "int" else STR_KEY_SIZE
        self.leaf_entry_size = self.key_size + LEAF_TAIL_SIZE
        self.internal_entry_size = self.key_size + INTERNAL_TAIL_SIZE
        # Reserve one extra slot for the shift-during-insert pattern: the
        # insert loop writes the new entry into slot `num_keys` BEFORE the
        # split is triggered. Capacity = ⌊(page-header)/entry⌋; max valid
        # in-page index is `capacity - 1`, so we split when num_keys reaches
        # `capacity - 1` (= leaf_max here).
        self.leaf_max = (self.page_size - NODE_HEADER_SIZE) // self.leaf_entry_size - 1
        self.internal_max = (
            self.page_size - NODE_HEADER_SIZE
        ) // self.internal_entry_size - 1
        self._on_visit = on_visit or (lambda: None)

    @property
    def file_id(self) -> str:
        return self.meta.index_file_id

    # ---------- key encoding ----------

    def _key_bytes(self, key) -> bytes:
        if self.key_type == "int":
            return struct.pack(INT_KEY_FMT, int(key))
        s = str(key).encode("ascii")[:STR_KEY_SIZE]
        return s + b"\x00" * (STR_KEY_SIZE - len(s))

    def _decode_key(self, raw: bytes):
        if self.key_type == "int":
            return struct.unpack(INT_KEY_FMT, raw)[0]
        return raw.rstrip(b"\x00").decode("ascii")

    # ---------- low-level page helpers ----------

    def _fetch(self, pid):
        self._on_visit()
        return self.buffer.get_page(self.file_id, pid)

    def _read_header(self, page_data):
        """Returns (node_type, num_keys, parent_pid, next_pid)."""
        return struct.unpack_from(NODE_HEADER_FMT, page_data, 0)

    def _write_header(self, page_data, node_type, num_keys, parent_pid, next_pid):
        struct.pack_into(
            NODE_HEADER_FMT, page_data, 0,
            node_type, num_keys, parent_pid, next_pid,
        )

    def _read_leaf_entry(self, page_data, i):
        off = NODE_HEADER_SIZE + i * self.leaf_entry_size
        key = self._decode_key(bytes(page_data[off:off + self.key_size]))
        pid, slot = struct.unpack_from(
            LEAF_TAIL_FMT, page_data, off + self.key_size
        )
        return key, pid, slot

    def _write_leaf_entry(self, page_data, i, key, pid, slot):
        off = NODE_HEADER_SIZE + i * self.leaf_entry_size
        page_data[off:off + self.key_size] = self._key_bytes(key)
        struct.pack_into(LEAF_TAIL_FMT, page_data, off + self.key_size, pid, slot)

    def _read_internal_entry(self, page_data, i):
        off = NODE_HEADER_SIZE + i * self.internal_entry_size
        key = self._decode_key(bytes(page_data[off:off + self.key_size]))
        child = struct.unpack_from(
            INTERNAL_TAIL_FMT, page_data, off + self.key_size
        )[0]
        return key, child

    def _write_internal_entry(self, page_data, i, key, child_pid):
        off = NODE_HEADER_SIZE + i * self.internal_entry_size
        page_data[off:off + self.key_size] = self._key_bytes(key)
        struct.pack_into(
            INTERNAL_TAIL_FMT, page_data, off + self.key_size, child_pid
        )

    def _zero_range(self, page_data, start, end):
        for b in range(start, end):
            page_data[b] = 0

    # ---------- root pointer ----------

    def _root_pid(self) -> int:
        res = self._fetch(META_PAGE_ID)
        return struct.unpack_from(META_FMT, res.data, 0)[0]

    def _set_root_pid(self, pid: int) -> None:
        res = self._fetch(META_PAGE_ID)
        struct.pack_into(META_FMT, res.data, 0, pid)
        self.buffer.mark_dirty(self.file_id, META_PAGE_ID)

    # ---------- file initialization ----------

    def create(self) -> None:
        """Allocate the meta page + an empty leaf root."""
        if not self.buffer.file_exists(self.file_id):
            self.buffer.create_file(self.file_id)
        meta_alloc = self.buffer.allocate_page(self.file_id)
        root_alloc = self.buffer.allocate_page(self.file_id)
        # Empty leaf root: type=leaf, no keys, no parent, no next.
        self._write_header(root_alloc.data, LEAF_TYPE, 0, 0, 0)
        self.buffer.mark_dirty(self.file_id, root_alloc.page_id)
        struct.pack_into(META_FMT, meta_alloc.data, 0, root_alloc.page_id)
        self.buffer.mark_dirty(self.file_id, meta_alloc.page_id)

    # ---------- traversal ----------

    def _find_leaf(self, key):
        """Return (leaf_pid, leaf_page_data) for the leaf that should contain key."""
        pid = self._root_pid()
        while True:
            res = self._fetch(pid)
            node_type, num_keys, _, leftmost = self._read_header(res.data)
            if node_type == LEAF_TYPE:
                return pid, res.data
            # Internal: descend into the appropriate child.
            next_pid = leftmost
            for i in range(num_keys):
                k_i, c_i = self._read_internal_entry(res.data, i)
                if key < k_i:
                    break
                next_pid = c_i
            pid = next_pid

    # ---------- public ops ----------

    def lookup(self, key):
        _, data = self._find_leaf(key)
        _, num_keys, _, _ = self._read_header(data)
        for i in range(num_keys):
            k, dpid, slot = self._read_leaf_entry(data, i)
            if k == key:
                return dpid, slot
        return None

    def range_search(self, lo, hi):
        """Yield (data_pid, slot) for every leaf entry with lo <= key <= hi."""
        pid, _ = self._find_leaf(lo)
        while pid:
            res = self._fetch(pid)
            _, num_keys, _, next_leaf = self._read_header(res.data)
            for i in range(num_keys):
                k, dpid, slot = self._read_leaf_entry(res.data, i)
                if k < lo:
                    continue
                if k > hi:
                    return
                yield dpid, slot
            pid = next_leaf

    def supports_range(self) -> bool:
        return True

    def insert(self, key, data_pid: int, data_slot: int) -> None:
        leaf_pid, _ = self._find_leaf(key)
        self._insert_into_leaf(leaf_pid, key, data_pid, data_slot)

    def delete(self, key) -> bool:
        leaf_pid, _ = self._find_leaf(key)
        res = self._fetch(leaf_pid)
        _, num_keys, parent_pid, next_pid = self._read_header(res.data)
        for i in range(num_keys):
            k, _, _ = self._read_leaf_entry(res.data, i)
            if k != key:
                continue
            for j in range(i, num_keys - 1):
                kj, pj, sj = self._read_leaf_entry(res.data, j + 1)
                self._write_leaf_entry(res.data, j, kj, pj, sj)
            tail_off = NODE_HEADER_SIZE + (num_keys - 1) * self.leaf_entry_size
            self._zero_range(res.data, tail_off, tail_off + self.leaf_entry_size)
            self._write_header(
                res.data, LEAF_TYPE, num_keys - 1, parent_pid, next_pid
            )
            self.buffer.mark_dirty(self.file_id, leaf_pid)
            return True
        return False

    # ---------- insertion + splits ----------

    def _insert_into_leaf(self, leaf_pid, key, data_pid, data_slot):
        res = self._fetch(leaf_pid)
        _, num_keys, parent_pid, next_pid = self._read_header(res.data)
        i = 0
        while i < num_keys:
            k_i, _, _ = self._read_leaf_entry(res.data, i)
            if key < k_i:
                break
            i += 1
        for j in range(num_keys, i, -1):
            kj, pj, sj = self._read_leaf_entry(res.data, j - 1)
            self._write_leaf_entry(res.data, j, kj, pj, sj)
        self._write_leaf_entry(res.data, i, key, data_pid, data_slot)
        num_keys += 1
        self._write_header(res.data, LEAF_TYPE, num_keys, parent_pid, next_pid)
        self.buffer.mark_dirty(self.file_id, leaf_pid)
        if num_keys > self.leaf_max:
            self._split_leaf(leaf_pid)

    def _split_leaf(self, leaf_pid):
        old = self._fetch(leaf_pid)
        _, num_keys, parent_pid, next_pid = self._read_header(old.data)
        mid = num_keys // 2
        right_count = num_keys - mid

        new_alloc = self.buffer.allocate_page(self.file_id)
        new_pid = new_alloc.page_id
        self._write_header(
            new_alloc.data, LEAF_TYPE, right_count, parent_pid, next_pid
        )
        for j in range(right_count):
            k, p, s = self._read_leaf_entry(old.data, mid + j)
            self._write_leaf_entry(new_alloc.data, j, k, p, s)
        self.buffer.mark_dirty(self.file_id, new_pid)

        # Trim the old leaf to entries [0, mid).
        self._write_header(old.data, LEAF_TYPE, mid, parent_pid, new_pid)
        for j in range(mid, num_keys):
            off = NODE_HEADER_SIZE + j * self.leaf_entry_size
            self._zero_range(old.data, off, off + self.leaf_entry_size)
        self.buffer.mark_dirty(self.file_id, leaf_pid)

        # Promote first key of the new leaf to the parent.
        first_new_key, _, _ = self._read_leaf_entry(new_alloc.data, 0)
        self._insert_into_parent(leaf_pid, parent_pid, first_new_key, new_pid)

    def _insert_into_parent(self, left_pid, parent_pid, key, right_pid):
        if parent_pid == 0:
            # Old root is splitting — make a fresh internal root.
            new_root_alloc = self.buffer.allocate_page(self.file_id)
            new_root_pid = new_root_alloc.page_id
            self._write_header(
                new_root_alloc.data, INTERNAL_TYPE, 1, 0, left_pid
            )
            self._write_internal_entry(new_root_alloc.data, 0, key, right_pid)
            self.buffer.mark_dirty(self.file_id, new_root_pid)
            self._set_parent(left_pid, new_root_pid)
            self._set_parent(right_pid, new_root_pid)
            self._set_root_pid(new_root_pid)
            return

        res = self._fetch(parent_pid)
        _, num_keys, gparent, leftmost = self._read_header(res.data)
        i = 0
        while i < num_keys:
            k_i, _ = self._read_internal_entry(res.data, i)
            if key < k_i:
                break
            i += 1
        for j in range(num_keys, i, -1):
            kj, cj = self._read_internal_entry(res.data, j - 1)
            self._write_internal_entry(res.data, j, kj, cj)
        self._write_internal_entry(res.data, i, key, right_pid)
        num_keys += 1
        self._write_header(res.data, INTERNAL_TYPE, num_keys, gparent, leftmost)
        self.buffer.mark_dirty(self.file_id, parent_pid)

        self._set_parent(right_pid, parent_pid)
        if num_keys > self.internal_max:
            self._split_internal(parent_pid)

    def _split_internal(self, node_pid):
        old = self._fetch(node_pid)
        _, num_keys, parent_pid, leftmost = self._read_header(old.data)
        mid = num_keys // 2  # this entry is promoted up
        promote_key, promote_child = self._read_internal_entry(old.data, mid)

        new_alloc = self.buffer.allocate_page(self.file_id)
        new_pid = new_alloc.page_id
        right_count = num_keys - mid - 1
        self._write_header(
            new_alloc.data, INTERNAL_TYPE, right_count, parent_pid, promote_child
        )
        # `promote_child` becomes the new node's leftmost — re-parent it.
        self._set_parent(promote_child, new_pid)
        for j in range(right_count):
            k, c = self._read_internal_entry(old.data, mid + 1 + j)
            self._write_internal_entry(new_alloc.data, j, k, c)
            self._set_parent(c, new_pid)
        self.buffer.mark_dirty(self.file_id, new_pid)

        # Old node keeps entries [0, mid).
        self._write_header(old.data, INTERNAL_TYPE, mid, parent_pid, leftmost)
        for j in range(mid, num_keys):
            off = NODE_HEADER_SIZE + j * self.internal_entry_size
            self._zero_range(old.data, off, off + self.internal_entry_size)
        self.buffer.mark_dirty(self.file_id, node_pid)

        self._insert_into_parent(node_pid, parent_pid, promote_key, new_pid)

    def _set_parent(self, child_pid: int, parent_pid: int) -> None:
        res = self._fetch(child_pid)
        node_type, num_keys, _, next_pid = self._read_header(res.data)
        self._write_header(res.data, node_type, num_keys, parent_pid, next_pid)
        self.buffer.mark_dirty(self.file_id, child_pid)

    # ---------- introspection (used for explain plan estimate) ----------

    def height(self) -> int:
        """Walk from root down the leftmost spine; returns 1 for a leaf-only tree."""
        pid = self._root_pid()
        h = 1
        while True:
            res = self._fetch(pid)
            node_type, _, _, leftmost = self._read_header(res.data)
            if node_type == LEAF_TYPE:
                return h
            h += 1
            pid = leftmost
