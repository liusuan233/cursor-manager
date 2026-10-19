"""cursorkit —— Linux 鼠标光标主题工具核心库

模块划分：
    xcursor   Xcursor 二进制格式 读 / 写
    windows   Windows .ani / .cur / .ico 光标解析
    importer  角色映射 + 主题包导入
    themes    主题扫描、布局校验、别名归并
    apply     把主题写入 niri / DMS / GTK / XWayland / gsettings
    cli       命令行界面（不依赖 GTK）
    gui       GTK4 图形界面

设计目标：核心库不依赖 GUI 框架，可单独在命令行或脚本里使用。
命令行子命令走 cli，只有真要开窗时才导入 gui。
"""

__version__ = "1.0.3"

from .xcursor import CursorFile, Frame, XcursorError, write_xcursor  # noqa: F401
from .themes import Cursor, Theme, scan_themes, iter_search_roots  # noqa: F401
from .apply import ApplyReport, apply_theme, read_state  # noqa: F401

__all__ = [
    "CursorFile", "Frame", "XcursorError", "write_xcursor",
    "Cursor", "Theme", "scan_themes", "iter_search_roots",
    "ApplyReport", "apply_theme", "read_state",
]
