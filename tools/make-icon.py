#!/usr/bin/env python3
"""生成 cursor-manager 的应用图标（全部尺寸）。

为什么要脚本而不是直接放几张 PNG：图标要 32/48/64/128/256/512 六个尺寸档，
手改一张就要重新导出六次，很容易漏。脚本化之后改设计只需跑一遍：

    python3 tools/make-icon.py            # 写进 icons/
    python3 tools/make-icon.py --preview  # 只生成预览图，不动 icons/

设计：
  · 圆角方块 + 蓝紫渐变底（现代应用图标的通行做法，深浅色桌面上都立得住）
  · 白色光标箭头，占画布约 62% —— 32px 下也要一眼认得出
  · 右下三个小色点 = 主题色板，暗示"管理主题"
  · 4 倍超采样后再缩，边缘干净

箭头形状是自己画的，没有用系统主题里的位图 —— 那些有各自的许可证。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "icons"
SIZES = (32, 48, 64, 128, 256, 512)
BASE = 512
SS = 4                       # 超采样倍数

# 配色
GRAD_TOP = (91, 141, 239)    # #5B8DEF 靛蓝
GRAD_BOT = (139, 92, 246)    # #8B5CF6 紫罗兰
SWATCHES = ((247, 118, 142), (224, 175, 104), (158, 206, 106))   # 粉 / 黄 / 绿

# 光标箭头，归一化到 0..1 的包围盒（尖端在左上）
ARROW = [
    (0.000, 0.000),
    (0.000, 0.760),
    (0.190, 0.590),
    (0.310, 0.900),
    (0.450, 0.840),
    (0.330, 0.540),
    (0.550, 0.540),
]


def gradient(size: int, top, bot) -> Image.Image:
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        k = y / max(1, size - 1)
        px[0, y] = tuple(int(top[i] + (bot[i] - top[i]) * k) for i in range(3))
    return img.resize((size, size), Image.NEAREST)


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def arrow_points(cx: float, cy: float, h: float) -> list[tuple[float, float]]:
    """把归一化箭头放到指定中心、按高度缩放。宽度按形状自身比例算。"""
    w = h * 0.66
    x0, y0 = cx - w / 2, cy - h / 2
    return [(x0 + px * w, y0 + py * h) for px, py in ARROW]


def build(size: int = BASE) -> Image.Image:
    S = size * SS
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 底色（圆角 + 渐变）
    bg = gradient(S, GRAD_TOP, GRAD_BOT).convert("RGBA")
    # 顶部高光：alpha 从上往下平滑衰减。用椭圆当遮罩会在画面中间留一道硬弧，
    # 所以按行算 alpha，不能有硬边界。
    import numpy as np
    ys = np.arange(S) / max(1, S - 1)
    alpha = np.clip(40 * (1 - ys) ** 2, 0, 255).astype("uint8")
    gloss = np.zeros((S, S, 4), "uint8")
    gloss[..., :3] = 255
    gloss[..., 3] = alpha[:, None]
    bg = Image.alpha_composite(bg, Image.fromarray(gloss, "RGBA"))

    radius = int(S * 0.225)
    icon.paste(bg, (0, 0), rounded_mask(S, radius))

    d = ImageDraw.Draw(icon)

    # 箭头（先画阴影）
    ah = S * 0.560
    pts = arrow_points(S * 0.500, S * 0.425, ah)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x + S * 0.008, y + S * 0.012) for x, y in pts],
                                   fill=(15, 18, 45, 88))
    shadow = shadow.filter(ImageFilter.GaussianBlur(S * 0.030))
    icon.alpha_composite(Image.composite(shadow, Image.new("RGBA", (S, S), (0, 0, 0, 0)),
                                         rounded_mask(S, radius)))
    d.polygon(pts, fill=(255, 255, 255, 255))

    # 底部三个色点（居中排一行，跟箭头分开）
    r = S * 0.050
    cy = S * 0.800
    gap = r * 2.55
    cx0 = S * 0.5 - gap            # 三个点整体居中
    for i, col in enumerate(SWATCHES):
        cx = cx0 + i * gap
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,),
                  outline=(255, 255, 255, 235), width=max(2, int(S * 0.008)))

    return icon.resize((size, size), Image.LANCZOS)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 cursor-manager 应用图标")
    ap.add_argument("--preview", action="store_true",
                    help="只生成 icons/_preview.png（含小尺寸对照），不写正式图标")
    args = ap.parse_args()

    big = build(BASE)

    if args.preview:
        # 拼一张对照图：大图 + 实际小尺寸放大，方便判断 32px 下认不认得出
        pad = 24
        smalls = [build(s) for s in (32, 48, 64)]
        zooms = [im.resize((s * 4, s * 4), Image.NEAREST)
                 for im, s in zip(smalls, (32, 48, 64))]
        w = BASE + pad * 2 + sum(z.width for z in zooms) + pad * len(zooms) + pad
        h = max(BASE, max(z.height for z in zooms)) + pad * 2
        canvas = Image.new("RGBA", (w, h), (28, 30, 40, 255))
        canvas.alpha_composite(big, (pad, (h - BASE) // 2))
        x = pad + BASE + pad
        for z in zooms:
            canvas.alpha_composite(z, (x, (h - z.height) // 2))
            x += z.width + pad
        p = OUT / "_preview.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(p)
        print(f"  预览 -> {p}  （右边三个是 32/48/64 放大 4 倍的像素对照）")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for s in SIZES:
        im = big if s == BASE else build(s)
        if s == BASE:
            p = OUT / "cursor-manager.png"
        else:
            p = OUT / f"cursor-manager-{s}.png"
        im.save(p)
        print(f"  {p.relative_to(ROOT)}  {s}x{s}  {p.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
