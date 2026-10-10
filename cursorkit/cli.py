"""命令行界面 —— 不导入 GTK，所以无图形环境下也能用。

    cursor-manager --list
    cursor-manager --import <路径> [选项]
    cursor-manager apply <主题名> [尺寸]
    cursor-manager --help

`dispatch()` 返回 None 表示"不是命令行子命令"，交给 GUI 处理。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .apply import apply_theme, read_state
from .importer import DEFAULT_SIZES, import_pack
from .themes import scan_themes

USAGE = f"""cursor-manager {__version__} —— Linux 光标主题管理器（niri / Wayland）

用法:
  cursor-manager                          打开图形界面
  cursor-manager --list                   列出已安装的光标主题
  cursor-manager --import <路径> [选项]    导入 Windows 光标包
  cursor-manager apply <主题名> [尺寸]     切换光标主题（命令行）

导入选项:
  --name NAME       指定主题名（默认取包内 .inf 的 SCHEME_NAME）
  --sizes 24,48,96  要生成的尺寸档（默认 {",".join(map(str, DEFAULT_SIZES))}，总是包含原始分辨率）
  --overwrite       覆盖同名主题
  --apply           导入后立即应用到系统

apply 选项:
  --dry-run         只报告会改哪些文件，不真的写

  <路径> 可以是：文件夹 / .zip / .tar.gz / .tar.xz / .tar.bz2 / 单个 .ani/.cur/.ico
"""


# ---------------------------------------------------------------------------

def cmd_list(verbose: bool = False) -> int:
    themes = scan_themes(with_sizes=verbose)
    if not themes:
        print("没有找到任何光标主题。")
        return 1
    valid = sum(1 for t in themes if t.valid)
    for t in themes:
        mark = "\033[32m✔\033[0m" if t.valid else "\033[31m✘\033[0m"
        if t.valid:
            extra = f"{len(t.cursors):>3} 光标 / {t.alias_count:>3} 别名"
            if verbose and t.cursors:
                sizes: set[int] = set()
                for c in t.cursors:
                    sizes.update(c.sizes)
                if sizes:
                    extra += f" / 尺寸 {min(sizes)}–{max(sizes)}"
        else:
            extra = t.reason
        print(f"  {mark} {t.name:<26} {extra}")
        if verbose:
            print(f"      {t.path}")

    active = read_state().get("niri") or read_state().get("gtk3") or "?"
    print(f"\n  共 {len(themes)} 个主题（{valid} 个可用），当前生效: {active}")
    bad = [t for t in themes if not t.valid]
    if bad:
        print(f"  \033[33m✘ 标记的主题布局不合法，Wayland 客户端会忽略它们并回落到 Adwaita。\033[0m")
        print(f"    需要把光标文件放进该目录的 cursors/ 子目录里。")
    return 0


def cmd_import(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cursor-manager --import", add_help=False)
    p.add_argument("path")
    p.add_argument("--name")
    p.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--apply", action="store_true")
    try:
        args = p.parse_args(argv)
    except SystemExit:
        print(USAGE)
        return 2

    try:
        sizes = tuple(sorted({int(s) for s in args.sizes.split(",") if s.strip()}))
    except ValueError:
        print("\033[31m--sizes 只能是逗号分隔的数字，例如 --sizes 24,32,48\033[0m")
        return 2
    if not sizes:
        sizes = DEFAULT_SIZES

    path = Path(args.path).expanduser()
    tty = sys.stdout.isatty()
    print(f"导入 {path}")
    print(f"  目标尺寸档: {', '.join(map(str, sizes))}")

    def progress(i: int, n: int, name: str) -> None:
        if not tty:
            return
        filled = i * 20 // max(n, 1)
        bar = "█" * filled + "░" * (20 - filled)
        print(f"\r  [{bar}] {i}/{n}  {name:<22}", end="", flush=True)

    try:
        res = import_pack(path, sizes=sizes, name=args.name,
                          overwrite=args.overwrite, progress=progress)
    except Exception as e:  # noqa: BLE001 —— CLI 顶层，直接报给用户
        print(f"\n\033[31m导入失败: {type(e).__name__}: {e}\033[0m")
        return 1

    if tty:
        print()
    info = res.inspection
    print(f"\n\033[32m✔ 导入完成\033[0m   {info.name}")
    print(f"  原名      : {info.raw_name}"
          + ("（主题名已清洗成 ASCII 安全）" if info.raw_name != info.name else ""))
    print(f"  角色映射  : {'包内 .inf（官方数据）' if info.used_inf else '按文件名推断'}")
    print(f"  识别角色  : {len(info.roles)} 个 / {info.total_frames} 帧 / "
          f"{info.animated_count} 个动画")
    if info.native_sizes:
        print("  原始分辨率: " + "、".join(f"{w}×{h}" for w, h in info.native_sizes))
    print(f"  转出      : {res.files_written} 个光标文件 + {res.aliases_created} 个别名"
          f"（{res.bytes_written / 1024 / 1024:.1f} MB）")
    print(f"  安装位置  : {res.dest}")
    for w in info.warnings:
        print(f"  \033[33m⚠ {w}\033[0m")
    if info.unmapped:
        print(f"  \033[33m⚠ 未识别 {len(info.unmapped)} 个文件: "
              + ", ".join(f.name for f in info.unmapped[:6]) + "\033[0m")
    for e in res.errors:
        print(f"  \033[31m✘ {e}\033[0m")

    if args.apply:
        return cmd_apply([info.name], quiet=False)
    print("\n下一步 —— 应用它:")
    print(f"  cursor-manager apply {info.name} 24")
    return 0 if res.ok else 1


def cmd_apply(argv: list[str], quiet: bool = True) -> int:
    p = argparse.ArgumentParser(prog="cursor-manager apply", add_help=False)
    p.add_argument("theme")
    p.add_argument("size", nargs="?", type=int, default=24)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="即使主题布局不合法也照样写配置")
    try:
        args = p.parse_args(argv)
    except SystemExit:
        print(USAGE)
        return 2

    themes = {t.name: t for t in scan_themes()}
    t = themes.get(args.theme)
    if t is None:
        print(f"\033[31m找不到主题 '{args.theme}'\033[0m")
        near = [n for n in themes if args.theme.lower() in n.lower()]
        if near:
            print("  你是不是想要: " + ", ".join(near[:5]))
        print("  用 cursor-manager --list 看全部可用主题。")
        return 1
    if not t.valid and not args.force:
        print(f"\033[31m主题 '{t.name}' 布局不合法，装上去也不会生效:\033[0m\n  {t.reason}")
        print("  确认要用就加 --force。")
        return 1

    if not 8 <= args.size <= 512:
        print(f"\033[31m尺寸 {args.size} 不合理（应在 8–512 之间）\033[0m")
        return 1

    verb = "试运行" if args.dry_run else "应用"
    print(f"{verb}: {t.name} / {args.size}px")
    res = apply_theme(t.name, args.size, dry_run=args.dry_run)
    for r in res.reports:
        print(f"  {r}")
    if not res.ok:
        print(f"\n\033[31m{len(res.failures)} 处失败\033[0m")
        return 1

    if not args.dry_run:
        print("\n生效方式:")
        print("  · niri 保存配置后会自动重载 —— 桌面光标立即变化")
        print("  · 新启动的程序立即生效；已开着的程序需要重启")
        print("  · 想让环境变量对所有新进程一致，最稳是注销重登一次会话")
    return 0


# ---------------------------------------------------------------------------

def dispatch(argv: list[str]) -> int | None:
    """处理命令行子命令。返回 None 表示"交给 GUI"。"""
    args = argv[1:]
    if not args:
        return None

    head = args[0]
    if head in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if head in ("-V", "--version", "version"):
        print(f"cursor-manager {__version__}")
        return 0
    if head == "--list":
        return cmd_list(verbose="-v" in args or "--verbose" in args)
    if head == "--import":
        return cmd_import(args[1:])
    if head == "apply":
        return cmd_apply(args[1:])
    if head.startswith("-"):
        print(f"\033[31m未知选项: {head}\033[0m\n")
        print(USAGE)
        return 2
    # 既不是选项也不是子命令 —— 当作主题名，等价于 apply
    return cmd_apply(args)
