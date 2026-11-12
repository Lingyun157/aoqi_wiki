"""Unreal Engine .pak 读取器。

支持 pak 版本 1~12（含鸣潮 / UE5.7 的 v12 变体）、AES-256-ECB 加密的
索引与文件体、zlib/gzip 压缩、v10+ 位编码索引条目、以及鸣潮的「部分加密」。

参考：repak (trumank/repak)、CUE4Parse (FabianFG/CUE4Parse) 的实现，
并用用户提供的真实 pak 逐字段校验过。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass, field
from typing import BinaryIO

MAGIC = 0x5A6F12E1
AES_BLOCK = 16
INT32_MIN = -(1 << 31)

# pak 版本主号
V_INITIAL = 1
V_NO_TIMESTAMPS = 2
V_COMPRESSION_ENCRYPTION = 3
V_INDEX_ENCRYPTION = 4
V_RELATIVE_CHUNK_OFFSETS = 5
V_DELETE_RECORDS = 6
V_ENCRYPTION_KEY_GUID = 7
V_FNAME_BASED_COMPRESSION = 8
V_FROZEN_INDEX = 9
V_PATH_HASH_INDEX = 10
V_FNV64_BUGFIX = 11

# FByteBulkData 标志（此处仅用于文档/调试，纹理解析在 uasset.py）
BULKDATA_PayloadAtEndOfFile = 1 << 0
BULKDATA_SerializeCompressedZLIB = 1 << 1
BULKDATA_ForceInlinePayload = 1 << 6
BULKDATA_PayloadInSeperateFile = 1 << 8
BULKDATA_Size64Bit = 1 << 13
BULKDATA_NoOffsetFixUp = 1 << 16


class PakError(Exception):
    """pak 解析/解密错误。"""


def align(value: int, boundary: int = AES_BLOCK) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    if len(data) % AES_BLOCK:
        raise PakError("AES 数据长度不是 16 的倍数，无法解密")
    from Cryptodome.Cipher import AES

    return AES.new(key, AES.MODE_ECB).decrypt(data)


def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    from Cryptodome.Cipher import AES

    return AES.new(key, AES.MODE_ECB).encrypt(data)


def parse_key(text: str) -> bytes:
    """把 0xHEX / 裸 HEX / base64 形式的 AES-256 密钥转成 32 字节。"""
    s = text.strip().replace(" ", "").replace("-", "")
    if s.lower().startswith("0x"):
        s = s[2:]
    try:
        if len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s):
            key = bytes.fromhex(s)
        else:
            key = base64.b64decode(s, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise PakError(f"无法解析 AES 密钥: {text!r} ({exc})") from exc
    if len(key) != 32:
        raise PakError(f"AES-256 密钥必须是 32 字节，实际 {len(key)} 字节")
    return key


class KeyStore:
    """从 FModel 的 keys.json 载入主密钥与按 GUID 索引的动态密钥。"""

    def __init__(self, path: str | None = None):
        self.main: bytes | None = None
        self.dynamic: dict[str, bytes] = {}
        self.all: list[bytes] = []
        if not path:
            return
        with open(path, "r", encoding="utf-8-sig") as fp:
            doc = json.load(fp)
        if isinstance(doc, dict):
            if doc.get("mainKey"):
                self.main = parse_key(doc["mainKey"])
            for item in doc.get("dynamicKeys", []):
                guid = str(item.get("guid", "")).upper()
                key = item.get("key")
                if guid and key:
                    self.dynamic[guid] = parse_key(key)
        elif isinstance(doc, list):
            for item in doc:
                guid = str(item.get("guid", "")).upper()
                key = item.get("key")
                if guid and key:
                    self.dynamic[guid] = parse_key(key)
        else:
            raise PakError("无法识别的 keys.json 结构")
        self.all = ([self.main] if self.main else []) + list(self.dynamic.values())

    def guid_forms(self, raw: bytes) -> list[str]:
        """FGuid 在 keys.json 里可能以两种字节序书写，两种都尝试。"""
        u = struct.unpack("<4I", raw)
        return [
            raw.hex().upper(),
            "".join(f"{x:08X}" for x in u),
        ]

    def by_guid(self, raw: bytes | None) -> bytes | None:
        if not raw or raw == b"\0" * 16:
            return self.main
        for form in self.guid_forms(raw):
            key = self.dynamic.get(form)
            if key:
                return key
        return self.main


class Cursor:
    """对小端 bytes 的顺序读取游标。"""

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.p = pos

    def raw(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.data):
            raise PakError(f"索引数据越界: 需要 {n} 字节，剩余 {len(self.data) - self.p}")
        out = self.data[self.p : self.p + n]
        self.p += n
        return out

    def u8(self) -> int:
        return self.raw(1)[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.raw(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.raw(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.raw(8))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.raw(8))[0]

    def fstring(self) -> str:
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:
            raw = self.raw(-n * 2)
            return raw.decode("utf-16-le", "replace").rstrip("\x00")
        raw = self.raw(n)
        return raw.decode("latin-1", "replace").rstrip("\x00")

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > len(self.data):
            raise PakError(f"索引 seek 越界: {pos}")
        self.p = pos

    @property
    def remaining(self) -> int:
        return len(self.data) - self.p


@dataclass
class Footer:
    version: int
    index_offset: int
    index_size: int
    index_hash: bytes
    encrypted: bool
    guid: bytes | None
    frozen: bool
    compression: list[str | None]
    size: int


@dataclass
class PakEntry:
    path: str = ""
    offset: int = 0
    compressed: int = 0
    uncompressed: int = 0
    method: str | None = None
    encrypted: bool = False
    blocks: list[tuple[int, int]] = field(default_factory=list)
    block_size: int = 0
    struct_size: int = 53
    wuwa_limit: int | None = None  # 鸣潮部分加密：仅前 N 字节加密

    @property
    def size(self) -> int:
        return self.uncompressed

    @property
    def is_compressed(self) -> bool:
        return self.method is not None or bool(self.blocks)

    def describe(self) -> str:
        tags = []
        if self.encrypted:
            tags.append("加密")
        if self.method:
            tags.append(self.method)
        return "/".join(tags) or "原始"


def _read_at(fp: BinaryIO, offset: int, size: int) -> bytes:
    fp.seek(offset)
    data = fp.read(size)
    if len(data) != size:
        raise PakError(f"读取 {size} 字节失败（偏移 {offset}，实际 {len(data)}）")
    return data


def read_footer(fp: BinaryIO, file_size: int) -> Footer:
    """从文件尾部定位并解析 FPakInfo。

    footer 位于文件最末端，各版本字段按「前缀 + 固定 44 字节 + 压缩方法名槽」
    排列；通过 magic 位置和文件末尾反推槽位数量，从而无需枚举每个版本尺寸。
    """
    tail_len = min(256, file_size)
    tail = _read_at(fp, file_size - tail_len, tail_len)
    magic_le = struct.pack("<I", MAGIC)
    candidates = []
    start = 0
    while True:
        i = tail.find(magic_le, start)
        if i < 0:
            break
        candidates.append(file_size - tail_len + i)
        start = i + 1

    for magic_pos in candidates:
        if magic_pos + 44 > file_size:
            continue
        version = struct.unpack("<I", _read_at(fp, magic_pos + 4, 4))[0]
        if not (1 <= version <= 20):
            continue
        index_offset = struct.unpack("<Q", _read_at(fp, magic_pos + 8, 8))[0]
        index_size = struct.unpack("<Q", _read_at(fp, magic_pos + 16, 8))[0]
        index_hash = _read_at(fp, magic_pos + 24, 20)
        if index_offset > file_size or index_offset + index_size > file_size:
            continue

        prefix = (16 if version >= V_ENCRYPTION_KEY_GUID else 0) + (
            1 if version >= V_INDEX_ENCRYPTION else 0
        )
        if magic_pos - prefix < 0:
            continue
        head = _read_at(fp, magic_pos - prefix, prefix)
        guid = head[:16] if version >= V_ENCRYPTION_KEY_GUID else None
        encrypted = bool(head[-1]) if version >= V_INDEX_ENCRYPTION else False

        frozen_extra = 1 if version == V_FROZEN_INDEX else 0
        rest = file_size - (magic_pos + 44) - frozen_extra
        if rest < 0 or rest % 32:
            continue
        slots = rest // 32
        if slots not in (0, 4, 5):
            continue
        frozen = False
        if frozen_extra:
            frozen = bool(_read_at(fp, magic_pos + 44, 1)[0])
        names: list[str | None] = []
        blob = _read_at(fp, magic_pos + 44 + frozen_extra, rest)
        for i in range(slots):
            name = blob[i * 32 : (i + 1) * 32].split(b"\x00")[0].decode("latin-1").strip()
            names.append(name or None)
        if version < V_FNAME_BASED_COMPRESSION:
            names = ["Zlib", "Gzip", "Oodle"]
        footer_size = prefix + 44 + frozen_extra + rest
        return Footer(
            version=version,
            index_offset=index_offset,
            index_size=index_size,
            index_hash=index_hash,
            encrypted=encrypted,
            guid=guid,
            frozen=frozen,
            compression=names,
            size=footer_size,
        )
    raise PakError("未找到合法的 pak footer（magic 0x5A6F12E1 或版本信息异常）")


def _wuwa_bitfield(bits: int) -> int:
    """鸣潮 v12 对编码条目位域做了位重排（CUE4Parse / GAME_WutheringWaves）。"""
    return (
        ((bits >> 16) & 0x3F)
        | ((bits & 0xFFFF) << 6)
        | ((bits & (1 << 28)) >> 6)
        | ((bits & 0x0FC00000) << 1)
        | ((bits & 0xC0000000) >> 1)
        | ((bits & 0x20000000) << 2)
    ) & 0xFFFFFFFF


WUWA_LIMITS = {0: None, 1: 0x200000, 2: 0x800, 4: 0}  # None = 全量加密


def decode_encoded_entry(cur: Cursor, version: int, wuwa_v12: bool) -> PakEntry:
    """解码 v10+ 的位编码 FPakEntry。"""
    bits = cur.u32()
    custom = None
    if wuwa_v12:
        bits = _wuwa_bitfield(bits)
        custom = cur.u8()

    method_index = (bits >> 23) & 0x3F
    encrypted = bool(bits & (1 << 22))
    block_count = (bits >> 6) & 0xFFFF
    block_size = bits & 0x3F
    if block_size == 0x3F:
        block_size = cur.u32()
    else:
        block_size <<= 11

    def var_int(bit: int) -> int:
        return cur.u32() if bits & (1 << bit) else cur.u64()

    offset = var_int(31)
    uncompressed = var_int(30)
    compressed = var_int(29) if method_index else None
    if wuwa_v12:
        # 鸣潮把前两个 32/64 位字段写反了，需对调后再确定存储大小
        offset, uncompressed = uncompressed, offset
    if compressed is None:
        compressed = uncompressed

    struct_size = 8 * 3 + 4 * 2 + 1 + 20
    blocks: list[tuple[int, int]] = []
    if method_index:
        struct_size += 4 + 16 * block_count
        if block_count == 1 and not encrypted:
            blocks = [(struct_size, struct_size + compressed)]
        elif block_count > 0:
            index = struct_size
            for _ in range(block_count):
                length = cur.u32()
                blocks.append((index, index + length))
                index += align(length) if encrypted else length

    limit = None
    if wuwa_v12 and custom is not None and encrypted:
        if custom not in WUWA_LIMITS:
            raise PakError(f"未知的鸣潮 CustomData 值 {custom}，无法判断部分加密长度")
        limit = WUWA_LIMITS[custom]

    return PakEntry(
        offset=offset,
        compressed=compressed,
        uncompressed=uncompressed,
        method=None if method_index == 0 else f"slot-{method_index - 1}",
        encrypted=encrypted,
        blocks=blocks,
        block_size=block_size,
        struct_size=struct_size,
        wuwa_limit=limit,
    )


def read_full_entry(cur: Cursor, version: int, u8_index: bool = False) -> PakEntry:
    """读取索引/内联头里的完整 FPakEntry。"""
    offset = cur.i64()
    compressed = cur.i64()
    uncompressed = cur.i64()
    method = cur.u8() if u8_index else cur.u32()
    if version == V_INITIAL:
        cur.i64()  # Timestamp
    cur.raw(20)  # SHA1
    blocks: list[tuple[int, int]] = []
    flags = 0
    block_size = 0
    struct_size = 8 * 3 + (1 if u8_index else 4) + (8 if version == V_INITIAL else 0) + 20
    if version >= V_COMPRESSION_ENCRYPTION:
        if method:
            count = cur.u32()
            blocks = [(cur.u64(), cur.u64()) for _ in range(count)]
            struct_size += 4 + 16 * count
        flags = cur.u8()
        block_size = cur.u32()
        struct_size += 1 + 4
    return PakEntry(
        offset=offset,
        compressed=compressed,
        uncompressed=uncompressed,
        method=None if method == 0 else f"slot-{method - 1}",
        encrypted=bool(flags & 1),
        blocks=blocks,
        block_size=block_size,
        struct_size=struct_size,
    )


class PakFile:
    """打开一个 pak 并按需读取其中的文件。"""

    def __init__(
        self,
        path: str,
        key: bytes | None = None,
        keystore: KeyStore | None = None,
        legacy_v12: bool = False,
    ):
        self.path = path
        self.fp = open(path, "rb")
        try:
            self.fp.seek(0, 2)
            self.file_size = self.fp.tell()
            self.footer = read_footer(self.fp, self.file_size)
            self.version = self.footer.version
            self.wuwa_v12 = self.version > V_FNV64_BUGFIX and not legacy_v12
            # v8A（UE 4.23~4.24）只有 4 个压缩方法槽，且条目用 u8 存方法索引
            self.u8_compression_index = (
                self.version == V_FNAME_BASED_COMPRESSION
                and len(self.footer.compression) == 4
            )
            self.keystore = keystore
            self.key = key
            self.mount_point = ""
            self.entries: dict[str, PakEntry] = {}
            self._index_hash_ok = False
            self._load_index()
        except BaseException:
            self.fp.close()
            raise

    def close(self) -> None:
        self.fp.close()

    def __enter__(self) -> "PakFile":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # -- 索引 -------------------------------------------------------
    def _decrypt_index(self, raw: bytes) -> bytes:
        if not self.footer.encrypted:
            return raw
        candidates: list[bytes] = []
        if self.key:
            candidates.append(self.key)
        elif self.keystore:
            matched = self.keystore.by_guid(self.footer.guid)
            if matched:
                candidates.append(matched)
            candidates.extend(k for k in self.keystore.all if k not in candidates)
        if not candidates:
            raise PakError(
                "索引已加密但未提供密钥。请用 --aes-key 指定，或用 --keys 载入 keys.json"
            )
        for cand in candidates:
            plain = aes_ecb_decrypt(cand, raw)
            if hashlib.sha1(plain).digest() == self.footer.index_hash:
                self.key = cand
                return plain
        raise PakError(
            "索引解密失败：提供的密钥均不匹配（SHA1 校验未通过）。"
            "请确认 AES 密钥，或检查 keys.json 是否包含该 pak 的 GUID 密钥。"
        )

    def _secondary(self, desc: tuple[int, int, bytes] | None) -> bytes | None:
        if not desc:
            return None
        off, size, expected = desc
        raw = _read_at(self.fp, off, size)
        if self.footer.encrypted:
            raw = aes_ecb_decrypt(self.key, raw)
        if hashlib.sha1(raw).digest() != expected:
            raise PakError(f"二级索引 SHA1 校验失败（偏移 {off}）")
        return raw

    def _descriptor(self, cur: Cursor) -> tuple[int, int, bytes] | None:
        if not cur.u32():
            return None
        return (cur.i64(), cur.i64(), cur.raw(20))

    def _parse_directory_index(self, data: bytes) -> list[tuple[str, str, int]]:
        cur = Cursor(data)
        out: list[tuple[str, str, int]] = []
        for _ in range(cur.u32()):
            directory = cur.fstring()
            for _ in range(cur.u32()):
                name = cur.fstring()
                location = cur.i32()
                out.append((directory, name, location))
        return out

    def _load_index(self) -> None:
        f = self.footer
        raw = _read_at(self.fp, f.index_offset, f.index_size)
        plain = self._decrypt_index(raw)
        self._index_hash_ok = hashlib.sha1(plain).digest() == f.index_hash
        cur = Cursor(plain)
        self.mount_point = cur.fstring()
        num_entries = cur.u32()

        if self.version >= V_PATH_HASH_INDEX:
            cur.u64()  # PathHashSeed
            self._descriptor(cur)  # PathHashIndex（仅校验，名称来自目录索引）
            fdi_desc = self._descriptor(cur)
            encoded_size = cur.u32()
            encoded = cur.raw(encoded_size)
            non_count = cur.u32()
            non_entries = [
                read_full_entry(cur, self.version, self.u8_compression_index)
                for _ in range(non_count)
            ]
            fdi = self._secondary(fdi_desc)
            if fdi is None:
                raise PakError("该 pak 没有 Full Directory Index（可能只含哈希文件名），无法列出文件名")
            for directory, name, location in self._parse_directory_index(fdi):
                if location == INT32_MIN:
                    continue
                if location >= 0:
                    ec = Cursor(encoded, location)
                    entry = decode_encoded_entry(ec, self.version, self.wuwa_v12)
                else:
                    idx = -location - 1
                    if idx >= len(non_entries):
                        continue
                    entry = non_entries[idx]
                path = self._join_path(directory, name)
                entry.path = path
                entry.method = self._method_name(entry.method)
                self.entries[path] = entry
        elif self.version == V_FROZEN_INDEX:
            raise PakError("pak v9 (FrozenIndex) 暂不支持，请换用未冻结索引的 pak")
        else:
            for _ in range(num_entries):
                path = cur.fstring()
                entry = read_full_entry(cur, self.version, self.u8_compression_index)
                entry.path = path
                entry.method = self._method_name(entry.method)
                self.entries[path] = entry

        if not self._index_hash_ok:
            # 已经解密成功（否则 _decrypt_index 会抛错），仅提示
            pass

    def _join_path(self, directory: str, name: str) -> str:
        prefix = directory.lstrip("/")
        return f"{self.mount_point}{prefix}{name}"

    def _method_name(self, slot: str | None) -> str | None:
        if slot is None:
            return None
        idx = int(slot.split("-")[1])
        methods = self.footer.compression
        if 0 <= idx < len(methods):
            return methods[idx]
        return f"unknown-slot-{idx}"

    # -- 文件内容 ---------------------------------------------------
    def _decompress(self, data: bytes, method: str | None) -> bytes:
        if not method:
            return data
        name = method.lower()
        if name == "zlib":
            return zlib.decompress(data)
        if name == "gzip":
            return gzip.decompress(data)
        if name in ("zstd", "zstandard"):
            try:
                import zstandard

                return zstandard.ZstdDecompressor().decompress(data)
            except ImportError as exc:
                raise PakError("该 pak 使用 Zstd 压缩，请安装 zstandard 模块") from exc
        if name == "lz4":
            try:
                import lz4.block

                return lz4.block.decompress(data, uncompressed_size=self._expected_out)
            except ImportError as exc:
                raise PakError("该 pak 使用 LZ4 压缩，请安装 lz4 模块") from exc
        if "oodle" in name:
            raise PakError(
                "该 pak 使用 Oodle 压缩，需要游戏自带的 oo2core 动态库；本工具未内置。"
                "可先用 UnrealPak/repak 解包，或仅提取原始 .uasset 文件。"
            )
        raise PakError(f"不支持的压缩方式: {method}")

    def read(self, entry: PakEntry) -> bytes:
        """读取并还原（解密 + 解压）单个文件的原始字节。"""
        f = self.fp
        # 文件数据区前面有一份内联 FPakEntry，解析它以获得真实头长度
        header_len = min(256, self.file_size - entry.offset)
        header = _read_at(f, entry.offset, header_len)
        hc = Cursor(header)
        read_full_entry(hc, self.version, self.u8_compression_index)
        header_size = hc.p
        data_start = entry.offset + header_size

        need = entry.compressed or entry.uncompressed
        if need <= 0:
            return b""
        read_size = align(need) if entry.encrypted else need
        raw = _read_at(f, data_start, read_size)
        self._expected_out = entry.uncompressed

        if entry.encrypted:
            if entry.wuwa_limit is not None:
                # 鸣潮部分加密：仅前 limit 字节是 AES 密文，其余为明文
                enc_len = min(read_size, entry.wuwa_limit)
                enc_len = align(enc_len)
                raw = aes_ecb_decrypt(self.key, raw[:enc_len]) + raw[enc_len:]
            else:
                raw = aes_ecb_decrypt(self.key, raw)
        raw = raw[:need]

        if entry.blocks:
            parts = []
            for start, end in entry.blocks:
                if self.version >= V_RELATIVE_CHUNK_OFFSETS:
                    s = start - header_size
                    e = end - header_size
                else:
                    s = start - data_start
                    e = end - data_start
                parts.append(self._decompress(raw[s:e], entry.method))
            out = b"".join(parts)
        else:
            out = self._decompress(raw, entry.method)
        if len(out) != entry.uncompressed:
            raise PakError(
                f"{entry.path}: 解压后大小 {len(out)} 与索引记录 {entry.uncompressed} 不符"
            )
        return out

    def output_relpath(self, path: str) -> str:
        """把 pak 内虚拟路径转成安全的输出相对路径。"""
        rel = path[len(self.mount_point) :] if self.mount_point and path.startswith(self.mount_point) else path
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".", "..")]
        return "/".join(parts) if parts else "_unnamed"
