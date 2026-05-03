"""Minimal ELF parser — extract ``DT_NEEDED`` and ``DT_SONAME``.

Pure ``struct``; no ``pyelftools`` dependency. Supports 32-bit and 64-bit,
little- and big-endian. The parser only needs to walk the program headers
to locate ``PT_DYNAMIC``, translate a few virtual addresses back to file
offsets via ``PT_LOAD`` segments, and read null-terminated strings out of
the string table.

Used at pack time to build a Linux dependency graph and at load time (via
the bundle's precomputed order) to know what to ``dlopen`` bottom-up.
"""

from __future__ import annotations

import struct

# ELF header / magic
_ELF_MAGIC = b"\x7fELF"
_ELFCLASS32 = 1
_ELFCLASS64 = 2
_ELFDATA2LSB = 1
_ELFDATA2MSB = 2

# Program header types
_PT_LOAD = 1
_PT_DYNAMIC = 2

# Dynamic tag entries we care about
_DT_NULL = 0
_DT_NEEDED = 1
_DT_STRTAB = 5
_DT_SONAME = 14


class ElfParseError(Exception):
    """Raised when ELF parsing encounters malformed input."""


def parse_needed_and_soname(data: bytes) -> tuple[str | None, list[str]]:
    """Extract ``(soname, [needed])`` from an ELF shared object.

    ``soname`` is the ``DT_SONAME`` string (may be ``None`` for a library
    that was built without one — in which case callers typically fall back
    to the on-disk filename). ``needed`` is the list of ``DT_NEEDED`` entries
    in the order they appear.

    Raises :class:`ElfParseError` on malformed input.
    """
    if len(data) < 52 or data[:4] != _ELF_MAGIC:
        raise ElfParseError("not an ELF file")

    ei_class = data[4]
    ei_data = data[5]
    if ei_class not in (_ELFCLASS32, _ELFCLASS64):
        raise ElfParseError(f"invalid ei_class {ei_class}")
    if ei_data == _ELFDATA2LSB:
        endian = "<"
    elif ei_data == _ELFDATA2MSB:
        endian = ">"
    else:
        raise ElfParseError(f"invalid ei_data {ei_data}")

    is_64 = ei_class == _ELFCLASS64

    # Header layout after e_ident (16 bytes): e_type, e_machine, e_version,
    # e_entry, e_phoff, e_shoff, e_flags, e_ehsize, e_phentsize, e_phnum, ...
    # On 32-bit: entry/phoff/shoff are 4 bytes; on 64-bit 8 bytes.
    if is_64:
        fmt = endian + "HHIQQQIHHHHHH"
    else:
        fmt = endian + "HHIIIIIHHHHHH"
    hdr_size = struct.calcsize(fmt)
    if len(data) < 16 + hdr_size:
        raise ElfParseError("ELF header truncated")
    fields = struct.unpack_from(fmt, data, 16)
    # fields: e_type, e_machine, e_version, e_entry, e_phoff, e_shoff,
    #         e_flags, e_ehsize, e_phentsize, e_phnum, e_shentsize,
    #         e_shnum, e_shstrndx
    _, _, _, _, e_phoff, _, _, _, e_phentsize, e_phnum, *_ = fields

    # Collect PT_LOAD segments (for vaddr → offset translation) and the
    # PT_DYNAMIC segment.
    loads: list[tuple[int, int, int, int]] = []  # (vaddr, offset, filesz, memsz)
    dyn: tuple[int, int] | None = None  # (offset, filesz)

    if is_64:
        ph_fmt = endian + "IIQQQQQQ"  # p_type p_flags p_offset p_vaddr p_paddr p_filesz p_memsz p_align
    else:
        ph_fmt = endian + "IIIIIIII"
    expected_phsize = struct.calcsize(ph_fmt)
    if e_phentsize < expected_phsize:
        raise ElfParseError(
            f"phentsize {e_phentsize} smaller than parsed size {expected_phsize}"
        )
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if off + expected_phsize > len(data):
            raise ElfParseError("program header out of bounds")
        entry = struct.unpack_from(ph_fmt, data, off)
        if is_64:
            p_type, _, p_offset, p_vaddr, _, p_filesz, p_memsz, _ = entry
        else:
            p_type, p_offset, p_vaddr, _, p_filesz, p_memsz, _, _ = entry
        if p_type == _PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz, p_memsz))
        elif p_type == _PT_DYNAMIC:
            dyn = (p_offset, p_filesz)

    if dyn is None:
        # No dynamic section — not a dynamically linked object. Return empty.
        return None, []
    dyn_offset, dyn_size = dyn

    # Walk the dynamic entries.
    if is_64:
        dyn_fmt = endian + "qQ"  # d_tag (signed), d_val/d_ptr
    else:
        dyn_fmt = endian + "ii"
    dyn_entry_size = struct.calcsize(dyn_fmt)
    strtab_vaddr: int | None = None
    needed_strtab_offsets: list[int] = []
    soname_strtab_offset: int | None = None

    cursor = dyn_offset
    end = dyn_offset + dyn_size
    while cursor + dyn_entry_size <= end and cursor + dyn_entry_size <= len(data):
        tag, val = struct.unpack_from(dyn_fmt, data, cursor)
        cursor += dyn_entry_size
        if tag == _DT_NULL:
            break
        if tag == _DT_NEEDED:
            needed_strtab_offsets.append(val)
        elif tag == _DT_SONAME:
            soname_strtab_offset = val
        elif tag == _DT_STRTAB:
            strtab_vaddr = val

    if strtab_vaddr is None:
        raise ElfParseError("no DT_STRTAB found")

    # Translate strtab virtual address to a file offset via the PT_LOAD map.
    strtab_offset = _vaddr_to_offset(loads, strtab_vaddr)
    if strtab_offset is None:
        raise ElfParseError(
            f"DT_STRTAB vaddr 0x{strtab_vaddr:x} not in any PT_LOAD segment"
        )

    def _read_c_string(base: int, offset: int) -> str:
        start = base + offset
        if start >= len(data):
            raise ElfParseError(f"string offset {offset} past EOF")
        end_idx = data.find(b"\x00", start)
        if end_idx == -1:
            end_idx = len(data)
        return data[start:end_idx].decode("utf-8", errors="replace")

    needed = [_read_c_string(strtab_offset, off) for off in needed_strtab_offsets]
    soname = (
        _read_c_string(strtab_offset, soname_strtab_offset)
        if soname_strtab_offset is not None
        else None
    )
    return soname, needed


def _vaddr_to_offset(
    loads: list[tuple[int, int, int, int]],
    vaddr: int,
) -> int | None:
    """Translate a virtual address to a file offset via the ``PT_LOAD`` map.

    ELF stores the string table's location as a virtual address; to read it
    from the raw bytes we need to find which loadable segment it lives in
    and compute ``offset = segment.p_offset + (vaddr - segment.p_vaddr)``.
    """
    for seg_vaddr, seg_offset, seg_filesz, seg_memsz in loads:
        if seg_vaddr <= vaddr < seg_vaddr + seg_memsz:
            delta = vaddr - seg_vaddr
            if delta >= seg_filesz:
                return None
            return seg_offset + delta
    return None
