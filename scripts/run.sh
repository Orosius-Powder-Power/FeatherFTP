#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -d ".venv" ]; then
  "${PYTHON_BIN}" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if command -v fc-match >/dev/null 2>&1; then
  if ! fc-match "Noto Sans CJK SC" | grep -Eiq "Noto|CJK|WenQuanYi|YaHei|SimSun|SimHei|Source Han"; then
    echo "提示：当前 Linux/WSL 环境未检测到中文字体。若界面中文显示为方块，请安装 fonts-noto-cjk。"
    echo "Ubuntu/WSL 示例：sudo apt install fonts-noto-cjk"
  fi
fi

export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
python -m ftp_client

# WSL 运行：bash scripts/run.sh
# Windows PowerShell 运行：
#   py -m venv .venv
#   .\.venv\Scripts\Activate.ps1
#   python -m pip install -r requirements.txt
#   $env:PYTHONPATH = "src"
#   python -m ftp_client
# Windows 批处理运行：scripts\run_windows.bat
