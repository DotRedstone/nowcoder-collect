#!/usr/bin/env bash
# 牛客代码收集 · 一键启动（登录 → 选比赛 → 爬取 → 分析 → 结果）
set -euo pipefail
cd "$(dirname "$0")"
exec python3 tui.py "$@"
