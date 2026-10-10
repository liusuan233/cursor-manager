"""GTK4 图形界面。

比 nwg-look、lxappearance 多做三件事：
  1. 直接认 niri / DMS，而不是只认 GNOME / XFCE 的 gsettings 键；
  2. 把"布局不合法导致静默回落到 Adwaita"这个隐形故障显式标红；
  3. 把 Xcursor 文件解码出来做动画预览 —— 普通图片查看器打不开这种格式。

这个模块只负责界面；命令行子命令在 cursorkit/cli.py，入口在 cursorkit/__main__.py。
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from cursorkit import CursorFile, XcursorError, apply_theme, read_state, scan_themes  # noqa: E402
from cursorkit.importer import (  # noqa: E402
    DEFAULT_SIZES, PackSource, import_pack,
)
from cursorkit.themes import (  # noqa: E402
    Cursor, Theme, delete_theme, deletion_blockers, theme_size,
)

APP_ID = "io.github.cursorkit.Manager"
DISPLAY_PX = 96          # 预览格子边长
TICK_MS = 50             # 动画帧率 tick
SIZES = [16, 20, 24, 28, 32, 40, 48, 64]

CSS = """
.tile {
    padding: 6px;
    border-radius: 8px;
}
.tile:hover { background-color: alpha(currentColor, 0.08); }
.tile-name { font-size: 0.78em; opacity: 0.75; }
.tile-anim { font-size: 0.68em; opacity: 0.5; }

.theme-name { font-weight: bold; }
.theme-sub  { font-size: 0.82em; opacity: 0.65; }
.dim        { opacity: 0.6; }
.h1         { font-size: 1.35em; font-weight: bold; }
.mono       { font-family: monospace; font-size: 0.85em; }

.badge-ok   { color: #2ec27e; font-weight: bold; }
.badge-bad  { color: #e01b24; font-weight: bold; }
.warn-box   { padding: 10px; border-radius: 8px; background-color: alpha(#e01b24, 0.12); }
.ok-box     { padding: 10px; border-radius: 8px; background-color: alpha(#2ec27e, 0.12); }
"""


def load_css(css: str) -> Gtk.CssProvider:
    """CSS API 在 GTK 小版本间换过签名，按可用性依次尝试。"""
    provider = Gtk.CssProvider()
    for attempt in (
        lambda: provider.load_from_string(css),
        lambda: provider.load_from_data(css),
        lambda: provider.load_from_data(css.encode("utf-8")),
    ):
        try:
            attempt()
            return provider
        except Exception:
            continue
    return provider


def texture_from_rgba(width: int, height: int, rgba: bytes) -> Gdk.Texture:
    return Gdk.MemoryTexture.new(
        width, height, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(rgba), width * 4
    )


def _human(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


class DeleteDialog(Gtk.Window):
    """删除确认：先把"删什么、多大、能不能恢复"摆清楚，再让用户点。"""

    def __init__(self, parent: Gtk.Window, theme: Theme, on_confirm):
        super().__init__(title="删除主题", transient_for=parent, modal=True)
        self.set_default_size(540, -1)

        size, files = theme_size(theme.path)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(18)

        head = Gtk.Label(xalign=0)
        head.set_markup(
            f"<span size='large' weight='bold'>"
            f"删除 {GLib.markup_escape_text(theme.name)}</span>"
        )
        box.append(head)

        info = Gtk.Label(xalign=0, wrap=True)
        info.add_css_class("dim")
        info.set_selectable(True)
        info.set_text(f"{theme.path}\n{files} 个文件，{_human(size)}")
        box.append(info)

        purge = Gtk.CheckButton(label="永久删除（不放进回收站，无法恢复）")
        box.append(purge)

        hint = Gtk.Label(xalign=0, wrap=True)
        hint.add_css_class("dim")
        hint.set_markup(
            "<span size='small'>默认移入回收站，可以从文件管理器的回收站恢复。</span>"
        )
        box.append(hint)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="取消")
        cancel.connect("clicked", lambda *_: self.close())
        row.append(cancel)

        ok = Gtk.Button(label="删除")
        ok.add_css_class("destructive-action")

        def go(*_):
            self.close()
            on_confirm(theme, purge.get_active())
        ok.connect("clicked", go)
        row.append(ok)
        box.append(row)

        self.set_child(box)


# ---------------------------------------------------------------------------
# 预览格子
# ---------------------------------------------------------------------------

class CursorTile(Gtk.Box):
    """一个光标的动画预览格子。"""

    def __init__(self, cursor: Cursor, target_px: int, magnify: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("tile")
        self.set_tooltip_text(self._tooltip(cursor))

        self.cursor = cursor
        self.textures: list[Gdk.Texture] = []
        self.bounds: list[tuple[int, Gdk.Texture]] = []
        self.total = 0
        self.elapsed = 0
        self.index = -1
        self.error = ""
        self.factor = 1
        self.nominal = 0

        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(False)
        holder = Gtk.CenterBox()
        holder.set_size_request(DISPLAY_PX, DISPLAY_PX)
        holder.set_center_widget(self.picture)
        self.append(holder)

        name = Gtk.Label(label=cursor.name)
        name.add_css_class("tile-name")
        name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        name.set_max_width_chars(14)
        self.append(name)

        self.anim_label = Gtk.Label(label="")
        self.anim_label.add_css_class("tile-anim")
        self.append(self.anim_label)

        self.load(target_px, magnify)

    @staticmethod
    def _tooltip(cursor: Cursor) -> str:
        lines = [f"<b>{GLib.markup_escape_text(cursor.name)}</b>"]
        if cursor.size_map:
            pairs = ", ".join(f"{n}→{wh[0]}px" for n, wh in sorted(cursor.size_map.items()))
            lines.append(f"档位(标称→实际): {pairs}")
        if cursor.aliases:
            shown = cursor.aliases[:12]
            more = f" …（共 {len(cursor.aliases)} 个）" if len(cursor.aliases) > 12 else ""
            lines.append(f"别名: {GLib.markup_escape_text(', '.join(shown))}{more}")
        lines.append(f"<span size='small'>{GLib.markup_escape_text(str(cursor.path))}</span>")
        return "\n".join(lines)

    def load(self, target_px: int, magnify: bool) -> None:
        # 关键：按"实际像素宽度"选档，而不是标称尺寸。
        # 有的主题标称 96 却塞了 128x128 的图，照标称选会撑破格子。
        nominal, _expected = self.cursor.pick_nominal(target_px)
        self.nominal = nominal
        try:
            cf = CursorFile.load(self.cursor.path, want=nominal)
            frames = cf.frames(nominal)
        except XcursorError as e:
            self.error = str(e)
            self.anim_label.set_text("无法解码")
            return

        factor = 1
        if magnify and frames[0].width < target_px:
            factor = max(1, min(target_px // frames[0].width, 4))
        self.factor = factor
        if factor > 1:
            frames = [f.scaled(factor) for f in frames]

        self.textures = [texture_from_rgba(f.width, f.height, f.rgba) for f in frames]
        self.bounds = []
        t = 0
        for tex, f in zip(self.textures, frames):
            d = f.delay or 50
            t += d
            self.bounds.append((t, tex))
        self.total = max(t, 1)

        shown = frames[0].width
        suffix = f" · ×{factor}" if factor > 1 else ""
        kind = f"{len(frames)} 帧" if len(frames) > 1 else "静态"
        self.anim_label.set_text(f"{kind} · {shown}px{suffix}")

        self.elapsed = 0
        self._apply(0)

    def _apply(self, elapsed: int) -> None:
        for end, tex in self.bounds:
            if elapsed < end:
                if tex is not self.picture.get_paintable():
                    self.picture.set_paintable(tex)
                return
        if self.bounds:
            self.picture.set_paintable(self.bounds[-1][1])

    def advance(self, dt: int) -> None:
        if self.total <= 1 or len(self.bounds) <= 1:
            return
        self.elapsed = (self.elapsed + dt) % self.total
        self._apply(self.elapsed)


# ---------------------------------------------------------------------------
# 左侧主题条目
# ---------------------------------------------------------------------------

class ThemeRow(Gtk.ListBoxRow):
    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        badge = Gtk.Label(label="✔" if theme.valid else "✘")
        badge.add_css_class("badge-ok" if theme.valid else "badge-bad")
        badge.set_valign(Gtk.Align.START)
        box.append(badge)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)

        name = Gtk.Label(label=theme.name, xalign=0)
        name.add_css_class("theme-name")
        name.set_ellipsize(3)
        col.append(name)

        if theme.valid:
            sub = f"{len(theme.cursors)} 光标 · {theme.alias_count} 别名"
        else:
            sub = "布局不合法 —— 会被 Wayland 忽略"
        sublabel = Gtk.Label(label=sub, xalign=0)
        sublabel.add_css_class("theme-sub")
        sublabel.set_ellipsize(3)
        col.append(sublabel)
        box.append(col)

        self.set_child(box)
        self.set_tooltip_markup(
            f"<b>{GLib.markup_escape_text(theme.name)}</b>\n"
            f"{GLib.markup_escape_text(str(theme.path))}\n"
            + (GLib.markup_escape_text(theme.reason) if theme.reason else
               f"来源: {GLib.markup_escape_text(str(theme.root))}"
               + (f"\n继承: {GLib.markup_escape_text(theme.inherits)}" if theme.inherits else ""))
        )


# ---------------------------------------------------------------------------
# 结果对话框
# ---------------------------------------------------------------------------

class ResultDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, result):
        super().__init__(title="应用结果", transient_for=parent, modal=True)
        self.set_default_size(620, 420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)

        head = Gtk.Label(xalign=0)
        head.set_markup(
            f"<span size='large' weight='bold'>{'✔ 已应用到系统' if result.ok else '✘ 部分失败'}</span>\n"
            f"主题 <b>{GLib.markup_escape_text(result.theme)}</b> · 尺寸 {result.size}"
        )
        box.append(head)

        for r in result.reports:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon = Gtk.Label(label=r.icon)
            icon.add_css_class("badge-ok" if r.ok else "badge-bad")
            row.append(icon)
            tgt = Gtk.Label(label=r.target, xalign=0)
            tgt.set_size_request(170, -1)
            tgt.add_css_class("theme-name")
            row.append(tgt)
            det = Gtk.Label(label=r.detail, xalign=0)
            det.set_wrap(True)
            det.set_hexpand(True)
            det.add_css_class("dim")
            row.append(det)
            box.append(row)

        hint = Gtk.Label(xalign=0)
        hint.set_wrap(True)
        hint.set_markup(
            "<span size='small'>生效范围：niri 保存配置后会自动重载；新启动的程序立即生效；"
            "已开着的程序需要重启。想让环境变量对所有新进程一致，最稳是注销重登一次会话。</span>"
        )
        box.append(hint)

        btn = Gtk.Button(label="知道了")
        btn.set_halign(Gtk.Align.END)
        btn.connect("clicked", lambda *_: self.close())
        box.append(btn)

        self.set_child(box)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class ImportResultDialog(Gtk.Window):
    """导入完成后的报告：识别到哪些角色、转出了什么、有哪些警告。"""

    def __init__(self, parent: Gtk.Window, res):
        super().__init__(title="导入结果", transient_for=parent, modal=True)
        self.set_default_size(700, 560)
        info = res.inspection

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{m}")(16)

        head = Gtk.Label(xalign=0)
        head.set_markup(
            f"<span size='large' weight='bold'>"
            f"{'✔ 导入完成' if res.ok else '✘ 导入完成但有错误'}</span>\n"
            f"主题 <b>{GLib.markup_escape_text(info.name)}</b>"
            + (f"  <span size='small'>(原名 {GLib.markup_escape_text(info.raw_name)})</span>"
               if info.raw_name != info.name else "")
        )
        root.append(head)

        meta = Gtk.Label(xalign=0, wrap=True)
        meta.add_css_class("dim")
        meta.set_text(
            f"{len(info.roles)} 个角色 · {info.total_frames} 帧 · "
            f"{info.animated_count} 个动画光标 · "
            f"原始分辨率 {'/'.join(f'{w}×{h}' for w, h in info.native_sizes) or '未知'}\n"
            f"转出尺寸档 {', '.join(map(str, res.sizes))} · "
            f"{res.files_written} 个文件 + {res.aliases_created} 个别名 · "
            f"{res.bytes_written / 1024 / 1024:.1f} MB\n{res.dest}"
        )
        root.append(meta)

        if info.used_inf:
            badge = Gtk.Label(xalign=0)
            badge.add_css_class("dim")
            badge.set_markup("<span size='small'>角色映射来自包内 .inf —— 官方数据，非猜测</span>")
            root.append(badge)

        # 角色明细
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        grid = Gtk.ListBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.add_css_class("boxed-list")
        for r in info.roles:
            row = Gtk.ListBoxRow()
            b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            b.set_margin_top(6)
            b.set_margin_bottom(6)
            b.set_margin_start(10)
            b.set_margin_end(10)
            b.append(Gtk.Label(label=r.label, xalign=0, width_chars=10))
            mono = Gtk.Label(label=r.canonical, xalign=0)
            mono.add_css_class("mono")
            mono.set_size_request(190, -1)
            b.append(mono)
            b.append(Gtk.Label(label=f"{r.frame_count} 帧", xalign=0, width_chars=7))
            b.append(Gtk.Label(
                label=f"{r.native[0]}×{r.native[1]}", xalign=0, width_chars=10))
            b.append(Gtk.Label(
                label=f"{r.duration_ms} ms", xalign=0, width_chars=9))
            alias = Gtk.Label(
                label=(f"+{len(r.aliases)} 别名" if r.aliases else ""),
                xalign=1, hexpand=True)
            alias.add_css_class("dim")
            b.append(alias)
            row.set_child(b)
            grid.append(row)
        sw.set_child(grid)
        root.append(sw)

        for w in info.warnings:
            lab = Gtk.Label(xalign=0, wrap=True)
            lab.add_css_class("dim")
            lab.set_markup(f"<span size='small'>⚠ {GLib.markup_escape_text(w)}</span>")
            root.append(lab)
        if info.unmapped:
            lab = Gtk.Label(xalign=0, wrap=True)
            lab.set_markup("<span size='small'>⚠ 未识别: "
                           + GLib.markup_escape_text(", ".join(f.name for f in info.unmapped[:6]))
                           + "</span>")
            root.append(lab)
        for e in res.errors:
            lab = Gtk.Label(xalign=0, wrap=True)
            lab.add_css_class("badge-bad")
            lab.set_text(f"✘ {e}")
            root.append(lab)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)

        copy_btn = Gtk.Button(label="复制路径")

        def do_copy(*_):
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(str(res.dest))
            copy_btn.set_label("已复制")
        copy_btn.connect("clicked", do_copy)
        row.append(copy_btn)

        ok = Gtk.Button(label="知道了")
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda *_: self.close())
        row.append(ok)
        root.append(row)

        self.set_child(root)


class ManagerWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="光标主题管理器")
        self.set_default_size(1080, 700)

        self.themes: list[Theme] = []
        self.tiles: list[CursorTile] = []
        self.selected: Theme | None = None
        self.animating = True
        self.magnify = True
        self.generation = 0
        self._busy = False
        self._import: dict = {}

        self._build_header()
        self._build_body()
        self._build_status()

        self.refresh(initial=True)
        GLib.timeout_add(TICK_MS, self._tick)

    # ---- 界面搭建 ----
    def _build_header(self) -> None:
        hb = Gtk.HeaderBar()

        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("重新扫描主题 (F5)")
        refresh.connect("clicked", lambda *_: self.refresh())
        hb.pack_start(refresh)

        label = Gtk.Label(label="尺寸")
        label.add_css_class("dim")
        hb.pack_start(label)

        self.size_dd = Gtk.DropDown.new_from_strings([str(s) for s in SIZES])
        self.size_dd.set_selected(SIZES.index(24))
        self.size_dd.set_tooltip_text("要应用的鼠标尺寸（同时影响预览）")
        self.size_dd.connect("notify::selected", lambda *_: self._render_preview())
        hb.pack_start(self.size_dd)

        self.anim_btn = Gtk.ToggleButton(label="动画")
        self.anim_btn.set_active(True)
        self.anim_btn.set_tooltip_text("开/关预览动画")
        self.anim_btn.connect("toggled", lambda b: setattr(self, "animating", b.get_active()))
        hb.pack_end(self.anim_btn)

        self.magnify_btn = Gtk.ToggleButton(label="放大预览")
        self.magnify_btn.set_active(True)
        self.magnify_btn.set_tooltip_text("放大到 96px 显示 / 按实际尺寸 1:1 显示")
        self.magnify_btn.connect("toggled", self._on_magnify)
        hb.pack_end(self.magnify_btn)

        self.apply_btn = Gtk.Button(label="应用到系统")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.set_tooltip_text("写入 niri / DMS / GTK3 / GTK4 / XWayland / gsettings")
        self.apply_btn.connect("clicked", self._on_apply)
        hb.pack_end(self.apply_btn)

        # 删除：只在"真能删"的时候可用，否则禁用并用 tooltip 说明原因
        self.delete_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self.delete_btn.set_tooltip_text("删除选中的主题")
        self.delete_btn.connect("clicked", self._on_delete)
        hb.pack_end(self.delete_btn)

        # 导入：菜单里两项，因为 GTK 的文件对话框一次只能选"文件"或"文件夹"
        actions = Gio.SimpleActionGroup()
        for name, handler in (("import-file", self._on_import_file),
                              ("import-folder", self._on_import_folder)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, h=handler: h())
            actions.add_action(act)
        self.insert_action_group("win", actions)
        self.actions = actions          # 留个引用，便于自检/调试

        menu = Gio.Menu()
        menu.append("从压缩包 / 光标文件导入…", "win.import-file")
        menu.append("从文件夹导入…", "win.import-folder")
        self.import_btn = Gtk.MenuButton(label="导入主题")
        self.import_btn.set_menu_model(menu)
        self.import_btn.set_tooltip_text(
            "导入 Windows 光标包（.zip / .tar.gz / 文件夹 / .ani / .cur / .ico）\n"
            "会自动读取包里的 .inf 拿到官方角色映射，再转成 Xcursor 主题"
        )
        hb.pack_end(self.import_btn)

        self.set_titlebar(hb)

    def _build_body(self) -> None:
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(300)
        paned.set_shrink_start_child(False)
        paned.set_resize_start_child(False)

        # ---- 左：搜索 + 主题列表 ----
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.set_margin_top(8)
        left.set_margin_bottom(8)
        left.set_margin_start(8)
        left.set_margin_end(4)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("筛选主题…")
        self.search.connect("search-changed", lambda *_: self._fill_theme_list())
        left.append(self.search)

        self.theme_list = Gtk.ListBox()
        self.theme_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.theme_list.add_css_class("navigation-sidebar")
        self.theme_list.connect("row-selected", self._on_theme_selected)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        sw.set_child(self.theme_list)
        left.append(sw)
        paned.set_start_child(left)

        # ---- 右：详情 + 预览网格 ----
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_margin_top(10)
        right.set_margin_bottom(8)
        right.set_margin_start(6)
        right.set_margin_end(10)

        self.title_label = Gtk.Label(xalign=0)
        self.title_label.add_css_class("h1")
        self.title_label.set_ellipsize(3)
        right.append(self.title_label)

        self.meta_label = Gtk.Label(xalign=0)
        self.meta_label.add_css_class("dim")
        self.meta_label.set_wrap(True)
        right.append(self.meta_label)

        self.banner = Gtk.Label(xalign=0)
        self.banner.set_wrap(True)
        self.banner.set_visible(False)
        right.append(self.banner)

        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_homogeneous(True)
        self.flow.set_min_children_per_line(3)
        self.flow.set_max_children_per_line(12)
        self.flow.set_row_spacing(4)
        self.flow.set_column_spacing(4)

        flow_sw = Gtk.ScrolledWindow()
        flow_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        flow_sw.set_vexpand(True)
        flow_sw.set_child(self.flow)
        right.append(flow_sw)

        paned.set_end_child(right)
        self.set_child(paned)

    def _build_status(self) -> None:
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("dim")
        self.status.set_margin_top(4)
        self.status.set_margin_bottom(6)
        self.status.set_margin_start(12)
        self.status.set_margin_end(12)
        self.status.set_ellipsize(3)
        # 放进一个 overlay 之外单独放不行，GTK4 单 child —— 用 Box 包一层
        root = self.get_child()
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(None)
        wrapper.append(root)
        wrapper.append(Gtk.Separator())
        wrapper.append(self.status)
        self.set_child(wrapper)

    # ---- 刷新 / 扫描 ----
    def refresh(self, initial: bool = False) -> None:
        self.themes = scan_themes()
        self._fill_theme_list()
        self._update_status()

        if initial:
            active = read_state().get("niri") or read_state().get("gtk3")
            for i, t in enumerate(self._visible_themes()):
                if t.name == active and t.valid:
                    self.theme_list.select_row(self.theme_list.get_row_at_index(i))
                    break

    def _visible_themes(self) -> list[Theme]:
        q = (self.search.get_text() or "").strip().lower()
        if not q:
            return self.themes
        return [t for t in self.themes if q in t.name.lower()]

    def _fill_theme_list(self) -> None:
        while (row := self.theme_list.get_row_at_index(0)) is not None:
            self.theme_list.remove(row)
        for t in self._visible_themes():
            self.theme_list.append(ThemeRow(t))

    def _on_theme_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        self.selected = row.theme
        self._render_preview()

    # ---- 预览 ----
    def _on_magnify(self, btn: Gtk.ToggleButton) -> None:
        self.magnify = btn.get_active()
        btn.set_label("放大预览" if self.magnify else "实际大小")
        self._render_preview()

    @property
    def apply_size(self) -> int:
        return SIZES[self.size_dd.get_selected()]

    def _preview_target_px(self) -> int:
        """预览的目标像素宽度：放大模式用格子边长，实际模式按要应用的尺寸 1:1。"""
        return DISPLAY_PX if self.magnify else self.apply_size

    def _clear_flow(self) -> None:
        self.generation += 1        # 让还在排队的旧批次失效
        self.tiles = []
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)

    def _render_preview(self) -> None:
        theme = self.selected
        self._clear_flow()

        if theme is None:
            self.title_label.set_text("请选择一个主题")
            self.meta_label.set_text("")
            self.banner.set_visible(False)
            self.apply_btn.set_sensitive(False)
            self._update_delete_button()
            return

        self.title_label.set_text(theme.name)
        self.meta_label.set_text(f"{theme.path}")

        if not theme.valid:
            self.banner.set_visible(True)
            self.banner.add_css_class("warn-box")
            self.banner.set_markup(
                f"<b>✘ 这个主题无法被 Wayland 客户端加载</b>\n{GLib.markup_escape_text(theme.reason)}\n"
                "<span size='small'>提示：需要把光标文件放进该目录的 cursors/ 子目录里。</span>"
            )
            self.apply_btn.set_sensitive(False)
            self._update_delete_button()      # 布局不合法的主题反而更该能删掉
            return

        self.banner.set_visible(False)
        self.apply_btn.set_sensitive(True)
        self._update_delete_button()

        sizes: set[int] = set()
        for c in theme.cursors:
            sizes.update(c.sizes)
        meta = [
            f"{len(theme.cursors)} 个光标",
            f"{theme.alias_count} 个别名",
            f"{len(sizes)} 档尺寸" + (f"（{min(sizes)}–{max(sizes)}）" if sizes else ""),
            f"来自 {theme.root}",
        ]
        if theme.inherits:
            meta.append(f"继承 {theme.inherits}")
        self.meta_label.set_text(" · ".join(meta))

        nominal = self._preview_target_px()
        self._build_tiles(theme.cursors, nominal)

    def _build_tiles(self, cursors: list[Cursor], target_px: int, chunk: int = 4) -> None:
        """分批构建，避免一次性解码卡住界面。用世代号丢弃过期批次。"""
        queue = list(cursors)
        gen = self.generation

        def step() -> bool:
            if gen != self.generation:   # 期间又切了主题/尺寸，这批作废
                return False
            for _ in range(chunk):
                if not queue:
                    return False
                c = queue.pop(0)
                tile = CursorTile(c, target_px, self.magnify)
                self.flow.append(tile)
                self.tiles.append(tile)
            return bool(queue)

        GLib.idle_add(step)

    def _tick(self) -> bool:
        if self.animating:
            for t in self.tiles:
                t.advance(TICK_MS)
        return True

    # ---- 应用 ----
    def _on_apply(self, _btn: Gtk.Button) -> None:
        if self.selected is None or not self.selected.valid:
            return
        result = apply_theme(self.selected.name, self.apply_size)
        ResultDialog(self, result).present()
        self._update_status()

    # ---- 删除 ----
    def _active_theme(self) -> str:
        st = read_state()
        return st.get("niri") or st.get("gtk3") or ""

    def _update_delete_button(self) -> None:
        """删不动的时候禁用按钮，并把原因写进 tooltip —— 别让用户点了才被拒。"""
        t = self.selected
        if t is None:
            self.delete_btn.set_sensitive(False)
            self.delete_btn.set_tooltip_text("先选一个主题")
            return
        blockers = deletion_blockers(t, active=self._active_theme())
        if blockers:
            self.delete_btn.set_sensitive(False)
            self.delete_btn.set_tooltip_text("不能删除：\n· " + "\n· ".join(blockers))
        else:
            size, files = theme_size(t.path)
            self.delete_btn.set_sensitive(True)
            self.delete_btn.set_tooltip_text(f"删除 {t.name}（{files} 个文件，{_human(size)}）")

    def _on_delete(self, _btn: Gtk.Button) -> None:
        t = self.selected
        if t is None:
            return
        blockers = deletion_blockers(t, active=self._active_theme())
        if blockers:
            self._report_error("不能删除这个主题：\n\n" + "\n".join(f"· {b}" for b in blockers))
            return
        DeleteDialog(self, t, self._do_delete).present()

    def _do_delete(self, theme: Theme, purge: bool) -> None:
        rep = delete_theme(theme, active=self._active_theme(), purge=purge)
        if not rep.ok:
            self._report_error(f"删除失败：\n\n{rep.detail}")
            return

        self.selected = None
        self.refresh()
        if (row := self.theme_list.get_row_at_index(0)) is not None:
            self.theme_list.select_row(row)

        verb = "永久删除" if purge else "移入回收站"
        tail = f" · 位置 {rep.moved_to}" if rep.moved_to else ""
        self.status.set_text(
            f"✔ 已{verb} {theme.name}（{rep.files} 个文件，{rep.size_human}）{tail}"
        )

    # ---- 导入 ----
    def _on_import_file(self) -> None:
        self._pick(files=True)

    def _on_import_folder(self) -> None:
        self._pick(files=False)

    def _pick(self, files: bool) -> None:
        """文件对话框一次只能选"文件"或"文件夹"，所以入口分两个。"""
        dialog = Gtk.FileDialog()
        if files:
            dialog.set_title("选择光标包")
            ff = Gtk.FileFilter()
            ff.set_name("光标包（压缩包 / 光标文件）")
            for pat in ("*.zip", "*.tar.gz", "*.tgz", "*.tar.xz", "*.txz",
                        "*.tar.bz2", "*.tar", "*.ani", "*.cur", "*.ico"):
                ff.add_pattern(pat)
            everything = Gtk.FileFilter()
            everything.set_name("所有文件")
            everything.add_pattern("*")
            store = Gio.ListStore.new(Gtk.FileFilter.__gtype__)
            store.append(ff)
            store.append(everything)
            dialog.set_filters(store)
            dialog.set_default_filter(ff)
            dialog.open(self, None, self._on_file_picked)
        else:
            dialog.set_title("选择存放光标包的文件夹")
            dialog.select_folder(self, None, self._on_folder_picked)

    def _on_file_picked(self, dialog, result) -> None:
        self._finish_pick(dialog.open_finish, result)

    def _on_folder_picked(self, dialog, result) -> None:
        self._finish_pick(dialog.select_folder_finish, result)

    def _finish_pick(self, finish, result) -> None:
        try:
            gfile = finish(result)
        except GLib.Error as e:
            msg = str(e)
            if "dismiss" in msg.lower() or "cancel" in msg.lower():
                return
            self._report_error(
                "文件选择器打不开（通常是桌面门户不可用）：\n\n"
                f"{msg}\n\n改用命令行一样可以导入：\n"
                "  cursor-manager --import <路径>"
            )
            return
        if gfile is not None and gfile.get_path():
            self._start_import(Path(gfile.get_path()))

    def _start_import(self, path: Path, overwrite: bool = False) -> None:
        self._import = {
            "path": path, "done": False, "result": None, "error": None,
            "conflict": None, "trace": "", "progress": (0, 0, ""), "overwrite": overwrite,
        }
        self._set_busy(True, f"正在分析并转换 {path.name} …")
        threading.Thread(target=self._import_worker, args=(path,), daemon=True).start()
        GLib.timeout_add(150, self._poll_import)

    def _import_worker(self, path: Path) -> None:
        """在后台线程做转换 —— 大包可能要几秒，别卡住界面。"""
        st = self._import
        try:
            st["result"] = import_pack(
                path,
                sizes=DEFAULT_SIZES,
                overwrite=st["overwrite"],
                progress=lambda i, n, name: st.__setitem__("progress", (i, n, name)),
            )
        except FileExistsError as e:
            st["conflict"] = str(e)
        except Exception as e:                       # noqa: BLE001 —— 要送到界面上
            st["error"] = f"{type(e).__name__}: {e}"
            st["trace"] = traceback.format_exc()
        finally:
            st["done"] = True

    def _poll_import(self) -> bool:
        st = self._import
        if not st["done"]:
            i, n, name = st["progress"]
            if n:
                self._set_busy(True, f"正在转换 {i}/{n}  {name}")
            return True

        self._set_busy(False)

        if st["conflict"]:
            self._confirm(
                "主题已存在",
                f"{st['conflict']}\n\n要覆盖它吗？原有文件会被删除。",
                "覆盖", lambda: self._start_import(Path(st["path"]), overwrite=True),
            )
            return False

        if st["error"]:
            self._report_error(f"导入失败：\n\n{st['error']}\n\n{st['trace'][-600:]}")
            return False

        res = st["result"]
        self.refresh()
        for i, t in enumerate(self._visible_themes()):
            if t.name == res.inspection.name:
                self.theme_list.select_row(self.theme_list.get_row_at_index(i))
                break
        ImportResultDialog(self, res).present()
        return False

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        for w in (self.apply_btn, self.import_btn, self.anim_btn, self.magnify_btn):
            w.set_sensitive(not busy)
        if busy:
            self.delete_btn.set_sensitive(False)
            self.status.set_text(f"⏳ {message}")
        else:
            self._update_delete_button()      # 删除键的可用性取决于选中的主题
            self._update_status()

    def _confirm(self, title: str, body: str, ok_label: str, on_ok,
                 cancel: bool = True, destructive: bool = True) -> None:
        dlg = Gtk.Window(title=title, transient_for=self, modal=True)
        dlg.set_default_size(520, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for m in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{m}")(18)
        label = Gtk.Label(label=body, xalign=0, wrap=True)
        label.set_selectable(True)          # 报错信息要能复制出来
        box.append(label)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        if cancel:
            cancel_btn = Gtk.Button(label="取消")
            cancel_btn.connect("clicked", lambda *_: dlg.close())
            row.append(cancel_btn)
        ok = Gtk.Button(label=ok_label)
        if destructive:
            ok.add_css_class("destructive-action")
        else:
            ok.add_css_class("suggested-action")

        def go(*_):
            dlg.close()
            on_ok()
        ok.connect("clicked", go)
        row.append(ok)
        box.append(row)
        dlg.set_child(box)
        dlg.present()

    def _report_error(self, message: str) -> None:
        self._confirm("出错了", message, "知道了", lambda: None,
                      cancel=False, destructive=False)

    def _update_status(self) -> None:
        state = read_state()
        active = state.get("niri") or "?"
        parts = [f"{k}={v or '未设置'}" for k, v in state.items()]
        mismatched = [k for k, v in state.items() if v and v != active]
        tail = "全部一致" if not mismatched else f"不一致: {', '.join(mismatched)}"
        self.status.set_text(f"当前生效: {active} · {tail}  ▏ " + "  ".join(parts))


class ManagerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = ManagerWindow(self)
        win.present()




def run_gui(argv: list[str] | None = None) -> int:
    """启动图形界面。"""
    argv = list(sys.argv if argv is None else argv)
    if Gdk.Display.get_default() is None:               # 没有显示服务器
        print("没有可用的显示服务器（需要 Wayland 或 X11 图形会话）。", file=sys.stderr)
        print("在纯终端里可以改用： cursor-manager --list / --import / apply", file=sys.stderr)
        return 1
    app = ManagerApp()
    provider = load_css(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    return app.run(argv)
