#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$PROJECT_DIR"
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$PROJECT_DIR/dist" \
  --workpath "$PROJECT_DIR/build" \
  "$PROJECT_DIR/tin_earthwork.spec"

APP_PATH="$PROJECT_DIR/dist/TIN土方自动算量系统.app"
ZIP_PATH="$PROJECT_DIR/dist/TIN土方自动算量系统-macos.zip"

# 清除构建环境可能遗留的 Finder 元数据。正式签名需要开发者证书，
# 当前脚本只生成未签名的本地 macOS 分发包。
xattr -cr "$APP_PATH"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"
printf 'Build completed (unsigned macOS bundle):\n  %s\n  %s\n' "$APP_PATH" "$ZIP_PATH"
