#!/usr/bin/env python3
"""生成 docs/preview.png —— README 用的光标预览图。

不依赖截图：直接从已安装的光标主题解码 Xcursor 文件，按棋盘底合成。
所以这张图是**可复现**的（同一主题跑两次结果一致），也不涉及任何屏幕内容。

用法:
    python3 docs/make_preview.py [主题名] [输出路径]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from cursorkit import CursorFile, scan_themes  # noqa: E402

# 挑一排有代表性的光标（Xcursor 规范名）
SHOWCASE = [
    ("left_ptr", "正常选择"),
    ("xterm", "文本"),
    ("hand2", "链接"),
    ("wait", "忙"),
    ("left_ptr_watch", "后台运行"),
    ("cross", "精确"),
    ("circle", "不可用"),
    ("move", "移动"),
    ("bottom_side", "垂直调整"),
    ("left_side", "水平调整"),
]

CANVAS_BG_A = (255, 255, 255)
CANVAS_BG_B = (232, 232, 236)
LABEL_FG = (60, 60, 70)
CHECKER = 8
CELL = 116
COLS = 5
PAD = 28
TOP = 46


def checkerboard(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h), CANVAS_BG_A)
    d = ImageDraw.Draw(img)
    for y in range(0, h, CHECKER):
        for x in range(0, w, CHECKER):
            if ((x // CHECKER) + (y // CHECKER)) % 2:
                d.rectangle([x, y, x + CHECKER - 1, y + CHECKER - 1], fill=CANVAS_BG_B)
    return img


def main() -> int:
    theme_name = sys.argv[1] if len(sys.argv) > 1 else "Kinomoto-Sakura"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "preview.png"

    themes = {t.name: t for t in scan_themes()}
    theme = themes.get(theme_name)
    if theme is None or not theme.valid:
        print(f"找不到可用的主题 {theme_name!r}；可用：{', '.join(t.name for t in themes.values() if t.valid)}")
        return 1

    rows = (len(SHOWCASE) + COLS - 1) // COLS
    width = COLS * CELL + PAD * 2
    height = TOP + rows * (CELL + 22) + PAD
    img = checkerboard((width, height))

    try:
        font = ImageFont.load_default(size=13)
    except TypeError:                      # 老版本 Pillow
        font = ImageFont.load_default()

    d = ImageDraw.Draw(img)
    d.text((PAD, 14), f"{theme.name}  ·  {len(theme.cursors)} cursors",
           fill=LABEL_FG, font=font)

    drawn = 0
    for i, (name, label) in enumerate(SHOWCASE):
        cur = theme.find(name)
        if cur is None:
            continue
        try:
            cf = CursorFile.load(cur.path, want=96)
            frame = cf.frames(96)[0]
        except Exception as e:                          # noqa: BLE001
            print(f"  跳过 {name}: {e}")
            continue

        art = Image.frombytes("RGBA", (frame.width, frame.height), frame.rgba)
        # 缩到格子内（保持比例），整数倍缩放更锐利
        scale = max(1, min(CELL - 24, 84) // max(art.width, 1))
        if scale > 1:
            art = art.resize((art.width * scale, art.height * scale), Image.NEAREST)

        col, row = i % COLS, i // COLS
        cx = PAD + col * CELL + (CELL - art.width) // 2
        cy = TOP + row * (CELL + 22) + (CELL - 8 - art.height) // 2
        img.paste(art, (cx, cy), art)

        tw = d.textlength(label, font=font)
        d.text((PAD + col * CELL + (CELL - tw) / 2, TOP + row * (CELL + 22) + CELL - 2),
               label, fill=LABEL_FG, font=font)
        drawn += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)

    # 自检：不是空图、能看到内容
    px = img.convert("RGB")
    colors = px.getcolors(maxcolors=1 << 20) or []
    non_bg = sum(c for c, col in colors if col not in (CANVAS_BG_A, CANVAS_BG_B))
    print(f"已生成 {out}")
    print(f"  尺寸 {img.width}×{img.height}   画了 {drawn} 个光标")
    print(f"  非背景像素 {non_bg}（{non_bg / (img.width * img.height) * 100:.1f}%）")
    return 0 if drawn and non_bg > 2000 else 1


if __name__ == "__main__":
    sys.exit(main())
