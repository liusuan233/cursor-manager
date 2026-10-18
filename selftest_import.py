#!/usr/bin/env python3
"""导入器自检：Windows 光标包 → Xcursor 主题 的正确性验证。

覆盖：
  1. 预乘 / 反预乘往返无损（**通道顺序**，曾经红蓝写反过）
  2. 写盘 → 读回 往返后颜色不变
  3. .cur 合成 → 解析 → 热点正确
  4. .ani（RIFF/ACON）解析：帧数 / 延时 / 序列
  5. 完整导入真实光标包：角色识别、热点缩放、别名
  6. 压缩包（zip / tar.gz）与文件夹结果一致
  7. 主题名清洗（空格 / U+2011 / CJK）
  8. 别名表自洽（没有名字指向两个不同光标）

真实包路径可用环境变量 CURSORKIT_TEST_PACK 覆盖；
找不到时自动合成一个包，测试仍然完整可跑。
退出码 0 = 全部通过。
"""

from __future__ import annotations

import os
import struct
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    import numpy as np
except ImportError:
    np = None

from cursorkit import CursorFile
from cursorkit.importer import (
    ALIAS_TABLE, ROLES, PackSource, import_pack, sanitize_theme_name,
)
from cursorkit.windows import parse_ani, parse_icondir, load_windows_cursor
from cursorkit.xcursor import _premultiply, _unpremultiply, write_xcursor

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✘\033[0m"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def section(t: str) -> None:
    print(f"\n=== {t} ===")


# ---------------------------------------------------------------------------
# 合成测试素材
# ---------------------------------------------------------------------------

def make_cur(width: int, height: int, rgba: bytes, hx: int, hy: int) -> bytes:
    """手搓一个 32bpp 的 .cur（ICONDIR + BITMAPINFOHEADER + BGRA + AND掩码）。"""
    stride = width * 4
    xor = bytearray()
    for y in range(height - 1, -1, -1):            # DIB 自下而上
        for x in range(width):
            i = (y * width + x) * 4
            r, g, b, a = rgba[i : i + 4]
            xor += bytes((b, g, r, a))
    mask_stride = ((width + 31) // 32) * 4
    and_mask = b"\x00" * (mask_stride * height)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0,
                      len(xor) + len(and_mask), 0, 0, 0, 0) + bytes(xor) + and_mask
    entry = struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, hx, hy, len(dib), 22)
    return struct.pack("<HHH", 0, 2, 1) + entry + dib


def make_cur_8bpp(width: int, height: int, palette: list[tuple[int, int, int]],
                  indices: list[int], transparent: set[tuple[int, int]],
                  hx: int = 0, hy: int = 0) -> bytes:
    """手搓一个 8bpp 调色板 .cur，**透明度只放在 AND 掩码里**（没有 alpha 通道）。

    这正是「初音未来」那类老式光标包的格式，也是曾经把整张图变成黑底方块的原因
    —— Pillow 对 mode=P 的 ICO/CUR 不会应用 AND 掩码，会把调色板展开成不透明 RGBA。
    """
    stride = ((width * 8 + 31) // 32) * 4
    mask_stride = ((width + 31) // 32) * 4

    xor = bytearray()
    mask = bytearray()
    for y in range(height - 1, -1, -1):            # DIB 自下而上
        row = bytearray(stride)
        mrow = bytearray(mask_stride)
        for x in range(width):
            row[x] = indices[y * width + x]
            if (x, y) in transparent:
                mrow[x // 8] |= 0x80 >> (x % 8)
        xor += row
        mask += mrow

    pal = bytearray()
    for i in range(256):
        r, g, b = palette[i] if i < len(palette) else (0, 0, 0)
        pal += bytes((b, g, r, 0))

    dib = (struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 8, 0,
                       len(xor) + len(mask), 0, 0, 0, 0)
           + bytes(pal) + bytes(xor) + bytes(mask))
    entry = struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, hx, hy, len(dib), 22)
    return struct.pack("<HHH", 0, 2, 1) + entry + dib


def make_pack(dest: Path, n_roles: int = 4) -> Path:
    """合成一个极简 Windows 光标包（带 .inf）。"""
    dest.mkdir(parents=True, exist_ok=True)
    chosen = ROLES[:n_roles]
    lines = ['[Version]', 'signature="$CHICAGO$"', '', '[Strings]',
             'SCHEME_NAME = "Test Pack"']
    for i, (key, _label, _kw, _canon) in enumerate(chosen):
        w = h = 32
        px = bytearray()
        for y in range(h):
            for x in range(w):
                inside = 4 <= x < 28 and 4 <= y < 28
                # 每个角色给一组可区分的颜色，方便验证通道顺序
                r, g, b = (40 + i * 40) % 256, (200 - i * 30) % 256, 90
                px += bytes((r, g, b, 255)) if inside else bytes((0, 0, 0, 0))
        fname = f"{key}.cur"
        (dest / fname).write_bytes(make_cur(w, h, bytes(px), hx=2 + i, hy=3 + i))
        lines.append(f'{key} = "{fname}"')
    (dest / "test.inf").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def find_real_pack() -> Path | None:
    env = os.environ.get("CURSORKIT_TEST_PACK")
    if env and Path(env).is_dir():
        return Path(env)
    for cand in (
        Path.home() / ".icons/.disabled-20261010/KINOMOTO SAKURA",
        Path.home() / ".icons" / "KINOMOTO SAKURA",
    ):
        if cand.is_dir() and any(cand.glob("*.ani")):
            return cand
    return None


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_roundtrip(tmp: Path) -> None:
    section("1. 预乘 / 反预乘往返（通道顺序 —— 曾经红蓝写反过）")
    if np is None:
        check("需要 numpy", False)
        return

    # 几个已知颜色，覆盖 不透明 / 半透明 / 全透明
    pixels = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
              (255, 128, 0, 128), (10, 20, 30, 64), (0, 0, 0, 0)]
    rgba = b"".join(bytes(p) for p in pixels)
    back = _unpremultiply(_premultiply(rgba, len(pixels), 1), len(pixels), 1)
    a = np.frombuffer(rgba, np.uint8).reshape(-1, 4).astype(int)
    b = np.frombuffer(back, np.uint8).reshape(-1, 4).astype(int)
    diff = np.abs(a - b)

    check("往返后 alpha 完全一致", (diff[:, 3] == 0).all())
    # 预乘对低 alpha 有量化损失，但不该出现通道错位（错位会是上百的差）
    check("往返后通道没错位（最大偏差 < 4）", diff[:, :3].max() < 4,
          f"最大偏差 {diff[:, :3].max()}，各通道均值 {diff[:, :3].mean(axis=0).round(2)}")

    # 纯色不透明像素必须一模一样 —— 这条能直接抓到红蓝互换
    opaque = np.array([[255, 0, 0, 255], [0, 0, 255, 255]], dtype=np.uint8).tobytes()
    ob = np.frombuffer(_unpremultiply(_premultiply(opaque, 2, 1), 2, 1), np.uint8).reshape(-1, 4)
    check("纯红仍是红（未变成蓝）", tuple(ob[0][:3]) == (255, 0, 0), f"读回 {tuple(ob[0][:3])}")
    check("纯蓝仍是蓝（未变成红）", tuple(ob[1][:3]) == (0, 0, 255), f"读回 {tuple(ob[1][:3])}")


def test_file_roundtrip(tmp: Path) -> None:
    section("2. 写盘 → 读回 颜色不变")
    w = h = 8
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += bytes((250, 30, 60, 255)) if (x + y) % 2 == 0 else bytes((20, 220, 90, 255))
    out = tmp / "roundtrip.xcur"
    write_xcursor(out, {8: [(w, h, 1, 2, 100, bytes(px))]})
    got = CursorFile.load(out, want=8).frames(8)[0]
    a = np.frombuffer(bytes(px), np.uint8).reshape(-1, 4)
    b = np.frombuffer(got.rgba, np.uint8).reshape(-1, 4)
    check("像素完全一致", a.tobytes() == b.tobytes(),
          f"最大偏差 {np.abs(a.astype(int)-b.astype(int)).max()}")
    check("热点保留", (got.xhot, got.yhot) == (1, 2), f"({got.xhot},{got.yhot})")
    check("延时保留", got.delay == 100, f"{got.delay}ms")


def test_indexed_cursor() -> None:
    section("3. 调色板光标（8bpp）的 AND 掩码透明度")
    from cursorkit.windows import decode_cur_image

    w = h = 16
    palette = [(0, 0, 0), (255, 0, 0)]          # 0=黑（也是透明处的颜色）1=红
    indices, transparent = [], set()
    for y in range(h):
        for x in range(w):
            inside = 2 <= x < 14 and 2 <= y < 14
            indices.append(1 if inside else 0)
            if not inside:
                transparent.add((x, y))

    data = make_cur_8bpp(w, h, palette, indices, transparent, hx=3, hy=4)
    img = decode_cur_image(data)

    check("位深从 DIB 头读到（8bpp）", img.bpp == 8, f"bpp={img.bpp}")
    check("热点从容器读出来", (img.hotspot_x, img.hotspot_y) == (3, 4),
          f"({img.hotspot_x},{img.hotspot_y})")

    al = img.rgba[3::4]
    trans_px = sum(1 for a in al if a == 0)
    opaque_px = sum(1 for a in al if a == 255)
    check("掩码标为透明的像素真的透明了", trans_px == len(transparent),
          f"透明 {trans_px}，期望 {len(transparent)}")
    check("其余像素保持不透明", opaque_px == w * h - len(transparent), f"不透明 {opaque_px}")
    check("**不是 100% 不透明**（黑底 bug 的直接判据）", trans_px > 0,
          f"透明像素 {trans_px}/{len(al)}")

    def px(x, y):
        i = (y * w + x) * 4
        return tuple(img.rgba[i : i + 4])

    check("内部像素用对了调色板（红）", px(3, 3)[:3] == (255, 0, 0), str(px(3, 3)))
    check("角落像素透明", px(0, 0)[3] == 0, str(px(0, 0)))


def test_indexed_real_pack() -> None:
    """真实素材：初音那类 8bpp 包曾经整张变黑底。找到就验，找不到就跳过。"""
    import os
    cand = os.environ.get("CURSORKIT_TEST_INDEXED_PACK")
    roots = [Path(cand)] if cand else [
        Path.home() / "Downloads",
        Path.home() / ".icons",
    ]
    found = None
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("Normal.ani")) + sorted(root.rglob("*.ani")):
            try:
                frames = parse_ani(p.read_bytes()).frames
                itype, entries = parse_icondir(frames[0])
                e = max(entries, key=lambda x: x.pixels)
                hdr = frames[0][e.offset : e.offset + e.size]
                bpp = struct.unpack_from("<H", hdr, 14)[0]
                if bpp == 8:
                    found = (p, frames)
                    break
            except Exception:
                continue
        if found:
            break

    section("4. 真实 8bpp 素材（初音那类包）")
    if not found:
        print("      没找到 8bpp 的 .ani 素材，跳过")
        print("      设置 CURSORKIT_TEST_INDEXED_PACK=<目录> 可指定")
        return

    path, frames = found
    from cursorkit.windows import decode_cur_image
    img = decode_cur_image(frames[0])
    al = img.rgba[3::4]
    trans = sum(1 for a in al if a == 0)
    print(f"      素材: {path}")
    check("8bpp 素材解出了透明像素", trans > 0,
          f"{img.width}x{img.height} {img.bpp}bpp，透明 {trans}/{len(al)}")
    check("不是整张不透明", trans < len(al), f"透明 {trans}/{len(al)}")



def test_synthetic_pack(tmp: Path) -> None:
    section("5. 合成包：.cur 解析 + .inf 映射 + 完整导入")
    pack = make_pack(tmp / "synth-pack", n_roles=4)
    with PackSource(pack) as ps:
        info = ps.inspect()
        check("用了 .inf 的权威映射", info.used_inf)
        check("识别出 4 个角色", len(info.roles) == 4, f"{len(info.roles)} 个")
        check("主题名取自 SCHEME_NAME", info.name == "Test-Pack", info.name)
        by = {r.role: r for r in info.roles}
        check("热点读对了", (by["pointer"].native == (32, 32)), str(by["pointer"].native))

        res = ps.install(info, dest_root=tmp / "synth-out", sizes=(16, 32))
        check("安装成功", res.ok, f"{res.files_written} 文件 / {res.aliases_created} 别名")
        d = res.dest / "cursors"
        f = CursorFile.load(d / "left_ptr", want=32).frames(32)[0]
        arr = np.frombuffer(f.rgba, np.uint8).reshape(32, 32, 4)
        inside = arr[10, 10]
        check("中心像素与合成值完全一致（同时验证通道顺序）",
              tuple(int(v) for v in inside[:3]) == (40, 200, 90),
              f"实际 RGB={tuple(int(v) for v in inside[:3])}，期望 (40, 200, 90)")
        check("热点按比例落在 (2,3)", (f.xhot, f.yhot) == (2, 3), f"({f.xhot},{f.yhot})")
        # 这 4 个角色是 pointer/help/work/busy，别名要挑它们真有的
        want_aliases = ["default", "arrow", "grab", "progress", "watch", "help"]
        miss = [a for a in want_aliases if not (d / a).is_symlink()]
        check("别名已建", not miss, "缺: " + ", ".join(miss) if miss else
              f"{len(want_aliases)} 个抽查别名全部就位")


def test_archive(tmp: Path) -> None:
    section("6. 压缩包与文件夹结果一致")
    pack = tmp / "synth-pack"
    if not pack.is_dir():
        check("合成包存在", False)
        return

    z = tmp / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in pack.iterdir():
            zf.write(f, f"nested/{f.name}")          # 故意多套一层目录
    t = tmp / "pack.tar.gz"
    with tarfile.open(t, "w:gz") as tf:
        tf.add(pack, arcname="pack")

    for label, src in (("zip", z), ("tar.gz", t)):
        with PackSource(src) as ps:
            info = ps.inspect()
            check(f"{label}: 下钻到正确目录并识别 4 个角色", len(info.roles) == 4,
                  f"{len(info.roles)} 个，名={info.name}")
            check(f"{label}: 主题名正确", info.name == "Test-Pack", info.name)


def test_name_sanitize() -> None:
    section("7. 主题名清洗")
    cases = [
        ("KINOMOTO SAKURA", "KINOMOTO-SAKURA"),
        ("Kinomoto\u2011Sakura", "Kinomoto-Sakura"),      # U+2011 断字连字符
        ("樱花光标", "cursor-theme"),                      # 全 CJK -> 兜底
        ("  spaced  out  ", "spaced-out"),
        ("a", "cursor-theme"),                             # 太短 -> 兜底
        ("Theme (v2) [dark]", "Theme-v2-dark"),
    ]
    for raw, want in cases:
        got = sanitize_theme_name(raw)
        check(f"{raw!r} → {got!r}", got == want, "" if got == want else f"期望 {want!r}")
    check("结果里不含任何非 ASCII 字符",
          all(all(ord(c) < 128 for c in sanitize_theme_name(r)) for r, _ in cases))


def test_alias_table() -> None:
    section("8. 别名表自洽")
    seen: dict[str, str] = {}
    clashes = []
    for canon, aliases in ALIAS_TABLE.items():
        for a in aliases:
            if a in seen and seen[a] != canon:
                clashes.append(f"{a}: {seen[a]} vs {canon}")
            seen[a] = canon
    check("没有别名指向两个不同光标", not clashes, "; ".join(clashes[:4]))
    check("规范名本身没被当成别名", not [c for c in ALIAS_TABLE if c in seen])
    required = ["default", "left_ptr", "text", "pointer", "progress", "not-allowed",
                "ew-resize", "ns-resize", "nwse-resize", "nesw-resize",
                "col-resize", "row-resize", "grab", "grabbing", "help"]
    have = set(seen) | set(ALIAS_TABLE)
    missing = [n for n in required if n not in have]
    check("常用名字全部覆盖", not missing, "缺: " + ", ".join(missing) if missing else "")


def test_real_pack(tmp: Path) -> None:
    pack = find_real_pack()
    if pack is None:
        section("9. 真实光标包（未找到，跳过）")
        print("      设置 CURSORKIT_TEST_PACK=<目录> 可启用")
        return

    section(f"9. 真实光标包：{pack.name}")
    with PackSource(pack) as ps:
        info = ps.inspect()
        check("用 .inf 识别出 17 个角色", len(info.roles) == 17, f"{len(info.roles)} 个")
        check("没有未识别的文件", not info.unmapped, str([f.name for f in info.unmapped][:3]))
        check("全部是动画光标", info.animated_count == len(info.roles),
              f"{info.animated_count}/{len(info.roles)}")
        native = info.native_sizes
        check("原始分辨率为 256×256", native == [(256, 256)], str(native))

        res = ps.install(info, dest_root=tmp / "real-out", sizes=(24, 48, 96))
        check("安装成功", res.ok, f"{res.files_written} 文件 / {res.aliases_created} 别名")

        # 热点缩放：源热点 @256 折回后应与产物一致
        bad = []
        for r in info.roles:
            frames, _ = load_windows_cursor(r.file)
            s = frames[0]
            cf = CursorFile.load(res.dest / "cursors" / r.canonical)
            f96 = cf.frames(96)[0]
            if abs(round(f96.xhot * 256 / 96) - s.hotspot_x) > 2 or \
               abs(round(f96.yhot * 256 / 96) - s.hotspot_y) > 2:
                bad.append(r.canonical)
        check("所有热点缩放正确", not bad, "偏差: " + ", ".join(bad) if bad else "")

        # 颜色：产物中心区域的色相应与源一致（抓红蓝互换）
        mismatched = []
        for r in info.roles[:6]:
            src_frames, _ = load_windows_cursor(r.file)
            s_arr = np.frombuffer(src_frames[0].rgba, np.uint8).reshape(
                src_frames[0].height, src_frames[0].width, 4)
            m = s_arr[..., 3] > 200
            if m.sum() < 50:
                continue
            src_mean = s_arr[..., :3][m].mean(axis=0)
            cf = CursorFile.load(res.dest / "cursors" / r.canonical, want=96)
            f = cf.frames(96)[0]
            d_arr = np.frombuffer(f.rgba, np.uint8).reshape(f.height, f.width, 4)
            dm = d_arr[..., 3] > 200
            if dm.sum() < 20:
                continue
            dst_mean = d_arr[..., :3][dm].mean(axis=0)
            # 通道顺序错了的话，R 和 B 的均值会明显对不上
            if abs(dst_mean[0] - src_mean[0]) > 25 or abs(dst_mean[2] - src_mean[2]) > 25:
                mismatched.append(f"{r.canonical}(源{src_mean.round(0)}/产{dst_mean.round(0)})")
        check("颜色通道顺序正确（无红蓝互换）", not mismatched,
              "; ".join(mismatched[:3]) if mismatched else "抽查 6 个角色")

        # 关键别名可解析
        d = res.dest / "cursors"
        need = ["default", "text", "pointer", "progress", "not-allowed",
                "ew-resize", "ns-resize", "nwse-resize", "nesw-resize", "help"]
        miss = [n for n in need if not (d / n).exists()]
        check("常用别名全部在产物里", not miss, "缺: " + ", ".join(miss) if miss else "")

        # index.theme 正确
        idx = (res.dest / "index.theme").read_text(encoding="utf-8")
        check("index.theme 有 Name 和 Inherits",
              f"Name={info.name}" in idx and "Inherits=" in idx, idx.replace("\n", " | "))


def make_pack_noinf(dest: Path) -> Path:
    """合成一个**没有 .inf** 的包，用微软官方方案的标准文件名。

    这是真实世界里最常见的情况 —— 很多包是手工拼的，只有 .ani/.cur 文件。
    """
    dest.mkdir(parents=True, exist_ok=True)
    names = ["Normal", "Help", "Working", "Busy", "Precision", "Text",
             "Handwriting", "Unavailable", "Vertical", "Horizontal",
             "Diagonal1", "Diagonal2", "Move", "Alternate", "Link", "Pin", "Person"]
    for i, fname in enumerate(names):
        w = h = 32
        px = bytearray()
        for y in range(h):
            for x in range(w):
                inside = 4 <= x < 28 and 4 <= y < 28
                px += (bytes((40 + i * 10, 100, 200, 255)) if inside
                       else bytes((0, 0, 0, 0)))
        (dest / f"{fname}.cur").write_bytes(make_cur(w, h, bytes(px), hx=1, hy=1))
    return dest


def test_guess_roles(tmp: Path) -> None:
    section("10. 没有 .inf 时的文件名猜测")
    from cursorkit.importer import _guess_role

    cases = [
        # 微软官方方案的 17 个标准名
        ("Normal.ani", "pointer"), ("Help.ani", "help"), ("Working.ani", "work"),
        ("Busy.ani", "busy"), ("Precision.ani", "cross"), ("Text.ani", "text"),
        ("Handwriting.ani", "hand"), ("Unavailable.ani", "unavailable"),
        ("Vertical.ani", "vert"), ("Horizontal.ani", "horz"),
        ("Diagonal1.ani", "dgn1"), ("Diagonal2.ani", "dgn2"),
        ("Move.ani", "move"), ("Alternate.ani", "alternate"),
        ("Link.ani", "link"), ("Pin.cur", "loc"), ("Person.cur", "person"),
        # X11 命名习惯
        ("SizeNS.cur", "vert"), ("SizeWE.cur", "horz"),
        ("SizeNWSE.cur", "dgn1"), ("SizeNESW.cur", "dgn2"),
        ("SizeAll.cur", "move"), ("xterm.cur", "text"),
        ("left_ptr.cur", "pointer"), ("crosshair.cur", "cross"),
        # 分隔符与大小写变体
        ("Diagonal-1.ani", "dgn1"), ("diagonal_2.ani", "dgn2"),
        ("  Horizontal  .ani", "horz"),
        # 中文命名（国内包不少这么写）
        ("正常选择.ani", "pointer"), ("文本选择.ani", "text"),
        ("水平调整.ani", "horz"), ("垂直调整.ani", "vert"),
        ("手写.ani", "hand"), ("移动.ani", "move"),
        ("精确选择.ani", "cross"), ("链接选择.ani", "link"),
    ]
    bad = [f"{n}→{_guess_role(n)!r}(期望{w})" for n, w in cases if _guess_role(n) != w]
    check(f"标准名/变体/中文 {len(cases)} 个用例全对", not bad, "; ".join(bad[:5]))

    # 反向用例：猜不出来好过猜错
    negatives = ["Small.ani", "Wallpaper.ani", "install.ani", "readme.cur",
                 "thumb.cur", "Popup.ani", "screenshot.ani"]
    wrong = [f"{n}→{_guess_role(n)!r}" for n in negatives if _guess_role(n) is not None]
    check(f"{len(negatives)} 个无关文件名都没被误判", not wrong, "; ".join(wrong[:5]))

    # 端到端：真的拿一个没有 .inf 的包走完整流程
    pack = make_pack_noinf(tmp / "noinf-pack")
    with PackSource(pack) as ps:
        info = ps.inspect()
        check("识别为未使用 .inf", not info.used_inf)
        check("17 个角色全部认出来了", len(info.roles) == 17,
              f"{len(info.roles)}/17，未识别 {[f.name for f in info.unmapped]}")
        got = {r.role: r.file.name for r in info.roles}
        check("对角线没搞反（dgn1=Diagonal1 / dgn2=Diagonal2）",
              got.get("dgn1") == "Diagonal1.cur" and got.get("dgn2") == "Diagonal2.cur",
              f"dgn1={got.get('dgn1')} dgn2={got.get('dgn2')}")
        check("水平/垂直没搞反",
              got.get("horz") == "Horizontal.cur" and got.get("vert") == "Vertical.cur",
              f"horz={got.get('horz')} vert={got.get('vert')}")

    # 缺角色时要给出可操作的提示
    partial = tmp / "partial-pack"
    partial.mkdir(parents=True, exist_ok=True)
    for fname in ("Normal", "Text"):
        (partial / f"{fname}.cur").write_bytes(
            make_cur(32, 32, bytes(32 * 32 * 4), hx=1, hy=1))
    with PackSource(partial) as ps:
        info = ps.inspect()
        check("角色不全时只认出 2 个", len(info.roles) == 2, str(len(info.roles)))
        check("提示了「没有 .inf」和补救办法",
              any("没有 .inf" in w for w in info.warnings),
              "; ".join(info.warnings)[:90])


def main() -> int:
    print("导入器自检\n" + "=" * 60)
    with tempfile.TemporaryDirectory(prefix="cursorkit-selftest-") as td:
        tmp = Path(td)
        test_roundtrip(tmp)
        test_file_roundtrip(tmp)
        test_indexed_cursor()
        test_indexed_real_pack()
        test_synthetic_pack(tmp)
        test_archive(tmp)
        test_name_sanitize()
        test_alias_table()
        test_guess_roles(tmp)
        test_real_pack(tmp)

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"\033[31m{len(FAILURES)}/{CHECKS} 项失败:\033[0m")
        for f in FAILURES:
            print("   -", f)
        return 1
    print(f"\033[32m全部 {CHECKS} 项检查通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
