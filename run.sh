#!/bin/bash
# run.sh — FrameFlow 启动脚本
# 用法：./run.sh

set -e

cd "$(dirname "$0")"

echo "=============================="
echo "  FrameFlow"
echo "=============================="
echo ""

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 python3，请先安装 Python 3.8+"
    exit 1
fi

# 检查 ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "[错误] 未找到 ffmpeg，请先安装："
    echo "  macOS:  brew install ffmpeg"
    echo "  Ubuntu: sudo apt install ffmpeg"
    exit 1
fi

# 检查 Python 依赖
MISSING=""
python3 -c "import flask" 2>/dev/null || MISSING="$MISSING flask"
python3 -c "import PIL" 2>/dev/null || MISSING="$MISSING Pillow"
python3 -c "import imagehash" 2>/dev/null || MISSING="$MISSING imagehash"

if [ -n "$MISSING" ]; then
    echo "[提示] 安装缺失的依赖：$MISSING"
    python3 -m pip install $MISSING --break-system-packages
    echo ""
fi

echo "启动 Web 服务..."
echo "  浏览器访问：http://127.0.0.1:5000"
echo "  按 Ctrl+C 退出"
echo ""

# macOS 自动打开浏览器
if [ "$(uname)" = "Darwin" ]; then
    (sleep 1.5 && open http://127.0.0.1:5000) &
fi

python3 app.py
