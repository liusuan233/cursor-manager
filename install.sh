#!/usr/bin/env bash
# ============================================================================
# install.sh —— 把 cursor-manager 装成可从应用启动器打开的程序
#
# 做三件事（都只写用户目录，不需要 sudo）：
#   1. 生成 ~/.local/share/applications/cursor-manager.desktop
#   2. 把图标装进 ~/.local/share/icons/hicolor/<尺寸>/apps/
#   3. 在 ~/.local/bin 放一个 cursor-manager 命令
#
# 卸载：./install.sh --uninstall
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APPS="$HOME/.local/share/applications"
BIN="$HOME/.local/bin"
HICOLOR="$HOME/.local/share/icons/hicolor"
SIZES=(256 128 64 48 32)

ok()  { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
info(){ printf '\033[36m==>\033[0m %s\n' "$*"; }

uninstall() {
    info "卸载"
    rm -f "$APPS/cursor-manager.desktop" && ok "移除桌面入口"
    rm -f "$BIN/cursor-manager" && ok "移除 ~/.local/bin/cursor-manager"
    for s in "${SIZES[@]}"; do rm -f "$HICOLOR/${s}x${s}/apps/cursor-manager.png"; done
    ok "移除图标"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

[ -x "$HERE/cursor-manager" ] || { echo "找不到可执行的 cursor-manager" >&2; exit 1; }

info "安装 cursor-manager（源: $HERE）"

# ---- 1. 图标 ----
for s in "${SIZES[@]}"; do
    src="$HERE/icons/cursor-manager-$s.png"
    [ -f "$src" ] || { [ "$s" = 256 ] && src="$HERE/icons/cursor-manager.png"; }
    if [ -f "$src" ]; then
        mkdir -p "$HICOLOR/${s}x${s}/apps"
        cp -f "$src" "$HICOLOR/${s}x${s}/apps/cursor-manager.png"
    fi
done
mkdir -p "$HICOLOR/scalable/apps"
cp -f "$HERE/icons/cursor-manager.png" "$HICOLOR/256x256/apps/cursor-manager.png" 2>/dev/null || true
ok "图标 -> $HICOLOR/*/apps/cursor-manager.png"

# ---- 2. 桌面入口 ----
mkdir -p "$APPS"
cat > "$APPS/cursor-manager.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Cursor Theme Manager
Name[zh_CN]=光标主题管理器
Comment=Manage mouse cursor themes for niri / Wayland (niri, DMS, GTK, XWayland)
Comment[zh_CN]=管理 niri / Wayland 下的鼠标光标主题（niri、DMS、GTK、XWayland 一并生效）
Exec=$HERE/cursor-manager
Path=$HERE
Icon=cursor-manager
Terminal=false
Categories=Settings;DesktopSettings;GTK;
Keywords=cursor;mouse;pointer;theme;niri;wayland;
Keywords[zh_CN]=光标;鼠标;指针;主题;niri;wayland;
StartupNotify=true
EOF
chmod 644 "$APPS/cursor-manager.desktop"
ok "桌面入口 -> $APPS/cursor-manager.desktop"

# ---- 3. 命令行 ----
mkdir -p "$BIN"
ln -sf "$HERE/cursor-manager" "$BIN/cursor-manager"
ok "命令 -> $BIN/cursor-manager"

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk4-update-icon-cache >/dev/null && gtk4-update-icon-cache -f -t "$HICOLOR" >/dev/null 2>&1 || true

cat <<EOF

$(printf '\033[32m装好了。\033[0m')

  · 应用启动器里搜 "光标" 或 "Cursor" 就能打开
  · 终端里直接跑:  cursor-manager
  · 只列主题:      cursor-manager --list

若 ~/.local/bin 不在 PATH 里，加一句到 ~/.bashrc:
  export PATH="\$HOME/.local/bin:\$PATH"
EOF
