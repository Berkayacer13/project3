"""Fixed-length record encoding.

Field byte widths (documented choices for the report):
    int — 4 bytes, signed, little-endian (struct format "<i")
    str — 32 bytes, ASCII, null-padded on the right

A record is the concatenation of its fields in declared order.
"""

import struct

INT_WIDTH = 4
STR_WIDTH = 32

_INT_FMT = "<i"


def field_width(type_str: str) -> int:
    if type_str == "int":
        return INT_WIDTH
    if type_str == "str":
        return STR_WIDTH
    raise ValueError(f"unknown field type: {type_str!r}")


def encode_field(value, type_str: str) -> bytes:
    if type_str == "int":
        return struct.pack(_INT_FMT, int(value))
    if type_str == "str":
        s = str(value).encode("ascii")
        if len(s) > STR_WIDTH:
            raise ValueError(f"string {value!r} exceeds {STR_WIDTH} bytes")
        return s.ljust(STR_WIDTH, b"\x00")
    raise ValueError(f"unknown field type: {type_str!r}")


def decode_field(data: bytes, type_str: str):
    if type_str == "int":
        return struct.unpack(_INT_FMT, data)[0]
    if type_str == "str":
        return data.rstrip(b"\x00").decode("ascii")
    raise ValueError(f"unknown field type: {type_str!r}")


def compute_record_layout(fields):
    """Given [(name, type), ...] return (record_size, field_offsets)."""
    offsets = []
    off = 0
    for _name, type_str in fields:
        offsets.append(off)
        off += field_width(type_str)
    return off, offsets


def encode_record(values, fields) -> bytes:
    if len(values) != len(fields):
        raise ValueError(f"expected {len(fields)} values, got {len(values)}")
    out = bytearray()
    for value, (_name, type_str) in zip(values, fields):
        out.extend(encode_field(value, type_str))
    return bytes(out)


def decode_record(data: bytes, fields) -> list:
    out = []
    off = 0
    for _name, type_str in fields:
        w = field_width(type_str)
        out.append(decode_field(data[off:off + w], type_str))
        off += w
    return out
