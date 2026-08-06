#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video2images.py — FrameFlow 视频关键帧提取工具

Pipeline: 视频 → 抽帧(关键帧/固定间隔) → 感知哈希去重 → 均匀采样 → 输出(图片+清单+网格图)

解决的问题：大多数大模型无法直接读取视频。本工具把视频浓缩为一组
"有代表性且不重复"的图片，方便喂给多模态大模型理解视频内容。

依赖：
  - ffmpeg / ffprobe  (系统命令)
  - Pillow, imagehash (pip install Pillow imagehash)

两种使用方式：
  1. 命令行：python3 video2images.py video.mp4 --mode auto --max-frames 20
  2. Web API：from video2images import process_video; result = process_video(...)
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image
    import imagehash
except ImportError as e:
    sys.stderr.write(
        f"[ERROR] 缺少依赖 {e.name}。请安装：\n"
        f"  pip install Pillow imagehash --break-system-packages\n"
    )
    sys.exit(1)


# ======================== 工具函数 ========================

def check_tools():
    """确认 ffmpeg / ffprobe 可用。不可用则抛 FileNotFoundError。"""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FileNotFoundError(f"未找到 {tool}，请先安装 ffmpeg。")


def probe_video(video_path):
    """返回 (时长秒, 帧率)。失败则抛 RuntimeError。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate:format=duration",
        "-of", "json", str(video_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{res.stderr.strip()}")
    data = json.loads(res.stdout or "{}")
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    fr = data.get("streams", [{}])[0].get("avg_frame_rate", "25/1")
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) else 25.0
    except Exception:
        fps = 25.0
    return duration, fps


def fmt_timecode(seconds):
    """秒 → HH:MM:SS.mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def clean_old_outputs(out_dir):
    """清理上次运行的产物文件，保证每次输出干净。"""
    for pat in ("scene_*.jpg", "interval_*.jpg", "keyframe_*.jpg"):
        for f in out_dir.glob(pat):
            f.unlink(missing_ok=True)
    for name in ("manifest.json", "contact_sheet.jpg"):
        p = out_dir / name
        if p.exists():
            p.unlink(missing_ok=True)


# ======================== 抽帧 ========================

def extract_scene_frames(video_path, out_dir, threshold, quality=2):
    """
    场景检测抽取关键帧（镜头切换处）。
    返回 [(Path, timestamp), ...]，时间戳来自 showinfo 的 pts_time。
    """
    pattern = str(out_dir / "scene_%05d.jpg")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr", "-q:v", str(quality),
        pattern,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", res.stderr)]
    files = sorted(out_dir.glob("scene_*.jpg"))
    frames = []
    for i, fp in enumerate(files):
        t = times[i] if i < len(times) else i * 1.0
        frames.append((fp, t))
    return frames


def extract_interval_frames(video_path, out_dir, interval, quality=2):
    """
    按固定间隔抽帧。返回 [(Path, timestamp), ...]。
    时间戳 = 序号 * 间隔。
    """
    pattern = str(out_dir / "interval_%05d.jpg")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-i", str(video_path),
        "-vf", f"fps=1/{interval}",
        "-q:v", str(quality),
        pattern,
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    files = sorted(out_dir.glob("interval_*.jpg"))
    return [(fp, i * interval) for i, fp in enumerate(files)]


# ======================== 去重 ========================

def deduplicate(frames, hamming_threshold=8, hash_size=8):
    """
    感知哈希(pHash)去重：按时间顺序，与上一个"保留"的帧比较，
    汉明距离 <= 阈值则视为重复并删除文件。
    返回保留的 [(Path, timestamp), ...]。
    """
    if not frames:
        return []
    kept = []
    last_hash = None
    for fp, t in frames:
        try:
            with Image.open(fp) as img:
                h = imagehash.phash(img, hash_size=hash_size)
        except Exception:
            kept.append((fp, t))
            last_hash = None
            continue
        if last_hash is None or (h - last_hash) > hamming_threshold:
            kept.append((fp, t))
            last_hash = h
        else:
            fp.unlink(missing_ok=True)
    return kept


def even_sample(frames, max_count):
    """均匀采样到 max_count 帧（保持时间顺序），多余的删除文件。"""
    n = len(frames)
    if n <= max_count:
        return frames
    if max_count <= 1:
        for f in frames[1:]:
            f[0].unlink(missing_ok=True)
        return frames[:1]
    indices = set(round(i * (n - 1) / (max_count - 1)) for i in range(max_count))
    kept = []
    for i, f in enumerate(frames):
        if i in indices:
            kept.append(f)
        else:
            f[0].unlink(missing_ok=True)
    return kept


# ======================== 输出 ========================

def write_manifest(frames, out_path, source, duration, mode):
    """写入 manifest.json（大模型可直接读取的元信息）。"""
    manifest = {
        "source": str(source),
        "duration_seconds": round(duration, 2),
        "duration_timecode": fmt_timecode(duration),
        "extraction_mode": mode,
        "frame_count": len(frames),
        "description": "本文件由 video2images.py 生成。frames 按视频时间顺序排列，"
                       "每帧包含文件名、时间戳与时间码，可连同图片一起提供给多模态大模型。",
        "frames": [
            {
                "index": i,
                "file": fp.name,
                "timestamp": round(t, 2),
                "timecode": fmt_timecode(t),
            }
            for i, (fp, t) in enumerate(frames)
        ],
    }
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def make_contact_sheet(frames, out_path, cols=4, cell_w=384):
    """生成网格拼接图（contact sheet），方便单张图概览全部关键帧。"""
    if not frames:
        return
    thumbs = []
    for fp, t in frames:
        with Image.open(fp) as src:
            img = src.convert("RGB")
        ratio = cell_w / img.width
        img = img.resize((cell_w, max(1, int(img.height * ratio))))
        thumbs.append(img)
    rows = (len(thumbs) + cols - 1) // cols
    cell_h = max(th.height for th in thumbs)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    for i, th in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet.paste(th, (c * cell_w, r * cell_h))
    sheet.save(out_path, quality=90)


# ======================== 核心 API（CLI 和 Web 共用） ========================

def process_video(video_path, output_dir, mode='auto', scene_threshold=0.4,
                  interval=2.0, hamming=8, max_frames=30,
                  make_grid=True, grid_cols=4, progress_callback=None):
    """
    处理视频，返回结果字典。可被 CLI 和 Web App 共同调用。

    Args:
        video_path:      视频文件路径
        output_dir:      输出目录
        mode:            抽帧模式 'auto' | 'scene' | 'interval'
        scene_threshold: 场景检测阈值 0~1
        interval:        固定间隔秒数
        hamming:         pHash 去重汉明距离阈值
        max_frames:      最大输出帧数
        make_grid:       是否生成网格预览图
        grid_cols:       网格图列数
        progress_callback: 可选回调 fn(step, detail)

    Returns:
        dict: manifest, output_dir, frames, contact_sheet, stats
    """
    def report(step, detail=''):
        if progress_callback:
            progress_callback(step, detail)

    check_tools()
    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"视频不存在：{video_path}")
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_old_outputs(out_dir)

    report('probe', '正在分析视频...')
    duration, fps = probe_video(video_path)

    # ---- 抽帧 ----
    all_frames = []
    scene_count = 0
    interval_count = 0
    if mode in ('scene', 'auto'):
        report('extract_scene', '场景检测抽取关键帧...')
        sf = extract_scene_frames(video_path, out_dir, scene_threshold)
        scene_count = len(sf)
        all_frames.extend(sf)
    if mode in ('interval', 'auto'):
        need_interval = mode == 'interval' or (
            mode == 'auto' and len(all_frames) < max(3, int(duration / 30))
        )
        if need_interval:
            report('extract_interval', f'固定间隔抽帧(每 {interval}s)...')
            ivf = extract_interval_frames(video_path, out_dir, interval)
            interval_count = len(ivf)
            all_frames.extend(ivf)

    all_frames.sort(key=lambda x: x[1])
    total_extracted = len(all_frames)

    report('dedup', f'合并 {total_extracted} 张，pHash 去重...')
    kept = deduplicate(all_frames, hamming_threshold=hamming)
    after_dedup = len(kept)

    report('sample', f'去重后 {after_dedup} 张，均匀采样...')
    if len(kept) > max_frames:
        kept = even_sample(kept, max_frames)

    # ---- 输出 ----
    report('output', '生成输出文件...')
    renamed = []
    for i, (fp, t) in enumerate(kept):
        new_name = f"keyframe_{i + 1:04d}.jpg"
        new_path = out_dir / new_name
        if fp != new_path:
            fp.rename(new_path)
        renamed.append((new_path, t))

    manifest_path = out_dir / "manifest.json"
    manifest = write_manifest(renamed, manifest_path, video_path, duration, mode)

    contact_sheet = None
    if make_grid and renamed:
        contact_sheet = out_dir / "contact_sheet.jpg"
        make_contact_sheet(renamed, contact_sheet, cols=grid_cols)

    report('done', f'完成！共 {len(renamed)} 张关键图片')

    return {
        'manifest': manifest,
        'output_dir': out_dir,
        'frames': renamed,
        'contact_sheet': contact_sheet,
        'stats': {
            'duration': round(duration, 2),
            'fps': round(fps, 2),
            'scene_frames': scene_count,
            'interval_frames': interval_count,
            'total_extracted': total_extracted,
            'after_dedup': after_dedup,
            'final_frames': len(renamed),
        }
    }


# ======================== CLI 入口 ========================

_STEP_LABELS = {
    'probe': '分析视频',
    'extract_scene': '场景检测',
    'extract_interval': '固定间隔抽帧',
    'dedup': '去重',
    'sample': '采样',
    'output': '生成输出',
    'done': '完成',
}


def main():
    parser = argparse.ArgumentParser(
        description="FrameFlow：抽帧 → 关键帧 → 去重 → 输出给大模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", help="输入视频文件路径")
    parser.add_argument("-o", "--output", default="./output", help="输出目录 (默认 ./output)")
    parser.add_argument("--mode", choices=["scene", "interval", "auto"], default="auto",
                        help="抽帧模式：scene=场景检测, interval=固定间隔, auto=自动混合(默认)")
    parser.add_argument("--scene-threshold", type=float, default=0.4,
                        help="场景检测阈值 0~1，越小越敏感 (默认 0.4)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="固定间隔秒数 (默认 2.0)")
    parser.add_argument("--hamming", type=int, default=8,
                        help="pHash 去重汉明距离阈值，越小越严格 (默认 8)")
    parser.add_argument("--max-frames", type=int, default=30,
                        help="最终输出最大帧数，超出则均匀采样 (默认 30)")
    parser.add_argument("--no-grid", action="store_true", help="不生成网格拼接图")
    parser.add_argument("--grid-cols", type=int, default=4, help="网格图列数 (默认 4)")
    args = parser.parse_args()

    def print_progress(step, detail=''):
        label = _STEP_LABELS.get(step, step)
        if detail:
            print(f"[INFO] {label}：{detail}")
        else:
            print(f"[INFO] {label}...")

    try:
        result = process_video(
            args.video, args.output,
            mode=args.mode, scene_threshold=args.scene_threshold,
            interval=args.interval, hamming=args.hamming,
            max_frames=args.max_frames,
            make_grid=not args.no_grid, grid_cols=args.grid_cols,
            progress_callback=print_progress,
        )
    except Exception as e:
        sys.exit(f"[ERROR] {e}")

    stats = result['stats']
    print(f"\n[DONE] 输出目录：{result['output_dir']}")
    print(f"  关键图片：{stats['final_frames']} 张 (keyframe_0001.jpg ...)")
    print(f"  清单文件：manifest.json")
    if result['contact_sheet']:
        print(f"  网格图：contact_sheet.jpg (一张图概览全部)")
    print("\n提示：把 keyframe_*.jpg 连同 manifest.json 一起发给多模态大模型即可。")


if __name__ == "__main__":
    main()
