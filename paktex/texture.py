"""UE cooked 纹理像素格式解码 → PNG。

压缩格式优先用本地 `texture2ddecoder`（支持 BC1~7 / ASTC / ETC / PVRTC），
缺失时回退到 Pillow 的 DDS/BCn 解码器；未压缩格式用 numpy 做通道重排。
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image

try:
    import texture2ddecoder  # type: ignore

    _T2D = texture2ddecoder
except ImportError:  # pragma: no cover - 可选依赖
    _T2D = None


class TextureError(Exception):
    """纹理解码错误。"""


@dataclass(frozen=True)
class Format:
    kind: str
    bpp: int = 0  # 未压缩：每像素字节数
    bps: int = 0  # 块压缩：每块字节数
    bw: int = 1
    bh: int = 1
    decoder: str | None = None  # texture2ddecoder 函数名
    fourcc: bytes | None = None  # Pillow DDS 回退：旧式 fourcc
    dxgi: int | None = None  # Pillow DDS 回退：DX10 DXGI 编号

    @property
    def is_block(self) -> bool:
        return self.bps > 0


# UE EPixelFormat 名称 -> 解码描述
FORMATS: dict[str, Format] = {
    # ---- 未压缩 ----
    "PF_B8G8R8A8": Format("bgra", bpp=4),
    "PF_R8G8B8A8": Format("rgba", bpp=4),
    "PF_A8R8G8B8": Format("bgra", bpp=4),
    "PF_B8G8R8X8": Format("bgra", bpp=4),
    "PF_A8": Format("alpha", bpp=1),
    "PF_G8": Format("gray", bpp=1),
    "PF_R8G8": Format("rg", bpp=2),
    "PF_G16": Format("r16", bpp=2),
    "PF_A16B16G16R16": Format("rgba16", bpp=8),
    "PF_A32B32G32R32F": Format("float32x4", bpp=16),
    "PF_FloatRGBA": Format("float16x4", bpp=8),
    "PF_FloatR11G11B10": Format("r11g11b10", bpp=4),
    # ---- BCn ----
    "PF_DXT1": Format("BC1", bps=8, bw=4, bh=4, decoder="decode_bc1", fourcc=b"DXT1", dxgi=71),
    "PF_DXT3": Format("BC2", bps=16, bw=4, bh=4, fourcc=b"DXT3", dxgi=74),
    "PF_DXT5": Format("BC3", bps=16, bw=4, bh=4, decoder="decode_bc3", fourcc=b"DXT5", dxgi=77),
    "PF_BC4": Format("BC4", bps=8, bw=4, bh=4, decoder="decode_bc4", fourcc=b"BC4U", dxgi=80),
    "PF_BC5": Format("BC5", bps=16, bw=4, bh=4, decoder="decode_bc5", fourcc=b"BC5U", dxgi=83),
    "PF_BC6H": Format("BC6H", bps=16, bw=4, bh=4, decoder="decode_bc6", dxgi=95),
    "PF_BC7": Format("BC7", bps=16, bw=4, bh=4, decoder="decode_bc7", dxgi=98),
    # ---- ETC ----
    "PF_ETC1": Format("ETC1", bps=8, bw=4, bh=4, decoder="decode_etc1"),
    "PF_ETC2_RGB": Format("ETC2", bps=8, bw=4, bh=4, decoder="decode_etc2"),
    "PF_ETC2_RGBA": Format("ETC2A", bps=16, bw=4, bh=4, decoder="decode_etc2a8"),
    # ---- ASTC（16 字节/块，块尺寸随名称）----
    "PF_ASTC_4x4": Format("ASTC", bps=16, bw=4, bh=4, decoder="decode_astc"),
    "PF_ASTC_6x6": Format("ASTC", bps=16, bw=6, bh=6, decoder="decode_astc"),
    "PF_ASTC_8x8": Format("ASTC", bps=16, bw=8, bh=8, decoder="decode_astc"),
    "PF_ASTC_10x10": Format("ASTC", bps=16, bw=10, bh=10, decoder="decode_astc"),
    "PF_ASTC_12x12": Format("ASTC", bps=16, bw=12, bh=12, decoder="decode_astc"),
}


def normalize_format(name: str) -> str:
    """常见的大小写/别名归一化。"""
    key = name.strip()
    if key in FORMATS:
        return key
    upper = key.upper()
    for candidate in FORMATS:
        if candidate.upper() == upper:
            return candidate
    aliases = {
        "DXT1": "PF_DXT1",
        "DXT3": "PF_DXT3",
        "DXT5": "PF_DXT5",
        "BC1": "PF_DXT1",
        "BC2": "PF_DXT3",
        "BC3": "PF_DXT5",
        "BGRA8": "PF_B8G8R8A8",
        "RGBA8": "PF_R8G8B8A8",
    }
    if upper in aliases:
        return aliases[upper]
    return key


def get_format(name: str) -> Format:
    key = normalize_format(name)
    fmt = FORMATS.get(key)
    if fmt is None:
        raise TextureError(
            f"不支持的像素格式 {name!r}。已支持: {', '.join(sorted(FORMATS))}"
        )
    return fmt


def expected_size(width: int, height: int, name: str) -> int:
    """顶层 mip 应占的字节数（用于校验和从多 mip 数据中切出顶层）。"""
    fmt = get_format(name)
    if fmt.is_block:
        return math.ceil(width / fmt.bw) * math.ceil(height / fmt.bh) * fmt.bps
    return width * height * fmt.bpp


# ---- Pillow DDS 回退 -------------------------------------------------
def _dds_blob(width: int, height: int, fmt: Format, payload: bytes) -> bytes:
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    header = struct.pack("<I", 0x20534444)
    header += struct.pack("<7I", 124, flags, height, width, len(payload), 0, 0)
    header += b"\0" * 44
    if fmt.fourcc:
        header += struct.pack("<4I", 32, 0x4, struct.unpack("<I", fmt.fourcc)[0], 0)
        header += b"\0" * 16
    else:
        header += struct.pack("<4I", 32, 0x4, struct.unpack("<I", b"DX10")[0], 0)
        header += b"\0" * 16
    header += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    if fmt.fourcc is None:
        if fmt.dxgi is None:
            raise TextureError(f"{fmt.kind} 无法用 DDS 回退解码")
        header += struct.pack("<5I", fmt.dxgi, 3, 0, 0, 1)
    return header + payload


def _decode_with_pillow(width: int, height: int, fmt: Format, data: bytes) -> Image.Image:
    import io

    im = Image.open(io.BytesIO(_dds_blob(width, height, fmt, data)))
    im.load()
    return im.convert("RGBA")


# ---- 主解码 ---------------------------------------------------------
def decode(width: int, height: int, name: str, data: bytes) -> Image.Image:
    """把顶层 mip 的原始字节解码成 RGBA PIL 图像。"""
    fmt = get_format(name)
    want = expected_size(width, height, name)
    if len(data) < want:
        raise TextureError(
            f"{name}: 像素数据不足，需要 {want} 字节，实际 {len(data)} 字节"
        )
    data = data[:want]

    if not fmt.is_block:
        return _decode_raw(width, height, fmt, data)

    if _T2D is not None and fmt.decoder:
        fn = getattr(_T2D, fmt.decoder, None)
        if fn is not None:
            if fmt.kind == "ASTC":
                raw = fn(data, width, height, fmt.bw, fmt.bh)
            else:
                raw = fn(data, width, height)
            return _bgra_bytes_to_image(width, height, raw)

    if fmt.fourcc or fmt.dxgi:
        return _decode_with_pillow(width, height, fmt, data)

    raise TextureError(
        f"{name}: 需要 texture2ddecoder 才能解码（pip install texture2ddecoder）"
    )


def _bgra_bytes_to_image(width: int, height: int, raw: bytes) -> Image.Image:
    """texture2ddecoder 返回 BGRA（BC4 返回单通道），统一转 RGBA。"""
    if len(raw) == width * height:  # BC4 等单通道
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0] = arr
        rgba[:, :, 1] = arr
        rgba[:, :, 2] = arr
        rgba[:, :, 3] = 255
        return Image.fromarray(rgba, "RGBA")
    if len(raw) != width * height * 4:
        raise TextureError(f"解码器返回长度异常: {len(raw)}")
    return Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA")


def _decode_raw(width: int, height: int, fmt: Format, data: bytes) -> Image.Image:
    if fmt.kind == "bgra":
        arr = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)
        rgba = arr[:, :, [2, 1, 0, 3]].copy()
    elif fmt.kind == "rgba":
        rgba = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4).copy()
    elif fmt.kind == "gray":
        g = np.frombuffer(data, dtype=np.uint8).reshape(height, width)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0] = rgba[:, :, 1] = rgba[:, :, 2] = g
        rgba[:, :, 3] = 255
    elif fmt.kind == "alpha":
        a = np.frombuffer(data, dtype=np.uint8).reshape(height, width)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0] = rgba[:, :, 1] = rgba[:, :, 2] = 255
        rgba[:, :, 3] = a
    elif fmt.kind == "rg":
        rg = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 2)
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0] = rg[:, :, 0]
        rgba[:, :, 1] = rg[:, :, 1]
        rgba[:, :, 3] = 255
    elif fmt.kind == "r16":
        g = np.frombuffer(data, dtype="<u2").reshape(height, width)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        v = (g >> 8).astype(np.uint8)
        rgba[:, :, 0] = rgba[:, :, 1] = rgba[:, :, 2] = v
        rgba[:, :, 3] = 255
    elif fmt.kind == "rgba16":
        a = np.frombuffer(data, dtype="<u2").reshape(height, width, 4) >> 8
        rgba = a.astype(np.uint8)
    elif fmt.kind == "float16x4":
        f = np.frombuffer(data, dtype="<f2").reshape(height, width, 4).astype(np.float32)
        rgba = np.clip(f * 255.0, 0, 255).astype(np.uint8)
    elif fmt.kind == "float32x4":
        f = np.frombuffer(data, dtype="<f4").reshape(height, width, 4)
        rgba = np.clip(f * 255.0, 0, 255).astype(np.uint8)
    elif fmt.kind == "r11g11b10":
        # 10/11/11 位无符号浮点近似：取指数/尾数的高位做简单映射
        bits = np.frombuffer(data, dtype="<u4").reshape(height, width)
        r = ((bits >> 0) & 0x7FF) >> 3
        g = ((bits >> 11) & 0x7FF) >> 3
        b = ((bits >> 22) & 0x3FF) >> 2
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2] = r, g, b
        rgba[:, :, 3] = 255
        rgba = rgba.astype(np.uint8)
    else:
        raise TextureError(f"未实现的未压缩格式: {fmt.kind}")
    return Image.fromarray(rgba, "RGBA")


def save_png(image: Image.Image, path: str) -> None:
    import os

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    image.save(path, "PNG", optimize=True)
