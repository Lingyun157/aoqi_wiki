# Frame Studio · 帧工坊

把视频 / 动图按帧率拆成一张张图片，用于补帧、逐帧替换，再重新合成为视频或打包导出。
例如 **24 fps 的 5 秒视频 → 120 张顺序编号的图片**，替换其中几帧或插入 AI 插帧结果后，一键合回视频。

## 特点

- **零第三方依赖**：只用 Python 标准库 + 本机 `ffmpeg` / `ffprobe`，不需要 pip / npm 安装任何包，不联网、不上传素材。
- **只占 D 盘**：所有帧、缩略图、导出文件写在项目目录下的 `workspace/`，系统盘不增长。
- **为补帧设计**：帧序列可勾选、替换、插入、删除、整段 ×2 插帧、撤销、还原，导出时按当前顺序合成。
- **长任务有进度**：拆帧与编码通过后台任务和进度条反馈，可随时取消。

## 启动

环境要求：Python 3.10+、ffmpeg / ffprobe 在 PATH 中（本机已具备）。

```bash
python server.py --port 8765
```

Windows 可直接双击 `start.bat`。启动后浏览器打开 `http://127.0.0.1:8765/`（默认自动打开）。
服务只监听 `127.0.0.1`，素材不会离开本机。

## 使用流程

1. **导入素材**：拖入或选择 mp4 / mov / mkv / webm / gif / apng / webp；自动读出分辨率、帧率、总帧数、时长、编码，并算出预计输出帧数。
2. **拆帧设置**：
   - 帧率模式：`沿用原始帧率`（逐帧透传，不丢帧）或 `自定义帧率`（按 `fps` 滤镜重采样）。
   - 输出格式：PNG（无损，推荐补帧）/ JPG / WebP。
   - 可设置起止时间、缩放宽度、最大帧数，避免一次性生成过多图片占满磁盘。
3. **帧工作台**：缩略图画廊支持分页、大图预览、跨页多选。
   - `替换选中帧`：上传本地图片，自动缩放到项目尺寸并统一格式。
   - `后方插入补帧`：复制选中帧插入其后作为占位，随后替换成插帧结果。
   - `整段插帧 ×2`：每帧后插一帧，帧数翻倍，导出帧率同步加倍（如 24 → 48 fps）。
   - 支持删除、撤销、还原原始帧序列。
4. **导出**：合成 MP4（H.264 / yuv420p）、生成循环 GIF、或打包 ZIP 帧序列（附 `manifest.json`）。

## 目录结构

```
拆帧/
├── server.py            # HTTP 服务、ffprobe 探测、ffmpeg 拆帧 / 合成、帧编辑、导出
├── static/
│   ├── index.html       # 工作台页面
│   ├── style.css        # 浅色 / 深色主题样式
│   └── app.js           # 前端交互
├── start.bat            # Windows 一键启动
├── README.md
└── workspace/           # 运行期数据（.gitignore 忽略，可随时删除）
    ├── uploads/         # 导入的原始素材
    ├── projects/<id>/   # frames/ 帧序列、thumbs/ 缩略图、manifest.json、history/
    └── exports/<id>/    # 合成视频、GIF、ZIP
```

## 清理

- 界面右上角「清理缓存」：删除不再被任何项目引用的上传素材。
- 项目卡片上的 `×`：删除该项目及其全部帧和导出文件。
- 服务接口 `POST /api/projects/<id>/purge`：删除未被当前序列引用的帧文件与历史快照（清理后不可撤销）。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/env` | 运行环境与项目列表 |
| POST | `/api/upload?name=` | 上传素材（原始二进制，返回探测信息） |
| POST | `/api/extract` | 启动拆帧，返回 `job_id` |
| GET | `/api/jobs/<id>` | 任务进度 / 结果 |
| GET | `/api/projects/<id>` | 项目与帧序列 |
| POST | `/api/projects/<id>/edit` | 帧编辑（replace / insert / delete / double / undo / restore） |
| POST | `/api/projects/<id>/export` | 导出（mp4 / gif / zip） |
| GET | `/api/download?path=` | 下载导出文件 |
| DELETE | `/api/projects/<id>` | 删除项目 |

## 注意

- GIF / APNG 常含可变帧延迟，按恒定帧率重采样后时长可能与原文件略有差异；逐帧透传模式保留原始帧。
- PNG 帧体积较大，长视频建议先设置「最大帧数」或改用 JPG / WebP，也可缩小输出宽度。
- 替换帧会被统一到项目的尺寸与格式，保证重新合成时编码稳定。
