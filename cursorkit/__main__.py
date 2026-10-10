"""cursor-manager 统一入口。

命令行子命令（--list / --import / apply）走 cli 模块，**不导入 GTK**；
只有真的要开窗口时才导入 gui，所以无图形环境（服务器、纯终端）也能用。
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    from .cli import dispatch

    rc = dispatch(argv)
    if rc is not None:
        return rc

    from .gui import run_gui

    return run_gui(argv)


if __name__ == "__main__":
    sys.exit(main())
