"""Slotted page format (unpacked) with per-slot occupancy flags.

Page layout (little-endian), as seen by the FileIndexManager (this is the
BufferManager's offset-8 window; the physical page additionally carries an
8-byte pageLSN ahead of it):

    offset 0  page_id   uint32   self-validation
    offset 4  padding   4 bytes
    offset 8  slot 0    [occupied:1 byte][record:record_size bytes]
    ...       slot 1, slot 2, ...        (stride = 1 + record_size)
    rest of page  padding

Per-slot occupancy (rather than a shared bitmap + record_count in the header)
is deliberate: Write-Ahead Logging records physical before/after byte images,
and Undo of a loser transaction restores its records' before-images. If slot
occupancy lived in one shared header field, undoing one transaction's insert
would rewrite that field to a stale snapshot and silently drop records inserted
by *other* (committed) transactions on the same page. Giving each slot its own
flag byte means every insert/delete touches only that slot's bytes, so physical
Undo never clobbers a concurrent transaction's work.

"Unpacked" means slot positions are fixed: deletion clears the flag (and the
record bytes) but does not compact. Slot i always lives at the same offset.
"""

import struct

HEADER_FMT = "<I4x"  # page_id (u32) + 4 pad bytes
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 8 bytes

_OCCUPIED = 1


def init_page(page, page_id: int) -> None:
    """Initialise a freshly-allocated page in place (flags already zero)."""
    struct.pack_into(HEADER_FMT, page, 0, page_id)


def get_page_id(page) -> int:
    return struct.unpack_from(HEADER_FMT, page, 0)[0]


def slot_offset(slot: int, record_size: int) -> int:
    """Offset of slot `slot`'s occupancy flag (the record starts one byte in)."""
    return HEADER_SIZE + slot * (1 + record_size)


def is_slot_occupied(page, slot: int, record_size: int) -> bool:
    return page[slot_offset(slot, record_size)] == _OCCUPIED


def first_free_slot(page, max_slots: int, record_size: int) -> int:
    """Return the index of the first free slot, or -1 if the page is full."""
    for i in range(max_slots):
        if page[slot_offset(i, record_size)] != _OCCUPIED:
            return i
    return -1


def read_slot(page, slot: int, record_size: int) -> bytes:
    off = slot_offset(slot, record_size) + 1
    return bytes(page[off:off + record_size])


def write_slot(page, slot: int, record_size: int, data: bytes) -> None:
    off = slot_offset(slot, record_size)
    page[off] = _OCCUPIED
    page[off + 1:off + 1 + record_size] = data


def clear_slot(page, slot: int, record_size: int) -> None:
    off = slot_offset(slot, record_size)
    for i in range(0, 1 + record_size):
        page[off + i] = 0
