# v1.0.2

修掉一个让**导入的主题直接不可用**的 bug，并加上「检查更新」。

## 修复：调色板光标丢失透明度，整张变成黑底

如果你导入过 8bpp / 24bpp 的调色板光标包，光标会渲染成带**黑色底方块**的图。

根因两条：

**1. Pillow 对 8bpp 调色板图像（`mode=P`）不应用 AND 掩码。**
8bpp 是调色板格式，**根本没有 alpha 通道** —— 透明度全靠 DIB 末尾的 AND 掩码。
Pillow 会把调色板直接展开成不透明 RGBA，于是整个光标变成实心方块。
而原来的解码顺序是「Pillow 优先」，所以**所有调色板光标包都会中招**。
（32bpp 带真 alpha 的包不受影响，纯属运气。）

现在自己实现 `decode_dib()`，支持 1/4/8/24/32bpp + 调色板 + AND 掩码，
解码顺序改为「自己优先，Pillow 只作后备」（后备用于 RLE 等冷门编码）。

**2. `IconEntry.bpp` 的语义是错的。**
CUR(type=2) 容器里 `wPlanes` / `wBitCount` 被**复用成热点坐标**，不是位深 ——
真位深得从 DIB 头读。

> 重写过程中我还引入了一个新 bug：32bpp 分支写成 `r, g, b, a = row[si], ...`，
> 但 DIB 里第一个字节是 **B** 不是 R，红蓝对调。是靠「与 Pillow 输出逐字节比对」
> 发现的（差异特征平均 20.267，和历史上那个通道 bug 一模一样）。
> 现在 32bpp 的 18 个帧与 Pillow 完全一致。

**已经导入过这类主题的，需要重新导入一次**才能修好：

```bash
cursor-manager --import <原来的包> --name <原来的名字> --overwrite
```

## 新增：检查更新

```bash
cursor-manager --check-update          # 命令行
```

图形界面左侧的 ≡ 菜单里也有「检查更新…」。

三条刻意的设计：

- **只检查，不自动更新。** 这个工具会写用户的 `~/.config` 和 `~/.local`，
  自动下载替换出问题的代价太高。查出有新版本就告诉你怎么装，装不装由你决定
- **不在启动时联网。** 只有显式执行检查时才发请求 —— 一个光标工具没必要
  在每次开窗时暴露你的 IP 和启动时间
- **失败必须安静。** 没网、超时、被限流、仓库还没发布过，都只返回一条提示，
  不影响任何本地功能

版本比较用的是数字元组，不是字符串 —— 否则 `1.0.10` 会被判成小于 `1.0.9`。

## 从 v1.0.1 升级

```bash
sudo pacman -U cursor-manager-1.0.2-1-any.pkg.tar.zst
```

## 下载哪个

| 文件 | 用途 |
|---|---|
| `cursor-manager-1.0.2-1-any.pkg.tar.zst` | **Arch Linux**：`sudo pacman -U <文件>` |
| `cursor-manager-1.0.2.tar.gz` | 通用源码包，解包即用，也能被 `pip install` |
| `cursor_manager-1.0.2-py3-none-any.whl` | Python wheel，配合 `pipx install` |
| `SHA256SUMS` | 上面三个文件的校验和 |

## 安装

**Arch Linux**

```bash
sudo pacman -U cursor-manager-1.0.2-1-any.pkg.tar.zst
```

**其他发行版（源码包，解包即用）**

```bash
tar -xzf cursor-manager-1.0.2.tar.gz
cd cursor-manager-1.0.2
./cursor-manager            # 直接跑
./install.sh                # 或装到 ~/.local（含桌面入口和图标）
```

**pipx**

```bash
pipx install cursor_manager-1.0.2-py3-none-any.whl
```

PyGObject 无法用 pip 可靠安装，需要走发行版包管理器
（它只影响图形界面，命令行功能都不需要它）。

## 校验下载

```bash
sha256sum -c SHA256SUMS
```

## 自检

```bash
make test        # 导入器 45 + 删除 24 + 检查更新 25 + GUI 42 项
```

四套自检都不需要人看图，退出码非 0 即失败。
`selftest_update.py` 全部用 mock 打桩，**离线也能确定性跑**。
这次新增的回归用例专门钉住黑底 bug：合成一个 8bpp + AND 掩码的 `.cur`，
断言「掩码标为透明的像素真的透明」且「**不是 100% 不透明**」。

## 已知限制

- **Arch 包是按当前 Python 版本编译的**（3.14）。Arch 换 Python 大版本后需等重新构建；
  源码包不受影响
- `apply` 覆盖 niri / DMS / GTK / XWayland / gsettings；KDE 的 `kcminputrc` 未处理
- 导入功能对 RLE 压缩的老式光标仍依赖 Pillow
