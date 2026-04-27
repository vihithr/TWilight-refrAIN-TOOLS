#!/usr/bin/env python3
"""
arc_idx_tool.py

For game archives using:
  - idx entry layout: [name(32)][offset(4)][size(4)]
  - name xor key schedule: key[i%5] + 3*i + 113
  - offset xor const: 0xADC3FEA9
  - size xor const:   0xC3FEA9BA

Supports:
  - unpack
  - pack
  - list
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from collections import Counter
from typing import Callable, Iterable, List, Optional, Tuple


KEY = bytes([0xBA, 0xAD, 0xC3, 0xFE, 0xA9])
OFF_XOR = 0xADC3FEA9
SIZE_XOR = 0xC3FEA9BA
NAME_LEN = 32
ENTRY_LEN = 40


@dataclass
class Entry:
    name: str
    offset: int
    size: int


def xor_stream(data: bytes) -> bytes:
    out = bytearray(data)
    for i in range(len(out)):
        out[i] ^= (KEY[i % 5] + 3 * i + 113) & 0xFF
    return bytes(out)


def encode_name_32(name: str) -> bytes:
    raw = name.encode("utf-8", errors="ignore")
    buf = bytearray(NAME_LEN)
    # Match strncpy(..., 0x1F): up to 31 bytes + trailing zero.
    buf[: min(len(raw), NAME_LEN - 1)] = raw[: NAME_LEN - 1]
    return xor_stream(bytes(buf))


def decode_name_32(enc: bytes) -> str:
    dec = xor_stream(enc)
    nul = dec.find(b"\x00")
    if nul != -1:
        dec = dec[:nul]
    return dec.decode("utf-8", errors="ignore")


def parse_idx(idx_data: bytes) -> List[Entry]:
    if len(idx_data) % ENTRY_LEN != 0:
        raise ValueError(f"idx size {len(idx_data)} is not a multiple of {ENTRY_LEN}")
    entries: List[Entry] = []
    p = 0
    while p < len(idx_data):
        name_enc = idx_data[p : p + NAME_LEN]
        off_enc = struct.unpack_from("<I", idx_data, p + NAME_LEN)[0]
        size_enc = struct.unpack_from("<I", idx_data, p + NAME_LEN + 4)[0]
        p += ENTRY_LEN
        entries.append(
            Entry(
                name=decode_name_32(name_enc),
                offset=off_enc ^ OFF_XOR,
                size=size_enc ^ SIZE_XOR,
            )
        )
    return entries


def build_idx(entries: Iterable[Entry]) -> bytes:
    out = bytearray()
    for e in entries:
        out += encode_name_32(e.name)
        out += struct.pack("<I", e.offset ^ OFF_XOR)
        out += struct.pack("<I", e.size ^ SIZE_XOR)
    return bytes(out)


def rle4_compress(raw: bytes) -> bytes:
    """
    Custom 4-byte block RLE:
      [orig_size_be(4)] + repeated groups of [count(1) + block(4)]
    """
    if len(raw) == 0:
        return b"\x00\x00\x00\x00"
    if len(raw) % 4 != 0:
        raise ValueError("rle mode requires file size multiple of 4")

    out = bytearray(struct.pack(">I", len(raw)))
    chunks = [raw[i : i + 4] for i in range(0, len(raw), 4)]
    cur = chunks[0]
    cnt = 1

    for ch in chunks[1:]:
        if ch == cur and cnt < 255:
            cnt += 1
        else:
            out.append(cnt)
            out += cur
            cur = ch
            cnt = 1

    out.append(cnt)
    out += cur
    return bytes(out)


def rle4_decompress(blob: bytes) -> bytes:
    if len(blob) < 4:
        raise ValueError("rle blob too short")
    orig = struct.unpack("<I", blob[:4])[0]
    p = 4
    out = bytearray()

    while p + 5 <= len(blob):
        cnt = blob[p]
        block = blob[p + 1 : p + 5]
        p += 5
        if cnt == 0:
            continue
        out.extend(block * cnt)
        if len(out) >= orig:
            break

    if len(out) < orig:
        raise ValueError(f"rle decode truncated: got {len(out)}, expect {orig}")
    return bytes(out[:orig])


def rle4_decompress_be(blob: bytes) -> bytes:
    if len(blob) < 4:
        raise ValueError("rle blob too short")
    orig = struct.unpack(">I", blob[:4])[0]
    p = 4
    out = bytearray()
    while p + 5 <= len(blob):
        cnt = blob[p]
        block = blob[p + 1 : p + 5]
        p += 5
        if cnt == 0:
            continue
        out.extend(block * cnt)
        if len(out) >= orig:
            break
    if len(out) < orig:
        raise ValueError(f"rle(be) decode truncated: got {len(out)}, expect {orig}")
    return bytes(out[:orig])


def _score_payload(data: bytes) -> float:
    if not data:
        return -1.0
    head = data[:2048]
    printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in head) / len(head)
    bonus = 0.0
    h = head.lstrip()
    if h.startswith((b"<?xml", b"<")):
        bonus += 0.5
    if h.startswith((b"{", b"[", b"PK\x03\x04", b"OggS", b"RIFF", b"DDS ", b"\x89PNG")):
        bonus += 0.3
    nul_ratio = head.count(0) / len(head)
    penalty = 0.25 if nul_ratio > 0.35 else 0.0
    return printable + bonus - penalty


def _build_riff_wav_from_raw_wav(blob: bytes) -> bytes:
    """
    Convert game '.wav.raw' payload to standard RIFF/WAVE:
      first 18 bytes: WAVEFORMATEX
      remaining bytes: pcm/adpcm data
    """
    if len(blob) < 18:
        raise ValueError("raw-wav blob too short (<18)")
    wfex = blob[:18]
    pcm = blob[18:]
    fmt_size = 18
    data_size = len(pcm)
    riff_size = 4 + (8 + fmt_size) + (8 + data_size)
    out = bytearray()
    out += b"RIFF"
    out += struct.pack("<I", riff_size)
    out += b"WAVE"
    out += b"fmt "
    out += struct.pack("<I", fmt_size)
    out += wfex
    out += b"data"
    out += struct.pack("<I", data_size)
    out += pcm
    return bytes(out)


def normalize_output_name(name: str, data: bytes, convert_raw_wav: bool) -> Tuple[str, bytes]:
    if convert_raw_wav and name.lower().endswith(".wav.raw"):
        try:
            wav = _build_riff_wav_from_raw_wav(data)
            return name[:-4], wav  # drop trailing ".raw"
        except Exception:
            return name, data
    return name, data


def _is_script_entry(name: str) -> bool:
    low = name.lower()
    return low.endswith(".xml") or "\\script\\" in low or "/script/" in low


def _write_entry_output(
    out_dir: Path,
    entry_name: str,
    decoded_raw: bytes,
    original_payload: bytes,
    script_output: str,
    convert_raw_wav: bool,
) -> int:
    """
    Returns number of files written.
    script_output:
      - decoded: write decoded XML only
      - raw: write original payload only (.bin sidecar name)
      - both: write decoded XML + raw sidecar (.enc)
    """
    writes = 0
    is_script = _is_script_entry(entry_name)

    if is_script and script_output in ("raw", "both"):
        side = ".bin" if script_output == "raw" else ".enc"
        raw_dst = out_dir / (entry_name.replace("\\", "/") + side)
        raw_dst.parent.mkdir(parents=True, exist_ok=True)
        raw_dst.write_bytes(original_payload)
        writes += 1
        if script_output == "raw":
            return writes

    out_name, out_data = normalize_output_name(entry_name, decoded_raw, convert_raw_wav)
    dst = out_dir / out_name.replace("\\", "/")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out_data)
    writes += 1
    return writes


def probe_wav_format_tag(data: bytes) -> Optional[int]:
    if len(data) < 20:
        return None
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    p = 12
    while p + 8 <= len(data):
        ck = data[p : p + 4]
        sz = struct.unpack_from("<I", data, p + 4)[0]
        p += 8
        if ck == b"fmt ":
            if sz < 2 or p + sz > len(data):
                return None
            return struct.unpack_from("<H", data, p)[0]
        p += sz + (sz & 1)
    return None


class ScriptDecodeError(ValueError):
    pass


def _u32le_words(buf: bytes, count: int) -> List[int]:
    need = count * 4
    if len(buf) < need:
        raise ScriptDecodeError(f"not enough bytes for {count} u32 words")
    return list(struct.unpack("<" + ("I" * count), buf[:need]))


class ScriptBitReader:
    """
    Mirrors sub_458D50/sub_458D30 behavior:
      - MSB-first bit extraction from a 32-bit rolling buffer
      - refill by consuming next 4 little-endian bytes from stream
    """

    def __init__(self, stream: bytes):
        self.stream = stream
        self.p = 0
        self.buf = 0
        self.bits = 0

    def _refill_word(self) -> None:
        if self.p >= len(self.stream):
            # Game-side decoder may read past logical end from a larger buffer.
            # Mirror this behavior with zero padding to avoid hard underflow.
            self.buf = 0
            self.bits = 32
            return
        chunk = self.stream[self.p : self.p + 4]
        if len(chunk) < 4:
            chunk = chunk + (b"\x00" * (4 - len(chunk)))
        self.buf = struct.unpack("<I", chunk)[0]
        self.p += 4
        self.bits = 32

    def get_bits(self, n: int) -> int:
        if n < 0:
            raise ScriptDecodeError("negative bit count")
        if n == 0:
            return 0
        out = 0
        remain = n
        while remain > 0:
            if self.bits == 0:
                self._refill_word()
            take = self.bits if self.bits < remain else remain
            self.bits -= take
            remain -= take
            out |= ((self.buf >> self.bits) & ((1 << take) - 1)) << remain
        return out


class ScriptDecoderState:
    def __init__(self, max_len: int, first_code: List[int], first_sym: List[int], threshold: List[int], code: int):
        self.max_len = max_len
        self.first_code = first_code
        self.first_sym = first_sym
        self.threshold = threshold
        self.code = code


def _build_script_state(len_tbl: List[int], br: ScriptBitReader) -> ScriptDecoderState:
    if len(len_tbl) != 256:
        raise ScriptDecodeError("len_tbl must have 256 words")
    max_len = int(len_tbl[255])
    if max_len <= 0 or max_len > 31:
        raise ScriptDecodeError(f"invalid max code length: {max_len}")

    first_code = [0] * max_len
    first_sym = [0] * (max_len + 1)
    threshold = [0] * max_len

    sym_pos = 0
    for l in range(1, max_len):
        run = 0
        while sym_pos + run < len(len_tbl) and len_tbl[sym_pos + run] == l:
            run += 1
        first_code[l] = 2 * (run + first_code[l - 1])
        first_sym[l - 1] = sym_pos
        threshold[l - 1] = first_code[l] * (1 << (max_len - l - 1))
        sym_pos += run

    threshold[max_len - 1] = 1 << max_len
    first_sym[max_len - 1] = sym_pos
    first_sym[max_len] = 256

    code = br.get_bits(max_len)
    return ScriptDecoderState(max_len, first_code, first_sym, threshold, code)


def _decode_script_symbol(st: ScriptDecoderState, br: ScriptBitReader) -> int:
    i = 0
    while i < st.max_len and not (st.code < st.threshold[i]):
        i += 1
    if i >= st.max_len:
        raise ScriptDecodeError("failed to resolve symbol class")

    shift = st.max_len - i - 1
    prefix = st.code >> shift
    st.code = st.code & ((1 << shift) - 1) if shift > 0 else 0

    sym = prefix + (st.first_sym[i] - st.first_code[i])
    if sym < 0 or sym >= 256:
        raise ScriptDecodeError(f"decoded symbol index out of range: {sym}")

    st.code = (st.code << (i + 1)) + br.get_bits(i + 1)
    return sym


def decode_script_payload(blob: bytes) -> bytes:
    """
    Script second-stage decode (sub_4595A0 chain):
      u32 out_len + 256*u32 len_tbl + 256*u32 sym_tbl + bitstream
    """
    if len(blob) < 4 + 0x400 + 0x400:
        raise ScriptDecodeError("script blob too short")
    out_len = struct.unpack_from("<I", blob, 0)[0]
    if out_len <= 0 or out_len > 16 * 1024 * 1024:
        raise ScriptDecodeError(f"unreasonable script output length: {out_len}")
    len_tbl = _u32le_words(blob[4 : 4 + 0x400], 256)
    sym_tbl = _u32le_words(blob[4 + 0x400 : 4 + 0x800], 256)
    bitstream = blob[4 + 0x800 :]
    br = ScriptBitReader(bitstream)
    st = _build_script_state(len_tbl, br)

    out = bytearray(out_len)
    for i in range(out_len):
        sym_idx = _decode_script_symbol(st, br)
        out[i] = sym_tbl[sym_idx] & 0xFF
    return bytes(out)


def encode_script_payload(raw: bytes) -> bytes:
    """
    Minimal compatible script encoder for testing repack:
      - max_len = 8
      - len_tbl = [8] * 256
      - sym_tbl = identity mapping 0..255
      - bitstream = raw bytes + 1 trailing zero byte

    This matches current decoder logic and allows round-trip tests for script XML.
    """
    def _pack_msb_words(values: bytes) -> bytes:
        # Decoder reads MSB-first from 32-bit words loaded as little-endian.
        # So we write each 4 symbols into one word (b0..b3 -> bits 31..0),
        # then output that word as little-endian bytes.
        out_words = bytearray()
        i = 0
        while i < len(values):
            chunk = values[i : i + 4]
            i += 4
            if len(chunk) < 4:
                chunk = chunk + (b"\x00" * (4 - len(chunk)))
            w = (chunk[0] << 24) | (chunk[1] << 16) | (chunk[2] << 8) | chunk[3]
            out_words += struct.pack("<I", w)
        return bytes(out_words)

    out = bytearray()
    out += struct.pack("<I", len(raw))
    out += struct.pack("<256I", *([8] * 256))
    out += struct.pack("<256I", *list(range(256)))
    out += _pack_msb_words(raw + b"\x00")
    return bytes(out)


def normalize_idx_name(path: Path, base_dir: Path, flat: bool) -> str:
    if flat:
        name = path.name
    else:
        name = str(path.relative_to(base_dir)).replace("/", "\\")
    return name


def _decode_candidates(payload: bytes) -> List[Tuple[str, bytes, float]]:
    candidates: List[Tuple[str, bytes, float]] = []
    attempts: List[Tuple[str, Callable[[bytes], bytes]]] = [
        ("raw", lambda d: d),
        ("xor", xor_stream),
        ("rle-le", rle4_decompress),
        ("rle-be", rle4_decompress_be),
        ("xor-rle", lambda d: rle4_decompress(xor_stream(d))),
        ("script", decode_script_payload),
        ("xor-script", lambda d: decode_script_payload(xor_stream(d))),
    ]
    for name, fn in attempts:
        try:
            dec = fn(payload)
            candidates.append((name, dec, _score_payload(dec)))
        except Exception:
            pass
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates


def decode_payload(payload: bytes, mode: str) -> Tuple[str, bytes]:
    if mode == "raw":
        return "raw", payload
    if mode == "xor":
        return "xor", xor_stream(payload)
    if mode == "rle":
        return "rle-le", rle4_decompress(payload)
    if mode == "rle-le":
        return "rle-le", rle4_decompress(payload)
    if mode == "rle-be":
        return "rle-be", rle4_decompress_be(payload)
    if mode == "xor-rle":
        return "xor-rle", rle4_decompress(xor_stream(payload))
    if mode == "script":
        return "script", decode_script_payload(payload)
    if mode == "xor-script":
        return "xor-script", decode_script_payload(xor_stream(payload))
    if mode in ("game-auto", "auto"):
        candidates = _decode_candidates(payload)
        if not candidates:
            raise ValueError("game-auto decode failed for all modes")
        return candidates[0][0], candidates[0][1]
    raise ValueError(f"unknown mode: {mode}")


def decode_payload_for_entry(name: str, payload: bytes, mode: str) -> Tuple[str, bytes]:
    low = name.lower()
    if mode not in ("game-auto", "auto"):
        return decode_payload(payload, mode)

    # Extension-aware priority to avoid obviously wrong auto picks.
    if low.endswith(".dds"):
        for m in ("rle-be", "raw", "xor", "xor-rle", "rle-le"):
            try:
                return decode_payload(payload, m)
            except Exception:
                pass
    if low.endswith(".png") or low.endswith(".bmp") or low.endswith(".x"):
        for m in ("xor", "raw", "rle-be", "xor-rle", "rle-le"):
            try:
                return decode_payload(payload, m)
            except Exception:
                pass
    if low.endswith(".wav.raw"):
        for m in ("raw", "xor", "xor-rle", "rle-le", "rle-be"):
            try:
                return decode_payload(payload, m)
            except Exception:
                pass
    if low.endswith(".xml") or "\\script\\" in low or "/script/" in low:
        for m in ("xor-script", "script", "xor", "raw"):
            try:
                return decode_payload(payload, m)
            except Exception:
                pass
    return decode_payload(payload, mode)


def encode_payload_for_pack(raw: bytes, mode: str) -> bytes:
    if mode == "raw":
        return raw
    if mode == "xor":
        return xor_stream(raw)
    if mode == "rle":
        return rle4_compress(raw)
    if mode == "script":
        return xor_stream(encode_script_payload(raw))
    raise ValueError(f"unknown mode: {mode}")


def _riff_wav_to_raw_wav_blob(data: bytes) -> bytes:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    p = 12
    fmt = None
    pcm = None
    while p + 8 <= len(data):
        ck = data[p : p + 4]
        sz = struct.unpack_from("<I", data, p + 4)[0]
        p += 8
        if p + sz > len(data):
            break
        chunk = data[p : p + sz]
        if ck == b"fmt ":
            if sz < 18:
                chunk = chunk + (b"\x00" * (18 - sz))
            fmt = chunk[:18]
        elif ck == b"data":
            pcm = chunk
        p += sz + (sz & 1)
    if fmt is None or pcm is None:
        raise ValueError("missing fmt/data chunk")
    return fmt + pcm


def _encode_for_entry(group: str, entry_name: str, raw_data: bytes, used_mode: str) -> bytes:
    if group == "script":
        return encode_payload_for_pack(raw_data, "script")
    if group == "texture":
        if used_mode == "rle-be":
            return encode_payload_for_pack(raw_data, "rle")
        if used_mode == "xor":
            return encode_payload_for_pack(raw_data, "xor")
        return encode_payload_for_pack(raw_data, "raw")
    if group == "raw":
        if entry_name.lower().endswith(".wav.raw"):
            try:
                raw_data = _riff_wav_to_raw_wav_blob(raw_data)
            except Exception:
                pass
        return encode_payload_for_pack(raw_data, "raw")
    return encode_payload_for_pack(raw_data, "raw")


def _resolve_modified_source(unpacked_group_dir: Path, custom_group_dir: Path, entry_name: str) -> Optional[Path]:
    rel = Path(entry_name.replace("\\", "/"))
    cands = [unpacked_group_dir / rel]
    if entry_name.lower().endswith(".wav.raw"):
        cands.append(unpacked_group_dir / Path(entry_name[:-4].replace("\\", "/")))
    if custom_group_dir.exists():
        cands.append(custom_group_dir / rel)
        if entry_name.lower().endswith(".wav.raw"):
            cands.append(custom_group_dir / Path(entry_name[:-4].replace("\\", "/")))
    for p in cands:
        if p.exists() and p.is_file():
            return p
    return None


def _decode_old_entry_raw(group: str, entry_name: str, payload: bytes) -> bytes:
    _, raw = decode_payload_for_entry(entry_name, payload, "game-auto")
    out_name, raw = normalize_output_name(entry_name, raw, True)
    if group == "raw" and entry_name.lower().endswith(".wav.raw") and out_name.lower().endswith(".wav"):
        return raw
    return raw


def repack_group_from_template(
    root: Path,
    group: str,
    unpacked_root: Path,
    custom_root: Path,
    out_root: Path,
    allow_script_reencode: bool = False,
) -> Tuple[int, int, int, int]:
    src_dir = root / group
    idx_path = src_dir / "idx.dat"
    arc_path = src_dir / "arc.dat"
    if not idx_path.exists() or not arc_path.exists():
        raise FileNotFoundError(f"missing template idx/arc for group: {group}")

    idx_data = idx_path.read_bytes()
    arc_data = arc_path.read_bytes()
    entries = parse_idx(idx_data)
    unpacked_group_dir = unpacked_root / group
    custom_group_dir = custom_root / group

    new_entries: List[Entry] = []
    new_arc = bytearray()
    replaced = 0
    fallback_used = 0
    reused_original = 0
    changed_any = False
    for e in entries:
        old_payload = arc_data[e.offset : e.offset + e.size]
        src = _resolve_modified_source(unpacked_group_dir, custom_group_dir, e.name)
        if src is None:
            payload = old_payload
            fallback_used += 1
            reused_original += 1
        else:
            raw_data = src.read_bytes()
            old_raw = _decode_old_entry_raw(group, e.name, old_payload)
            if raw_data == old_raw:
                payload = old_payload
                reused_original += 1
            else:
                if group == "script" and not allow_script_reencode:
                    raise RuntimeError(
                        f"script 文件已修改但当前为严格模式: {e.name}。"
                        "请改用普通 repack，或命令行加 --strict-repack 时不要修改文件；"
                        "交互里请用 repack（非 repack-strict）。"
                    )
                used_mode, _ = decode_payload_for_entry(e.name, old_payload, "game-auto")
                payload = _encode_for_entry(group, e.name, raw_data, used_mode)
                replaced += 1
                changed_any = True
        new_entries.append(Entry(name=e.name, offset=len(new_arc), size=len(payload)))
        new_arc += payload

    out_dir = out_root / group
    out_dir.mkdir(parents=True, exist_ok=True)
    if not changed_any:
        # Exact restore path: preserve original archive layout/offsets/bytes.
        (out_dir / "idx.dat").write_bytes(idx_data)
        (out_dir / "arc.dat").write_bytes(arc_data)
    else:
        (out_dir / "idx.dat").write_bytes(build_idx(new_entries))
        (out_dir / "arc.dat").write_bytes(bytes(new_arc))
    return len(entries), replaced, fallback_used, reused_original


def _pick_existing(path: Path) -> Optional[Path]:
    return path if path.exists() else None


def resolve_idx_arc_paths(input_path: Optional[str], idx: Optional[str], arc: Optional[str]) -> Tuple[Path, Path]:
    if idx and arc:
        return Path(idx), Path(arc)

    if input_path:
        base = Path(input_path)
    else:
        base = Path(".")

    if base.is_file():
        if base.name.lower() == "idx.dat":
            idx_path = base
            arc_path = base.with_name("arc.dat")
            if not arc_path.exists():
                raise FileNotFoundError(f"arc.dat not found next to {idx_path}")
            return idx_path, arc_path
        if base.name.lower() == "arc.dat":
            arc_path = base
            idx_path = base.with_name("idx.dat")
            if not idx_path.exists():
                raise FileNotFoundError(f"idx.dat not found next to {arc_path}")
            return idx_path, arc_path
        raise FileNotFoundError(f"input file must be idx.dat or arc.dat: {base}")

    idx_auto = _pick_existing(base / "idx.dat")
    arc_auto = _pick_existing(base / "arc.dat")
    if idx_auto and arc_auto:
        return idx_auto, arc_auto
    raise FileNotFoundError(
        "cannot auto-locate idx.dat + arc.dat. "
        "Use --input <dir-with-idx-arc> or explicit --idx/--arc"
    )


def find_archive_roots(root: Path, recursive: bool = False) -> List[Path]:
    roots: List[Path] = []
    if recursive:
        candidates = [root] + [p for p in root.rglob("*") if p.is_dir()]
    else:
        candidates = [root] + [p for p in root.iterdir() if p.is_dir()]
    for d in candidates:
        if (d / "idx.dat").exists() and (d / "arc.dat").exists():
            roots.append(d)
    roots.sort(key=lambda p: str(p).lower())
    return roots


def scan_resource_dirs(root: Path, names: List[str], recursive: bool = False) -> dict[str, List[Path]]:
    name_set = {n.lower() for n in names}
    found: dict[str, List[Path]] = {n: [] for n in names}
    if recursive:
        candidates = [root] + [p for p in root.rglob("*") if p.is_dir()]
    else:
        candidates = [root] + [p for p in root.iterdir() if p.is_dir()]
    for d in candidates:
        low = d.name.lower()
        if low in name_set:
            for n in names:
                if low == n.lower():
                    found[n].append(d)
                    break
    for n in names:
        found[n].sort(key=lambda p: str(p).lower())
    return found


def cmd_list(args: argparse.Namespace) -> None:
    idx_path, _ = resolve_idx_arc_paths(args.input, args.idx, args.arc)
    entries = parse_idx(idx_path.read_bytes())
    for i, e in enumerate(entries):
        print(f"{i:6d}  off=0x{e.offset:08X}  size=0x{e.size:08X}  {e.name}")
    print(f"\nentries: {len(entries)}")


def cmd_unpack(args: argparse.Namespace) -> None:
    idx_path, arc_path = resolve_idx_arc_paths(args.input, args.idx, args.arc)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    idx_data = idx_path.read_bytes()
    arc_data = arc_path.read_bytes()
    entries = parse_idx(idx_data)

    ok = 0
    mode_counts: Counter[str] = Counter()
    wav_tag_counts: Counter[str] = Counter()
    for e in entries:
        end = e.offset + e.size
        if e.offset < 0 or e.size < 0 or end > len(arc_data):
            print(f"[跳过] 范围无效: {e.name} off={e.offset} size={e.size}")
            continue
        payload = arc_data[e.offset:end]
        try:
            used_mode, raw = decode_payload_for_entry(e.name, payload, args.mode)
        except Exception as ex:
            print(f"[跳过] 解码失败: {e.name} ({ex})")
            continue

        _write_entry_output(
            out_dir=out_dir,
            entry_name=e.name,
            decoded_raw=raw,
            original_payload=payload,
            script_output=args.script_output,
            convert_raw_wav=args.convert_raw_wav,
        )
        ok += 1
        mode_counts[used_mode] += 1
        out_name, out_data = normalize_output_name(e.name, raw, args.convert_raw_wav)
        if Path(out_name).suffix.lower() == ".wav":
            tag = probe_wav_format_tag(out_data)
            if tag is None:
                wav_tag_counts["unknown"] += 1
            else:
                wav_tag_counts[f"0x{tag:04X}"] += 1

    print(f"解包完成 {ok}/{len(entries)} -> {out_dir}")
    if mode_counts:
        stats = ", ".join(f"{k}:{v}" for k, v in sorted(mode_counts.items(), key=lambda x: (-x[1], x[0])))
        print(f"解码模式统计: {stats}")
    if wav_tag_counts:
        tags = ", ".join(f"{k}:{v}" for k, v in sorted(wav_tag_counts.items(), key=lambda x: (-x[1], x[0])))
        print(f"WAV 格式标签统计: {tags}")
        print("提示: 非 PCM (非 0x0001/0x0003) 可能在部分播放器中无法播放")


def _unpack_one_root(root: Path, out_root: Path, mode: str, script_output: str = "decoded") -> bool:
    idx_path = root / "idx.dat"
    arc_path = root / "arc.dat"
    out_dir = out_root / root.name
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        entries = parse_idx(idx_path.read_bytes())
        arc_data = arc_path.read_bytes()
    except Exception as ex:
        print(f"[跳过] {root} 读取失败: {ex}")
        return False

    ok = 0
    for e in entries:
        end = e.offset + e.size
        if e.offset < 0 or e.size < 0 or end > len(arc_data):
            continue
        payload = arc_data[e.offset:end]
        try:
            _, raw = decode_payload_for_entry(e.name, payload, mode)
        except Exception:
            continue
        _write_entry_output(
            out_dir=out_dir,
            entry_name=e.name,
            decoded_raw=raw,
            original_payload=payload,
            script_output=script_output,
            convert_raw_wav=True,
        )
        ok += 1
    print(f"[完成] {root} -> {out_dir} ({ok}/{len(entries)})")
    return True


def cmd_interactive(args: argparse.Namespace) -> None:
    root = Path(args.root)
    out_root = Path(args.out)
    mode = args.mode
    script_output = args.script_output
    recursive = args.recursive
    dir_names = ["mesh", "raw", "texture", "script"]

    def do_scan() -> List[Path]:
        found = find_archive_roots(root, recursive=recursive)
        dir_hits = scan_resource_dirs(root, dir_names, recursive=recursive)
        if not found:
            print("未找到可解包目录（需要同时存在 idx.dat 与 arc.dat）。")
        else:
            print("\n发现可解包目录:")
            for i, p in enumerate(found, start=1):
                print(f"  {i:2d}) {p}")
            print("")

        print("资源目录扫描:")
        for n in dir_names:
            hits = dir_hits[n]
            if hits:
                print(f"  {n:8s} : 找到 {len(hits)} 个")
                for hp in hits[:5]:
                    print(f"    - {hp}")
                if len(hits) > 5:
                    print(f"    ... ({len(hits) - 5} more)")
            else:
                print(f"  {n:8s} : 未找到")
        print("")
        return found

    found = do_scan()
    print(
        "交互模式。命令: scan, list, unpack <编号|名称>, unpack all, "
        "repack <组|all>, repack-strict <组|all>, scriptmode <decoded|raw|both>, quit"
    )
    while True:
        cmd = input("arc> ").strip()
        if not cmd:
            continue
        lo = cmd.lower()
        if lo in ("quit", "exit", "q"):
            print("已退出")
            break
        if lo in ("scan", "rescan"):
            found = do_scan()
            continue
        if lo in ("list", "ls"):
            if not found:
                print("当前没有扫描结果，请先执行 scan")
            else:
                for i, p in enumerate(found, start=1):
                    print(f"  {i:2d}) {p}")
            continue
        if lo in ("unpack all", "all"):
            if not found:
                print("当前没有扫描结果，请先执行 scan")
                continue
            done = 0
            for p in found:
                if _unpack_one_root(p, out_root, mode, script_output):
                    done += 1
            print(f"已完成 {done}/{len(found)} 个目录")
            continue
        if lo.isdigit():
            if not found:
                print("当前没有扫描结果，请先执行 scan")
                continue
            idx = int(lo)
            if idx < 1 or idx > len(found):
                print(f"编号超出范围: 1..{len(found)}")
                continue
            _unpack_one_root(found[idx - 1], out_root, mode, script_output)
            continue
        if lo == "unpack":
            if not found:
                print("当前没有扫描结果，请先执行 scan")
                continue
            if len(found) == 1:
                _unpack_one_root(found[0], out_root, mode, script_output)
            else:
                print("请使用: unpack <编号|名称> 或 unpack all")
            continue
        if lo.startswith("unpack "):
            if not found:
                print("当前没有扫描结果，请先执行 scan")
                continue
            tail = lo.split(None, 1)[1].strip()
            if tail == "all":
                done = 0
                for p in found:
                    if _unpack_one_root(p, out_root, mode, script_output):
                        done += 1
                print(f"已完成 {done}/{len(found)} 个目录")
                continue
            try:
                idx = int(tail)
                if idx < 1 or idx > len(found):
                    print(f"编号超出范围: 1..{len(found)}")
                    continue
                _unpack_one_root(found[idx - 1], out_root, mode, script_output)
                continue
            except ValueError:
                matches = [p for p in found if tail in p.name.lower() or tail in str(p).lower()]
                if not matches:
                    print("未匹配到目录。请使用: unpack <编号> / unpack <名称> / unpack all")
                    continue
                if len(matches) > 1:
                    print("匹配到多个目录:")
                    for p in matches:
                        print(f"  - {p}")
                    print("请使用更具体的名称或直接使用编号。")
                    continue
                _unpack_one_root(matches[0], out_root, mode, script_output)
            continue
        if lo.startswith("scriptmode "):
            v = lo.split(None, 1)[1].strip()
            if v not in ("decoded", "raw", "both"):
                print("scriptmode 仅支持: decoded/raw/both")
                continue
            script_output = v
            print(f"script 输出模式已切换为: {script_output}")
            continue
        if lo.startswith("repack-strict "):
            target = lo.split(None, 1)[1].strip()
            if target not in ("mesh", "raw", "texture", "script", "all"):
                print("repack-strict 仅支持: mesh/raw/texture/script/all")
                continue
            groups = ["mesh", "raw", "texture", "script"] if target == "all" else [target]
            unpacked_root = root / "unpacked_batch"
            custom_root = root / "custom_data"
            out_pack_root = root / "repacked"
            for g in groups:
                try:
                    total, replaced, fallback, reused = repack_group_from_template(
                        root, g, unpacked_root, custom_root, out_pack_root, allow_script_reencode=False
                    )
                    print(f"[回封-严格] {g}: 总条目={total}, 重编码替换={replaced}, 回退原始={fallback}, 原样复用={reused}")
                except Exception as ex:
                    print(f"[失败] {g}: {ex}")
            continue
        if lo.startswith("repack "):
            target = lo.split(None, 1)[1].strip()
            if target not in ("mesh", "raw", "texture", "script", "all"):
                print("repack 仅支持: mesh/raw/texture/script/all")
                continue
            groups = ["mesh", "raw", "texture", "script"] if target == "all" else [target]
            unpacked_root = root / "unpacked_batch"
            custom_root = root / "custom_data"
            out_pack_root = root / "repacked"
            for g in groups:
                try:
                    total, replaced, fallback, reused = repack_group_from_template(
                        root, g, unpacked_root, custom_root, out_pack_root, allow_script_reencode=True
                    )
                    print(f"[回封] {g}: 总条目={total}, 重编码替换={replaced}, 回退原始={fallback}, 原样复用={reused}")
                except Exception as ex:
                    print(f"[失败] {g}: {ex}")
            continue
        print(
            "未知命令。可用命令: scan, list, unpack <编号|名称>, unpack all, "
            "repack <组|all>, repack-strict <组|all>, scriptmode <decoded|raw|both>, quit"
        )


def cmd_decode_script(args: argparse.Namespace) -> None:
    inp = Path(args.input).read_bytes()
    if args.pre_xor:
        inp = xor_stream(inp)
    out = decode_script_payload(inp)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out)
    print(f"decoded script -> {out_path} ({len(out)} bytes)")


def cmd_encode_script(args: argparse.Namespace) -> None:
    inp = Path(args.input).read_bytes()
    out = encode_script_payload(inp)
    if args.post_xor:
        out = xor_stream(out)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out)
    print(f"encoded script -> {out_path} ({len(out)} bytes)")


def cmd_repack(args: argparse.Namespace) -> None:
    root = Path(args.root)
    groups = ["mesh", "raw", "texture", "script"] if args.group == "all" else [args.group]
    unpacked_root = root / args.unpacked_dir
    custom_root = root / args.custom_dir
    out_root = root / args.out_dir
    allow_reencode = not args.strict_repack
    done = 0
    for g in groups:
        try:
            total, replaced, fallback, reused = repack_group_from_template(
                root, g, unpacked_root, custom_root, out_root, allow_script_reencode=allow_reencode
            )
            print(f"[回封] {g}: 总条目={total}, 重编码替换={replaced}, 回退原始={fallback}, 原样复用={reused}")
            done += 1
        except Exception as ex:
            print(f"[失败] {g}: {ex}")
    print(f"回封完成: {done}/{len(groups)}")


def iter_input_files(in_dir: Path) -> List[Path]:
    files = [p for p in in_dir.rglob("*") if p.is_file()]
    files.sort(key=lambda p: str(p).lower())
    return files


def cmd_pack(args: argparse.Namespace) -> None:
    in_dir = Path(args.in_dir)
    idx_path = Path(args.idx_out)
    arc_path = Path(args.arc_out)

    files = iter_input_files(in_dir)
    if not files:
        raise RuntimeError(f"no files in {in_dir}")

    entries: List[Entry] = []
    arc_buf = bytearray()
    offset = 0

    for p in files:
        name = normalize_idx_name(p, in_dir, flat=args.flat_names)
        raw = p.read_bytes()
        payload = encode_payload_for_pack(raw, args.mode)

        entries.append(Entry(name=name, offset=offset, size=len(payload)))
        arc_buf += payload
        offset += len(payload)

    idx_data = build_idx(entries)
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    arc_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_bytes(idx_data)
    arc_path.write_bytes(bytes(arc_buf))

    print(
        f"packed {len(entries)} files, idx={len(idx_data)} bytes, arc={len(arc_buf)} bytes\n"
        f" -> {idx_path}\n -> {arc_path}"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pack/unpack arc.dat + idx.dat archives (reverse engineered format)."
    )
    sub = p.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="list idx entries")
    p_list.add_argument(
        "--input",
        default=".",
        help="archive folder or idx.dat/arc.dat path (default: current dir)",
    )
    p_list.add_argument("--idx", help="path to idx.dat (optional if --input is set)")
    p_list.add_argument("--arc", help="path to arc.dat (optional)")
    p_list.set_defaults(func=cmd_list)

    p_unpack = sub.add_parser("unpack", help="unpack archive")
    p_unpack.add_argument(
        "--input",
        default=".",
        help="archive folder or idx.dat/arc.dat path (default: current dir)",
    )
    p_unpack.add_argument("--idx", help="path to idx.dat (optional if --input is set)")
    p_unpack.add_argument("--arc", help="path to arc.dat (optional if --input is set)")
    p_unpack.add_argument("--out", default="unpacked", help="output directory (default: unpacked)")
    p_unpack.add_argument(
        "--mode",
        default="game-auto",
        choices=[
            "game-auto",
            "raw",
            "xor",
            "rle-be",
            "xor-script",
        ],
        help="payload decode mode (simplified for 4 resource groups)",
    )
    p_unpack.add_argument(
        "--no-convert-raw-wav",
        dest="convert_raw_wav",
        action="store_false",
        help="disable auto conversion from .wav.raw to standard .wav",
    )
    p_unpack.add_argument(
        "--script-output",
        default="decoded",
        choices=["decoded", "raw", "both"],
        help="script 输出策略: decoded(可读xml)/raw(原始payload)/both(两者都写)",
    )
    p_unpack.set_defaults(convert_raw_wav=True)
    p_unpack.set_defaults(func=cmd_unpack)

    p_inter = sub.add_parser("interactive", help="scan dirs and unpack by interactive commands")
    p_inter.add_argument(
        "--root",
        default=".",
        help="scan root directory (default: current dir)",
    )
    p_inter.add_argument(
        "--out",
        default="unpacked_batch",
        help="output root for interactive unpack (default: unpacked_batch)",
    )
    p_inter.add_argument(
        "--mode",
        default="game-auto",
        choices=[
            "game-auto",
            "raw",
            "xor",
            "rle-be",
            "xor-script",
        ],
        help="decode mode for interactive unpack",
    )
    p_inter.add_argument(
        "--recursive",
        action="store_true",
        help="scan subdirectories recursively",
    )
    p_inter.add_argument(
        "--script-output",
        default="decoded",
        choices=["decoded", "raw", "both"],
        help="interactive 下 script 输出策略",
    )
    p_inter.set_defaults(func=cmd_interactive)

    p_script = sub.add_parser("decode-script", help="decode one script second-stage blob")
    p_script.add_argument("--input", required=True, help="input blob path")
    p_script.add_argument("--out", required=True, help="output decoded file path")
    p_script.add_argument(
        "--pre-xor",
        action="store_true",
        help="apply archive XOR stream before script decode",
    )
    p_script.set_defaults(func=cmd_decode_script)

    p_script_enc = sub.add_parser("encode-script", help="encode one script XML/text to script blob")
    p_script_enc.add_argument("--input", required=True, help="input decoded script file")
    p_script_enc.add_argument("--out", required=True, help="output encoded blob path")
    p_script_enc.add_argument(
        "--post-xor",
        action="store_true",
        help="apply archive XOR stream after script encoding (recommended for repack)",
    )
    p_script_enc.set_defaults(func=cmd_encode_script)

    p_repack = sub.add_parser("repack", help="template-based repack for mesh/raw/texture/script")
    p_repack.add_argument(
        "--root",
        default=".",
        help="project root containing mesh/raw/texture/script and unpacked_batch",
    )
    p_repack.add_argument(
        "--group",
        default="all",
        choices=["mesh", "raw", "texture", "script", "all"],
        help="which group to repack (default: all)",
    )
    p_repack.add_argument(
        "--unpacked-dir",
        default="unpacked_batch",
        help="decoded files root dir (default: unpacked_batch)",
    )
    p_repack.add_argument(
        "--custom-dir",
        default="custom_data",
        help="custom fallback files root dir for missing entries (default: custom_data)",
    )
    p_repack.add_argument(
        "--out-dir",
        default="repacked",
        help="output repacked root dir (default: repacked)",
    )
    p_repack.add_argument(
        "--strict-repack",
        action="store_true",
        help="严格模式: 禁止对修改过的 script 重编码(用于验证与原包一致); 默认允许修改后重编码",
    )
    p_repack.set_defaults(func=cmd_repack)

    p_pack = sub.add_parser("pack", help="pack directory into idx+arc")
    p_pack.add_argument("--in-dir", required=True, help="input directory")
    p_pack.add_argument("--idx-out", required=True, help="output idx.dat path")
    p_pack.add_argument("--arc-out", required=True, help="output arc.dat path")
    p_pack.add_argument(
        "--mode",
        default="xor",
        choices=["raw", "xor", "rle", "script"],
        help="payload encode mode; use script for script/*.xml repack test",
    )
    p_pack.add_argument(
        "--flat-names",
        action="store_true",
        help="store only filename in idx (drop subdirectories)",
    )
    p_pack.set_defaults(func=cmd_pack)

    return p


def main() -> None:
    parser = build_parser()
    if len(sys.argv) == 1:
        args = parser.parse_args(["interactive"])
    else:
        args = parser.parse_args()
    if getattr(args, "cmd", None) is None:
        args = parser.parse_args(["interactive"])
    args.func(args)


if __name__ == "__main__":
    main()

