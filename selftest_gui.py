#!/usr/bin/env python3
"""GUI 自检：构造窗口、跑事件循环、检查控件树实际状态。

在无头环境下也能跑，用来自动验证：
  · 主题列表是否正确填充
  · 选中主题后预览网格是否真的建出了格子、贴上了纹理
  · 纹理尺寸是否与请求的标称尺寸一致
  · 布局不合法的主题是否触发警告条并禁用"应用"
  · 搜索过滤是否生效
退出码 0 = 全部通过。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from cursorkit import gui as cm  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✘\033[0m"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def rows_of(listbox: Gtk.ListBox) -> list:
    out, i = [], 0
    while (r := listbox.get_row_at_index(i)) is not None:
        out.append(r)
        i += 1
    return out


def main() -> int:
    provider = cm.load_css(cm.CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    app = cm.ManagerApp()
    state = {"phase": 0}

    def pump(ms: int, then):
        """每个阶段都套异常兜底：阶段里抛异常时不会让主循环一直空转挂住。"""
        def run():
            try:
                then()
            except Exception as e:                       # noqa: BLE001
                import traceback
                FAILURES.append(f"{then.__name__} 抛异常: {type(e).__name__}: {e}")
                print(f"\n\033[31m阶段 {then.__name__} 抛异常:\033[0m")
                traceback.print_exc()
                app.quit()
            return False
        GLib.timeout_add(ms, run)

    def phase1():
        win = app.props.active_window
        state["win"] = win
        print("\n=== 1. 列表填充 ===")
        rows = rows_of(win.theme_list)
        check("主题列表非空", len(rows) > 0, f"{len(rows)} 行")
        names = [r.theme.name for r in rows]
        check("包含 Kinomoto-Sakura", "Kinomoto-Sakura" in names, ", ".join(names[:5]))
        check("坏的主题也在列表里", any(not r.theme.valid for r in rows))

        sel = win.theme_list.get_selected_row()
        check("自动选中当前生效的主题", sel is not None and sel.theme.name == "Kinomoto-Sakura",
              sel.theme.name if sel else "无")
        pump(1200, phase2)

    def phase2():
        win = state["win"]
        print("\n=== 2. 预览网格 ===")
        check("选中主题有效", win.selected is not None and win.selected.valid,
              win.selected.name if win.selected else "无")
        n_expected = len(win.selected.cursors)
        check("格子数 == 光标数", len(win.tiles) == n_expected, f"{len(win.tiles)} / {n_expected}")
        with_tex = [t for t in win.tiles if t.picture.get_paintable() is not None]
        check("所有格子都贴上了纹理", len(with_tex) == len(win.tiles),
              f"{len(with_tex)}/{len(win.tiles)}")
        errors = [t.cursor.name for t in win.tiles if t.error]
        check("没有解码失败的光标", not errors, ", ".join(errors) or "无")

        print("\n=== 3. 纹理尺寸 ===")
        for t in win.tiles[:4]:
            tex = t.picture.get_paintable()
            w = tex.get_width()
            print(f"      {t.cursor.name:<18} {w}x{tex.get_height()}  {t.anim_label.get_text()}")
        anim = [t for t in win.tiles if len(t.bounds) > 1]
        check("存在带动画的光标", len(anim) > 0, f"{len(anim)} 个多帧光标")

        print("\n=== 4. 动画推进 ===")
        t0 = anim[0]
        before = t0.picture.get_paintable()
        for _ in range(6):
            t0.advance(cm.TICK_MS)
        check("推进后帧发生变化", t0.picture.get_paintable() is not before,
              f"{t0.cursor.name} 共 {len(t0.bounds)} 帧")

        print("\n=== 5. 改变尺寸后重绘 ===")
        win.size_dd.set_selected(cm.SIZES.index(48))
        pump(900, phase3)

    def phase3():
        win = state["win"]
        widths = {}
        for t in win.tiles:
            tex = t.picture.get_paintable()
            if tex is not None:
                widths.setdefault(tex.get_width(), []).append(t.cursor.name)
        check("尺寸改变后纹理重建", len(win.tiles) == len(win.selected.cursors),
              f"{len(win.tiles)} 格")
        check("所有纹理都不超过格子边长 96px", all(w <= 96 for w in widths),
              "宽度分布: " + ", ".join(f"{w}px×{len(n)}" for w, n in sorted(widths.items())))
        check("纹理宽度统一", len(widths) == 1,
              ", ".join(f"{w}px: {', '.join(n[:3])}" for w, n in sorted(widths.items())))

        print("\n=== 6. 实际大小模式 ===")
        win.magnify_btn.set_active(False)
        pump(900, phase4)

    def phase4():
        win = state["win"]
        w = next((t.picture.get_paintable().get_width() for t in win.tiles
                  if t.picture.get_paintable()), 0)
        check("切到实际大小后纹理等于应用尺寸", w == win.apply_size,
              f"纹理 {w}px，应用尺寸 {win.apply_size}px")

        print("\n=== 7. 选中坏主题 ===")
        win.magnify_btn.set_active(True)
        bad_i = next(i for i, r in enumerate(rows_of(win.theme_list)) if not r.theme.valid)
        win.theme_list.select_row(win.theme_list.get_row_at_index(bad_i))
        pump(400, phase5)

    def phase5():
        win = state["win"]
        check("警告条可见", win.banner.get_visible())
        check("警告条是红色样式", win.banner.has_css_class("warn-box"))
        check("应用按钮已禁用", not win.apply_btn.get_sensitive())
        print(f"      警告文案: {(win.banner.get_text() or '')[:60]}…")

        print("\n=== 8. 搜索过滤 ===")
        win.search.set_text("breeze")
        pump(200, phase6)

    def phase6():
        win = state["win"]
        rows = rows_of(win.theme_list)
        check("过滤只剩 breeze*", rows and all("breeze" in r.theme.name.lower() for r in rows),
              ", ".join(r.theme.name for r in rows))
        win.search.set_text("")
        pump(200, phase7)

    def phase7():
        win = state["win"]
        print("\n=== 9. 状态栏 ===")
        txt = win.status.get_text()
        check("状态栏显示当前生效主题", "Kinomoto-Sakura" in txt, txt[:100])

        print("\n=== 10. 干跑应用（不写盘） ===")
        from cursorkit import apply_theme
        res = apply_theme("Kinomoto-Sakura", 24, dry_run=True)
        check("六个配置点全部就绪", len(res.reports) == 6, f"{len(res.reports)} 项")
        check("干跑全部成功", res.ok)
        pump(200, phase8)

    def phase8():
        win = state["win"]
        print("\n=== 11. 导入界面 ===")
        check("头部有导入按钮", win.import_btn is not None)
        model = win.import_btn.get_menu_model()
        check("导入菜单有 2 项", model is not None and model.get_n_items() == 2,
              f"{model.get_n_items() if model else 0} 项"
              + (f": {model.get_item_attribute_value(0, 'label', None).get_string()}"
                 if model and model.get_n_items() else ""))
        acts = win.actions
        names = acts.list_actions() if acts else []
        check("win.import-file / win.import-folder 已注册",
              {"import-file", "import-folder"} <= set(names), str(names))

        print("\n=== 12. 导入结果对话框 ===")
        import tempfile
        from cursorkit.importer import import_pack
        with tempfile.TemporaryDirectory(prefix="gui-selftest-") as td:
            src = Path(__file__).resolve().parent / "_selftest_pack"
            if src.is_dir():
                res = import_pack(src, dest_root=Path(td), sizes=(24, 48))
                check("导入产出主题", res.ok,
                      f"{res.files_written} 文件 / {res.aliases_created} 别名")
                dlg = cm.ImportResultDialog(win, res)
                check("结果对话框可构造", dlg.get_child() is not None)
                dlg.destroy()
            else:
                print("      （合成包不存在，跳过 —— 由 selftest_import.py 覆盖）")

        print("\n=== 13. 忙碌状态 ===")
        win._set_busy(True, "测试中…")
        check("忙碌时禁用导入按钮", not win.import_btn.get_sensitive())
        check("忙碌时状态栏有提示", "测试中" in win.status.get_text(), win.status.get_text()[:40])
        win._set_busy(False)
        check("恢复后重新启用", win.import_btn.get_sensitive())

        app.quit()

    pump(900, phase1)
    app.run([])

    print(f"\n{'='*54}")
    if FAILURES:
        print(f"\033[31m{len(FAILURES)}/{CHECKS} 项失败:\033[0m")
        for f in FAILURES:
            print("   -", f)
        return 1
    print(f"\033[32m全部 {CHECKS} 项检查通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
