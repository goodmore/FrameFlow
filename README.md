# FrameFlow

将视频切片成关键图片，提取关键节点，去除重复帧，输出给多模态大模型。

大多数大模型无法直接读取视频。FrameFlow 把视频浓缩为一组「有代表性且不重复」的图片，附带时间戳清单和网格预览图，方便喂给多模态大模型理解视频内容。

## 处理流程

```
视频 → 抽帧(场景检测/固定间隔) → pHash 感知哈希去重 → 均匀采样 → 输出(图片+清单+网格图)
```

## 快速开始

### 环境要求

- Python 3.8+
- ffmpeg / ffprobe（系统命令）

### 安装

```bash
pip install -r requirements.txt --break-system-packages
```

macOS 安装 ffmpeg：
```bash
brew install ffmpeg
```

### 启动 Web 应用

```bash
chmod +x run.sh
./run.sh
```

浏览器访问 http://127.0.0.1:5000 即可使用。

### 命令行使用

```bash
# 基础用法（自动混合模式）
python3 video2images.py video.mp4

# 指定输出目录和帧数上限
python3 video2images.py video.mp4 -o ./out --max-frames 20

# 只用场景检测
python3 video2images.py video.mp4 --mode scene --scene-threshold 0.3

# 只用固定间隔（适合监控录像）
python3 video2images.py video.mp4 --mode interval --interval 3
```

## Web 界面功能

| 功能 | 说明 |
|------|------|
| 拖拽上传 | 支持拖拽或点击选择视频文件 |
| 三种模式 | 自动混合 / 场景检测 / 固定间隔 |
| 实时进度 | 处理过程中显示每一步进度 |
| 统计面板 | 展示抽帧数、去重数、最终帧数等 |
| 帧画廊 | 网格展示所有关键帧缩略图，点击可下载 |
| 网格预览图 | 一张图概览全部关键帧 |
| 批量下载 | ZIP 打包下载全部结果 |
| 大模型提示词 | 一键复制已组装好的提示词模板 |

## 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--mode` | auto | 抽帧模式：auto / scene / interval |
| `--scene-threshold` | 0.4 | 场景检测阈值 0~1，越小越敏感 |
| `--interval` | 2.0 | 固定间隔秒数 |
| `--hamming` | 8 | pHash 去重汉明距离，越小越严格 |
| `--max-frames` | 30 | 最终输出最大帧数 |
| `--grid-cols` | 4 | 网格图列数 |

## 输出文件

| 文件 | 说明 |
|------|------|
| `keyframe_0001.jpg` ... | 按时间排序的关键图片 |
| `manifest.json` | 每帧的文件名、时间戳、时间码元信息 |
| `contact_sheet.jpg` | 网格拼接图，一张图概览全部关键帧 |

## 项目结构

```
FrameFlow/
├── video2images.py     # 核心处理模块（CLI + API）
├── app.py              # Flask Web 后端
├── run.sh              # 启动脚本
├── requirements.txt    # Python 依赖
├── templates/
│   └── index.html      # Web 界面
└── static/
    ├── style.css       # 样式
    └── app.js          # 前端交互
```

## 技术原理

### 场景检测抽帧
使用 ffmpeg 的 `select='gt(scene,阈值)'` 滤镜，自动检测镜头切换处并提取那一帧，适合有镜头切换的视频。

### 感知哈希去重
对每帧计算 pHash 指纹，按时间顺序与上一个保留帧比较汉明距离，距离低于阈值则视为重复并删除。相比逐帧比较，只与上一个保留帧比能避免在长时间静止画面里反复触发保留。

### 均匀采样
去重后若超过最大帧数，均匀抽取到上限，保证大模型单次能消化。

## 编程调用

```python
from video2images import process_video

result = process_video(
    'video.mp4', './output',
    mode='auto',
    scene_threshold=0.4,
    interval=2.0,
    hamming=8,
    max_frames=30,
    progress_callback=lambda step, detail: print(f'{step}: {detail}'),
)

print(result['stats'])
print(result['manifest'])
```
