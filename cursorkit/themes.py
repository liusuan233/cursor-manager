"""光标主题发现与校验。

Wayland 下的硬规则：一个主题必须长成 ::

    <主题目录>/cursors/<光标名>

`cursors/` 这一层不能省 —— libwayland-cursor（GTK4 / Firefox / Electron 在
Wayland 下都走它）只认这个布局。少一层就会被静默忽略并回落到 Adwaita，
不报任何错。本模块的核心职责就是把这个"静默失败"变成显式可见的 ✔/✘。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .xcursor import CursorFile, XcursorError, probe_size_map

# 展示顺序：常用的排前面，比纯字母序好找得多
_DISPLAY_ORDER = [
    "left_ptr", "default", "arrow", "text", "xterm", "hand2", "pointer", "pointing_hand",
    "wait", "watch", "left_ptr_watch", "progress", "half-busy",
    "cross", "crosshair", "crossed_circle", "circle", "not-allowed",
    "move", "fleur", "size_all", "grab", "grabbing",
    "left_side", "right_side", "top_side", "bottom_side", "col-resize", "row-resize",
    "ew-resize", "ns-resize", "size_hor", "size_ver",
    "top_left_corner", "top_right_corner", "bottom_left_corner", "bottom_right_corner",
    "nwse-resize", "nesw-resize", "size_fdiag", "size_bdiag",
    "question_arrow", "help", "left_ptr_help", "whats_this",
    "pencil", "draft", "up_arrow", "center_ptr", "right_ptr", "dotbox", "vertical-text",
    "zoom-in", "zoom-out", "cell", "target", "color-picker", "context-menu",
    "copy", "alias", "link", "plus", "no-drop", "dnd-move", "dnd-copy", "dnd-link",
]
_ORDER_INDEX = {n: i for i, n in enumerate(_DISPLAY_ORDER)}

# 图标主题的特征：有 16x16 / 32x32 / scalable 这类尺寸子目录，
# 或者 index.theme 里带 Directories= 键。用来把 hicolor/breeze/exo 这类
# 纯图标主题排除掉，避免它们被误报成"坏掉的光标主题"。
_ICON_SIZE_DIR = re.compile(r"^(\d+x\d+|scalable|symbolic|apps|places|mimetypes|actions|devices|categories|emblems|status|panel|notifications)$")

# 判定"这堆平铺文件是不是光标"用的常见名字（不需要全中，命中几个就够）
_CURSOR_HINTS = frozenset({
    "left_ptr", "default", "pointer", "arrow", "text", "xterm", "hand", "hand1", "hand2",
    "wait", "watch", "busy", "progress", "cross", "crosshair", "help", "move", "fleur",
    "size_hor", "size_ver", "size_fdiag", "size_bdiag", "size_all", "normal", "precision",
    "unavailable", "handwriting", "link", "person", "alternate", "horizontal", "vertical",
    "diagonal1", "diagonal2", "not-allowed", "pencil", "up_arrow", "center_ptr", "dotbox",
})


def _looks_like_cursor_theme(d: Path, flat: list[Path], index: dict[str, str]) -> bool:
    """区分"平铺布局的光标主题"和"图标主题"。

    图标主题不该出现在光标管理器里，更不该被标成红色错误。
    """
    if "directories" in index:
        return False
    try:
        if any(s.is_dir() and _ICON_SIZE_DIR.match(s.name) for s in d.iterdir()):
            return False
    except OSError:
        return False
    hits = sum(1 for f in flat if f.name.lower() in _CURSOR_HINTS)
    return hits >= 2


def iter_search_roots() -> list[Path]:
    """按查找优先级返回主题搜索根目录（与 libXcursor / libwayland-cursor / DMS 一致）。"""
    roots: list[Path] = []
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    roots.append(Path(data_home) / "icons")
    roots.append(Path.home() / ".icons")
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d.strip():
            roots.append(Path(d) / "icons")
    roots.append(Path("/usr/share/pixmaps"))
    # 去重保序
    seen, out = set(), []
    for r in roots:
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


@dataclass(slots=True)
class Cursor:
    """主题里的一个光标（实体 Xcursor 文件），带它的别名列表。"""

    name: str
    path: Path
    aliases: list[str] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    size_map: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def all_names(self) -> list[str]:
        return [self.name] + sorted(self.aliases)

    @property
    def nominal_sizes(self) -> list[int]:
        return self.sizes

    def pick_nominal(self, target_px: int) -> tuple[int, int]:
        """挑一个档位，使其**实际像素宽度**最接近 target_px。

        返回 (标称尺寸, 预计宽度)。优先选不超过目标的 —— 放大比缩小清晰得多
        （光标是像素画，缩小会糊）。
        """
        if self.size_map:
            pairs = [(nom, wh[0]) for nom, wh in self.size_map.items()]
        elif self.sizes:
            pairs = [(s, s) for s in self.sizes]
        else:
            return target_px, target_px
        under = [p for p in pairs if p[1] <= target_px]
        if under:
            return max(under, key=lambda p: p[1])
        return min(pairs, key=lambda p: p[1])

    def load(self, want: int | None = None) -> CursorFile:
        return CursorFile.load(self.path, want=want)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Cursor {self.name} aliases={len(self.aliases)} sizes={self.sizes}>"


@dataclass(slots=True)
class Theme:
    name: str
    path: Path
    root: Path
    valid: bool
    reason: str = ""
    cursors: list[Cursor] = field(default_factory=list)
    index_theme: dict[str, str] = field(default_factory=dict)

    @property
    def cursor_dir(self) -> Path:
        return self.path / "cursors"

    @property
    def alias_count(self) -> int:
        return sum(len(c.aliases) for c in self.cursors)

    @property
    def inherits(self) -> str | None:
        return self.index_theme.get("inherits")

    def find(self, name: str) -> Cursor | None:
        for c in self.cursors:
            if c.name == name or name in c.aliases:
                return c
        return None

    def __repr__(self) -> str:  # pragma: no cover
        flag = "OK" if self.valid else "BAD"
        return f"<Theme {self.name} [{flag}] cursors={len(self.cursors)}>"


def _read_index_theme(path: Path) -> dict[str, str]:
    """解析 index.theme 的 [Icon Theme] 段。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        section = ""
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().lower()
                continue
            if "=" in line and section in ("icon theme", "cursor"):
                k, v = line.split("=", 1)
                out.setdefault(k.strip().lower(), v.strip())
    except OSError:
        pass
    return out


def _collect_cursors(cursor_dir: Path, with_sizes: bool = True) -> tuple[list[Cursor], str]:
    """把一个 cursors/ 目录读成 Cursor 列表；返回 (列表, 失败原因)。"""
    try:
        entries = list(os.scandir(cursor_dir))
    except OSError as e:
        return [], f"无法读取 cursors/: {e}"

    real: dict[str, Cursor] = {}
    links: list[tuple[str, str]] = []

    for e in entries:
        if e.name.startswith("."):
            continue
        try:
            is_link = e.is_symlink()
        except OSError:
            continue
        if is_link:
            try:
                target = os.path.basename(os.readlink(e.path))
            except OSError:
                continue
            links.append((e.name, target))
        elif e.is_file():
            c = Cursor(name=e.name, path=Path(e.path))
            if with_sizes:
                c.size_map = probe_size_map(e.path)
                c.sizes = sorted(c.size_map)
            real[e.name] = c

    if not real:
        return [], "cursors/ 里没有真实的光标文件"

    # 把别名挂到目标上（跟随多级符号链接）
    for alias, target in links:
        seen = {alias}
        cur = target
        while cur in links and cur not in seen:
            seen.add(cur)
            cur = dict(links)[cur]
        if cur in real:
            real[cur].aliases.append(alias)
        # 指向不存在的目标就丢掉

    ordered = sorted(
        real.values(),
        key=lambda c: (_ORDER_INDEX.get(c.name, len(_DISPLAY_ORDER)), c.name),
    )
    return ordered, ""


def scan_themes(with_sizes: bool = True, deep: bool = False) -> list[Theme]:
    """扫描所有搜索根下的主题。

    同名主题以优先级更高的根为准（用户目录遮蔽系统目录），与 libXcursor 行为一致。
    deep=True 时会解析每个光标文件的标称尺寸（较慢，但预览更准）。
    """
    themes: list[Theme] = []
    seen: set[str] = set()

    for root in iter_search_roots():
        if not root.is_dir():
            continue
        try:
            subdirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda p: p.name.lower())
        except OSError:
            continue
        for d in subdirs:
            if d.name.startswith(".") or d.name in seen:
                continue
            seen.add(d.name)

            index = _read_index_theme(d / "index.theme")
            cursor_dir = d / "cursors"
            has_cursors = cursor_dir.is_dir()

            if not has_cursors:
                # 只有"看起来真是光标主题"的才报为坏布局；否则是图标主题，直接跳过
                flat = [
                    f for f in d.iterdir()
                    if f.is_file() and f.name not in ("index.theme", "cursor.theme")
                ] if d.is_dir() else []
                if flat and _looks_like_cursor_theme(d, flat, index):
                    themes.append(Theme(
                        name=d.name, path=d, root=root, valid=False,
                        reason="缺少 cursors/ 子目录（Wayland 客户端会忽略它并回落到 Adwaita）",
                        index_theme=index,
                    ))
                continue

            cursors, reason = _collect_cursors(cursor_dir, with_sizes=with_sizes)
            themes.append(Theme(
                name=d.name, path=d, root=root,
                valid=bool(cursors), reason=reason, cursors=cursors, index_theme=index,
            ))

    if deep:
        for t in themes:
            for c in t.cursors:
                if not c.sizes:
                    try:
                        c.sizes = CursorFile.load(c.path).sizes
                    except XcursorError:
                        pass

    return themes


def grouped_by_root(themes: list[Theme]) -> dict[str, list[Theme]]:
    out: dict[str, list[Theme]] = {}
    for t in themes:
        out.setdefault(str(t.root), []).append(t)
    return out


# ---------------------------------------------------------------------------
# 删除主题
#
# 这是本项目唯一不可逆的操作，所以四道闸门：
#   1. 只允许删用户目录下的主题（/usr/share 里的是系统资源，动它要 root 且会坏系统）
#   2. 不允许删当前正在生效的主题（删完所有程序会静默回落到 Adwaita —— 正是本项目要防的坑）
#   3. 删之前必须先确认它"长得像个主题"
#   4. 默认走回收站而不是 rm，误删可以从文件管理器恢复
# ---------------------------------------------------------------------------

@dataclass
class DeleteReport:
    theme: str
    path: Path
    method: str = ""            # "trash" | "purge" | "dry-run" | ""
    size: int = 0               # 字节
    files: int = 0
    moved_to: Path | None = None
    ok: bool = False
    detail: str = ""

    @property
    def size_human(self) -> str:
        n = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{self.size} B"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def user_theme_roots() -> list[Path]:
    """允许删除的根目录。系统目录不在其中。"""
    return [_data_home() / "icons", Path.home() / ".icons"]


def theme_size(path: Path) -> tuple[int, int]:
    """(总字节数, 文件数)。符号链接只计数、不计入大小（避免跟到主题外）。"""
    total = files = 0
    for f in path.rglob("*"):
        try:
            if f.is_symlink():
                files += 1
            elif f.is_file():
                files += 1
                total += f.stat().st_size
        except OSError:
            continue
    return total, files


def deletion_blockers(theme: Theme, active: str = "") -> list[str]:
    """返回阻止删除的原因；空列表 = 可以删。"""
    reasons: list[str] = []
    try:
        path = theme.path.resolve()
    except OSError:
        return ["路径无法解析"]

    allowed = [r.resolve() for r in user_theme_roots()]
    if not any(path == r or path.is_relative_to(r) for r in allowed):
        reasons.append(
            f"它位于系统目录 {theme.root} —— 删除需要 root，而且会破坏系统主题，"
            "请改用发行版的包管理器"
        )

    if active and theme.name == active:
        reasons.append(
            f"它正是当前生效的主题 —— 删掉之后所有程序会静默回落到 Adwaita。"
            f"请先切换到别的主题"
        )

    if not theme.path.is_dir():
        reasons.append("不是一个目录")
    elif not (theme.path / "cursors").is_dir() and not (theme.path / "index.theme").is_file():
        reasons.append("看着不像光标主题（既没有 cursors/ 也没有 index.theme）")

    return reasons


def move_to_trash(path: Path) -> Path:
    """按 freedesktop 回收站规范搬运，使其能从文件管理器里恢复。

    结构：
        <XDG_DATA_HOME>/Trash/files/<名字>
        <XDG_DATA_HOME>/Trash/info/<名字>.trashinfo
    """
    from datetime import datetime
    from urllib.parse import quote

    files_dir = _data_home() / "Trash" / "files"
    info_dir = _data_home() / "Trash" / "info"
    files_dir.mkdir(parents=True, exist_ok=True)
    info_dir.mkdir(parents=True, exist_ok=True)

    dest = files_dir / path.name
    n = 1
    while dest.exists() or (info_dir / f"{dest.name}.trashinfo").exists():
        dest = files_dir / f"{path.name}.{n}"
        n += 1

    shutil.move(str(path), str(dest))          # 跨设备时 shutil 会自动退化为复制+删除

    (info_dir / f"{dest.name}.trashinfo").write_text(
        "[Trash Info]\n"
        f"Path={quote(str(path))}\n"
        f"DeletionDate={datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )
    return dest


def delete_theme(theme: Theme, active: str = "", purge: bool = False,
                 dry_run: bool = False) -> DeleteReport:
    """删除一个光标主题。

    purge=False（默认）移进回收站，可恢复；purge=True 才真的 rm -rf。
    """
    rep = DeleteReport(theme=theme.name, path=theme.path)

    blockers = deletion_blockers(theme, active=active)
    if blockers:
        rep.detail = "；".join(blockers)
        return rep

    rep.size, rep.files = theme_size(theme.path)

    if dry_run:
        rep.method = "dry-run"
        rep.ok = True
        rep.detail = f"将{'永久删除' if purge else '移入回收站'}"
        return rep

    try:
        if purge:
            shutil.rmtree(theme.path)
            rep.method = "purge"
            rep.detail = "已永久删除"
        else:
            rep.moved_to = move_to_trash(theme.path)
            rep.method = "trash"
            rep.detail = f"已移入回收站（{rep.moved_to}），可恢复"
        rep.ok = True
    except OSError as e:
        rep.detail = f"{type(e).__name__}: {e}"

    return rep
