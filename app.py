#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — FrameFlow Web 应用后端

启动：python3 app.py
访问：http://127.0.0.1:5000
"""

import io
import os
import threading
import uuid
import zipfile
from pathlib import Path

from flask import (
    Flask, request, jsonify, send_file, render_template, abort
)

from video2images import process_video, check_tools

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

WORK_DIR = Path(__file__).parent / 'work'
WORK_DIR.mkdir(exist_ok=True)

# 任务存储（内存中，进程退出后清理）
_jobs = {}
_jobs_lock = threading.Lock()

STEP_LABELS = {
    'probe': '分析视频',
    'extract_scene': '场景检测抽帧',
    'extract_interval': '固定间隔抽帧',
    'dedup': 'pHash 去重',
    'sample': '均匀采样',
    'output': '生成输出文件',
    'done': '完成',
}

STEP_ORDER = ['probe', 'extract_scene', 'extract_interval', 'dedup', 'sample', 'output', 'done']


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/check')
def api_check():
    """检查 ffmpeg/ffprobe 是否可用。"""
    try:
        check_tools()
        return jsonify({'ok': True})
    except FileNotFoundError as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/process', methods=['POST'])
def api_process():
    """接收视频上传，在后台线程中处理，立即返回 job_id。"""
    file = request.files.get('video')
    if not file or not file.filename:
        return jsonify({'error': '未上传视频文件'}), 400

    # 安全文件名
    safe_name = ''.join(c for c in file.filename if c.isalnum() or c in '._-')
    if not safe_name:
        safe_name = 'input.mp4'

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    video_path = job_dir / f'input_{safe_name}'
    file.save(str(video_path))

    # 解析参数
    params = {
        'mode': request.form.get('mode', 'auto'),
        'scene_threshold': float(request.form.get('scene_threshold', 0.4)),
        'interval': float(request.form.get('interval', 2.0)),
        'hamming': int(request.form.get('hamming', 8)),
        'max_frames': int(request.form.get('max_frames', 30)),
        'make_grid': request.form.get('make_grid', 'true') == 'true',
        'grid_cols': int(request.form.get('grid_cols', 4)),
    }

    output_dir = job_dir / 'output'

    with _jobs_lock:
        _jobs[job_id] = {
            'status': 'processing',
            'steps': [],
            'result': None,
            'error': None,
            'params': params,
        }

    def worker():
        def progress_cb(step, detail=''):
            with _jobs_lock:
                _jobs[job_id]['steps'].append({
                    'step': step,
                    'label': STEP_LABELS.get(step, step),
                    'detail': detail,
                })

        try:
            result = process_video(
                str(video_path), str(output_dir),
                progress_callback=progress_cb,
                **params,
            )
            with _jobs_lock:
                _jobs[job_id]['result'] = {
                    'manifest': result['manifest'],
                    'stats': result['stats'],
                    'has_contact_sheet': result['contact_sheet'] is not None,
                    'frame_count': len(result['frames']),
                }
                _jobs[job_id]['status'] = 'done'
        except Exception as e:
            with _jobs_lock:
                _jobs[job_id]['error'] = str(e)
                _jobs[job_id]['status'] = 'error'

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    return jsonify({'job_id': job_id})


@app.route('/api/status/<job_id>')
def api_status(job_id):
    """轮询任务进度。"""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({'error': '任务不存在'}), 404
        # 计算当前进度百分比
        steps_done = len(job['steps'])
        total_steps = len(STEP_ORDER)
        progress = min(100, int(steps_done / total_steps * 100))
        return jsonify({
            'status': job['status'],
            'progress': progress,
            'steps': list(job['steps']),
            'error': job.get('error'),
        })


@app.route('/api/results/<job_id>')
def api_results(job_id):
    """获取最终结果。"""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({'error': '任务不存在'}), 404
        if job['status'] != 'done':
            return jsonify({'error': '结果未就绪', 'status': job['status']}), 400
        return jsonify(job['result'])


@app.route('/api/results/<job_id>/frame/<filename>')
def serve_frame(job_id, filename):
    """返回单张关键帧图片。"""
    job_dir = WORK_DIR / job_id / 'output'
    fp = job_dir / filename
    if not fp.is_file():
        abort(404)
    return send_file(str(fp), mimetype='image/jpeg')


@app.route('/api/results/<job_id>/contact_sheet')
def serve_contact_sheet(job_id):
    """返回网格预览图。"""
    job_dir = WORK_DIR / job_id / 'output'
    fp = job_dir / 'contact_sheet.jpg'
    if not fp.is_file():
        abort(404)
    return send_file(str(fp), mimetype='image/jpeg')


@app.route('/api/results/<job_id>/manifest')
def serve_manifest(job_id):
    """返回 manifest.json（可下载）。"""
    job_dir = WORK_DIR / job_id / 'output'
    fp = job_dir / 'manifest.json'
    if not fp.is_file():
        abort(404)
    return send_file(str(fp), as_attachment=True, download_name='manifest.json')


@app.route('/api/results/<job_id>/download')
def download_all(job_id):
    """打包下载全部结果（ZIP）。"""
    job_dir = WORK_DIR / job_id / 'output'
    if not job_dir.is_dir():
        abort(404)

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(job_dir.iterdir()):
            if f.is_file():
                zf.write(f, f.name)
    mem_zip.seek(0)
    return send_file(
        mem_zip, as_attachment=True,
        download_name=f'keyframes_{job_id}.zip',
        mimetype='application/zip',
    )


@app.route('/api/results/<job_id>/pack', methods=['POST'])
def pack_to_path(job_id):
    """将输出文件打包保存到服务器本地指定路径。"""
    import shutil as _shutil

    try:
        body = request.get_json(silent=True) or {}
        save_path = body.get('path', '').strip()
        if not save_path:
            return jsonify({'error': '未指定保存路径'}), 400

        job_dir = WORK_DIR / job_id / 'output'
        if not job_dir.is_dir():
            return jsonify({'error': '输出文件不存在，请先处理视频'}), 404

        save_path = Path(save_path).expanduser()
        # 相对路径基于应用目录解析
        if not save_path.is_absolute():
            save_path = Path(__file__).parent / save_path
        save_path = save_path.resolve()
        # 确保父目录存在
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 如果以 .zip 结尾则打包为 ZIP，否则复制到目录
        if save_path.suffix.lower() == '.zip':
            with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(job_dir.iterdir()):
                    if f.is_file():
                        zf.write(f, f.name)
            file_count = sum(1 for _ in job_dir.iterdir() if _.is_file())
            size = save_path.stat().st_size
        else:
            # 保存为目录
            save_path.mkdir(parents=True, exist_ok=True)
            file_count = 0
            size = 0
            for f in sorted(job_dir.iterdir()):
                if f.is_file():
                    dst = save_path / f.name
                    _shutil.copy2(f, dst)
                    file_count += 1
                    size += f.stat().st_size

        return jsonify({
            'ok': True,
            'path': str(save_path),
            'file_count': file_count,
            'size': size,
        })
    except PermissionError:
        return jsonify({'error': f'没有权限写入该位置：{save_path}\n请选择其他目录（如下载文件夹）'}), 403
    except Exception as e:
        return jsonify({'error': f'打包失败：{str(e)}'}), 500


@app.route('/api/browse', methods=['POST'])
def browse_path():
    """让用户通过系统文件对话框选择保存路径（调用 osascript）。"""
    import subprocess as _sp
    try:
        # macOS 文件对话框
        script = (
            'set theFile to (choose file name with prompt "选择保存位置" '
            'default name "keyframes_output.zip")\n'
            'return POSIX path of theFile'
        )
        res = _sp.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=120,
        )
        if res.returncode == 0:
            path = res.stdout.strip()
            return jsonify({'ok': True, 'path': path})
        else:
            return jsonify({'ok': False, 'cancelled': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/open-finder', methods=['POST'])
def open_in_finder():
    """在 macOS Finder 中显示指定文件或文件夹。"""
    import subprocess as _sp
    body = request.get_json(silent=True) or {}
    path = body.get('path', '').strip()
    if not path:
        return jsonify({'error': '未指定路径'}), 400

    target = Path(path).expanduser().resolve()
    try:
        if target.is_file():
            # 选中文件
            _sp.run(['open', '-R', str(target)], check=True)
        elif target.is_dir():
            # 打开文件夹
            _sp.run(['open', str(target)], check=True)
        else:
            return jsonify({'error': '路径不存在'}), 404
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/results/<job_id>/cleanup', methods=['POST'])
def cleanup_job(job_id):
    """清理任务文件。"""
    job_dir = WORK_DIR / job_id
    if job_dir.exists():
        import shutil
        shutil.rmtree(job_dir, ignore_errors=True)
    with _jobs_lock:
        _jobs.pop(job_id, None)
    return jsonify({'ok': True})


if __name__ == '__main__':
    print('=' * 50)
    print('  FrameFlow — Web 应用')
    print('  访问 http://127.0.0.1:5000')
    print('  按 Ctrl+C 退出')
    print('=' * 50)
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
