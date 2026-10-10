#!/usr/bin/env bash
# ============================================================================
# install-kinomoto-cursor.sh
#
# 把你现有的 Windows 光标包整理成一个 Linux/Wayland 能真正加载的光标主题。
#
# 背景：
#   你手上的 KINOMOTO SAKURA 是 Windows 主题包（.ani 动画光标 + .inf 安装脚本），
#   Linux 完全不认。但包里的 ~/.icons/"KINOMOTO SAKURA"/cursors/ 下已经有人
#   转换好了一套完整的 Xcursor 光标（22 个实体文件 + 124 个别名，12 档尺寸，
#   8~20 帧动画）。只是它躺在一个"没有任何配置引用"的名字下。
#
#   本脚本把那套光标搬到规范位置和规范名字下，让 DMS / niri / GTK 都能认出来。
#
# 用法: ./install-kinomoto-cursor.sh
# ============================================================================
set -euo pipefail

SRC="$HOME/.icons/KINOMOTO SAKURA/cursors"
DST_NAME="Kinomoto-Sakura"          # 纯 ASCII 连字符，避开 U+2011 断字连字符陷阱
DST="$HOME/.local/share/icons/$DST_NAME"

ok()  { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m  !\033[0m %s\n' "$*"; }
info(){ printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m错误: %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$SRC" ] || die "找不到源光标目录: $SRC"

info "源: $SRC"
info "目标: $DST"

# ---- 1. 搬运光标文件 -------------------------------------------------------
mkdir -p "$DST"
if [ -d "$DST/cursors" ] && [ -n "$(ls -A "$DST/cursors" 2>/dev/null)" ]; then
    warn "$DST/cursors 已存在，跳过搬运（如需重装请先手动删除该目录）"
else
    # mv 保持同分区秒级完成；-a 保留符号链接（很多别名是相对链接，必须保真）
    mv "$SRC" "$DST/cursors"
    ok "已搬运 $(find "$DST/cursors" -maxdepth 1 | wc -l) 个条目到 $DST/cursors"
fi

# ---- 2. 清掉不是光标的杂物 ------------------------------------------------
for junk in thumbnail.png wayland-cursor; do
    if [ -e "$DST/cursors/$junk" ]; then
        rm -f "$DST/cursors/$junk"
        ok "清理杂物: cursors/$junk"
    fi
done

# ---- 3. 写主题元数据 ------------------------------------------------------
cat > "$DST/index.theme" <<EOF
[Icon Theme]
Name=${DST_NAME}
Comment=KINOMOTO SAKURA cursor theme (converted from Windows .ani)
Inherits=Adwaita
EOF
cp -a "$DST/index.theme" "$DST/cursor.theme"
ok "已写入 index.theme / cursor.theme (Inherits=Adwaita 作为缺失光标名的兜底)"

# ---- 4. 把两个坏掉的旧安装挪走 --------------------------------------------
BK="$HOME/.icons/.disabled-$(date +%Y%m%d)"
mkdir -p "$BK"
for stale in "$HOME/.icons/Kinomoto‑Sakura" "$HOME/.icons/KINOMOTO SAKURA"; do
    if [ -d "$stale" ]; then
        mv "$stale" "$BK/" 2>/dev/null && ok "旧安装已挪走: $stale -> $BK/" || true
    fi
done
warn "/usr/share/icons/Kinomoto‑Sakura 是 root 所有且布局同样不合法，可选清理："
warn "  sudo rm -rf '/usr/share/icons/Kinomoto‑Sakura'"

# ---- 5. 自检 --------------------------------------------------------------
info "自检："
for n in left_ptr default text hand2 wait progress crosshair not-allowed ew-resize ns-resize; do
    if [ -f "$DST/cursors/$n" ] || [ -L "$DST/cursors/$n" ]; then
        printf '  ✓ %s\n' "$n"
    else
        printf '  \033[31m✘ %s 缺失\033[0m\n' "$n"
    fi
done
printf '\n'
info "实体文件 $(find "$DST/cursors" -maxdepth 1 -type f | wc -l) 个 / 条目共 $(ls -1 "$DST/cursors" | wc -l) 个 / $(du -sh "$DST" | cut -f1)"
