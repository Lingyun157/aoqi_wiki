"""从 cooked .uasset/.uexp/.ubulk 中提取 UTexture2D 的顶层 mip。

UE 把 `FTexturePlatformData` 写在导出数据里，像素本体由 `FByteBulkData`
指向（内联 / .uasset 尾部 / .uexp / .ubulk）。由于不同 UE 版本的字段略有差异，
这里用「像素格式 FString 签名」定位结构，再对多种布局逐一试解析，并用
期望的 mip 字节数做校验，避免猜错偏移。
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from . import texture

# FByteBulkData 标志
BD_PAYLOAD_AT_END = 1 << 0
BD_COMPRESSED_ZLIB = 1 << 1
BD_FORCE_INLINE = 1 << 6
BD_PAYLOAD_SEPARATE = 1 << 8
BD_SIZE_64BIT = 1 << 13

# 已知像素格式名（用于签名匹配）
KNOWN_PREFIX = b"PF_"
MAX_DIM = 16384


class UAssetError(Exception):
    """纹理资产解析错误。"""


@dataclass
class TextureData:
    width: int
    height: int
    pixel_format: str
    mip_data: bytes
    source: str = ""

    @property
    def expected(self) -> int:
        return texture.expected_size(self.width, self.height, self.pixel_format)


@dataclass
class _Bulk:
    flags: int
    element_count: int
    size_on_disk: int
    offset: int
    end_pos: int  # bulk 头结束后在同一缓冲区中的位置


@dataclass
class _Mip:
    bulk: _Bulk
    width: int
    height: int
    depth: int


class _Buf:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self.data = data

    def i32(self, p: int) -> int:
        if p + 4 > len(self.data):
            raise ValueError
        return struct.unpack_from("<i", self.data, p)[0]

    def u32(self, p: int) -> int:
        if p + 4 > len(self.data):
            raise ValueError
        return struct.unpack_from("<I", self.data, p)[0]

    def i64(self, p: int) -> int:
        if p + 8 > len(self.data):
            raise ValueError
        return struct.unpack_from("<q", self.data, p)[0]

    def u64(self, p: int) -> int:
        if p + 8 > len(self.data):
            raise ValueError
        return struct.unpack_from("<Q", self.data, p)[0]


def looks_like_texture2d(asset: bytes) -> bool:
    """轻量判定：名称表里是否出现 Texture2D 类名。"""
    return b"Texture2D" in asset[:65536]


def _find_signatures(buf: _Buf) -> list[tuple[int, int, int, str, int, int]]:
    """返回 (fstring_pos, fstring_len, packed, pixel_format, size_x, size_y)。"""
    out = []
    data = buf.data
    start = 0
    while True:
        p = data.find(KNOWN_PREFIX, start)
        if p < 0:
            break
        start = p + 1
        if p < 16:
            continue
        end = data.find(b"\x00", p)
        if end < 0 or end - p > 40:
            continue
        name = data[p:end].decode("latin-1")
        try:
            texture.get_format(name)
        except texture.TextureError:
            continue
        n = buf.i32(p - 4)
        if n != (end - p) + 1:  # FString 长度含结尾 NUL
            continue
        size_x = buf.i32(p - 16)
        size_y = buf.i32(p - 12)
        packed = buf.u32(p - 8)
        if not (1 <= size_x <= MAX_DIM and 1 <= size_y <= MAX_DIM):
            continue
        num_slices = packed & 0x3FFFFFFF
        if not (1 <= num_slices <= 2048):
            continue
        out.append((p, n, packed, name, size_x, size_y))
    return out


def _read_bulk(buf: _Buf, p: int) -> _Bulk | None:
    """尝试三种 FByteBulkData 头布局，返回最合理的一个。"""
    variants = []
    try:
        flags = buf.u32(p)
        if flags & BD_SIZE_64BIT:
            variants.append(
                _Bulk(flags, buf.i64(p + 4), buf.i64(p + 12), buf.i64(p + 20), p + 28)
            )
        else:
            variants.append(
                _Bulk(flags, buf.i32(p + 4), buf.u32(p + 8), buf.i64(p + 12), p + 20)
            )
            variants.append(
                _Bulk(flags, buf.i32(p + 4), buf.u32(p + 8), buf.i32(p + 12), p + 16)
            )
    except (ValueError, struct.error):
        return None
    for v in variants:
        if not (0 < v.element_count < 1 << 31):
            continue
        if not (0 < v.size_on_disk <= v.element_count * 4 + 4096):
            continue
        if v.offset < 0 or v.offset > (1 << 40):
            continue
        if v.flags & ~0x7FFFFFFF:
            continue
        return v
    return None


def _parse_mips(buf: _Buf, pos: int, count: int, width: int, height: int, fmt: str) -> list[_Mip] | None:
    """尝试若干 mip 记录布局，返回顶层尺寸匹配的一组。"""
    layouts = [
        # (前置 bool, bulk 后尺寸字段数)
        (False, 3),
        (True, 3),
        (False, 2),
        (True, 2),
    ]
    want = texture.expected_size(width, height, fmt)
    for extra_bool, dims in layouts:
        p = pos
        mips: list[_Mip] = []
        ok = True
        for _ in range(count):
            try:
                if extra_bool:
                    cooked = buf.u32(p)
                    if cooked > 1:
                        ok = False
                        break
                    p += 4
                bulk = _read_bulk(buf, p)
                if bulk is None:
                    ok = False
                    break
                p = bulk.end_pos
                # 内联 payload 紧跟在 bulk 头之后，尺寸字段在其后
                if not bulk.flags & (BD_PAYLOAD_SEPARATE | BD_PAYLOAD_AT_END):
                    p += bulk.size_on_disk
                w = buf.i32(p)
                h = buf.i32(p + 4)
                d = buf.i32(p + 8) if dims >= 3 else 1
                p += 4 * dims
                if not (1 <= w <= MAX_DIM and 1 <= h <= MAX_DIM and 1 <= d <= 2048):
                    ok = False
                    break
                mips.append(_Mip(bulk, w, h, d))
            except (ValueError, struct.error):
                ok = False
                break
        if not ok or not mips:
            continue
        top = mips[0]
        if top.width != width or top.height != height:
            continue
        if top.bulk.size_on_disk < want // 4:
            continue
        return mips
    return None


def _try_payload(
    blob: bytes, bulk: _Bulk, want: int, compressed: bool
) -> bytes | None:
    """校验一段候选像素数据：必要时 zlib 解压，长度必须 >= 顶层 mip。"""
    if len(blob) < bulk.size_on_disk:
        return None
    data = blob[: bulk.size_on_disk]
    if compressed:
        for candidate in (data, data[8:]):  # UE 可能带 8 字节压缩头
            try:
                data = zlib.decompress(candidate)
                break
            except zlib.error:
                continue
        else:
            return None
    if len(data) < want:
        return None
    return data[:want]


def _resolve_mip(
    mip: _Mip, buf: _Buf, buffers: list[_Buf], want: int, inline_pos: int
) -> tuple[bytes, str] | None:
    bulk = mip.bulk
    compressed = bool(bulk.flags & BD_COMPRESSED_ZLIB)
    separate = bool(bulk.flags & BD_PAYLOAD_SEPARATE)
    at_end = bool(bulk.flags & BD_PAYLOAD_AT_END)

    candidates: list[tuple[_Buf, int]] = []
    if separate:
        for b in buffers:
            if b.name == "ubulk":
                candidates.append((b, bulk.offset))
        for b in buffers:
            candidates.append((b, bulk.offset))
    elif at_end:
        candidates.append((buf, bulk.offset))
        for b in buffers:
            candidates.append((b, bulk.offset))
    else:
        candidates.append((buf, inline_pos))
    # 兜底：常见起点（文件头 / 缓冲区末尾 / 索引偏移附近）
    for b in buffers:
        candidates.extend(
            [
                (b, 0),
                (b, bulk.offset),
                (b, max(0, len(b.data) - bulk.size_on_disk)),
            ]
        )

    seen = set()
    for b, off in candidates:
        key = (b.name, off)
        if key in seen:
            continue
        seen.add(key)
        if off < 0 or off >= len(b.data):
            continue
        result = _try_payload(b.data[off:], bulk, want, compressed)
        if result is not None:
            return result, f"{b.name}@{off}"
    return None


def parse_texture(
    asset: bytes,
    uexp: bytes | None = None,
    ubulk: bytes | None = None,
    name: str = "",
) -> TextureData | None:
    """解析 UTexture2D，返回顶层 mip；不是纹理或解析失败则返回 None。"""
    buffers = [_Buf("uasset", asset)]
    if uexp:
        buffers.append(_Buf("uexp", uexp))
    if ubulk:
        buffers.append(_Buf("ubulk", ubulk))

    for buf in buffers:
        for sig in _find_signatures(buf):
            fstring_pos, fstring_len, packed, fmt, width, height = sig
            if packed & 0x20000000:  # HasCpuCopy (UE5.4+)，结构过于复杂，跳过
                continue
            p = fstring_pos + fstring_len
            if packed & 0x40000000:  # HasOptData
                p += 8
            try:
                first_mip = buf.i32(p)
                mip_count = buf.i32(p + 4)
            except (ValueError, struct.error):
                continue
            if not (1 <= mip_count <= 32) or first_mip < 0:
                continue
            mips = _parse_mips(buf, p + 8, mip_count, width, height, fmt)
            if not mips:
                continue
            top = mips[0]
            want = texture.expected_size(width, height, fmt)
            resolved = _resolve_mip(top, buf, buffers, want, top.bulk.end_pos)
            if resolved is None:
                continue
            data, source = resolved
            return TextureData(width, height, fmt, data, source)
    return None
