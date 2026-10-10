"""把 Windows 光标主题包转换成 Linux Xcursor 主题。

支持三种输入：
  · 一个文件夹（含 .ani/.cur/.ico，可能还有 .inf）
  · 一个压缩包（.zip / .tar.gz / .tar.xz / .tgz / .tar.bz2）
  · 单个 .ani/.cur/.ico 文件

关于角色映射：如果包里有 `.inf`（微软光标包都有），它的 `[Strings]` 段里
**直接写着官方映射** —— 例如::

    [Strings]
    SCHEME_NAME = "KINOMOTO SAKURA"
    pointer     = "pointer.ani"
    work        = "working.ani"
    hand        = "handwriting.ani"

这是权威数据，比按文件名猜可靠得多。没有 .inf 时才退回关键词猜测。

尺寸：Windows 光标包通常是 256x256，而 Linux 上 24~96 才是常用范围。
所以会从原始分辨率**降采样**出多档（降采样画质损失很小，升采样才会糊）。
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .windows import (
    WINDOWS_SUFFIXES,
    AniFile,
    CursorParseError,
    DecodedImage,
    load_windows_cursor,
    parse_ani,
)
from .xcursor import write_xcursor

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

try:
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

DEFAULT_SIZES = (24, 32, 48, 64, 96)
ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tar")


# ---------------------------------------------------------------------------
# 别名表
#
# 这张表决定了"哪些名字指向同一个光标"。漏了某个名字，那个名字的请求就会
# 回落到 Inherits 主题（通常是 Adwaita）—— 于是出现"一半 Sakura 一半白箭头"。
#
# 其中的哈希名（如 00000000000000020006000e7e9ffc3f）不是乱码：那是旧版
# Firefox / Chromium 用名字的 MD5 去查表的历史遗留，很多程序仍在用。
# ---------------------------------------------------------------------------

ALIAS_TABLE: dict[str, list[str]] = {
    "left_ptr": [
        "default", "arrow", "top_left_arrow", "left-arrow", "grab", "openhand",
        "context-menu", "dnd-none", "fcf21c00b30f7e3f83fe0dfd12e71cff",
    ],
    "xterm": ["text", "ibeam"],
    "hand2": [
        "pointer", "pointing_hand", "hand1", "9d800788f1b08800ae810202380a0822",
        "e29285e634086352946a0e7090d73106",
    ],
    "wait": ["watch", "00000000000000020006000e7e9ffc3f"],
    "left_ptr_watch": [
        "progress", "half-busy", "08e8e1c95fe2fc01f976f1e063a24ccd",
        "3ecb610c1bf2410f44200f48c40d3599",
    ],
    "cross": ["crosshair", "tcross", "cross_reverse", "diamond_cross", "color-picker"],
    "circle": [
        "not-allowed", "crossed_circle", "forbidden", "no-drop", "dnd-no-drop",
        "dnd_no_drop", "X_cursor", "x-cursor", "pirate", "03b6e0fcb3499374a867c041f52298f0",
    ],
    "move": [
        "fleur", "size_all", "all-scroll", "dnd-move", "grabbing", "closedhand",
        "4498f0e0c1937ffe01fd06f973665830", "9081237383d90e509aa00f00170e968f",
    ],
    "left_side": [
        "ew-resize", "size_hor", "sb_h_double_arrow", "h_double_arrow", "col-resize",
        "split_h", "w-resize", "e-resize",
        "sb_left_arrow", "sb_right_arrow", "left_tee", "right_tee",
        "028006030e0e7ebffc7f7070c0600140", "14fef782d02440884392942c1120523",
    ],
    "bottom_side": [
        "ns-resize", "size_ver", "sb_v_double_arrow", "v_double_arrow", "row-resize",
        "split_v", "n-resize", "s-resize", "top_side", "bottom_tee", "top_tee",
        "sb_down_arrow", "double_arrow",
        "00008160000006810000408080010102", "2870a09082c31050810ffdffffe0204",
    ],
    "bottom_right_corner": [
        "nwse-resize", "size_fdiag", "bd_double_arrow", "se-resize", "nw-resize",
        "ul_angle", "lr_angle", "top_left_corner", "c7088f0f3e6c8088236ef8e1e3e70000",
    ],
    "bottom_left_corner": [
        "nesw-resize", "size_bdiag", "fd_double_arrow", "sw-resize", "ne-resize",
        "ur_angle", "ll_angle", "top_right_corner", "fcf1c3c7cd4491d801f1e1c78f100000",
    ],
    "question_arrow": [
        "help", "left_ptr_help", "whats_this", "dnd-ask",
        "5c6cd98b3f3ebcb1f9c7f1c204630408", "d9ce0ab605698f320427677b458ad60b",
    ],
    "pencil": [
        "draft", "dnd-copy", "dnd-link", "copy", "alias",
        "1081e37283d90000800003c07f3ef6bf", "640fb0e74195791501fd1ed57b41487f",
        "6407b0e94181790501fd1e167b474872", "a2a266d0498c3104214a47bd64ab0fc8",
        "b66166c04f8c3109214a4fbd64a50fc8", "3085a0e285430894940527032f8b26df",
    ],
    "center_ptr": ["up_arrow", "up-arrow", "sb_up_arrow"],
    "right_ptr": ["draft_large", "draft_small", "right-arrow"],
    "dotbox": ["dot_box_mask", "draped_box", "icon", "target"],
}

# Windows 光标包里的 17 个角色。
# inf_key 是 .inf [Strings] 段里用的键名（微软官方命名）。
# 顺序 = Windows 注册表 Scheme 值的顺序，不能乱。
ROLES: list[tuple[str, str, str, list[str]]] = [
    # (inf_key,     中文含义,     源文件关键词,                        规范 Xcursor 名)
    ("pointer",     "正常选择",   ["pointer", "normal", "arrow"],       "left_ptr"),
    ("help",        "帮助选择",   ["help", "question", "whats"],        "question_arrow"),
    ("work",        "后台运行",   ["work", "progress", "appstarting"],  "left_ptr_watch"),
    ("busy",        "忙",        ["busy", "wait", "watch"],            "wait"),
    ("cross",       "精确选择",   ["cross", "precision"],               "cross"),
    ("text",        "文本选择",   ["text", "beam", "ibeam", "caret"],   "xterm"),
    ("hand",        "手写",      ["handwriting", "nwpen", "pen"],      "pencil"),
    ("unavailable", "不可用",     ["unavailable", "no", "forbidden"],   "circle"),
    ("vert",        "垂直调整",   ["vert", "ns", "northsouth"],         "bottom_side"),
    ("horz",        "水平调整",   ["horz", "we", "eastwest"],           "left_side"),
    ("dgn1",        "对角线＼",   ["dgn1", "nwse", "fdiag", "diag1"],   "bottom_right_corner"),
    ("dgn2",        "对角线／",   ["dgn2", "nesw", "bdiag", "diag2"],   "bottom_left_corner"),
    ("move",        "移动",      ["move", "sizeall", "fleur", "all"],  "move"),
    ("alternate",   "候选选择",   ["alternate", "uparrow", "alt", "up"], "center_ptr"),
    ("link",        "链接选择",   ["link", "hand", "pointing"],         "hand2"),
    ("loc",         "位置选择",   ["loc", "pin", "location"],           "cell"),
    ("person",      "人员",      ["person", "people"],                 "person"),
]
ROLE_BY_KEY = {r[0]: r for r in ROLES}
ROLE_BY_CANON = {r[3]: r for r in ROLES}
# loc 的规范名 'cell' 不在别名表里，补上（person 同理，但没有额外别名）
ALIAS_TABLE.setdefault("cell", ["loc"])


def aliases_for(canonical: str, taken: set[str]) -> list[str]:
    """取该规范名的别名，去掉已经被别人占用的（同名不能指向两个光标）。"""
    out = []
    for a in ALIAS_TABLE.get(canonical, []):
        if a != canonical and a not in taken:
            taken.add(a)
            out.append(a)
    return out


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class RoleSource:
    role: str
    label: str
    canonical: str
    file: Path
    aliases: list[str] = field(default_factory=list)
    frame_count: int = 0
    native: tuple[int, int] = (0, 0)
    delays: list[int] = field(default_factory=list)

    @property
    def animated(self) -> bool:
        return self.frame_count > 1

    @property
    def duration_ms(self) -> int:
        return sum(self.delays)


@dataclass
class PackInspection:
    name: str                       # 清洗后的主题名
    raw_name: str                   # 原始名（可能含空格/CJK）
    root: Path                      # 解包后的目录
    roles: list[RoleSource] = field(default_factory=list)
    unmapped: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    used_inf: bool = False

    @property
    def total_frames(self) -> int:
        return sum(r.frame_count for r in self.roles)

    @property
    def animated_count(self) -> int:
        return sum(1 for r in self.roles if r.animated)

    @property
    def native_sizes(self) -> list[tuple[int, int]]:
        return sorted({r.native for r in self.roles if r.native != (0, 0)})


@dataclass
class InstallResult:
    inspection: PackInspection
    dest: Path
    sizes: list[int]
    files_written: int = 0
    aliases_created: int = 0
    bytes_written: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

_NON_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_theme_name(raw: str) -> str:
    """把任意名字变成可安全用作目录名 / KDL 字符串 / 环境变量的名字。

    只保留 ASCII 字母数字和 `._-`，其余（空格、CJK、U+2011 断字连字符等）
    一律折成 `-`。这不是洁癖：主题名要写进 niri 的 KDL、GTK 的 ini 和
    XCURSOR_THEME 环境变量，任何"看起来一样但码点不同"的字符都是灾难点。
    """
    name = _NON_SAFE.sub("-", (raw or "").strip())
    name = re.sub(r"-{2,}", "-", name).strip("-.")
    if len(name) < 2 or not re.search(r"[A-Za-z0-9]", name):
        name = "cursor-theme"
    return name[:64]


def is_archive(path: Path) -> bool:
    n = path.name.lower()
    return any(n.endswith(s) for s in ARCHIVE_SUFFIXES)


def _extract_archive(path: Path, dest: Path) -> None:
    if path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            for m in zf.infolist():
                # 防目录穿越
                target = (dest / m.filename).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    continue
                zf.extract(m, dest)
        return
    with tarfile.open(path) as tf:
        for m in tf.getmembers():
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue
            tf.extract(m, dest, filter="data")


def _find_pack_root(root: Path) -> Path:
    """压缩包常常多套一层目录，下钻到真正装光标的那个。"""
    for _ in range(4):
        if any(f.suffix.lower() in WINDOWS_SUFFIXES for f in root.iterdir() if f.is_file()):
            return root
        dirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith("__")]
        if len(dirs) != 1:
            return root
        root = dirs[0]
    return root


def _stem_of(name: str) -> str:
    """去掉压缩包的多段后缀：theme.tar.gz -> theme。"""
    low = name.lower()
    for s in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
        if low.endswith(s):
            return name[: -len(s)]
    return Path(name).stem


def _parse_inf(path: Path) -> tuple[dict[str, str], str]:
    """解析 .inf 的 [Strings] 段，返回 (角色->文件名, 方案名)。"""
    mapping: dict[str, str] = {}
    scheme = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return mapping, scheme
    if text[:2] in ("\xff\xfe", "\xfe\xff") or "\x00" in text[:64]:
        try:
            text = path.read_text(encoding="utf-16", errors="replace")
        except OSError:
            return mapping, scheme

    section = ""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"')
        if section == "strings":
            lk = k.lower()
            if lk == "scheme_name":
                scheme = v
            elif lk in ROLE_BY_KEY:
                mapping[lk] = v
        elif section == "scheme.reg" and not scheme:
            # HKCU,...,"%SCHEME_NAME%",,... 方案名在注册表段里
            m = re.search(r'"([^"]+)"', v)
            if m and "%" not in m.group(1):
                scheme = m.group(1)
    return mapping, scheme


def _guess_role(filename: str) -> str | None:
    """没有 .inf 时按文件名猜角色。"""
    stem = Path(filename).stem.lower()
    # 先做精确匹配，避免 "hand" 抢走 "handwriting"
    for key, _label, _kw, _canon in ROLES:
        if stem == key:
            return key
    best: tuple[int, str] | None = None
    for key, _label, keywords, _canon in ROLES:
        for kw in keywords:
            if kw in stem:
                score = len(kw)
                if best is None or score > best[0]:
                    best = (score, key)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# 缩放
# ---------------------------------------------------------------------------

def _resize_rgba(img: DecodedImage, size: int) -> tuple[bytes, int, int]:
    """把一帧缩放到 size×size，返回 (rgba, hotspot_x, hotspot_y)。"""
    if img.width == size and img.height == size:
        return img.rgba, img.hotspot_x, img.hotspot_y

    if Image is not None:
        im = Image.frombytes("RGBA", (img.width, img.height), img.rgba)
        # 光标是像素画：缩小用 LANCZOS 保边缘质量，放大用 NEAREST 保锐利
        resample = Image.LANCZOS if size < img.width else Image.NEAREST
        im = im.resize((size, size), resample)
        rgba = im.tobytes()
    elif _np is not None:
        a = _np.frombuffer(img.rgba, dtype=_np.uint8).reshape(img.height, img.width, 4)
        idx_y = (_np.arange(size) * img.height // size).clip(0, img.height - 1)
        idx_x = (_np.arange(size) * img.width // size).clip(0, img.width - 1)
        rgba = a[idx_y][:, idx_x].tobytes()
    else:
        raise CursorParseError("缩放需要 Pillow 或 numpy")

    hx = round(img.hotspot_x * size / img.width)
    hy = round(img.hotspot_y * size / img.height)
    return rgba, min(max(hx, 0), size - 1), min(max(hy, 0), size - 1)


# ---------------------------------------------------------------------------
# 包来源
# ---------------------------------------------------------------------------

class PackSource:
    """一个光标包的来源（文件夹 / 压缩包 / 单文件）。

    作为上下文管理器使用，退出时清理解压出来的临时目录::

        with PackSource("theme.zip") as ps:
            info = ps.inspect()
            result = ps.install(info)
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._tmp: Path | None = None
        self.root: Path | None = None

    def __enter__(self) -> "PackSource":
        if not self.path.exists():
            raise FileNotFoundError(f"找不到: {self.path}")
        if self.path.is_dir():
            self.root = self.path
        elif is_archive(self.path):
            self._tmp = Path(tempfile.mkdtemp(prefix="cursorkit-"))
            _extract_archive(self.path, self._tmp)
            self.root = _find_pack_root(self._tmp)
        elif self.path.suffix.lower() in WINDOWS_SUFFIXES:
            self._tmp = Path(tempfile.mkdtemp(prefix="cursorkit-"))
            shutil.copy2(self.path, self._tmp / self.path.name)
            self.root = self._tmp
        else:
            raise ValueError(f"不认识的格式: {self.path.name}（支持文件夹 / zip / tar.* / .ani / .cur / .ico）")
        return self

    def __exit__(self, *exc) -> None:
        if self._tmp and self._tmp.exists():
            shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- 检查 ----
    def inspect(self, name: str | None = None) -> PackInspection:
        assert self.root is not None, "PackSource 必须作为上下文管理器使用"
        root = self.root

        # 按"目录层级浅的优先"排序：压缩包/解压目录里常有嵌套重复的同名文件
        # （例如重复安装一次就会多出一层），浅的那个才是用户想装的那份。
        all_files = [
            f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in WINDOWS_SUFFIXES
        ]
        files = sorted(all_files, key=lambda p: (len(p.relative_to(root).parts), str(p).lower()))
        if not files:
            raise CursorParseError(f"{root} 里没有找到 .ani / .cur / .ico 文件")

        inf_files = sorted(root.rglob("*.inf"))
        inf_map: dict[str, str] = {}
        inf_scheme = ""
        if inf_files:
            inf_map, inf_scheme = _parse_inf(inf_files[0])

        raw_name = name or inf_scheme or _stem_of(root.name)
        inspection = PackInspection(
            name=sanitize_theme_name(raw_name),
            raw_name=raw_name,
            root=root,
            used_inf=bool(inf_map),
        )
        if raw_name != inspection.name:
            inspection.warnings.append(f"主题名已清洗: {raw_name!r} → {inspection.name!r}（ASCII 安全）")

        # 1) .inf 权威映射
        by_name: dict[str, Path] = {}
        for f in files:
            by_name.setdefault(f.name.lower(), f)      # files 已按层级排序，浅的胜出
        assigned: dict[str, Path] = {}
        if inf_map:
            for role, fname in inf_map.items():
                target = by_name.get(Path(fname).name.lower())
                if target is None:                      # 大小写或路径差异，模糊找一次
                    cands = [f for f in files if f.name.lower() == Path(fname).name.lower()]
                    target = cands[0] if cands else None
                if target is not None:
                    assigned[role] = target
            if len(assigned) < len(inf_map):
                inspection.warnings.append(
                    f".inf 里声明了 {len(inf_map)} 个角色，实际找到 {len(assigned)} 个文件"
                )

        # 2) 文件名猜测补漏
        for f in files:
            if f in assigned.values():
                continue
            role = _guess_role(f.name)
            if role and role not in assigned:
                assigned[role] = f

        taken: set[str] = set()
        for key, label, _kw, canonical in ROLES:
            src = assigned.get(key)
            if src is None:
                continue
            taken.add(canonical)
            rs = RoleSource(
                role=key, label=label, canonical=canonical, file=src,
                aliases=aliases_for(canonical, taken),
            )
            try:
                frames, delays = load_windows_cursor(src)
                rs.frame_count = len(frames)
                rs.delays = delays
                rs.native = (frames[0].width, frames[0].height)
            except CursorParseError as e:
                inspection.warnings.append(f"{src.name}: 解析失败（{e}），已跳过")
                taken.discard(canonical)
                continue
            inspection.roles.append(rs)

        used = {r.file for r in inspection.roles}
        used_names = {f.name.lower() for f in used}
        # 同名但没被采用的文件 = 嵌套重复，不算"未识别"
        inspection.unmapped = [
            f for f in files if f not in used and f.name.lower() not in used_names
        ]
        dupes = len(all_files) - len(files)
        if dupes > 0:
            inspection.warnings.append(f"发现 {dupes} 个重复/嵌套的同名光标文件，已取层级最浅的一份")

        if not inspection.roles:
            raise CursorParseError("一个光标角色都没能识别出来")
        missing = [k for k, _l, _kw, _c in ROLES if k not in assigned]
        if missing:
            names = "、".join(ROLE_BY_KEY[k][1] for k in missing)
            inspection.warnings.append(f"包内缺少 {len(missing)} 个角色（{names}），将继承系统默认外观")
        return inspection

    # ---- 安装 ----
    def install(
        self,
        inspection: PackInspection,
        dest_root: str | Path | None = None,
        sizes: Sequence[int] = DEFAULT_SIZES,
        overwrite: bool = False,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> InstallResult:
        base = Path(dest_root) if dest_root else Path(
            os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        ) / "icons"
        dest = base / inspection.name
        result = InstallResult(inspection=inspection, dest=dest, sizes=list(sizes))

        if dest.exists():
            if not overwrite:
                raise FileExistsError(f"主题 '{inspection.name}' 已存在于 {dest}")
            shutil.rmtree(dest)

        cursors_dir = dest / "cursors"
        cursors_dir.mkdir(parents=True, exist_ok=True)

        total = len(inspection.roles)
        for i, rs in enumerate(inspection.roles, 1):
            if progress:
                progress(i - 1, total, rs.canonical)
            try:
                frames, delays = load_windows_cursor(rs.file)
                groups: dict[int, list[tuple[int, int, int, int, int, bytes]]] = {}
                native_w = frames[0].width
                targets = sorted({*sizes, native_w})
                for size in targets:
                    bucket = []
                    for fi, fr in enumerate(frames):
                        rgba, hx, hy = _resize_rgba(fr, size)
                        delay = delays[fi] if fi < len(delays) else 0
                        bucket.append((size, size, hx, hy, delay, rgba))
                    groups[size] = bucket
                out = cursors_dir / rs.canonical
                write_xcursor(out, groups)
                result.files_written += 1
                result.bytes_written += out.stat().st_size
            except (CursorParseError, OSError, ValueError) as e:
                result.errors.append(f"{rs.canonical}: {e}")
                continue

            for alias in rs.aliases:
                link = cursors_dir / alias
                try:
                    if link.is_symlink() or link.exists():
                        link.unlink()
                    os.symlink(rs.canonical, link)      # 相对链接，主题目录可整体搬移
                    result.aliases_created += 1
                except OSError as e:
                    result.errors.append(f"别名 {alias}: {e}")

        if progress:
            progress(total, total, "写入元数据")

        inherits = "Adwaita" if (Path("/usr/share/icons/Adwaita/cursors")).is_dir() else "default"
        (dest / "index.theme").write_text(
            "[Icon Theme]\n"
            f"Name={inspection.name}\n"
            f"Comment=Converted from Windows cursor pack ({inspection.raw_name})\n"
            f"Inherits={inherits}\n",
            encoding="utf-8",
        )
        (dest / "cursor.theme").write_text(
            "[Icon Theme]\n"
            f"Name={inspection.name}\n"
            f"Inherits={inherits}\n",
            encoding="utf-8",
        )
        return result


def import_pack(
    path: str | Path,
    dest_root: str | Path | None = None,
    sizes: Sequence[int] = DEFAULT_SIZES,
    name: str | None = None,
    overwrite: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> InstallResult:
    """一步到位：检查 + 安装。"""
    with PackSource(path) as ps:
        info = ps.inspect(name=name)
        return ps.install(info, dest_root=dest_root, sizes=sizes,
                          overwrite=overwrite, progress=progress)
