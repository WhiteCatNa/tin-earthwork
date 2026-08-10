#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v docker &>/dev/null; then
  echo "错误: 未检测到 Docker。"
  echo "请先安装 Docker Desktop: https://www.docker.com/products/docker-desktop"
  echo "安装后启动 Docker Desktop，等待菜单栏图标停止动画，再运行此脚本。"
  exit 1
fi

if ! docker info &>/dev/null; then
  echo "错误: Docker 未启动或无权限访问。"
  echo "请启动 Docker Desktop，等待完全就绪后重试。"
  exit 1
fi

echo "=== 开始 Windows 交叉编译打包 ==="
echo "首次运行会下载 Wine 容器镜像（约 500MB），请耐心等待..."
echo ""

docker run --rm -v "$PROJECT_DIR:/src" \
  cdrx/pyinstaller-windows:python3 \
  "pip install -q -r /src/requirements.txt && pyinstaller --noconfirm --clean /src/tin_earthwork.spec"

if [ -f "$PROJECT_DIR/dist/TIN土方自动算量系统.exe" ]; then
  EXE_SIZE=$(du -h "$PROJECT_DIR/dist/TIN土方自动算量系统.exe" | awk '{print $1}')
  echo ""
  echo "=== 打包完成 ==="
  echo "Windows 可执行文件: $PROJECT_DIR/dist/TIN土方自动算量系统.exe"
  echo "文件大小: $EXE_SIZE"
else
  echo ""
  echo "错误: 打包失败，未找到输出文件。"
  exit 1
fi
