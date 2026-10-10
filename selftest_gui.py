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

    def wait_until(pred, then, what: str = "", timeout_ms: int = 15000):
        """等到条件成立再进入下一阶段。

        预览格子是分批 idle 构建的，主题里光标越多、机器越忙，用时越长。
        用固定延时会在慢机器上偶发失败（曾经在 47 个光标的主题上翻过车），
        所以这里改成轮询等待 —— 测试必须确定性地通过或失败。
        """
        remaining = [timeout_ms]

        def tick():
            try:
                if pred():
                    then()
                    return False
            except Exception as e:                       # noqa: BLE001
                FAILURES.append(f"等待 {what} 时抛异常: {type(e).__name__}: {e}")
                app.quit()
                return False
            remaining[0] -= 50
            if remaining[0] <= 0:
                FAILURES.append(f"等待超时：{what or pred}")
                print(f"\n\033[31m等待超时：{what or pred}\033[0m")
                app.quit()
                return False
            return True

        GLib.timeout_add(50, tick)

    def phase1():
        win = app.props.active_window
        state["win"] = win
        print("\n=== 1. 列表填充 ===")
        rows = rows_of(win.theme_list)
        check("主题列表非空", len(rows) > 0, f"{len(rows)} 行")
        names = [r.theme.name for r in rows]

        # 不要硬编码主题名 —— 用户随时可能换主题，写死会让自检莫名其妙地失败。
        # 改为从实际状态里读出"当前生效的是哪个"，再断言界面选中了它。
        state_now = cm.read_state()
        active = state_now.get("niri") or state_now.get("gtk3") or ""
        valid_names = [r.theme.name for r in rows if r.theme.valid]
        state["active"] = active if active in valid_names else (valid_names[0] if valid_names else "")
        check("有可用的主题", bool(valid_names), ", ".join(valid_names[:4]))
        print(f"      当前生效: {active or '（读不到）'}   自检将使用: {state['active']}")
        check("坏的主题（缺 cursors/）也在列表里并被标红",
              any(not r.theme.valid for r in rows),
              next((r.theme.name for r in rows if not r.theme.valid), "本机没有这样的主题"))

        sel = win.theme_list.get_selected_row()
        check("自动选中当前生效的主题",
              sel is not None and sel.theme.name == state["active"],
              f"选中 {sel.theme.name if sel else '无'}，期望 {state['active']}")
        tiles_ready = (lambda: win.selected is not None
                       and len(win.tiles) == len(win.selected.cursors)
                       and len(win.tiles) > 0)
        wait_until(tiles_ready, phase2, "首屏预览格子构建完成")

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
        wait_until(lambda: len(win.tiles) == len(win.selected.cursors) and len(win.tiles) > 0,
                   phase3, "改尺寸后预览重建完成")

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
        wait_until(lambda: len(win.tiles) == len(win.selected.cursors) and len(win.tiles) > 0,
                   phase4, "切到实际大小后预览重建完成")

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
        wait_until(lambda: win.banner.get_visible(), phase5, "坏主题的警告条出现")

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
        check("状态栏显示当前生效主题", state["active"] in txt, txt[:100])
        check("状态栏同时给出各处是否一致", "一致" in txt)

        print("\n=== 10. 干跑应用（不写盘） ===")
        from cursorkit import apply_theme
        # 必须 dry_run=True —— 自检绝不能改用户真实的光标配置
        res = apply_theme(state["active"], 24, dry_run=True)
        check("六个配置点全部就绪", len(res.reports) == 6, f"{len(res.reports)} 项")
        check("干跑全部成功", res.ok)
        check("干跑确实没写盘（报告里都标了试运行/跳过）",
              all("试运行" in r.detail or r.skipped for r in res.reports),
              "; ".join(r.detail for r in res.reports if "试运行" not in r.detail and not r.skipped) or "全部标记正确")
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

        check("头部有应用菜单（检查更新入口）", win.menu_btn is not None)
        m = win.menu_btn.get_menu_model()
        check("应用菜单里有检查更新",
              m is not None and m.get_n_items() >= 1
              and "检查更新" in (m.get_item_attribute_value(0, "label", None) or
                                 GLib.Variant.new_string("")).get_string(),
              f"{m.get_n_items() if m else 0} 项")
        check("win.check-update 动作已注册", "check-update" in names, str(names))

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

        print("\n=== 13. 删除界面 ===")
        check("头部有删除按钮", win.delete_btn is not None)

        from cursorkit.themes import deletion_blockers
        active = state["active"]
        rows = rows_of(win.theme_list)
        themes = [r.theme for r in rows]

        # 找一个"不可删"的和一个"可删"的，分别验证按钮状态
        blocked = next((t for t in themes if deletion_blockers(t, active=active)), None)
        deletable = next((t for t in themes if not deletion_blockers(t, active=active)), None)

        if blocked is not None:
            win.selected = blocked
            win._update_delete_button()
            check("选中不可删的主题 → 按钮禁用", not win.delete_btn.get_sensitive(),
                  blocked.name)
            tip = win.delete_btn.get_tooltip_text() or ""
            check("tooltip 说明了不能删的原因", "不能删除" in tip, tip.split("\n")[-1][:60])

        if deletable is not None:
            win.selected = deletable
            win._update_delete_button()
            check("选中可删的主题 → 按钮启用", win.delete_btn.get_sensitive(), deletable.name)
            tip = win.delete_btn.get_tooltip_text() or ""
            check("tooltip 给出了大小和文件数", "个文件" in tip, tip[:60])

            dlg = cm.DeleteDialog(win, deletable, lambda *_: None)
            check("删除确认对话框可构造", dlg.get_child() is not None)
            dlg.destroy()
        else:
            print("      （本机没有可删的用户主题，跳过可删分支 —— 由 selftest_delete.py 覆盖）")

        # 恢复选中，免得影响后面的忙碌状态检查
        win.selected = themes[0] if themes else None
        win._update_delete_button()

        print("\n=== 14. 忙碌状态 ===")
        win._set_busy(True, "测试中…")
        check("忙碌时禁用导入按钮", not win.import_btn.get_sensitive())
        check("忙碌时禁用删除按钮", not win.delete_btn.get_sensitive())
        check("忙碌时状态栏有提示", "测试中" in win.status.get_text(), win.status.get_text()[:40])
        win._set_busy(False)
        check("恢复后重新启用", win.import_btn.get_sensitive())
        check("恢复后删除按钮按选中项重算", win.delete_btn.get_sensitive() ==
              (not deletion_blockers(win.selected, active=active) if win.selected else False))

        app.quit()

    wait_until(lambda: (w := app.props.active_window) is not None
               and w.theme_list.get_selected_row() is not None,
               phase1, "窗口就绪并自动选中主题", timeout_ms=20000)
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
