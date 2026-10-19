NAME    := cursor-manager
VERSION := $(shell python3 -c "import cursorkit; print(cursorkit.__version__)")
PY      ?= python3
DISTDIR := dist
STAGE   := $(DISTDIR)/$(NAME)-$(VERSION)
TARBALL := $(DISTDIR)/$(NAME)-$(VERSION).tar.gz

# 要打进源码包的东西（显式列出，避免把构建产物带进去）
# 注意 packaging/ 只列两个真源文件：makepkg 会在 packaging/ 下生成
# src/ 和 pkg/ 工作目录，整个目录塞进去会滚雪球（源码包里套源码包）。
SOURCES := cursorkit \
           cursor-manager set-cursor.sh install.sh install-kinomoto-cursor.sh \
           selftest_gui.py selftest_import.py selftest_delete.py selftest_update.py \
           xcursor_tool.py verify_cursor.c \
           _selftest_pack icons docs tools \
           packaging/PKGBUILD packaging/cursor-manager.desktop \
           pyproject.toml MANIFEST.in Makefile .gitignore \
           README.md DIAGNOSIS.md LICENSE

.PHONY: help check test dist sdist wheel install uninstall pip-install \
        pkg pkg-install verify release _release_verify demo icon icon-preview clean

help:
	@echo "cursor-manager $(VERSION) —— 打包与安装"
	@echo ""
	@echo "  make dist        打源码 tarball（含全部脚本、图标、自检素材）"
	@echo "  make sdist       Python 源码包（pip 用）"
	@echo "  make wheel       Python wheel"
	@echo "  make pkg         Arch 包（只构建）"
	@echo "  make pkg-install Arch 包并安装（sudo pacman -U）"
	@echo "  make verify      把 wheel 装进临时 venv 并跑起来，验证导出可用"
	@echo ""
	@echo "  make icon        重新生成应用图标（6 个尺寸档）"
	@echo "  make icon-preview 只出预览图（含 32px 实际大小的像素对照）"
	@echo "  make demo        自动生成演示视频（真跑命令 + 真解码光标，不需要录屏）"
	@echo "  make demo-list   只列出演示视频的场景"
	@echo ""
	@echo "  make release     打完整发布包并逐项实测（源码包 + wheel + Arch 包 + SHA256SUMS）"
	@echo ""
	@echo "  make install     用户级安装（~/.local，自动探测 PATH 配置）"
	@echo "  make uninstall   卸载上面的安装"
	@echo "  make pip-install pipx / pip 安装（任意发行版）"
	@echo ""
	@echo "  make test        三个自检（导入器 38 + 删除 24 + GUI 31 项）"
	@echo "  make check       只跑不需要显示服务器的那个自检"
	@echo "  make clean       清理构建产物"

# ---------------------------------------------------------------- 自检

test:
	@echo "== 导入器自检 =="
	@$(PY) selftest_import.py
	@echo ""
	@echo "== 删除功能自检 =="
	@$(PY) selftest_delete.py
	@echo ""
	@echo "== 检查更新自检 =="
	@$(PY) selftest_update.py
	@echo ""
	@echo "== GUI 自检 =="
	@$(PY) selftest_gui.py

check:
	@$(PY) selftest_import.py

# ---------------------------------------------------------------- 源码包

dist: clean
	@mkdir -p $(STAGE)
	@# --parents：保留 packaging/ 这样的子目录结构（cp 多文件时不带 --parents 会拍平）
	@cp -a --parents $(SOURCES) $(STAGE)/
	@# 双保险：万一还有残留的构建产物，别让它进包
	@rm -rf $(STAGE)/packaging/src $(STAGE)/packaging/pkg
	@rm -f $(STAGE)/packaging/*.pkg.tar.* $(STAGE)/packaging/*.tar.gz \
	       $(STAGE)/packaging/*.tar.xz $(STAGE)/packaging/*.tar.bz2
	@find $(STAGE) -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find $(STAGE) -name '*.pyc' -delete 2>/dev/null || true
	@find $(STAGE) -name '*.bak.*' -delete 2>/dev/null || true
	@find $(STAGE) -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@# SOURCES 是按目录整体拷的（cp -a --parents），会把 .gitignore 里的调试
	@# 产物一并带进来 —— icons/_preview.png 就这样混进过 1.0.3 的源码包。
	@# 这里把所有 git 不跟踪的文件删掉，让 sdist 严格等于 git 的内容。
	@git ls-files | sort > $(DISTDIR)/.keep
	@cd $(STAGE) && find . -type f | sed 's|^\./||' | sort > ../../$(DISTDIR)/.have
	@comm -13 $(DISTDIR)/.keep $(DISTDIR)/.have > $(DISTDIR)/.extra || true
	@if [ -s $(DISTDIR)/.extra ]; then \
	    echo "  移除 git 未跟踪的文件："; sed 's/^/    - /' $(DISTDIR)/.extra; \
	    while IFS= read -r f; do rm -f "$(STAGE)/$$f"; done < $(DISTDIR)/.extra; \
	fi
	@rm -f $(DISTDIR)/.keep $(DISTDIR)/.have $(DISTDIR)/.extra
	@test -f $(STAGE)/packaging/PKGBUILD || { echo "打包文件没进源码包，检查 SOURCES 的复制方式"; exit 1; }
	@tar -czf $(TARBALL) -C $(DISTDIR) $(NAME)-$(VERSION)
	@rm -rf $(STAGE)
	@echo ""
	@echo "→ $(TARBALL)  ($$(du -h $(TARBALL) | cut -f1), $$(tar -tzf $(TARBALL) | wc -l) 项)"
	@if tar -tzf $(TARBALL) | grep -qE '\.pkg\.tar|\.whl$$|egg-info|__pycache__|/src/cursor-manager'; then \
	    echo "  ✘ 源码包里混进了构建产物："; \
	    tar -tzf $(TARBALL) | grep -E '\.pkg\.tar|\.whl$$|egg-info|__pycache__|/src/cursor-manager' | head -5 | sed 's/^/    /'; \
	    exit 1; \
	fi
	@# 源码包内容必须与 git 跟踪的文件**完全一致**，否则 sdist 无法从仓库复现。
	@# 之前 icons/_preview.png（被 gitignore 的调试图）就这样混进过包里，
	@# 而且已经发出去了才在"下载回来逐文件比对"时发现。
	@tar -tzf $(TARBALL) | grep -v '/$$' | sed 's|^$(NAME)-$(VERSION)/||' | sort > $(DISTDIR)/.sdist.list
	@git ls-files | sort > $(DISTDIR)/.git.list
	@if diff -q $(DISTDIR)/.git.list $(DISTDIR)/.sdist.list >/dev/null; then \
	    printf '  \033[32m✓ 内容与 git 跟踪的文件完全一致（%s 个）\033[0m\n' "$$(wc -l < $(DISTDIR)/.git.list)"; \
	else \
	    echo "  ✘ 源码包与 git 跟踪的文件不一致（< 只在 git 里 / > 只在包里）："; \
	    diff $(DISTDIR)/.git.list $(DISTDIR)/.sdist.list | grep -E '^[<>]' | head -10 | sed 's/^/    /'; \
	    rm -f $(DISTDIR)/.git.list $(DISTDIR)/.sdist.list; exit 1; \
	fi
	@rm -f $(DISTDIR)/.git.list $(DISTDIR)/.sdist.list
	@echo "  解包后直接可用:  tar -xzf $(notdir $(TARBALL)) && cd $(NAME)-$(VERSION) && ./cursor-manager"

sdist:
	@mkdir -p $(DISTDIR)
	@$(PY) -c "import setuptools.build_meta as b; print('→', b.build_sdist('$(DISTDIR)'))"

wheel:
	@mkdir -p $(DISTDIR)
	@$(PY) -c "import setuptools.build_meta as b; print('→', b.build_wheel('$(DISTDIR)'))"

# ---------------------------------------------------------------- 安装

install:
	@./install.sh

uninstall:
	@./install.sh --uninstall

pip-install:
	@if command -v pipx >/dev/null 2>&1; then \
	    pipx install --force . ; \
	else \
	    echo "没装 pipx，用 pip --user 安装到 ~/.local"; \
	    $(PY) -m pip install --user --upgrade . ; \
	fi

pkg: dist
	@cp $(TARBALL) packaging/
	@cd packaging && makepkg -f --nodeps --nocheck
	@echo "→ packaging/$$(cd packaging && ls *.pkg.tar.zst | head -1)"

# 构建并安装（会调 sudo pacman -U）
pkg-install: pkg
	@sudo pacman -U --noconfirm packaging/*.pkg.tar.zst

# 真装一遍，证明"导出"出来的东西是可用的
# 注意 wheel 文件名里项目名的 '-' 会被规范化成 '_'（PEP 427）
verify: wheel
	@rm -rf $(DISTDIR)/venv
	@$(PY) -m venv $(DISTDIR)/venv
	@$(DISTDIR)/venv/bin/pip install --quiet --no-deps \
	    $(DISTDIR)/cursor_manager-$(VERSION)-*.whl $(DISTDIR)/cursor-manager-$(VERSION)-*.whl 2>/dev/null \
	    || $(DISTDIR)/venv/bin/pip install --quiet --no-deps $$(ls $(DISTDIR)/*.whl | head -1)
	@echo "== 安装到临时 venv 后运行 =="
	@$(DISTDIR)/venv/bin/cursor-manager --version
	@$(DISTDIR)/venv/bin/cursor-manager --list
	@echo ""
	@echo "== 这个 venv 里没有 numpy/pillow，确认纯 Python 回退也能跑 =="
	@$(DISTDIR)/venv/bin/python -c "$$(printf '%s\n' \
	    'import sys' \
	    'try:' \
	    '    import numpy; print("  意外: venv 里有 numpy")' \
	    'except ImportError:' \
	    '    print("  numpy 缺席 -> 走纯 Python 解码回退 OK")' \
	    'from cursorkit.importer import ROLES' \
	    'from cursorkit.xcursor import _np' \
	    'print(f"  角色表 {len(ROLES)} 项；numpy 加速={_np is not None}")')"
	@rm -rf $(DISTDIR)/venv
	@printf '\033[32m✔ 导出验证通过\033[0m\n'

# ---------------------------------------------------------------- 发布

RELDIR   := $(DISTDIR)/release
SHAFILE  := $(RELDIR)/SHA256SUMS
TAG      := v$(VERSION)

# 打一个完整的发布包：源码包 + wheel + Arch 包 + 校验和，并逐项实测
release:
	@# 1. 版本号必须三处一致，否则发出去的包对不上号
	@test "$$(grep -oP '^pkgver=\K.*' packaging/PKGBUILD)" = "$(VERSION)" || { \
	    printf '\033[31m✘ PKGBUILD 的 pkgver 与 cursorkit.__version__ (%s) 不一致\033[0m\n' "$(VERSION)"; exit 1; }
	@# 2. 工作区必须干净 —— 保证产物对应某个确定的提交
	@test -z "$$(git status --porcelain 2>/dev/null)" || { \
	    printf '\033[31m✘ 工作区不干净，先提交再发版:\033[0m\n'; git status --short; exit 1; }
	@$(MAKE) --no-print-directory clean >/dev/null
	@printf '\033[36m==>\033[0m 构建源码包 / wheel / Arch 包\n'
	@# 注意：dist 目标内部会跑 clean，所以 $(RELDIR) 必须等这些都跑完再建
	@$(MAKE) --no-print-directory dist >/dev/null 2>&1
	@$(MAKE) --no-print-directory wheel >/dev/null 2>&1
	@cp $(TARBALL) packaging/ && cd packaging && makepkg -f --nodeps --nocheck >/dev/null 2>&1
	@mkdir -p $(RELDIR)
	@cp $(TARBALL) $(RELDIR)/
	@cp $(DISTDIR)/cursor_manager-$(VERSION)-*.whl $(RELDIR)/
	@cp packaging/*.pkg.tar.zst $(RELDIR)/
	@rm -f packaging/$(NAME)-$(VERSION).tar.gz
	@ls -1 $(RELDIR) | sed 's/^/    /'
	@echo ""
	@printf '\033[36m==>\033[0m 生成校验和\n'
	@cd $(RELDIR) && sha256sum * > SHA256SUMS && cat SHA256SUMS | sed 's/^/    /'
	@echo ""
	@printf '\033[36m==>\033[0m 实测每个产物\n'
	@$(MAKE) --no-print-directory _release_verify
	@echo ""
	@printf '\033[32m✔ 发布包就绪: %s\033[0m\n' "$(RELDIR)"
	@echo ""
	@echo "下一步（推 tag 并上传）:"
	@echo "    git tag -a $(TAG) -m '$(TAG)'"
	@echo "    git push origin $(TAG)"
	@echo "    gh release create $(TAG) --title '$(TAG)' --notes-file docs/RELEASE-$(TAG).md $(RELDIR)/*"

_release_verify:
	@rm -rf $(DISTDIR)/rv && mkdir -p $(DISTDIR)/rv
	@# a) 源码包解包即用
	@tar -xzf $(RELDIR)/$(NAME)-$(VERSION).tar.gz -C $(DISTDIR)/rv
	@$(DISTDIR)/rv/$(NAME)-$(VERSION)/cursor-manager --version | sed 's/^/  ✓ 源码包解包: /'
	@# b) 源码包能被 pip 直接装（pip 要求 tar 内是单一顶层目录）
	@$(PY) -m venv $(DISTDIR)/rv/venv-src
	@$(DISTDIR)/rv/venv-src/bin/pip install --quiet --no-deps $(RELDIR)/$(NAME)-$(VERSION).tar.gz
	@$(DISTDIR)/rv/venv-src/bin/cursor-manager --version | sed 's/^/  ✓ 源码包 pip 安装: /'
	@# c) wheel 能装能跑
	@$(PY) -m venv $(DISTDIR)/rv/venv-whl
	@$(DISTDIR)/rv/venv-whl/bin/pip install --quiet --no-deps $(RELDIR)/cursor_manager-$(VERSION)-*.whl
	@$(DISTDIR)/rv/venv-whl/bin/cursor-manager --version | sed 's/^/  ✓ wheel 安装: /'
	@# d) Arch 包结构完整
	@mkdir -p $(DISTDIR)/rv/arch
	@bsdtar -xf $(RELDIR)/$(NAME)-$(VERSION)-1-any.pkg.tar.zst -C $(DISTDIR)/rv/arch
	@test -f "$(DISTDIR)/rv/arch/usr/bin/cursor-manager" \
	    && echo "  ✓ Arch 包: /usr/bin/cursor-manager" \
	    || { echo "  \033[31m✘ Arch 包缺少主程序\033[0m"; exit 1; }
	@test -f "$(DISTDIR)/rv/arch/usr/share/applications/cursor-manager.desktop" \
	    && echo "  ✓ Arch 包: 桌面入口"
	@test -f "$(DISTDIR)/rv/arch/usr/share/licenses/cursor-manager/LICENSE" \
	    && echo "  ✓ Arch 包: 许可证文件"
	@# e) 校验和自检
	@cd $(RELDIR) && sha256sum -c SHA256SUMS >/dev/null && echo "  ✓ SHA256SUMS 全部匹配"
	@rm -rf $(DISTDIR)/rv
# ---------------------------------------------------------------- 演示视频

icon:
	@$(PY) tools/make-icon.py

icon-preview:
	@$(PY) tools/make-icon.py --preview

demo:
	@$(PY) tools/make-demo.py

demo-list:
	@$(PY) tools/make-demo.py --list

# ---------------------------------------------------------------- 清理

clean:
	@rm -rf $(DISTDIR) build *.egg-info
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@# makepkg 的工作目录和产物
	@rm -rf packaging/src packaging/pkg
	@rm -f demo/*.mp4 demo/*.gif
	@rm -f packaging/$(NAME)-*.tar.gz packaging/$(NAME)-*.tar.xz \
	       packaging/$(NAME)-*.tar.bz2 packaging/*.pkg.tar.*
