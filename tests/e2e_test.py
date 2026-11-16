#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Frame Studio 端到端回归测试（仅标准库）。

前置：先在另一个终端运行 `python server.py --no-open`
运行：`python tests/e2e_test.py`

会生成一个 24fps / 5s / 120 帧的测试视频，验证探测、拆帧、缩略图、
替换、插入、删除、撤销、整段插帧、还原、MP4/GIF/ZIP 导出与下载，
结束后自动删除全部测试产物。
"""
from __future__ import annotations

import glob
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parent.parent
TESTDIR = ROOT / "workspace" / "_e2e"
PASS = 0
PIDS: list[str] = []


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    hits = glob.glob("D:/ffmpeg/*/bin/ffmpeg.exe")
    if hits:
        return hits[0]
    raise RuntimeError("未找到 ffmpeg")


def ffprobe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    hits = glob.glob("D:/ffmpeg/*/bin/ffprobe.exe")
    if hits:
        return hits[0]
    raise RuntimeError("未找到 ffprobe")


def request(method: str, path: str, obj=None, raw: bytes | None = None):
    headers, data = {}, None
    if obj is not None:
        data = json.dumps(obj).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def api(method: str, path: str, obj=None):
    code, body = request(method, path, obj)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        raise AssertionError(f"{method} {path} 返回非 JSON (HTTP {code}): {body[:200]!r}")
    if code >= 400 or data.get("error"):
        raise AssertionError(f"{method} {path} 失败 (HTTP {code}): {data.get('error')}")
    return data


def upload(path: Path) -> dict:
    code, body = request("POST", "/api/upload?name=" + urllib.parse.quote(path.name),
                         raw=path.read_bytes())
    assert code == 200, f"上传失败 HTTP {code}: {body[:200]!r}"
    return json.loads(body.decode("utf-8"))


def check(cond: bool, msg: str):
    global PASS
    if not cond:
        raise AssertionError("断言失败: " + msg)
    PASS += 1
    print(f"  [OK] {msg}", flush=True)


def wait_job(jid: str, timeout: int = 240) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        job = api("GET", "/api/jobs/" + jid)
        if job["status"] == "done":
            print(f"       任务完成 ({job['kind']})", flush=True)
            return job["result"] or {}
        if job["status"] == "error":
            raise AssertionError(f"任务 {jid} 失败: {job.get('error')}")
        msg = f"{job['message']} {job['progress']:.0f}%"
        if msg != last:
            print("       " + msg, flush=True)
            last = msg
        time.sleep(0.35)
    raise TimeoutError(f"任务 {jid} 超时")


def probe_file(path: Path) -> dict:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    st = data["streams"][0]
    return {"nb_frames": int(st.get("nb_frames", 0)),
            "fps": st.get("avg_frame_rate"), "duration": float(data["format"]["duration"]),
            "width": int(st["width"]), "height": int(st["height"])}


def cleanup():
    for pid in PIDS:
        try:
            api("DELETE", "/api/projects/" + pid)
        except Exception:
            pass
    try:
        api("POST", "/api/cleanup", {})
    except Exception:
        pass
    shutil.rmtree(TESTDIR, ignore_errors=True)


def main():
    TESTDIR.mkdir(parents=True, exist_ok=True)
    video = TESTDIR / "sample_24fps_5s.mp4"
    patch = TESTDIR / "patch.png"
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24", "-t", "5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=320x180", "-frames:v", "1",
                    str(patch)], check=True)

    try:
        print("\n[1] 环境与素材探测", flush=True)
        env = api("GET", "/api/env")
        check(bool(env["ffmpeg"]), "ffmpeg 已就绪")
        up = upload(video)
        p = up["probe"]
        check(abs(p["fps"] - 24) < 0.01, f"探测帧率 24 fps（实际 {p['fps']}）")
        check(p["nb_frames"] == 120, f"探测总帧数 120（实际 {p['nb_frames']}）")
        check(abs(p["duration"] - 5) < 0.05, f"探测时长 5s（实际 {p['duration']}）")

        print("\n[2] 拆帧 24fps × 5s = 120 张", flush=True)
        job = api("POST", "/api/extract", {
            "upload_id": up["upload_id"], "name": "E2E-120帧", "fps_mode": "source",
            "format": "png", "start": 0, "end": None, "scale_width": 0, "max_frames": 0,
        })
        res = wait_job(job["job_id"])
        pid = res["project_id"]
        PIDS.append(pid)
        check(res["frames"] == 120, f"拆出 120 张（实际 {res['frames']}）")
        proj = api("GET", "/api/projects/" + pid)["project"]
        check(len(proj["frames"]) == 120, "项目清单含 120 帧")
        check(proj["width"] == 320 and proj["height"] == 180, "项目尺寸 320×180")

        code, body = request("GET", "/media/" + pid + "/frames/" + proj["frames"][0]["file"])
        check(code == 200 and body[:4] == b"\x89PNG", "首帧 PNG 可正常读取")
        code, body = request("GET", "/media/" + pid + "/thumbs/" + proj["frames"][0]["thumb"])
        check(code == 200 and body[:2] == b"\xff\xd8", "缩略图 JPEG 可正常读取")

        print("\n[3] 帧编辑：替换 / 插入 / 删除 / 撤销 / ×2 / 还原", flush=True)
        patch_up = upload(patch)
        proj = api("POST", "/api/projects/" + pid + "/edit", {
            "action": "replace", "ids": [proj["frames"][0]["id"]],
            "upload_id": patch_up["upload_id"],
        })["project"]
        check(len(proj["frames"]) == 120 and proj["frames"][0]["type"] == "replaced",
              "替换首帧后帧数不变且标记为已替换")

        proj = api("POST", "/api/projects/" + pid + "/edit", {
            "action": "insert", "ids": [proj["frames"][0]["id"]],
        })["project"]
        check(len(proj["frames"]) == 121 and proj["frames"][1]["type"] == "inserted",
              "插入补帧后 121 帧")
        inserted_id = proj["frames"][1]["id"]

        proj = api("POST", "/api/projects/" + pid + "/edit", {
            "action": "delete", "ids": [inserted_id],
        })["project"]
        check(len(proj["frames"]) == 120, "删除补帧后回到 120 帧")

        proj = api("POST", "/api/projects/" + pid + "/edit", {
            "action": "undo", "ids": [],
        })["project"]
        check(len(proj["frames"]) == 121, "撤销删除恢复到 121 帧")
        proj = api("POST", "/api/projects/" + pid + "/edit", {
            "action": "undo", "ids": [],
        })["project"]
        check(len(proj["frames"]) == 120, "再次撤销插入恢复到 120 帧")

        proj = api("POST", "/api/projects/" + pid + "/edit", {
            "action": "double", "ids": [],
        })["project"]
        check(len(proj["frames"]) == 240 and abs(proj["fps"] - 48) < 0.01,
              "整段插帧后 240 帧 / 48 fps")

        proj = api("POST", "/api/projects/" + pid + "/edit", {
            "action": "restore", "ids": [],
        })["project"]
        check(len(proj["frames"]) == 120 and abs(proj["fps"] - 24) < 0.01,
              "还原原始帧序列 120 帧 / 24 fps")
        check(all(f["type"] == "original" for f in proj["frames"]), "所有帧恢复为原帧")

        print("\n[4] 导出与下载", flush=True)
        job = api("POST", "/api/projects/" + pid + "/export", {"type": "mp4", "fps": 24})
        mp4 = wait_job(job["job_id"])
        check(mp4["size"] > 1000, "MP4 导出成功")
        code, body = request("GET", "/api/download?path=" + urllib.parse.quote(mp4["path"]))
        check(code == 200, "MP4 下载接口可用")
        out_mp4 = TESTDIR / "out.mp4"
        out_mp4.write_bytes(body)
        info = probe_file(out_mp4)
        check(info["nb_frames"] == 120, f"合成视频 120 帧（实际 {info['nb_frames']}）")
        check(info["fps"] == "24/1", f"合成视频 24 fps（实际 {info['fps']}）")
        check(info["width"] == 320 and info["height"] == 180, "合成视频尺寸正确")

        job = api("POST", "/api/projects/" + pid + "/export", {"type": "zip", "fps": 24})
        zp = wait_job(job["job_id"])
        code, body = request("GET", "/api/download?path=" + urllib.parse.quote(zp["path"]))
        check(code == 200, "ZIP 下载接口可用")
        out_zip = TESTDIR / "out.zip"
        out_zip.write_bytes(body)
        with zipfile.ZipFile(out_zip) as zf:
            names = zf.namelist()
            imgs = [n for n in names if n.startswith("frames/") and n.endswith(".png")]
            check(len(imgs) == 120, f"ZIP 内含 120 张帧图（实际 {len(imgs)}）")
            check("manifest.json" in names and "README.txt" in names, "ZIP 含清单与说明")

        job = api("POST", "/api/projects/" + pid + "/export", {"type": "gif", "fps": 12})
        gif = wait_job(job["job_id"])
        code, body = request("GET", "/api/download?path=" + urllib.parse.quote(gif["path"]))
        check(code == 200 and body[:6] in (b"GIF89a", b"GIF87a"), "GIF 导出并下载正常")

        print("\n[5] 自定义帧率与区间、安全与清理", flush=True)
        job = api("POST", "/api/extract", {
            "upload_id": up["upload_id"], "name": "E2E-12fps", "fps_mode": "custom",
            "fps": 12, "format": "jpg", "start": 0, "end": None,
            "scale_width": 0, "max_frames": 0,
        })
        res2 = wait_job(job["job_id"])
        PIDS.append(res2["project_id"])
        check(res2["frames"] == 60, f"12 fps 重采样得到 60 张（实际 {res2['frames']}）")

        job = api("POST", "/api/extract", {
            "upload_id": up["upload_id"], "name": "E2E-区间", "fps_mode": "source",
            "format": "png", "start": 1, "end": 3, "scale_width": 160, "max_frames": 0,
        })
        res3 = wait_job(job["job_id"])
        PIDS.append(res3["project_id"])
        check(res3["frames"] == 48, f"截取 1~3s 得到 48 张（实际 {res3['frames']}）")
        p3 = api("GET", "/api/projects/" + res3["project_id"])["project"]
        check(p3["width"] == 160, "缩放宽度生效")

        code, _body = request("GET", "/api/download?path=../server.py")
        check(code >= 400, "下载接口阻止路径穿越")

        purged = api("POST", "/api/projects/" + pid + "/purge", {})
        check(purged["removed"] >= 0, f"清理未引用帧文件 {purged['removed']} 个，释放 {purged['freed']}")

        print(f"\n全部通过：{PASS} 项断言", flush=True)
    finally:
        cleanup()
        print("测试产物已清理", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\n[FAIL] " + str(exc), file=sys.stderr, flush=True)
        raise
