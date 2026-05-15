"""Slotted page format (unpacked) with a bitmap.

Page layout (little-endian):
    offset  0  page_id        uint32   self-validation
    offset  4  record_count   uint32   number of occupied slots
    offset  8  slot_bitmap    uint16   bit i = 1 iff slot i is occupied
    offset 10  padding        2 bytes
    offset 12  slot 0         record_size bytes
    offset 12 + record_size  slot 1
    ...                       up to max_records_per_page slots
    rest of page              padding

"Unpacked" means slot positions are fixed: deletion clears the bitmap bit
but does not compact the slots. Slot i always lives at the same offset.
"""

import struct

HEADER_FMT = "<IIH2x"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 12 bytes


def init_page(page: bytearray, page_id: int) -> None:
    """Initialise a freshly-allocated page in place."""
    struct.pack_into(HEADER_FMT, page, 0, page_id, 0, 0)


def get_header(page) -> tuple:
    """Return (page_id, record_count, slot_bitmap)."""
    return struct.unpack_from(HEADER_FMT, page, 0)


def set_header(page: bytearray, page_id: int, record_count: int, bitmap: int) -> None:
    struct.pack_into(HEADER_FMT, page, 0, page_id, record_count, bitmap)


def is_slot_occupied(bitmap: int, slot: int) -> bool:
    return bool(bitmap & (1 << slot))


def set_slot(bitmap: int, slot: int) -> int:
    return bitmap | (1 << slot)


def clear_slot(bitmap: int, slot: int) -> int:
    return bitmap & ~(1 << slot)


def first_free_slot(bitmap: int, max_slots: int) -> int:
    """Return the index of the first free slot, or -1 if full."""
    for i in range(max_slots):
        if not (bitmap & (1 << i)):
            return i
    return -1


def slot_offset(slot: int, record_size: int) -> int:
    return HEADER_SIZE + slot * record_size


def read_slot(page, slot: int, record_size: int) -> bytes:
    off = slot_offset(slot, record_size)
    return bytes(page[off:off + record_size])


def write_slot(page: bytearray, slot: int, record_size: int, data: bytes) -> None:
    off = slot_offset(slot, record_size)
    page[off:off + record_size] = data


def clear_slot_bytes(page: bytearray, slot: int, record_size: int) -> None:
    off = slot_offset(slot, record_size)
    for i in range(record_size):
        page[off + i] = 0
