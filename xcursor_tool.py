#!/usr/bin/env python3
"""
xcursor_tool.py — 读取 / 校验 / 导出 Xcursor 主题文件

用途：
  list   <主题目录>              列出所有光标名，标注实体文件与符号链接
  info   <xcursor文件>           打印帧数、标称尺寸、热点、延时
  render <xcursor文件> <out.png> 导出指定标称尺寸的一帧 PNG（默认 24）
"""
import os
import struct
import sys

XCURSOR_MAGIC = b"Xcur"
IMAGE_TYPE = 0xFFFD0002
COMMENT_TYPE = 0xFFFE0001


def read_header(f):
    f.seek(0)
    magic = f.read(4)
    if magic != XCURSOR_MAGIC:
        raise ValueError(f"不是 Xcursor 文件（magic={magic!r}）")
    header_size, version, ntoc = struct.unpack("<III", f.read(12))
    toc = []
    for _ in range(ntoc):
        ctype, subtype, position = struct.unpack("<III", f.read(12))
        toc.append((ctype, subtype, position))
    return version, toc


def read_image(f, position):
    """返回 (nominal, width, height, xhot, yhot, delay, pixels) ；pixels 为未预乘 RGBA bytes"""
    f.seek(position)
    header, ctype, subtype, version, width, height, xhot, yhot, delay = struct.unpack(
        "<IIIIIIIII", f.read(36)
    )
    raw = f.read(width * height * 4)
    out = bytearray(width * height * 4)
    for i in range(width * height):
        b, g, r, a = raw[i * 4 : i * 4 + 4]
        if a == 0:
            out[i * 4 : i * 4 + 4] = b"\x00\x00\x00\x00"
        elif a == 255:
            out[i * 4 : i * 4 + 4] = bytes((r, g, b, 255))
        else:  # 反预乘，还原成直通 alpha
            out[i * 4 : i * 4 + 4] = bytes(
                (
                    min(255, r * 255 // a),
                    min(255, g * 255 // a),
                    min(255, b * 255 // a),
                    a,
                )
            )
    return subtype, width, height, xhot, yhot, delay, bytes(out)


def load(path):
    with open(path, "rb") as f:
        version, toc = read_header(f)
        images = []
        for ctype, subtype, position in toc:
            if ctype != IMAGE_TYPE:
                continue
            images.append(read_image(f, position))
    return version, images


def cmd_list(theme_dir):
    real, links = [], []
    for name in sorted(os.listdir(theme_dir)):
        p = os.path.join(theme_dir, name)
        (links if os.path.islink(p) else real).append(name)
    print(f"实体文件 {len(real)} 个：")
    for n in real:
        p = os.path.join(theme_dir, n)
        try:
            _, imgs = load(p)
            sizes = sorted({i[0] for i in imgs})
            frames = len(imgs) // max(1, len(sizes))
            print(f"  {n:<26} 标称尺寸{sizes}  {frames} 帧/尺寸  {os.path.getsize(p)} bytes")
        except Exception as e:
            print(f"  {n:<26} !! {e}")
    print(f"\n符号链接 {len(links)} 个：")
    for n in links:
        print(f"  {n:<26} -> {os.readlink(os.path.join(theme_dir, n))}")


def cmd_info(path):
    version, images = load(path)
    print(f"{path}\n  版本 {version:#x}  图片总数 {len(images)}")
    by_size = {}
    for img in images:
        by_size.setdefault(img[0], []).append(img)
    for size in sorted(by_size):
        frames = by_size[size]
        w, h, xh, yh, d = frames[0][1], frames[0][2], frames[0][3], frames[0][4], frames[0][5]
        print(
            f"  标称 {size}: {len(frames)} 帧  {w}x{h}  热点({xh},{yh})  延时 {d}ms  总时长 {sum(f[5] for f in frames)}ms"
        )


def cmd_render(path, out_png, want_size=24):
    from PIL import Image

    _, images = load(path)
    by_size = {}
    for img in images:
        by_size.setdefault(img[0], []).append(img)
    size = min(by_size, key=lambda s: abs(s - want_size))
    frame = by_size[size][0]
    nominal, w, h, xh, yh, delay, px = frame
    im = Image.frombytes("RGBA", (w, h), px)
    # 棋盘背景，便于观察 alpha
    bg = Image.new("RGBA", (w, h))
    for y in range(h):
        for x in range(w):
            v = 200 if ((x // 4) + (y // 4)) % 2 else 160
            bg.putpixel((x, y), (v, v, v, 255))
    bg.alpha_composite(im)
    # 热点画十字
    for d in range(-4, 5):
        for (px_, py_) in ((xh + d, yh), (xh, yh + d)):
            if 0 <= px_ < w and 0 <= py_ < h:
                bg.putpixel((px_, py_), (255, 0, 0, 255))
    bg.convert("RGB").resize((w * 6, h * 6), Image.NEAREST).save(out_png)
    print(f"{os.path.basename(path)}: 导出标称 {size} 第 0 帧 -> {out_png}  热点({xh},{yh}) 画布{w}x{h}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list(sys.argv[2])
    elif cmd == "info":
        cmd_info(sys.argv[2])
    elif cmd == "render":
        cmd_render(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 24)
    else:
        print(__doc__)
        sys.exit(1)
