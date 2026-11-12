"""paktex 自测：合成夹具 + 用户提供的真实 pak 校验。

运行: python tests/test_all.py
"""
from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Cryptodome.Cipher import AES  # noqa: E402

from paktex import texture, uasset  # noqa: E402
from paktex.core import KeyStore, PakFile, aes_ecb_encrypt  # noqa: E402

REAL_PAK = os.path.join(ROOT, "Video_319_0-HD-WindowsNoEditor.pak")
REAL_KEYS = os.path.join(ROOT, "wuwa-keys-main", "keys.json")
REAL_GUID = bytes.fromhex("62608ba1c442e8e9391f4ea367814c91")
REAL_KEY = bytes.fromhex(
    "80112C0F1746E98F5D336A3BE5D08BAFD597DCB96983AF06DBF4CB1B7CDDC869"
)
PAK_MAGIC = 0x5A6F12E1
BD_PAYLOAD_AT_END = 1 << 0
BD_FORCE_INLINE = 1 << 6
BD_PAYLOAD_SEPARATE = 1 << 8


def fstring(s: str) -> bytes:
    raw = s.encode("latin-1") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def pad16(data: bytes) -> bytes:
    return data + b"\x00" * ((-len(data)) % 16)


def inline_entry(size: int, encrypted: bool = False) -> bytes:
    """每个文件数据区前面的 53 字节内联 FPakEntry（v3+ / v12）。"""
    out = struct.pack("<QQQ", 0, size, size)
    out += struct.pack("<I", 0)  # 压缩方法索引 0 = None
    out += hashlib.sha1(b"payload").digest()  # 占位 hash（20 字节）
    out += struct.pack("<BI", 1 if encrypted else 0, 0)
    assert len(out) == 53
    return out


def encoded_v12(size: int, data_offset: int, custom: int, encrypted: bool) -> bytes:
    """构造鸣潮 v12 位编码条目。

    0xE0000000 / 0xF0000000 经 CUE4Parse 位重排后分别得到
    0xE0000000 / 0xE0400000（后者带加密位）；随后 CustomData 一字节，
    两个 u32 字段在解码时会互换，故先写 size 再写 offset。
    """
    bits = 0xF0000000 if encrypted else 0xE0000000
    return struct.pack("<I", bits) + bytes([custom]) + struct.pack("<II", size, data_offset)


def build_v12_pak(files: list[dict], key: bytes, guid: bytes) -> bytes:
    """files: [{name, data, encrypted, custom}]"""
    # 1) 数据区
    data_region = bytearray()
    offsets = []
    stored_sizes = []
    for f in files:
        data = f["data"]
        offsets.append(len(data_region))
        if f["encrypted"]:
            padded = pad16(data)
            limit = {0: None, 1: 0x200000, 2: 0x800, 4: 0}[f["custom"]]
            if limit is None:
                blob = aes_ecb_encrypt(key, padded)
            else:
                n = min((limit + 15) & ~15, len(padded))
                blob = aes_ecb_encrypt(key, padded[:n]) + padded[n:]
        else:
            blob = data
        data_region += inline_entry(len(data), f["encrypted"])
        data_region += blob
        stored_sizes.append(len(data))

    # 2) 编码条目 / 目录索引 / 路径哈希索引
    encoded = bytearray()
    locations = []
    for i, f in enumerate(files):
        locations.append(len(encoded))
        encoded += encoded_v12(stored_sizes[i], offsets[i], f["custom"], f["encrypted"])

    phi = struct.pack("<I", len(files))
    for i in range(len(files)):
        phi += struct.pack("<Qi", 0x1000 + i, locations[i])
    phi += struct.pack("<I", 0)
    phi = pad16(phi)

    fdi = struct.pack("<I", 1) + fstring("/") + struct.pack("<I", len(files))
    for i, f in enumerate(files):
        fdi += fstring(f["name"]) + struct.pack("<i", locations[i])
    fdi = pad16(fdi)

    # 3) 主索引（descriptor 里的偏移需要回填）
    mount = "../../../Project/Content/Textures/"
    head = fstring(mount) + struct.pack("<I", len(files)) + struct.pack("<Q", 0x12345678)
    encoded_size_part = struct.pack("<I", len(encoded)) + bytes(encoded) + struct.pack("<I", 0)
    descriptor_len = 4 + 8 + 8 + 20
    index_len_placeholder = len(head) + descriptor_len * 2 + len(encoded_size_part)
    index_padded_len = (index_len_placeholder + 15) & ~15
    index_offset = len(data_region)
    phi_offset = index_offset + index_padded_len
    fdi_offset = phi_offset + len(phi)

    index_plain = bytearray(head)
    index_plain += struct.pack("<I", 1) + struct.pack("<QQ", phi_offset, len(phi)) + hashlib.sha1(phi).digest()
    index_plain += struct.pack("<I", 1) + struct.pack("<QQ", fdi_offset, len(fdi)) + hashlib.sha1(fdi).digest()
    index_plain += encoded_size_part
    index_plain = pad16(bytes(index_plain))
    assert len(index_plain) == index_padded_len

    # 4) footer
    footer = guid + b"\x01"  # EncryptionKeyGuid + bEncryptedIndex
    footer += struct.pack("<I", PAK_MAGIC)
    footer += struct.pack("<I", 12)
    footer += struct.pack("<QQ", index_offset, len(index_plain))
    footer += hashlib.sha1(index_plain).digest()
    footer += b"\x00" * (5 * 32)  # 5 个空的压缩方法槽
    assert len(footer) == 221

    blob = bytes(data_region) + aes_ecb_encrypt(key, index_plain) + aes_ecb_encrypt(key, phi) + aes_ecb_encrypt(key, fdi) + footer
    return blob


def build_v7_pak(files: list[dict]) -> bytes:
    """旧版索引格式（无路径哈希索引 / 无编码条目），不加密。"""
    data_region = bytearray()
    offsets = []
    for f in files:
        data = f["data"]
        offsets.append(len(data_region))
        data_region += inline_entry(len(data)) + data

    mount = "../../../Project/Content/"
    index = bytearray(fstring(mount) + struct.pack("<I", len(files)))
    for i, f in enumerate(files):
        path = mount + f["name"]
        index += fstring(path)
        index += struct.pack("<QQQ", offsets[i], len(f["data"]), len(f["data"]))
        index += struct.pack("<I", 0) + hashlib.sha1(b"x").digest()
        index += struct.pack("<BI", 0, 0)

    footer = bytes(16) + b"\x00"  # guid + bEncryptedIndex=false
    footer += struct.pack("<I", PAK_MAGIC) + struct.pack("<I", 7)
    footer += struct.pack("<QQ", len(data_region), len(index))
    footer += hashlib.sha1(bytes(index)).digest()
    assert len(footer) == 61
    return bytes(data_region) + bytes(index) + footer


def build_texture_asset(width: int, height: int, fmt: str, pixels: bytes, target: str):
    """构造一个最小的 cooked UTexture2D：.uasset(名称表) + .uexp(平台数据) + .ubulk。"""
    uasset_bytes = struct.pack("<I", 0x9E2A83C1) + b"\x00" * 16 + b"Texture2D\x00" + b"\x00" * 16
    fpd = struct.pack("<iiI", width, height, 1) + fstring(fmt) + struct.pack("<ii", 0, 1)
    bulk = struct.pack("<IIiQ", BD_PAYLOAD_SEPARATE if target == "ubulk" else BD_FORCE_INLINE,
                       len(pixels), len(pixels), 0)
    if target == "ubulk":
        fpd += bulk + struct.pack("<iii", width, height, 1)
        ubulk = pixels
    else:
        fpd += bulk + pixels + struct.pack("<iii", width, height, 1)
        ubulk = None
    uexp = b"\x00" * 32 + fpd
    return uasset_bytes, uexp, ubulk


class TestSyntheticPak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="paktex_test_", dir=ROOT)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fp:
            fp.write(data)
        return path

    def test_v12_encrypted_key_by_guid(self):
        payloads = [
            {"name": "A.bin", "data": b"hello-paktex" * 100, "encrypted": False, "custom": 4},
            {"name": "B.bin", "data": bytes(range(256)) * 20, "encrypted": True, "custom": 2},
            {"name": "C.bin", "data": b"Q" * 3000, "encrypted": True, "custom": 0},
        ]
        blob = build_v12_pak(payloads, REAL_KEY, REAL_GUID)
        path = self._write("v12.pak", blob)
        ks = KeyStore(REAL_KEYS)
        with PakFile(path, keystore=ks) as pak:
            self.assertEqual(pak.version, 12)
            self.assertTrue(pak.wuwa_v12)
            self.assertTrue(pak.footer.encrypted)
            self.assertTrue(pak._index_hash_ok)
            self.assertEqual(len(pak.entries), 3)
            for item in payloads:
                entry = pak.entries[f"{pak.mount_point}{item['name']}"]
                self.assertEqual(pak.read(entry), item["data"], item["name"])

    def test_v12_explicit_key(self):
        key = bytes(range(32))
        data = b"explicit-key" * 400
        blob = build_v12_pak(
            [{"name": "X.uasset", "data": data, "encrypted": True, "custom": 1}],
            key,
            b"\x00" * 16,
        )
        path = self._write("v12_explicit.pak", blob)
        with PakFile(path, key=key) as pak:
            self.assertTrue(pak._index_hash_ok)
            self.assertEqual(pak.read(next(iter(pak.entries.values()))), data)

    def test_v7_legacy_index(self):
        data = b"legacy-index-data" * 50
        blob = build_v7_pak([{"name": "old.uasset", "data": data}])
        path = self._write("v7.pak", blob)
        with PakFile(path) as pak:
            self.assertEqual(pak.version, 7)
            self.assertFalse(pak.wuwa_v12)
            self.assertEqual(len(pak.entries), 1)
            self.assertEqual(pak.read(next(iter(pak.entries.values()))), data)

    def test_cli_all_end_to_end(self):
        from PIL import Image

        from paktex import cli

        w = h = 8
        pixels = bytearray()
        for y in range(h):
            for x in range(w):
                pixels += bytes((x * 32, y * 32, 128, 255))
        asset, uexp, ubulk = build_texture_asset(w, h, "PF_B8G8R8A8", bytes(pixels), "ubulk")
        files = [
            {"name": "T_Grad.uasset", "data": asset, "encrypted": False, "custom": 4},
            {"name": "T_Grad.uexp", "data": uexp, "encrypted": False, "custom": 4},
            {"name": "T_Grad.ubulk", "data": ubulk, "encrypted": False, "custom": 4},
        ]
        path = self._write("textures.pak", build_v12_pak(files, REAL_KEY, REAL_GUID))
        out = os.path.join(self.tmp, "out")
        code = cli.main(["all", path, "-o", out, "--keys", REAL_KEYS])
        self.assertEqual(code, 0)
        # 原始文件
        for f in files:
            with open(os.path.join(out, f["name"]), "rb") as fp:
                self.assertEqual(fp.read(), f["data"])
        # PNG
        png = os.path.join(out, "T_Grad.png")
        self.assertTrue(os.path.isfile(png))
        with Image.open(png) as im:
            self.assertEqual(im.mode, "RGBA")
            self.assertEqual(im.getpixel((3, 5)), (128, 5 * 32, 3 * 32, 255))

    def test_wrong_key_fails_cleanly(self):
        blob = build_v12_pak(
            [{"name": "A.bin", "data": b"x" * 64, "encrypted": False, "custom": 4}],
            REAL_KEY,
            REAL_GUID,
        )
        path = self._write("badkey.pak", blob)
        try:
            with PakFile(path, key=bytes(range(32))):
                self.fail("错误密钥应当抛错")
        except Exception as exc:
            self.assertIn("解密失败", str(exc))


class TestTexture(unittest.TestCase):
    def test_bgra_gradient_ubulk(self):
        w = h = 8
        pixels = bytearray()
        for y in range(h):
            for x in range(w):
                pixels += bytes((x * 32, y * 32, 128, 255))  # BGRA
        asset, uexp, ubulk = build_texture_asset(w, h, "PF_B8G8R8A8", bytes(pixels), "ubulk")
        self.assertTrue(uasset.looks_like_texture2d(asset))
        td = uasset.parse_texture(asset, uexp, ubulk)
        self.assertIsNotNone(td)
        self.assertEqual((td.width, td.height, td.pixel_format), (8, 8, "PF_B8G8R8A8"))
        self.assertEqual(len(td.mip_data), texture.expected_size(8, 8, "PF_B8G8R8A8"))
        im = texture.decode(td.width, td.height, td.pixel_format, td.mip_data)
        self.assertEqual(im.mode, "RGBA")
        self.assertEqual(im.getpixel((3, 5)), (128, 5 * 32, 3 * 32, 255))

    def test_bc1_inline(self):
        w = h = 8
        red_block = struct.pack("<HHI", 0xF800, 0, 0)  # color0=红, 索引全 0
        pixels = red_block * 4  # 2x2 个块
        asset, uexp, ubulk = build_texture_asset(w, h, "PF_DXT1", pixels, "inline")
        td = uasset.parse_texture(asset, uexp, None)
        self.assertIsNotNone(td)
        self.assertEqual(td.pixel_format, "PF_DXT1")
        self.assertEqual(len(td.mip_data), 32)
        im = texture.decode(td.width, td.height, td.pixel_format, td.mip_data)
        self.assertEqual(im.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(im.getpixel((7, 7)), (255, 0, 0, 255))

    def test_pillow_dds_fallback(self):
        w = h = 4
        block = struct.pack("<HHI", 0xF800, 0, 0)
        original = texture._T2D
        try:
            texture._T2D = None
            im = texture.decode(w, h, "PF_DXT1", block)
            self.assertEqual(im.getpixel((0, 0)), (255, 0, 0, 255))
        finally:
            texture._T2D = original

    def test_format_sizes(self):
        self.assertEqual(texture.expected_size(8, 8, "PF_DXT1"), 32)
        self.assertEqual(texture.expected_size(8, 8, "PF_B8G8R8A8"), 256)
        self.assertEqual(texture.expected_size(12, 12, "PF_ASTC_6x6"), 4 * 16)
        with self.assertRaises(texture.TextureError):
            texture.get_format("PF_NotAFormat")

    def test_non_texture_returns_none(self):
        self.assertIsNone(uasset.parse_texture(b"not an asset at all"))


class TestRealPak(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(REAL_PAK), "未提供真实 pak")
    def test_real_pak_end_to_end(self):
        ks = KeyStore(REAL_KEYS)
        with PakFile(REAL_PAK, keystore=ks) as pak:
            self.assertEqual(pak.version, 12)
            self.assertTrue(pak.footer.encrypted)
            self.assertTrue(pak._index_hash_ok)
            self.assertEqual(pak.mount_point, "../../../Client/Content/Aki/Movies/LevelB_Seq/02_7/")
            self.assertEqual(len(pak.entries), 1)
            entry = next(iter(pak.entries.values()))
            self.assertEqual(entry.path.endswith("M2_12_07_Nv.mp4"), True)
            self.assertEqual(entry.offset, 0)
            self.assertEqual(entry.uncompressed, 404956207)
            self.assertEqual(entry.compressed, 404956207)
            self.assertEqual(pak.output_relpath(entry.path), "M2_12_07_Nv.mp4")
            # 分块校验整个载荷，避免一次把 386MB 读进内存
            h = hashlib.sha1()
            pak.fp.seek(53)
            remaining = entry.uncompressed
            while remaining:
                chunk = pak.fp.read(min(1 << 20, remaining))
                h.update(chunk)
                remaining -= len(chunk)
            self.assertEqual(h.hexdigest(), "26f6a4167589f8e5258912a872acf7d2a6992e23")


if __name__ == "__main__":
    unittest.main(verbosity=2)
