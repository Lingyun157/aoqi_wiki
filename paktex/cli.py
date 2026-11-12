"""paktex 命令行入口。"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys

from . import texture, uasset
from .core import KeyStore, PakError, PakFile, parse_key


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paktex",
        description="解包 Unreal Engine .pak，并把 UTexture2D 的 .uasset 导出为 PNG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python paktex.py list Game.pak --keys keys.json\n"
            "  python paktex.py extract Game.pak -o out --keys keys.json --filter '*.uasset'\n"
            "  python paktex.py png Game.pak -o out --keys keys.json\n"
            "  python paktex.py png out/ -o png_out          # 对已解包的目录\n"
            "  python paktex.py all Game.pak -o out --keys keys.json\n"
        ),
    )
    p.add_argument("command", choices=["list", "info", "extract", "png", "all"])
    p.add_argument("path", help=".pak 文件，或（png 子命令）已解包的目录")
    p.add_argument("-o", "--out", default="output", help="输出目录（默认 output）")
    p.add_argument("--keys", help="FModel 格式的 keys.json（含 mainKey / dynamicKeys）")
    p.add_argument("--aes-key", help="直接指定 AES-256 密钥（0xHEX 或 base64）")
    p.add_argument("--filter", default="*", help="路径通配符过滤，如 '*.uasset'（默认全部）")
    p.add_argument("--limit", type=int, default=0, help="最多处理 N 个文件（0=不限）")
    p.add_argument("--legacy-v12", action="store_true", help="禁用鸣潮 v12 索引条目的特殊处理")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _match(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern)


def _open_pak(args) -> PakFile:
    ks = KeyStore(args.keys) if args.keys else None
    key = parse_key(args.aes_key) if args.aes_key else None
    return PakFile(args.path, key=key, keystore=ks, legacy_v12=args.legacy_v12)


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(data)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def cmd_list(pak: PakFile, args) -> int:
    print(f"pak      : {pak.path}")
    print(f"版本     : v{pak.version}" + ("（鸣潮 / UE5.7 变体）" if pak.wuwa_v12 else ""))
    print(f"大小     : {_human(pak.file_size)}")
    print(f"索引加密 : {'是' if pak.footer.encrypted else '否'}")
    print(f"挂载点   : {pak.mount_point}")
    print(f"压缩方式 : {', '.join(m or 'None' for m in pak.footer.compression) or '（旧版固定表）'}")
    print(f"文件数   : {len(pak.entries)}")
    print("-" * 72)
    for path, e in sorted(pak.entries.items()):
        if not _match(path, args.filter):
            continue
        print(f"  {_human(e.size):>9}  {e.describe():<12}  {pak.output_relpath(path)}")
    return 0


def cmd_extract(pak: PakFile, args) -> int:
    selected = [
        (p, e)
        for p, e in sorted(pak.entries.items())
        if _match(p, args.filter)
    ]
    if args.limit:
        selected = selected[: args.limit]
    print(f"提取 {len(selected)} 个文件 -> {args.out}")
    total = 0
    for path, entry in selected:
        rel = pak.output_relpath(path)
        dest = os.path.join(args.out, rel)
        data = pak.read(entry)
        _write(dest, data)
        total += len(data)
        if args.verbose:
            print(f"  {rel}  ({_human(len(data))})")
    print(f"完成，共 {_human(total)}")
    return 0


def _png_from_entry(pak: PakFile, entry, args, by_path: dict) -> tuple[bool, str]:
    path = entry.path
    stem = path[: -len(".uasset")]
    uexp_entry = by_path.get(stem + ".uexp")
    ubulk_entry = by_path.get(stem + ".ubulk")
    asset = pak.read(entry)
    uexp = pak.read(uexp_entry) if uexp_entry else None
    ubulk = pak.read(ubulk_entry) if ubulk_entry else None
    td = uasset.parse_texture(asset, uexp, ubulk, name=path)
    if td is None:
        return False, "非 UTexture2D 或结构不兼容"
    image = texture.decode(td.width, td.height, td.pixel_format, td.mip_data)
    rel = pak.output_relpath(path)
    dest = os.path.join(args.out, rel[: -len(".uasset")] + ".png")
    texture.save_png(image, dest)
    return True, f"{td.width}x{td.height} {td.pixel_format} -> {dest}"


def _png_from_dir(root: str, args) -> int:
    done = skipped = failed = 0
    for cur, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.lower().endswith(".uasset"):
                continue
            path = os.path.join(cur, name)
            stem = path[: -len(".uasset")]
            with open(path, "rb") as fp:
                asset = fp.read()
            uexp = ubulk = None
            if os.path.isfile(stem + ".uexp"):
                with open(stem + ".uexp", "rb") as fp:
                    uexp = fp.read()
            if os.path.isfile(stem + ".ubulk"):
                with open(stem + ".ubulk", "rb") as fp:
                    ubulk = fp.read()
            try:
                td = uasset.parse_texture(asset, uexp, ubulk, name=path)
                if td is None:
                    skipped += 1
                    if args.verbose:
                        print(f"  跳过 {path}（非 UTexture2D 或结构不兼容）")
                    continue
                image = texture.decode(td.width, td.height, td.pixel_format, td.mip_data)
                rel = os.path.relpath(path, root)
                dest = os.path.join(args.out, rel[: -len(".uasset")] + ".png")
                texture.save_png(image, dest)
                done += 1
                print(f"  {td.width}x{td.height} {td.pixel_format} -> {dest}")
            except (texture.TextureError, uasset.UAssetError, PakError) as exc:
                failed += 1
                print(f"  失败 {path}: {exc}", file=sys.stderr)
            if args.limit and done + skipped + failed >= args.limit:
                print(f"PNG: 成功 {done} / 跳过 {skipped} / 失败 {failed}")
                return 0
    print(f"PNG: 成功 {done} / 跳过 {skipped} / 失败 {failed}")
    return 0 if failed == 0 else 1


def cmd_png(pak: PakFile | None, args) -> int:
    if pak is None:
        print(f"扫描目录 {args.path} 中的 .uasset")
        return _png_from_dir(args.path, args)
    by_path = {e.path: e for e in pak.entries.values()}
    assets = [e for e in pak.entries.values() if e.path.lower().endswith(".uasset")]
    if args.filter != "*":
        assets = [e for e in assets if _match(e.path, args.filter)]
    if args.limit:
        assets = assets[: args.limit]
    print(f"检查 {len(assets)} 个 .uasset -> {args.out}")
    done = skipped = failed = 0
    for entry in assets:
        try:
            ok, msg = _png_from_entry(pak, entry, args, by_path)
            if ok:
                done += 1
                print(f"  {msg}")
            else:
                skipped += 1
                if args.verbose:
                    print(f"  跳过 {entry.path}: {msg}")
        except (texture.TextureError, uasset.UAssetError, PakError) as exc:
            failed += 1
            print(f"  失败 {entry.path}: {exc}", file=sys.stderr)
    print(f"PNG: 成功 {done} / 跳过 {skipped} / 失败 {failed}")
    if done == 0:
        print("提示：该 pak 中没有可转换的 UTexture2D 资产（例如本例是视频 pak）。")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "png" and os.path.isdir(args.path):
            return cmd_png(None, args)
        if not os.path.isfile(args.path):
            raise PakError(f"找不到文件: {args.path}")
        with _open_pak(args) as pak:
            if args.command in ("list", "info"):
                return cmd_list(pak, args)
            if args.command in ("extract", "all"):
                code = cmd_extract(pak, args)
                if code:
                    return code
            if args.command in ("png", "all"):
                return cmd_png(pak, args)
        return 0
    except (PakError, texture.TextureError, uasset.UAssetError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
