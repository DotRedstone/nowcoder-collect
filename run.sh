#!/usr/bin/env bash
# 默认使用新的 Textual 审查工作台；--legacy 保留原 curses 界面。
set -euo pipefail
cd "$(dirname "$0")"
if [[ "${1:-}" == "--legacy" ]]; then
  shift
  exec python3 tui.py "$@"
fi
if ! python3 -c 'import textual' >/dev/null 2>&1; then
  echo "需要安装 Textual: python3 -m pip install -r requirements-tui.txt" >&2
  echo "或者使用 ./run.sh --legacy 启动旧版零依赖界面。" >&2
  exit 1
fi
exec python3 review_tui.py "$@"
