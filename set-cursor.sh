#!/usr/bin/env bash
# ============================================================================
# set-cursor.sh —— 兼容入口
#
# 实际实现已经搬进 Python（cursorkit/cli.py + cursorkit/apply.py），
# 这里只做转发，避免两套实现各改各的、慢慢漂移。
#
# 用法（和以前一样）:
#   ./set-cursor.sh <主题名> [尺寸]     # 切换
#   ./set-cursor.sh --list              # 列出可用主题
#   ./set-cursor.sh --help
#
# 等价于:
#   cursor-manager <主题名> [尺寸]
#   cursor-manager --list
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

if [ -x "$HERE/cursor-manager" ]; then
    exec "$HERE/cursor-manager" "$@"
fi

# 源码目录不完整时（比如只装了包）退回 PATH 上的命令
if command -v cursor-manager >/dev/null 2>&1; then
    exec cursor-manager "$@"
fi

echo "找不到 cursor-manager。请在项目目录里运行，或先安装：" >&2
echo "  ./install.sh          # 用户级安装" >&2
echo "  pipx install .        # 或者用 pipx" >&2
exit 127
