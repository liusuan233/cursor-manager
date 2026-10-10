"""把选定的光标主题写到系统的各个配置点。

Wayland 下没有全局光标设置。要让"所有程序"都换过来，必须同时写这几处：

    niri config.kdl   cursor { xcursor-theme / xcursor-size }   niri 自己画的光标
    niri config.kdl   environment { XCURSOR_THEME / _SIZE }      niri 启动的子进程
    dms/cursor.kdl    cursor { ... }                             DMS 托管的那一份
    gtk-3.0/4.0       settings.ini gtk-cursor-theme-name         GTK3 / GTK4 应用
    ~/.Xresources     Xcursor.theme                              XWayland 应用
    gsettings         org.gnome.desktop.interface                走 GSettings 的程序

漏掉任何一处，那一类程序就会继续用 Adwaita。
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()

NIRI_CFG = HOME / ".config" / "niri" / "config.kdl"
DMS_CFG = HOME / ".config" / "niri" / "dms" / "cursor.kdl"
XRESOURCES = HOME / ".Xresources"
GTK_INIS = {
    "GTK3": HOME / ".config" / "gtk-3.0" / "settings.ini",
    "GTK4": HOME / ".config" / "gtk-4.0" / "settings.ini",
}


@dataclass
class ApplyReport:
    """一处配置的写入结果。"""

    target: str
    ok: bool
    detail: str = ""
    skipped: bool = False

    @property
    def icon(self) -> str:
        if self.skipped:
            return "○"
        return "✔" if self.ok else "✘"

    def __str__(self) -> str:
        return f"{self.icon} {self.target}: {self.detail}"


@dataclass
class ApplyResult:
    theme: str
    size: int
    reports: list[ApplyReport] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return all(r.ok or r.skipped for r in self.reports)

    @property
    def failures(self) -> list[ApplyReport]:
        return [r for r in self.reports if not r.ok and not r.skipped]


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    bak = path.with_name(f"{path.name}.bak.{int(time.time())}")
    try:
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


# --------------------------------------------------------------------------
# 各处写入
# --------------------------------------------------------------------------

_CURSOR_BLOCK = re.compile(r"^([ \t]*)cursor[ \t]*\{", re.M)
_ENV_BLOCK = re.compile(r"^([ \t]*)environment[ \t]*\{", re.M)
_KDL_THEME = re.compile(r"^([ \t]*)xcursor-theme[ \t]+.*$", re.M)
_KDL_SIZE = re.compile(r"^([ \t]*)xcursor-size[ \t]+.*$", re.M)
_ENV_THEME = re.compile(r"^([ \t]*)XCURSOR_THEME[ \t]+.*$", re.M)
_ENV_SIZE = re.compile(r"^([ \t]*)XCURSOR_SIZE[ \t]+.*$", re.M)


def _patch_niri_kdl(text: str, theme: str, size: int) -> tuple[str, list[str]]:
    """就地改写 KDL 文本：同步已有 cursor{} / 追加 cursor{} / 注入环境变量。"""
    notes: list[str] = []

    if _KDL_THEME.search(text):
        text = _KDL_THEME.sub(lambda m: f'{m.group(1)}xcursor-theme "{theme}"', text)
        notes.append("更新 cursor 块")
    elif _CURSOR_BLOCK.search(text):
        text = _CURSOR_BLOCK.sub(lambda m: f'{m.group(0)}\n{m.group(1)}    xcursor-theme "{theme}"', text, count=1)
        notes.append("cursor 块内插入 xcursor-theme")
    else:
        text = text.rstrip("\n") + f'\n\ncursor {{\n    xcursor-theme "{theme}"\n    xcursor-size {size}\n}}\n'
        notes.append("追加 cursor 块")

    if _KDL_SIZE.search(text):
        text = _KDL_SIZE.sub(lambda m: f"{m.group(1)}xcursor-size {size}", text)
    elif _CURSOR_BLOCK.search(text):
        text = _CURSOR_BLOCK.sub(lambda m: f"{m.group(0)}\n{m.group(1)}    xcursor-size {size}", text, count=1)

    if _ENV_THEME.search(text):
        text = _ENV_THEME.sub(lambda m: f'{m.group(1)}XCURSOR_THEME "{theme}"', text)
        text = _ENV_SIZE.sub(lambda m: f'{m.group(1)}XCURSOR_SIZE "{size}"', text)
        notes.append("更新环境变量")
    elif _ENV_BLOCK.search(text):
        text = _ENV_BLOCK.sub(
            lambda m: f'{m.group(0)}\n{m.group(1)}    XCURSOR_THEME "{theme}"\n{m.group(1)}    XCURSOR_SIZE "{size}"',
            text, count=1,
        )
        notes.append("注入环境变量")
    else:
        text = text.rstrip("\n") + f'\n\nenvironment {{\n    XCURSOR_THEME "{theme}"\n    XCURSOR_SIZE "{size}"\n}}\n'
        notes.append("追加 environment 块")

    return text, notes


def set_niri_config(theme: str, size: int, dry_run: bool = False) -> ApplyReport:
    if not NIRI_CFG.is_file():
        return ApplyReport("niri config.kdl", True, "不存在，跳过", skipped=True)
    try:
        text = NIRI_CFG.read_text(encoding="utf-8")
        new, notes = _patch_niri_kdl(text, theme, size)
        if dry_run:
            return ApplyReport("niri config.kdl", True, f"[试运行] {', '.join(notes)}")
        if new != text:
            _backup(NIRI_CFG)
            NIRI_CFG.write_text(new, encoding="utf-8")
        return ApplyReport("niri config.kdl", True, "、".join(notes))
    except OSError as e:
        return ApplyReport("niri config.kdl", False, str(e))


def set_dms_cursor_kdl(theme: str, size: int, dry_run: bool = False) -> ApplyReport:
    # 没有 niri 的机器（GNOME / KDE / X11 用户）不该被凭空造出 ~/.config/niri/
    if not NIRI_CFG.parent.is_dir():
        return ApplyReport("DMS cursor.kdl", True, "没有 niri，跳过", skipped=True)

    content = (
        "// ! DO NOT EDIT !\n"
        "// ! AUTO-GENERATED BY DMS !\n"
        "// ! CHANGES WILL BE OVERWRITTEN !\n"
        "// ! PLACE YOUR CUSTOM CONFIGURATION ELSEWHERE !\n"
        "\n"
        f'cursor {{\n    xcursor-theme "{theme}"\n    xcursor-size {size}\n}}\n'
    )
    if dry_run:
        return ApplyReport("DMS cursor.kdl", True, "[试运行] 将写入")
    try:
        DMS_CFG.parent.mkdir(parents=True, exist_ok=True)
        DMS_CFG.write_text(content, encoding="utf-8")
        return ApplyReport("DMS cursor.kdl", True, "已写入（DMS 若改回，请在 DMS 设置→Cursors 里选）")
    except OSError as e:
        return ApplyReport("DMS cursor.kdl", False, str(e))


def set_gtk_settings(theme: str, size: int, dry_run: bool = False) -> list[ApplyReport]:
    out: list[ApplyReport] = []
    for label, path in GTK_INIS.items():
        try:
            cp = configparser.ConfigParser()
            cp.optionxform = str  # 保留键名大小写
            if path.is_file():
                cp.read(path, encoding="utf-8")
            if not cp.has_section("Settings"):
                cp.add_section("Settings")
            cp.set("Settings", "gtk-cursor-theme-name", theme)
            cp.set("Settings", "gtk-cursor-theme-size", str(size))
            if dry_run:
                out.append(ApplyReport(f"{label} settings.ini", True, "[试运行] 将写入"))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                cp.write(fh, space_around_delimiters=False)
            out.append(ApplyReport(f"{label} settings.ini", True, f"gtk-cursor-theme-name={theme}"))
        except (OSError, configparser.Error) as e:
            out.append(ApplyReport(f"{label} settings.ini", False, str(e)))
    return out


def set_xresources(theme: str, size: int, dry_run: bool = False) -> ApplyReport:
    try:
        lines: list[str] = []
        if XRESOURCES.is_file():
            lines = [
                ln for ln in XRESOURCES.read_text(encoding="utf-8").splitlines()
                if not re.match(r"^\s*Xcursor\.(theme|size)", ln)
            ]
        lines += [f"Xcursor.theme: {theme}", f"Xcursor.size: {size}"]
        if dry_run:
            return ApplyReport(".Xresources", True, "[试运行] 将写入")
        _backup(XRESOURCES)
        XRESOURCES.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ApplyReport(".Xresources", True, f"Xcursor.theme: {theme}（XWayland）")
    except OSError as e:
        return ApplyReport(".Xresources", False, str(e))


def set_gsettings(theme: str, size: int, dry_run: bool = False) -> ApplyReport:
    if not shutil.which("gsettings"):
        return ApplyReport("gsettings", True, "未安装，跳过", skipped=True)
    if dry_run:
        return ApplyReport("gsettings", True, "[试运行] 将写入")
    try:
        r1 = subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", theme],
            capture_output=True, text=True, timeout=10,
        )
        if r1.returncode != 0:
            return ApplyReport("gsettings", True, f"写入失败（dconf 不可用），不影响其它程序", skipped=True)
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.interface", "cursor-size", str(size)],
            capture_output=True, text=True, timeout=10,
        )
        return ApplyReport("gsettings", True, f"cursor-theme={theme}")
    except (OSError, subprocess.SubprocessError) as e:
        return ApplyReport("gsettings", True, f"跳过：{e}", skipped=True)


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------

def apply_theme(theme: str, size: int = 24, dry_run: bool = False) -> ApplyResult:
    """把主题写到所有配置点，返回逐项报告。"""
    res = ApplyResult(theme=theme, size=size, dry_run=dry_run)
    res.reports.append(set_niri_config(theme, size, dry_run))
    res.reports.append(set_dms_cursor_kdl(theme, size, dry_run))
    res.reports.extend(set_gtk_settings(theme, size, dry_run))
    res.reports.append(set_xresources(theme, size, dry_run))
    res.reports.append(set_gsettings(theme, size, dry_run))
    return res


_CUR_THEME_RE = re.compile(r'xcursor-theme\s+"([^"]*)"')
_GTK_RE = re.compile(r"^gtk-cursor-theme-name\s*=\s*(.*)$", re.M)
_XRES_RE = re.compile(r"^Xcursor\.theme:\s*(.*)$", re.M)


def read_state() -> dict[str, str]:
    """读回当前各处实际生效的主题名，用于界面上的"是否已同步"判断。"""
    state: dict[str, str] = {}

    def grab(key: str, path: Path, rx: re.Pattern[str]) -> None:
        try:
            m = rx.search(path.read_text(encoding="utf-8", errors="replace"))
            state[key] = m.group(1).strip() if m else ""
        except OSError:
            state[key] = ""

    grab("niri", NIRI_CFG, _CUR_THEME_RE)
    grab("dms", DMS_CFG, _CUR_THEME_RE)
    grab("gtk3", GTK_INIS["GTK3"], _GTK_RE)
    grab("gtk4", GTK_INIS["GTK4"], _GTK_RE)
    grab("xresources", XRESOURCES, _XRES_RE)

    if shutil.which("gsettings"):
        try:
            r = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "cursor-theme"],
                capture_output=True, text=True, timeout=10,
            )
            state["gsettings"] = r.stdout.strip().strip("'") if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            state["gsettings"] = ""
    return state


def state_is_synced(state: dict[str, str], theme: str) -> tuple[bool, list[str]]:
    """返回 (是否全部一致, 不一致的项)。"""
    mismatched = [k for k, v in state.items() if v and v != theme]
    empty = [k for k, v in state.items() if not v]
    return (not mismatched), mismatched + [f"{k}(未设置)" for k in empty]
