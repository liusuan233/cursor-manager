"""检查 GitHub 上有没有新版本。

三条设计原则，都是刻意的：

1. **只检查，不自动更新。** 这个工具会写用户的 ~/.config 和 ~/.local，
   自动下载替换出问题的代价太高。查出有新版本就告诉用户，怎么装由他决定。
2. **不在启动时联网。** 只有用户显式执行检查时才发请求 —— 一个光标工具
   没必要在每次开窗时暴露你的 IP 和启动时间。
3. **失败必须安静。** 没网、超时、被限流、仓库还没发布过……都不能让
   主功能受影响，返回带 error 的结果即可。

只用标准库（urllib），不引入新依赖。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import __version__

REPO = "liusuan233/cursor-manager"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DEFAULT_TIMEOUT = 6.0

# GitHub 要求带 User-Agent，否则直接 403
_UA = f"cursor-manager/{__version__} (+{RELEASES_PAGE})"


def parse_version(text: str) -> tuple[int, ...]:
    """把 'v1.0.2' / '1.0.2' / '1.0.2-beta' 解析成可比较的数字元组。

    不能用字符串比较 —— 那样 '1.0.10' 会小于 '1.0.9'。
    """
    nums = re.findall(r"\d+", (text or "").lstrip("vV"))
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def compare_versions(a: str, b: str) -> int:
    """a > b 返回 1，a < b 返回 -1，相等返回 0。"""
    ta, tb = parse_version(a), parse_version(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


@dataclass
class UpdateInfo:
    current: str = __version__
    latest: str = ""            # 形如 "1.0.2"（已去掉 v 前缀）
    url: str = ""               # release 页面
    published: str = ""
    notes: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.latest)

    @property
    def has_update(self) -> bool:
        return self.ok and compare_versions(self.latest, self.current) > 0

    @property
    def summary(self) -> str:
        if self.error:
            return f"检查更新失败：{self.error}"
        if not self.ok:
            return "检查更新失败：没拿到版本号"
        if self.has_update:
            return f"有新版本：v{self.current} → v{self.latest}"
        if compare_versions(self.latest, self.current) < 0:
            return f"当前 v{self.current} 比最新发布（v{self.latest}）还新"
        return f"已是最新版（v{self.current}）"


def check(timeout: float = DEFAULT_TIMEOUT) -> UpdateInfo:
    """查询 GitHub 最新 release。任何异常都收敛成 UpdateInfo.error，不往外抛。"""
    info = UpdateInfo()
    req = urllib.request.Request(
        API_LATEST,
        headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            info.error = "仓库还没有发布过任何 release"
        elif e.code == 403:
            info.error = "被 GitHub 限流了（未认证请求每小时 60 次），过一会儿再试"
        else:
            info.error = f"HTTP {e.code}"
        return info
    except urllib.error.URLError as e:
        info.error = f"连不上 GitHub（{getattr(e, 'reason', e)}）"
        return info
    except (TimeoutError, OSError) as e:
        info.error = f"网络错误：{e}"
        return info
    except (json.JSONDecodeError, ValueError) as e:
        info.error = f"返回内容无法解析：{e}"
        return info

    if not isinstance(data, dict):
        info.error = "返回内容格式不对"
        return info

    tag = str(data.get("tag_name") or "")
    info.latest = tag.lstrip("vV")
    info.url = str(data.get("html_url") or RELEASES_PAGE)
    info.published = str(data.get("published_at") or "")[:10]
    body = str(data.get("body") or "")
    info.notes = body[:2000]
    if not info.latest:
        info.error = "release 里没有 tag_name"
    return info
