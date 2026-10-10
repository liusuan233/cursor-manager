#!/usr/bin/env python3
"""检查更新自检。

默认**完全不联网** —— 全部用 mock 打桩，所以结果确定、可在 CI 里跑。
末尾有一项真实网络检查，连不上就跳过。

覆盖：
  1. 版本号解析与比较（'1.0.10' vs '1.0.9' 这类字符串比较的陷阱）
  2. 有新版本 / 已是最新 / 本地比发布还新
  3. 各种失败：404、403 限流、连不上、返回内容坏掉
  4. 失败时**不能抛异常**，主功能不受影响
退出码 0 = 全部通过。
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import cursorkit.update as U  # noqa: E402

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


class FakeResp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self) -> bytes:
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def mock_ok(payload: dict):
    return patch("urllib.request.urlopen",
                 return_value=FakeResp(json.dumps(payload).encode()))


def mock_raw(raw: bytes):
    return patch("urllib.request.urlopen", return_value=FakeResp(raw))


def mock_http(code: int):
    def boom(*a, **k):
        raise urllib.error.HTTPError(U.API_LATEST, code, "err", {}, None)
    return patch("urllib.request.urlopen", side_effect=boom)


def mock_urlerr(reason: str):
    def boom(*a, **k):
        raise urllib.error.URLError(reason)
    return patch("urllib.request.urlopen", side_effect=boom)


def release(tag: str, body: str = "notes") -> dict:
    return {"tag_name": tag, "html_url": f"https://example.invalid/{tag}",
            "published_at": "2026-10-10T10:00:00Z", "body": body}


def main() -> int:
    print("检查更新自检\n" + "=" * 56)

    # ---------------------------------------------------------------- 1
    section("1. 版本号解析与比较")
    for text, want in [("v1.0.2", (1, 0, 2)), ("1.0.2", (1, 0, 2)),
                       ("1.0.2-beta", (1, 0, 2)), ("2", (2,)), ("", (0,))]:
        got = U.parse_version(text)
        check(f"parse({text!r}) = {got}", got == want, f"期望 {want}")

    cases = [
        ("1.0.10", "1.0.9", 1, "字符串比较会把 1.0.10 判成更小"),
        ("1.0.9", "1.0.10", -1, ""),
        ("1.0.1", "1.0.1", 0, ""),
        ("v1.0.2", "1.0.1", 1, "带 v 前缀"),
        ("1.0", "1.0.0", 0, "长度不同要补零"),
        ("2.0.0", "1.99.99", 1, ""),
        ("1.0.2-beta", "1.0.2", 0, "预发布后缀不影响数字比较"),
        ("", "1.0.0", -1, ""),
    ]
    bad = []
    for a, b, want, why in cases:
        got = U.compare_versions(a, b)
        if got != want:
            bad.append(f"{a} vs {b}: {got}≠{want}")
    check("compare_versions 全部正确", not bad, "; ".join(bad) or f"{len(cases)} 个用例")

    # ---------------------------------------------------------------- 2
    section("2. 三种版本关系")
    with mock_ok(release("v99.0.0")):
        i = U.check()
        check("有新版本 → has_update=True", i.has_update, i.summary)
        check("解析出 latest/url/published", (i.latest, bool(i.url), bool(i.published))
              == ("99.0.0", True, True), f"{i.latest} {i.url} {i.published}")
        check("summary 提到两个版本号", "99.0.0" in i.summary and i.current in i.summary,
              i.summary)

    with mock_ok(release(f"v{U.__version__}")):
        i = U.check()
        check("版本相同 → has_update=False", not i.has_update, i.summary)
        check("summary 说已是最新", "已是最新" in i.summary, i.summary)

    with mock_ok(release("v0.0.1")):
        i = U.check()
        check("本地更新 → has_update=False", not i.has_update, i.summary)
        check("summary 说明本地更新", "还新" in i.summary, i.summary)

    # ---------------------------------------------------------------- 3
    section("3. 失败路径（都不能抛异常）")
    with mock_http(404):
        i = U.check()
        check("404 → error 有值且 ok=False", bool(i.error) and not i.ok, i.error)
        check("404 提示没有发布过", "发布" in i.error, i.error)

    with mock_http(403):
        i = U.check()
        check("403 → 提示限流", "限流" in i.error, i.error)

    with mock_urlerr("boom"):
        i = U.check()
        check("连不上 → 提示连不上", "连不上" in i.error, i.error)

    with mock_raw(b"{ not json"):
        i = U.check()
        check("坏 JSON → error 有值", bool(i.error), i.error)

    with mock_raw(b'["not", "a", "dict"]'):
        i = U.check()
        check("返回不是对象 → error 有值", bool(i.error), i.error)

    with mock_ok({"html_url": "x"}):
        i = U.check()
        check("缺 tag_name → error 有值", bool(i.error) and not i.latest, i.error)

    for label, ctx in (("404", mock_http(404)), ("网络错误", mock_urlerr("x")),
                       ("坏内容", mock_raw(b"nope"))):
        with ctx:
            try:
                U.check()
                check(f"{label} 不抛异常", True)
            except Exception as e:                      # noqa: BLE001
                check(f"{label} 不抛异常", False, f"{type(e).__name__}: {e}")

    # ---------------------------------------------------------------- 4
    section("4. 真实网络（可选）")
    try:
        real = U.check(timeout=8)
    except Exception as e:                              # noqa: BLE001
        check("真实调用不抛异常", False, f"{type(e).__name__}: {e}")
        real = None
    if real is None:
        pass
    elif real.error:
        print(f"      连不上或出错，跳过：{real.error}")
    else:
        check("真实 API 拿到了版本号", bool(real.latest), f"latest={real.latest}")
        check("真实 latest 解析成了数字元组",
              len(U.parse_version(real.latest)) >= 2, str(U.parse_version(real.latest)))
        print(f"      当前 v{real.current}，GitHub 最新 v{real.latest}"
              + ("（有新版本）" if real.has_update else ""))

    print("\n" + "=" * 56)
    if FAILURES:
        print(f"\033[31m{len(FAILURES)}/{CHECKS} 项失败:\033[0m")
        for f in FAILURES:
            print("   -", f)
        return 1
    print(f"\033[32m全部 {CHECKS} 项检查通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
