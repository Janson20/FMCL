.PHONY: help install build build-installer build-dmg build-deb build-appimage release clean test lint fix

VERSION ?= $(shell python -c "import re; print(re.search(r'version\s*=s*[\"'']([^\"'']+)[\"'']', open('pyproject.toml').read()).group(1))" 2>/dev/null || echo "2.0.2")

help: ## 显示帮助信息
	@echo "可用命令:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## 安装依赖
	pip install -r requirements.txt
	npm install

install-dev: ## 安装开发依赖
	pip install -r requirements-dev.txt
	npm install

build: ## 构建可执行文件 (PyInstaller)
	pyinstaller build.spec --noconfirm

build-installer: build ## 构建 Windows 安装包 (需要 NSIS)
	makensis /DVERSION=$(VERSION) installer.nsi

build-dmg: build ## 构建 macOS DMG (仅 macOS)
	@echo "Creating DMG for version $(VERSION)..."
	@mkdir -p dmg_temp
	@cp -R dist/MCL.app dmg_temp/
	@ln -sf /Applications dmg_temp/Applications
	@hdiutil create -volname "MCL" -srcfolder dmg_temp -ov -format UDZO "MCL-$(VERSION)-mac.dmg"
	@rm -rf dmg_temp

build-deb: build ## 构建 Linux DEB 包
	@echo "Creating DEB package for version $(VERSION)..."
	@mkdir -p mcl_$(VERSION)_amd64/DEBIAN
	@mkdir -p mcl_$(VERSION)_amd64/usr/local/bin
	@mkdir -p mcl_$(VERSION)_amd64/usr/share/applications
	@cp dist/MCL mcl_$(VERSION)_amd64/usr/local/bin/mcl
	@chmod +x mcl_$(VERSION)_amd64/usr/local/bin/mcl
	@echo "[Desktop Entry]\nName=MCL\nComment=Minecraft Launcher\nExec=/usr/local/bin/mcl\nTerminal=false\nType=Application\nCategories=Game;" > mcl_$(VERSION)_amd64/usr/share/applications/mcl.desktop
	@echo "Package: mcl\nVersion: $(VERSION)\nArchitecture: amd64\nMaintainer: MCL Team\nDescription: Minecraft Launcher\n A feature-rich Minecraft launcher." > mcl_$(VERSION)_amd64/DEBIAN/control
	@fakeroot dpkg-deb --build mcl_$(VERSION)_amd64
	@mv mcl_$(VERSION)_amd64.deb MCL-$(VERSION)-linux-amd64.deb
	@rm -rf mcl_$(VERSION)_amd64

build-appimage: build ## 构建 Linux AppImage
	@echo "Creating AppImage for version $(VERSION)..."
	@mkdir -p MCL.AppDir/usr/bin
	@cp dist/MCL MCL.AppDir/usr/bin/mcl
	@chmod +x MCL.AppDir/usr/bin/mcl
	@echo '#!/bin/sh\nSELF=$$(readlink -f "$$0")\nHERE=$${SELF%/*}\nexec "$${HERE}/usr/bin/mcl" "$$@"' > MCL.AppDir/AppRun
	@chmod +x MCL.AppDir/AppRun
	@echo "[Desktop Entry]\nName=MCL\nComment=Minecraft Launcher\nExec=mcl\nTerminal=false\nType=Application\nCategories=Game;" > MCL.AppDir/mcl.desktop
	@appimagetool MCL.AppDir MCL-$(VERSION)-x86_64.AppImage
	@rm -rf MCL.AppDir

release: ## 创建新版本发布 (使用: make release VERSION=2.0.1)
	@python scripts/release.py $(VERSION)

clean: ## 清理构建文件
	rm -rf build/ dist/
	rm -rf node_modules/
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +

test: ## 运行测试
	python -m pytest tests/ -v

lint: ## 代码检查
	python -m flake8 *.py
	python -m mypy *.py

dev: ## 开发模式运行
	python main.py

run: ## 运行程序
	python main.py

setup-hooks: ## 设置Git hooks
	npm install
	npm run prepare

fix: ## 运行快速修复工具
	python scripts/fix_common_issues.py

check: ## 检查环境和依赖
	@echo "检查Python版本..."
	@python --version
	@echo "\n检查依赖..."
	@pip list | grep -E "minecraft-launcher-lib|forgepy|logzero|pyautogui"
	@echo "\n检查Java..."
	@java -version 2>&1 | head -n 1 || echo "Java未安装"

release-dry-run: ## 测试发布流程（不推送）
	@echo "测试发布流程..."
	@python scripts/release.py 9.9.9
	@echo "\n⚠️  这只是测试，不要推送tag v9.9.9"
	@git tag -d v9.9.9 2>/dev/null || true
	@git reset --hard HEAD~1
