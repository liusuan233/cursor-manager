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
           selftest_gui.py selftest_import.py xcursor_tool.py verify_cursor.c \
           _selftest_pack icons docs \
           packaging/PKGBUILD packaging/cursor-manager.desktop \
           pyproject.toml MANIFEST.in Makefile .gitignore \
           README.md DIAGNOSIS.md LICENSE

.PHONY: help check test dist sdist wheel install uninstall pip-install pkg verify clean

help:
	@echo "cursor-manager $(VERSION) —— 打包与安装"
	@echo ""
	@echo "  make dist        打源码 tarball（含全部脚本、图标、自检素材）"
	@echo "  make sdist       Python 源码包（pip 用）"
	@echo "  make wheel       Python wheel"
	@echo "  make pkg         Arch 包（dist + makepkg -si）"
	@echo "  make verify      把 wheel 装进临时 venv 并跑起来，验证导出可用"
	@echo ""
	@echo "  make install     用户级安装（~/.local，自动探测 PATH 配置）"
	@echo "  make uninstall   卸载上面的安装"
	@echo "  make pip-install pipx / pip 安装（任意发行版）"
	@echo ""
	@echo "  make test        两个自检（GUI 29 项 + 导入器 38 项）"
	@echo "  make check       只跑不需要显示服务器的那个自检"
	@echo "  make clean       清理构建产物"

# ---------------------------------------------------------------- 自检

test:
	@echo "== 导入器自检 =="
	@$(PY) selftest_import.py
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
	@test -f $(STAGE)/packaging/PKGBUILD || { echo "打包文件没进源码包，检查 SOURCES 的复制方式"; exit 1; }
	@tar -czf $(TARBALL) -C $(DISTDIR) $(NAME)-$(VERSION)
	@rm -rf $(STAGE)
	@echo ""
	@echo "→ $(TARBALL)  ($$(du -h $(TARBALL) | cut -f1), $$(tar -tzf $(TARBALL) | wc -l) 项)"
	@if tar -tzf $(TARBALL) | grep -qE '\.pkg\.tar|\.whl$$|egg-info|__pycache__|/src/cursor-manager'; then \
	    echo "  ✘ 源码包里混进了构建产物："; \
	    tar -tzf $(TARBALL) | grep -E '\.pkg\.tar|\.whl$$|egg-info|__pycache__|/src/cursor-manager' | head -5 | sed 's/^/    /'; \
	    exit 1; \
	else \
	    printf '  \033[32m✓ 内容干净（无构建产物）\033[0m\n'; \
	fi
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
	@cd packaging && makepkg -si

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

# ---------------------------------------------------------------- 清理

clean:
	@rm -rf $(DISTDIR) build *.egg-info
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@# makepkg 的工作目录和产物
	@rm -rf packaging/src packaging/pkg
	@rm -f packaging/$(NAME)-*.tar.gz packaging/$(NAME)-*.tar.xz \
	       packaging/$(NAME)-*.tar.bz2 packaging/*.pkg.tar.*
