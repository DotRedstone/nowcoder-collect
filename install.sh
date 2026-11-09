#!/usr/bin/env bash
# 一键安装：把本仓库的 skill/ 软链为 DSH skill，供 skill-filesystem 自动发现
set -euo pipefail

DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
SKILL_NAME="nowcoder-collect"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skill"
DEST="$DSH_HOME/skills/$SKILL_NAME"

mkdir -p "$DSH_HOME/skills"

if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  echo "目标已存在：$DEST"
  echo "覆盖安装会删除该路径，是否继续？(y/N)"
  read -r ans
  case "${ans:-}" in
    y|Y) rm -rf "$DEST" ;;
    *)   echo "已取消"; exit 0 ;;
  esac
fi

ln -s "$SRC" "$DEST"
echo "✅ 已安装 skill：$DEST -> $SRC"
