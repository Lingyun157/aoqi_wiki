#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Frame Studio / 帧工坊
本地视频 / 动图拆帧、补帧与帧替换工作台。

设计约束：
  * 零第三方依赖，仅使用 Python 标准库 + 本机 ffmpeg / ffprobe
  * 所有运行期数据写入项目目录下的 workspace/，不占用系统盘
  * HTTP 服务只监听 127.0.0.1，素材不出本机
"""
from __future__ import annotations

import argparse
import glob
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "workspace"
UPLOADS = WORK / "uploads"
PROJECTS = WORK / "projects"
EXPORTS = WORK / "exports"
STATIC = ROOT / "static"
for _d in (WORK, UPLOADS, PROJECTS, EXPORTS, STATIC):
    _d.mkdir(parents=True, exist_ok=True)

LOCK = threading.RLock()
JOBS: dict[str, dict] = {}
FORMAT_EXT = {"png": "png", "jpg": "jpg", "webp": "webp"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# --------------------------------------------------------------------------- 工具

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def find_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for pattern in (
            "D:/ffmpeg/*/bin/{}.exe",
            "D:/ffmpeg/bin/{}.exe",
            "C:/ffmpeg/*/bin/{}.exe",
        ):
            hits = glob.glob(pattern.format(name))
            if hits:
                return hits[0]
    raise RuntimeError(f"未找到 {name}，请先安装 ffmpeg 并加入 PATH")


FFMPEG = find_bin("ffmpeg")
FFPROBE = find_bin("ffprobe")


def safe_name(name: str) -> str:
    name = os.path.basename(str(name).replace("\\", "/"))
    name = re.sub(r"[^\w.\-+ ()（）\u4e00-\u9fff]", "_", name).strip()
    return (name or "source")[:120]


def parse_rate(value, default=0.0) -> float:
    try:
        s = str(value)
        if "/" in s:
            num, den = s.split("/", 1)
            num, den = float(num), float(den)
            return num / den if den else default
        return float(s)
    except (TypeError, ValueError):
        return default


def as_float(value, default=0.0) -> float:
    try:
        f = float(value)
        return f if f == f and f not in (float("inf"), float("-inf")) else default
    except (TypeError, ValueError):
        return default


def human_size(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "命令执行失败").strip()[-1200:])
    return json.loads(proc.stdout or "{}")


def probe_source(path: Path) -> dict:
    data = run_json([
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration,pix_fmt"
        ":format=duration,size,format_name",
        "-of", "json", str(path),
    ])
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    if not stream:
        raise RuntimeError("文件中没有可读取的视频流，请确认是视频或动图素材")

    duration = as_float(stream.get("duration")) or as_float(fmt.get("duration"))
    fps = parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate"), 25.0)
    if fps <= 0:
        fps = 25.0
    raw_frames = stream.get("nb_frames")
    nb_frames = int(raw_frames) if str(raw_frames).isdigit() else None
    if nb_frames is None:
        nb_frames = max(1, round(duration * fps)) if duration > 0 else 1
    if duration <= 0:
        duration = nb_frames / fps

    return {
        "filename": path.name,
        "size": int(fmt.get("size") or path.stat().st_size),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": round(fps, 6),
        "fps_label": stream.get("avg_frame_rate") or stream.get("r_frame_rate") or str(fps),
        "duration": round(duration, 4),
        "nb_frames": nb_frames,
        "codec": stream.get("codec_name") or "unknown",
        "pix_fmt": stream.get("pix_fmt") or "",
        "container": fmt.get("format_name") or "",
    }


def ffmpeg_run(cmd: list[str], job: dict | None = None, expected: int = 0,
               offset: float = 0.0, span: float = 100.0) -> None:
    """执行 ffmpeg，解析 -progress 输出并回写任务进度。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", bufsize=1,
        creationflags=CREATE_NO_WINDOW,
    )
    if job is not None:
        job["process"] = proc
    stderr_box: list[str] = []

    def drain_stderr():
        try:
            stderr_box.append(proc.stderr.read())
        except Exception:
            pass

    t = threading.Thread(target=drain_stderr, daemon=True)
    t.start()
    try:
        for line in proc.stdout:
            line = line.strip()
            if job is not None and job.get("cancel"):
                proc.kill()
                break
            if job is not None and line.startswith("frame="):
                try:
                    done = int(line.split("=", 1)[1].strip())
                    ratio = min(1.0, done / max(1, expected)) if expected else 0.0
                    job["progress"] = round(offset + ratio * span, 1)
                    job["message"] = f"已输出 {done} 帧"
                except ValueError:
                    pass
        proc.wait()
        t.join(timeout=3)
        if job is not None and job.get("cancel"):
            raise RuntimeError("任务已取消")
        if proc.returncode != 0:
            err = (stderr_box[0] if stderr_box else "").strip() or "ffmpeg 执行失败"
            raise RuntimeError(err[-1500:])
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if job is not None:
            job["process"] = None


def ffmpeg_quick(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "ffmpeg 执行失败").strip()[-1200:])
    return proc.stdout


# --------------------------------------------------------------------------- 项目持久化

def project_dir(pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{8,32}", pid or ""):
        raise RuntimeError("非法项目 ID")
    return PROJECTS / pid


def load_manifest(pid: str) -> tuple[Path, dict]:
    base = project_dir(pid)
    mf = base / "manifest.json"
    if not mf.exists():
        raise RuntimeError("项目不存在")
    with mf.open("r", encoding="utf-8") as f:
        return base, json.load(f)


def save_manifest(base: Path, mf: dict) -> None:
    mf["updated_at"] = now()
    tmp = base / "manifest.tmp"
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=2)
    tmp.replace(base / "manifest.json")


def push_history(base: Path, mf: dict) -> None:
    hist = base / "history"
    hist.mkdir(exist_ok=True)
    items = sorted(hist.glob("*.json"))
    for old in items[:-29]:
        old.unlink(missing_ok=True)
    idx = int(items[-1].stem) + 1 if items else 1
    with (hist / f"{idx:06d}.json").open("w", encoding="utf-8") as f:
        json.dump({"frames": mf["frames"], "fps": mf.get("fps")}, f, ensure_ascii=False)


def pop_history(base: Path) -> dict | None:
    hist = base / "history"
    items = sorted(hist.glob("*.json"))
    if not items:
        return None
    target = items[-1]
    with target.open("r", encoding="utf-8") as f:
        snap = json.load(f)
    target.unlink(missing_ok=True)
    return snap


def list_projects() -> list[dict]:
    out = []
    for mf_path in PROJECTS.glob("*/manifest.json"):
        try:
            with mf_path.open("r", encoding="utf-8") as f:
                mf = json.load(f)
            out.append({
                "id": mf["id"], "name": mf["name"], "created_at": mf.get("created_at"),
                "updated_at": mf.get("updated_at"), "frames": len(mf["frames"]),
                "fps": mf.get("fps"), "width": mf.get("width"), "height": mf.get("height"),
                "format": mf.get("format"), "source": mf.get("source", {}).get("filename"),
            })
        except Exception:
            continue
    return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)


def frame_abs(base: Path, filename: str) -> Path:
    return base / "frames" / filename


# --------------------------------------------------------------------------- 后台任务

def new_job(kind: str) -> str:
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {
        "id": jid, "kind": kind, "status": "running", "progress": 0.0,
        "message": "准备中", "result": None, "error": None, "cancel": False,
        "process": None, "created_at": now(),
    }
    return jid


def finish_job(jid: str, fn) -> None:
    def runner():
        try:
            result = fn(jid)
            JOBS[jid].update(status="done", progress=100.0, message="完成", result=result)
        except Exception as exc:
            JOBS[jid].update(status="error", error=str(exc), message=str(exc))
            traceback.print_exc()
    threading.Thread(target=runner, daemon=True).start()


# --------------------------------------------------------------------------- 拆帧

def build_extract_cmd(src: Path, opts: dict, pattern: Path) -> list[str]:
    fmt = opts["format"]
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-progress", "pipe:1", "-nostats", "-i", str(src)]
    if opts["start"] > 0:
        cmd += ["-ss", f"{opts['start']:.6f}"]
    if opts.get("end") is not None:
        cmd += ["-t", f"{max(0.04, opts['end'] - opts['start']):.6f}"]
    filters = []
    if opts["fps_mode"] == "custom":
        filters.append(f"fps={opts['fps']:.6f}")
    if opts.get("scale_width"):
        filters.append(f"scale={int(opts['scale_width'])}:-2")
    cmd += ["-map", "0:v:0", "-an", "-sn", "-dn", "-vf", ",".join(filters) if filters else "null"]
    if opts["fps_mode"] == "source":
        cmd += ["-fps_mode", "passthrough"]
    if opts.get("max_frames"):
        cmd += ["-frames:v", str(int(opts["max_frames"]))]
    if fmt == "png":
        cmd += ["-c:v", "png", "-compression_level", "3"]
    elif fmt == "jpg":
        cmd += ["-c:v", "mjpeg", "-q:v", "2"]
    else:
        cmd += ["-c:v", "libwebp", "-quality", "92", "-compression_level", "2"]
    cmd += ["-start_number", "1", str(pattern)]
    return cmd


def make_thumbs(base: Path, fmt: str, count: int) -> bool:
    thumbs = base / "thumbs"
    thumbs.mkdir(exist_ok=True)
    try:
        ffmpeg_quick([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-start_number", "1", "-i", str(base / "frames" / f"f_%06d.{fmt}"),
            "-vf", "scale=-1:160,format=yuvj420p", "-fps_mode", "passthrough",
            "-c:v", "mjpeg", "-q:v", "5", "-start_number", "1",
            str(thumbs / "t_%06d.jpg"),
        ])
        return True
    except Exception as exc:
        print(f"[warn] 缩略图生成失败，将回退使用原图预览: {exc}", flush=True)
        return False


def do_extract(jid: str, payload: dict) -> dict:
    upload_id = payload["upload_id"]
    src = UPLOADS / upload_id
    if not src.exists():
        raise RuntimeError("上传文件已失效，请重新导入")

    meta = probe_source(src)
    fmt = payload.get("format", "png")
    if fmt not in FORMAT_EXT:
        raise RuntimeError("不支持的输出格式")
    fps_mode = payload.get("fps_mode", "source")
    src_fps = meta["fps"]
    fps = as_float(payload.get("fps"), src_fps) if fps_mode == "custom" else src_fps
    if fps <= 0 or fps > 240:
        raise RuntimeError("帧率需介于 0 与 240 之间")
    start = max(0.0, as_float(payload.get("start"), 0.0))
    end = payload.get("end")
    end = as_float(end, meta["duration"]) if end not in (None, "") else None
    if end is not None:
        end = min(max(end, start + 0.04), meta["duration"] or end)
    clip = (end if end is not None else meta["duration"]) - start
    if clip <= 0:
        raise RuntimeError("截取区间无效")

    opts = {
        "format": fmt, "fps_mode": fps_mode, "fps": round(fps, 6),
        "start": start, "end": end, "scale_width": int(payload.get("scale_width") or 0),
        "max_frames": int(payload.get("max_frames") or 0),
    }
    pid = uuid.uuid4().hex[:12]
    base = PROJECTS / pid
    (base / "frames").mkdir(parents=True, exist_ok=True)
    pattern = base / "frames" / f"f_%06d.{fmt}"

    expected = max(1, round(clip * fps))
    if opts["max_frames"]:
        expected = min(expected, opts["max_frames"])
    if fps_mode == "source" and not opts["max_frames"] and start == 0 and end is None:
        expected = max(expected, meta["nb_frames"])

    JOBS[jid]["message"] = "正在拆解帧序列"
    ffmpeg_run(build_extract_cmd(src, opts, pattern), JOBS[jid], expected, 0, 88)

    files = sorted((base / "frames").glob(f"f_*.{fmt}"))
    if not files:
        raise RuntimeError("没有拆解出任何帧，请检查素材或截取区间")
    count = len(files)
    JOBS[jid].update(progress=92, message="正在生成预览缩略图")
    has_thumb = make_thumbs(base, fmt, count)

    width = int(payload.get("scale_width") or meta["width"])
    height = meta["height"]
    if opts["scale_width"] and meta["width"]:
        height = int(round(meta["height"] * opts["scale_width"] / meta["width"]))
        height = height + (height % 2)

    frames = []
    for i, fp in enumerate(files, 1):
        frames.append({
            "id": uuid.uuid4().hex[:10],
            "file": fp.name,
            "thumb": f"t_{i:06d}.jpg" if has_thumb else fp.name,
            "has_thumb": has_thumb,
            "type": "original",
            "size": fp.stat().st_size,
        })
    mf = {
        "id": pid,
        "name": payload.get("name") or Path(meta["filename"]).stem or "未命名项目",
        "created_at": now(), "updated_at": now(),
        "source": meta, "upload_id": upload_id, "options": opts,
        "format": fmt, "fps": round(fps, 6), "fps_mode": fps_mode,
        "width": width, "height": height,
        "frame_count_original": count, "frames": frames,
        "exports": [],
    }
    save_manifest(base, mf)
    with (base / "original.json").open("w", encoding="utf-8") as f:
        json.dump({"frames": frames, "fps": mf["fps"]}, f, ensure_ascii=False)
    return {"project_id": pid, "frames": count, "project": project_view(mf)}


# --------------------------------------------------------------------------- 帧编辑

def normalize_upload(src: Path, target: Path, mf: dict) -> None:
    """把用户上传的补帧 / 替换帧统一到项目尺寸与格式，保证可重新合成。"""
    w, h = int(mf["width"]), int(mf["height"])
    if w <= 0 or h <= 0:
        raise RuntimeError("项目尺寸信息缺失")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    fmt = mf["format"]
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-vf", vf, "-frames:v", "1", "-map", "0:v:0"]
    if fmt == "png":
        cmd += ["-c:v", "png", "-compression_level", "3"]
    elif fmt == "jpg":
        cmd += ["-c:v", "mjpeg", "-q:v", "2"]
    else:
        cmd += ["-c:v", "libwebp", "-quality", "92"]
    cmd += [str(target)]
    ffmpeg_quick(cmd)

    thumb_target = target.parent.parent / "thumbs" / (target.stem + ".jpg")
    if thumb_target.parent.exists():
        ffmpeg_quick([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(target),
            "-vf", "scale=-1:160,format=yuvj420p", "-frames:v", "1",
            "-c:v", "mjpeg", "-q:v", "5", str(thumb_target),
        ])


def find_index(mf: dict, frame_id: str) -> int:
    for i, fr in enumerate(mf["frames"]):
        if fr["id"] == frame_id:
            return i
    raise RuntimeError("目标帧不存在，请刷新后重试")


def edit_frames(pid: str, action: str, payload: dict) -> dict:
    with LOCK:
        base, mf = load_manifest(pid)
        frames = mf["frames"]
        ids = payload.get("ids") or []
        if not isinstance(ids, list):
            raise RuntimeError("参数错误")
        if action != "undo":
            push_history(base, mf)

        if action == "replace":
            if len(ids) != 1:
                raise RuntimeError("替换帧请只选中一帧")
            idx = find_index(mf, ids[0])
            upload_id = payload.get("upload_id")
            src = UPLOADS / str(upload_id)
            if not src.exists():
                raise RuntimeError("上传图片已失效")
            fname = f"custom_{uuid.uuid4().hex[:10]}.{mf['format']}"
            normalize_upload(src, frame_abs(base, fname), mf)
            frames[idx] = {
                "id": uuid.uuid4().hex[:10], "file": fname,
                "thumb": Path(fname).stem + ".jpg", "has_thumb": True,
                "type": "replaced", "size": frame_abs(base, fname).stat().st_size,
            }

        elif action == "insert":
            if not ids:
                raise RuntimeError("请先选中帧")
            positions = sorted((find_index(mf, i) for i in ids), reverse=True)
            upload_id = payload.get("upload_id")
            for pos in positions:
                if upload_id:
                    src = UPLOADS / str(upload_id)
                    if not src.exists():
                        raise RuntimeError("上传图片已失效")
                    fname = f"custom_{uuid.uuid4().hex[:10]}.{mf['format']}"
                    normalize_upload(src, frame_abs(base, fname), mf)
                    item = {
                        "id": uuid.uuid4().hex[:10], "file": fname,
                        "thumb": Path(fname).stem + ".jpg", "has_thumb": True,
                        "type": "inserted", "size": frame_abs(base, fname).stat().st_size,
                    }
                else:
                    # 补帧默认复用相邻帧，用户可随后替换成 AI 插帧结果
                    ref = frames[pos]
                    fname = f"custom_{uuid.uuid4().hex[:10]}.{mf['format']}"
                    shutil.copy2(frame_abs(base, ref["file"]), frame_abs(base, fname))
                    has_thumb = bool(ref.get("has_thumb", True))
                    thumb_src = base / "thumbs" / ref["thumb"]
                    if has_thumb and thumb_src.exists():
                        shutil.copy2(thumb_src, base / "thumbs" / (Path(fname).stem + ".jpg"))
                    else:
                        has_thumb = False
                    item = {
                        "id": uuid.uuid4().hex[:10], "file": fname,
                        "thumb": Path(fname).stem + ".jpg" if has_thumb else fname,
                        "has_thumb": has_thumb,
                        "type": "inserted", "size": frame_abs(base, fname).stat().st_size,
                    }
                frames.insert(pos + 1, item)

        elif action == "delete":
            idxs = sorted({find_index(mf, i) for i in ids}, reverse=True)
            for i in idxs:
                frames.pop(i)
            if not frames:
                raise RuntimeError("至少保留一帧")

        elif action == "double":
            # 每帧之后插入一帧（24fps → 48fps 的补帧占位）
            added = []
            for i in range(len(frames) - 1, -1, -1):
                ref = frames[i]
                fname = f"custom_{uuid.uuid4().hex[:10]}.{mf['format']}"
                shutil.copy2(frame_abs(base, ref["file"]), frame_abs(base, fname))
                thumb_src = base / "thumbs" / ref["thumb"]
                has_thumb = bool(ref.get("has_thumb", True)) and thumb_src.exists()
                if has_thumb:
                    shutil.copy2(thumb_src, base / "thumbs" / (Path(fname).stem + ".jpg"))
                frames.insert(i + 1, {
                    "id": uuid.uuid4().hex[:10], "file": fname,
                    "thumb": Path(fname).stem + ".jpg" if has_thumb else fname,
                    "has_thumb": has_thumb,
                    "type": "inserted", "size": frame_abs(base, fname).stat().st_size,
                })
                added.append(fname)
            mf["fps"] = round(mf["fps"] * 2, 6)
            mf["options"]["fps"] = mf["fps"]

        elif action == "undo":
            snap = pop_history(base)
            if snap is None:
                raise RuntimeError("没有可撤销的操作")
            mf["frames"] = snap["frames"]
            mf["fps"] = snap.get("fps", mf["fps"])
            mf["options"]["fps"] = mf["fps"]

        elif action == "restore":
            with (base / "original.json").open("r", encoding="utf-8") as f:
                snap = json.load(f)
            mf["frames"] = snap["frames"]
            mf["fps"] = snap.get("fps", mf["fps"])
            mf["options"]["fps"] = mf["fps"]

        else:
            raise RuntimeError("未知操作")

        save_manifest(base, mf)
        return project_view(mf)


def purge_unused(pid: str) -> dict:
    """删除未被当前序列引用的帧文件、缩略图和历史快照，释放磁盘。"""
    with LOCK:
        base, mf = load_manifest(pid)
        used_f = {fr["file"] for fr in mf["frames"]}
        with (base / "original.json").open("r", encoding="utf-8") as f:
            used_f |= {fr["file"] for fr in json.load(f)["frames"]}
        used_t = {fr["thumb"] for fr in mf["frames"]}
        removed, freed = 0, 0
        for folder, used in ((base / "frames", used_f), (base / "thumbs", used_t)):
            for fp in folder.glob("*"):
                if fp.is_file() and fp.name not in used:
                    freed += fp.stat().st_size
                    fp.unlink()
                    removed += 1
        hist = base / "history"
        for fp in hist.glob("*.json"):
            fp.unlink()
        return {"removed": removed, "freed": human_size(freed)}


def project_view(mf: dict) -> dict:
    return {
        "id": mf["id"], "name": mf["name"], "source": mf["source"],
        "options": mf["options"], "format": mf["format"], "fps": mf["fps"],
        "width": mf["width"], "height": mf["height"],
        "frame_count_original": mf.get("frame_count_original", len(mf["frames"])),
        "frames": mf["frames"], "exports": mf.get("exports", []),
        "created_at": mf.get("created_at"), "updated_at": mf.get("updated_at"),
        "can_undo": bool(list((PROJECTS / mf["id"] / "history").glob("*.json"))),
    }


# --------------------------------------------------------------------------- 导出

def do_export(jid: str, pid: str, payload: dict) -> dict:
    with LOCK:
        base, mf = load_manifest(pid)
        frames = list(mf["frames"])
        fmt = mf["format"]
        fps = as_float(payload.get("fps"), mf["fps"])
        kind = payload.get("type", "mp4")
        if kind not in ("mp4", "gif", "zip"):
            raise RuntimeError("不支持的导出类型")
        if fps <= 0 or fps > 240:
            raise RuntimeError("帧率不合法")
        seq = EXPORTS / pid / f"seq_{uuid.uuid4().hex[:8]}"
        seq.mkdir(parents=True, exist_ok=True)
        out_dir = EXPORTS / pid
        stamp = datetime.now().strftime("%H%M%S")
        stem = re.sub(r"[^\w\-+()（）\u4e00-\u9fff]", "_", mf["name"])[:40] or "frames"

    try:
        JOBS[jid]["message"] = "正在整理帧序列"
        for i, fr in enumerate(frames, 1):
            shutil.copy2(frame_abs(base, fr["file"]), seq / f"f_{i:06d}.{fmt}")
            if i % 25 == 0:
                JOBS[jid]["progress"] = round(5 + 10 * i / max(1, len(frames)), 1)
        pattern = str(seq / f"f_%06d.{fmt}")

        if kind == "zip":
            out = out_dir / f"{stem}_帧序列_{stamp}.zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for i, fr in enumerate(frames, 1):
                    zf.write(seq / f"f_{i:06d}.{fmt}", f"frames/frame_{i:06d}.{fmt}")
                info = {
                    "project": mf["name"], "frames": len(frames), "fps": fps,
                    "width": mf["width"], "height": mf["height"],
                    "format": fmt, "exported_at": now(),
                }
                zf.writestr("manifest.json", json.dumps(info, ensure_ascii=False, indent=2))
                zf.writestr("README.txt",
                            "Frame Studio 帧序列\r\n"
                            f"帧数: {len(frames)}\r\n帧率: {fps}\r\n"
                            f"尺寸: {mf['width']}x{mf['height']}\r\n"
                            "按 frame_000001 顺序命名，补帧后可直接重新导入合成。\r\n")
            JOBS[jid]["progress"] = 100

        elif kind == "mp4":
            out = out_dir / f"{stem}_合成_{stamp}.mp4"
            cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                   "-progress", "pipe:1", "-nostats",
                   "-framerate", f"{fps:.6f}", "-start_number", "1", "-i", pattern,
                   "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1",
                   "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
            JOBS[jid]["message"] = "正在编码 MP4"
            ffmpeg_run(cmd, JOBS[jid], len(frames), 15, 85)

        else:
            out = out_dir / f"{stem}_动图_{stamp}.gif"
            cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                   "-progress", "pipe:1", "-nostats",
                   "-framerate", f"{fps:.6f}", "-start_number", "1", "-i", pattern,
                   "-vf", "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3",
                   "-loop", "0", str(out)]
            JOBS[jid]["message"] = "正在生成 GIF"
            ffmpeg_run(cmd, JOBS[jid], len(frames), 15, 85)

        rel = str(out.relative_to(WORK)).replace("\\", "/")
        with LOCK:
            base, mf = load_manifest(pid)
            exports = mf.get("exports", [])
            exports.insert(0, {
                "name": out.name, "path": rel, "type": kind,
                "size": out.stat().st_size, "created_at": now(), "fps": fps,
                "frames": len(frames),
            })
            mf["exports"] = exports[:20]
            save_manifest(base, mf)
        return {"file": out.name, "path": rel, "size": out.stat().st_size,
                "download": "/api/download?path=" + rel}
    finally:
        shutil.rmtree(seq, ignore_errors=True)


# --------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FrameStudio/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("FRAME_STUDIO_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- 响应辅助 -------------------------------------------------------------
    def _headers(self, ctype: str, length: int, extra: dict | None = None,
                 cache: str = "no-store"):
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)

    def json_resp(self, obj, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers("application/json; charset=utf-8", len(data))
        self.end_headers()
        self.wfile.write(data)

    def file_resp(self, path: Path, download_name: str | None = None):
        if not path.is_file():
            return self.json_resp({"error": "文件不存在"}, 404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        extra = {}
        if download_name:
            from urllib.parse import quote as urlquote
            extra["Content-Disposition"] = (
                f"attachment; filename*=UTF-8''{urlquote(download_name)}")
        cacheable = (ctype.startswith("image/") or "css" in ctype
                     or "javascript" in ctype) and not download_name
        self.send_response(200)
        self._headers(ctype, path.stat().st_size, extra,
                      "public, max-age=3600" if cacheable else "no-store")
        self.end_headers()
        try:
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, 1024 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise RuntimeError("请求体不是合法 JSON")

    def error(self, exc: Exception, status: int = 400):
        self.json_resp({"error": str(exc)}, status)

    # -- 路由 -----------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self.file_resp(STATIC / "index.html")
            if path.startswith("/static/"):
                rel = path[len("/static/"):]
                if ".." in rel or "/" in rel or "\\" in rel:
                    return self.json_resp({"error": "非法路径"}, 403)
                return self.file_resp(STATIC / rel)

            if path == "/api/env":
                return self.json_resp({
                    "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
                    "python": sys.version.split()[0], "workspace": str(WORK),
                    "projects": list_projects(),
                    "max_upload_gb": 4,
                })
            if path == "/api/projects":
                return self.json_resp({"projects": list_projects()})
            if path.startswith("/api/projects/"):
                pid = path.rsplit("/", 1)[-1]
                _, mf = load_manifest(pid)
                return self.json_resp({"project": project_view(mf)})
            if path.startswith("/api/jobs/"):
                jid = path.rsplit("/", 1)[-1]
                job = JOBS.get(jid)
                if not job:
                    return self.json_resp({"error": "任务不存在"}, 404)
                return self.json_resp({k: v for k, v in job.items()
                                      if k not in ("process", "cancel")})
            if path.startswith("/media/"):
                parts = path.split("/", 3)
                if len(parts) != 4:
                    return self.json_resp({"error": "非法路径"}, 403)
                pid, tail = parts[2], parts[3]
                folder, sep, filename = tail.partition("/")
                base = project_dir(pid)
                if not sep or folder not in ("frames", "thumbs") or "/" in filename or "\\" in filename:
                    return self.json_resp({"error": "非法路径"}, 403)
                return self.file_resp(base / folder / filename)
            if path == "/api/download":
                rel = (query.get("path") or [""])[0]
                target = (WORK / rel).resolve()
                if not target.is_relative_to(WORK.resolve()) or not target.is_file():
                    return self.json_resp({"error": "文件不存在或路径非法"}, 404)
                return self.file_resp(target, target.name)
            return self.json_resp({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.error(exc)

    def do_POST(self):
        parsed = urlparse(self.path)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        try:
            if path == "/api/upload":
                return self.handle_upload(query)
            payload = self.body_json()

            if path == "/api/extract":
                jid = new_job("extract")
                finish_job(jid, lambda j: do_extract(j, payload))
                return self.json_resp({"job_id": jid})
            if path.startswith("/api/projects/") and path.endswith("/export"):
                pid = path.split("/")[3]
                project_dir(pid)
                jid = new_job("export")
                finish_job(jid, lambda j: do_export(j, pid, payload))
                return self.json_resp({"job_id": jid})
            if path.startswith("/api/projects/") and path.endswith("/edit"):
                pid = path.split("/")[3]
                return self.json_resp({"project": edit_frames(pid, payload.get("action", ""), payload)})
            if path.startswith("/api/projects/") and path.endswith("/purge"):
                pid = path.split("/")[3]
                return self.json_resp(purge_unused(pid))
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                jid = path.split("/")[3]
                job = JOBS.get(jid)
                if job:
                    job["cancel"] = True
                    if job.get("process") and job["process"].poll() is None:
                        job["process"].kill()
                return self.json_resp({"ok": True})
            if path == "/api/cleanup":
                return self.json_resp(self.cleanup_uploads())
            return self.json_resp({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.error(exc)

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/projects/"):
                pid = path.rsplit("/", 1)[-1]
                base = project_dir(pid)
                if not base.exists():
                    return self.json_resp({"error": "项目不存在"}, 404)
                shutil.rmtree(base, ignore_errors=True)
                shutil.rmtree(EXPORTS / pid, ignore_errors=True)
                return self.json_resp({"ok": True})
            return self.json_resp({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.error(exc)

    def handle_upload(self, query):
        name = safe_name((query.get("name") or ["source"])[0])
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise RuntimeError("上传内容为空")
        if length > 4 * 1024 * 1024 * 1024:
            raise RuntimeError("文件超过 4GB 上限")
        uid = uuid.uuid4().hex[:12] + "_" + name
        dest = UPLOADS / uid
        remaining = length
        with dest.open("wb") as f:
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            raise RuntimeError("上传内容为空")
        result = {"upload_id": uid, "name": name, "size": dest.stat().st_size}
        try:
            result["probe"] = probe_source(dest)
        except Exception as exc:
            result["probe_error"] = str(exc)
        return self.json_resp(result)

    def cleanup_uploads(self) -> dict:
        used = set()
        for mf_path in PROJECTS.glob("*/manifest.json"):
            try:
                with mf_path.open("r", encoding="utf-8") as f:
                    used.add(json.load(f).get("upload_id"))
            except Exception:
                pass
        removed, freed = 0, 0
        for fp in UPLOADS.glob("*"):
            if fp.is_file() and fp.name not in used:
                freed += fp.stat().st_size
                fp.unlink()
                removed += 1
        return {"removed": removed, "freed": human_size(freed)}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Frame Studio 本地拆帧工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    srv = Server((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("=" * 60, flush=True)
    print("  Frame Studio / 帧工坊 已启动", flush=True)
    print(f"  打开: {url}", flush=True)
    print(f"  ffmpeg : {FFMPEG}", flush=True)
    print(f"  数据目录: {WORK}", flush=True)
    print("  按 Ctrl+C 停止", flush=True)
    print("=" * 60, flush=True)
    if not args.no_open:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
