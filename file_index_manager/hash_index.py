"""Static hash index on the primary key.

Layout of <type>.idx:
    page 0          DSM-internal header (managed by DiskSpaceManager)
    pages 1..N      one primary bucket page per bucket (N = NUM_BUCKETS)
    pages N+1..     overflow pages, allocated on demand and chained from a
                    primary bucket via the `next_overflow_pid` field

Bucket page format (little-endian):
    offset 0   next_overflow_pid (u32)   0 if none
    offset 4   entry_count       (u16)
    offset 6   pad               (2 bytes)
    offset 8.. entries           (entry_count entries)

Each entry is the PK serialized (4 bytes for int, 32 bytes null-padded ASCII
for str) followed by (data_page_id u32, data_slot u16) for 6 trailing bytes.

Stable hashing matters: Python's built-in ``hash()`` is salted across runs,
which would invalidate a persisted index. We use FNV-1a for strings and
``int(key) % N`` for ints. (See pitfall #1 in IMPLEMENTATION_GUIDE.md.)

Range search is not supported — the FileIndexManager falls back to a heap
scan when the active strategy is hash_index and a range_search is requested.
"""

import struct


NUM_BUCKETS = 16  # fixed at type creation; static hashing per spec §4.3

# Bucket page header: next_overflow_pid (u32), entry_count (u16), 2-byte pad.
BUCKET_HEADER_FMT = "<IH2x"
BUCKET_HEADER_SIZE = 8

INT_KEY_FMT = "<i"
INT_KEY_SIZE = 4
STR_KEY_SIZE = 32

# Entry tail (after the key): data_pid (u32), data_slot (u16).
ENTRY_TAIL_FMT = "<IH"
ENTRY_TAIL_SIZE = 6


def fnv1a(s: str, n: int) -> int:
    """FNV-1a 32-bit, mod n. Stable across Python runs."""
    h = 0x811c9dc5
    for byte in s.encode("ascii"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h % n


class HashIndex:
    """Static hash index. One per indexed type; cached on FileIndexManager."""

    def __init__(self, meta, buffer, page_size, on_visit=None,
                 num_buckets: int = NUM_BUCKETS):
        self.meta = meta
        self.buffer = buffer
        self.page_size = page_size
        self.num_buckets = num_buckets
        self.key_type = meta.fields[meta.pk_index][1]  # "int" or "str"
        self.key_size = INT_KEY_SIZE if self.key_type == "int" else STR_KEY_SIZE
        self.entry_size = self.key_size + ENTRY_TAIL_SIZE
        self.capacity_per_page = (
            self.page_size - BUCKET_HEADER_SIZE
        ) // self.entry_size
        # Called once per bucket-page fetch — FIM uses it to bump its
        # global `index_nodes_visited` counter for stats / explain.
        self._on_visit = on_visit or (lambda: None)

    @property
    def file_id(self) -> str:
        return self.meta.index_file_id

    # ---------- key encoding / hashing ----------

    def _bucket_for(self, key) -> int:
        if self.key_type == "int":
            # Negative ints wrap around; result is always in [0, N).
            return int(key) % self.num_buckets
        return fnv1a(str(key), self.num_buckets)

    def _key_bytes(self, key) -> bytes:
        if self.key_type == "int":
            return struct.pack(INT_KEY_FMT, int(key))
        s = str(key).encode("ascii")[:STR_KEY_SIZE]
        return s + b"\x00" * (STR_KEY_SIZE - len(s))

    def _read_key(self, buf, off) -> bytes:
        return bytes(buf[off:off + self.key_size])

    # ---------- file initialization ----------

    def create(self) -> None:
        """Allocate N empty bucket pages. Called once at type creation."""
        if not self.buffer.file_exists(self.file_id):
            self.buffer.create_file(self.file_id)
        for _ in range(self.num_buckets):
            alloc = self.buffer.allocate_page(self.file_id)
            # New pages come back zeroed; headers (next=0, count=0) are correct.
            self.buffer.mark_dirty(self.file_id, alloc.page_id)

    # ---------- core operations ----------

    def _bucket_pid(self, bucket: int) -> int:
        return bucket + 1  # bucket i lives on page i+1 (page 0 is DSM header)

    def _fetch(self, pid):
        self._on_visit()
        return self.buffer.get_page(self.file_id, pid)

    def lookup(self, key):
        """Return (data_pid, data_slot) for key, or None if absent."""
        pid = self._bucket_pid(self._bucket_for(key))
        target = self._key_bytes(key)
        while pid:
            res = self._fetch(pid)
            next_pid, count = struct.unpack_from(BUCKET_HEADER_FMT, res.data, 0)
            for i in range(count):
                off = BUCKET_HEADER_SIZE + i * self.entry_size
                if self._read_key(res.data, off) == target:
                    return struct.unpack_from(
                        ENTRY_TAIL_FMT, res.data, off + self.key_size
                    )
            pid = next_pid
        return None

    def insert(self, key, data_pid: int, data_slot: int) -> None:
        """Append a (key, location) entry, allocating an overflow page if full."""
        pid = self._bucket_pid(self._bucket_for(key))
        while True:
            res = self._fetch(pid)
            next_pid, count = struct.unpack_from(BUCKET_HEADER_FMT, res.data, 0)
            if count < self.capacity_per_page:
                off = BUCKET_HEADER_SIZE + count * self.entry_size
                res.data[off:off + self.key_size] = self._key_bytes(key)
                struct.pack_into(
                    ENTRY_TAIL_FMT, res.data, off + self.key_size,
                    data_pid, data_slot,
                )
                struct.pack_into(
                    BUCKET_HEADER_FMT, res.data, 0, next_pid, count + 1
                )
                self.buffer.mark_dirty(self.file_id, pid)
                return
            if next_pid == 0:
                # Chain a new overflow page off the current one.
                alloc = self.buffer.allocate_page(self.file_id)
                struct.pack_into(
                    BUCKET_HEADER_FMT, res.data, 0, alloc.page_id, count
                )
                self.buffer.mark_dirty(self.file_id, pid)
                # alloc.data is zeroed already; header (next=0, count=0) correct.
                self.buffer.mark_dirty(self.file_id, alloc.page_id)
                pid = alloc.page_id
            else:
                pid = next_pid

    def delete(self, key) -> bool:
        """Remove the entry for key. Returns True on success."""
        pid = self._bucket_pid(self._bucket_for(key))
        target = self._key_bytes(key)
        while pid:
            res = self._fetch(pid)
            next_pid, count = struct.unpack_from(BUCKET_HEADER_FMT, res.data, 0)
            for i in range(count):
                off = BUCKET_HEADER_SIZE + i * self.entry_size
                if self._read_key(res.data, off) != target:
                    continue
                # Swap-with-last, then zero the tail and decrement.
                last_off = BUCKET_HEADER_SIZE + (count - 1) * self.entry_size
                if last_off != off:
                    res.data[off:off + self.entry_size] = bytes(
                        res.data[last_off:last_off + self.entry_size]
                    )
                for j in range(last_off, last_off + self.entry_size):
                    res.data[j] = 0
                struct.pack_into(
                    BUCKET_HEADER_FMT, res.data, 0, next_pid, count - 1
                )
                self.buffer.mark_dirty(self.file_id, pid)
                return True
            pid = next_pid
        return False

    def supports_range(self) -> bool:
        return False
