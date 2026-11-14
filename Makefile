.PHONY: help install install-dev build build-qml build-installer build-qml-installer build-dmg build-deb build-appimage release clean test lint fix

VERSION ?= $(shell python -c "import re; print(re.search(r'version\s*=\s*[\"\']([^\"\']+)[\"\']', open('pyproject.toml').read()).group(1))" 2>/dev/null || echo "2.12.6")

help: ## 显示帮助信息
	@echo "可用命令:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## 安装依赖
	pip install -r requirements.txt
	npm install

install-dev: ## 安装开发依赖
	pip install -r requirements-dev.txt
	npm install

build: ## 构建 QML 目录产物 (PyInstaller，4.3 起唯一产物 dist/FMCL/)
	pyinstaller build.spec --noconfirm

build-qml: build ## 兼容旧命令：QML 目录产物

build-installer: build-qml-installer ## 构建 Windows 安装包 (需要 NSIS)

build-qml-installer: build ## 构建 QML 安装包 (目录产物; 需要 NSIS)
	makensis /INPUTCHARSET UTF8 /DVERSION=$(VERSION) installer.nsi

build-dmg: build ## 构建 macOS DMG (仅 macOS)
	@echo "Creating DMG for version $(VERSION)..."
	@mkdir -p dmg_temp
	@cp -R dist/FMCL.app dmg_temp/
	@ln -sf /Applications dmg_temp/Applications
	@hdiutil create -volname "FMCL" -srcfolder dmg_temp -ov -format UDZO "FMCL-$(VERSION)-mac.dmg"
	@rm -rf dmg_temp

build-deb: build ## 构建 Linux DEB 包
	@echo "Creating DEB package for version $(VERSION)..."
	@mkdir -p fmcl_$(VERSION)_amd64/DEBIAN
	@mkdir -p fmcl_$(VERSION)_amd64/usr/bin
	@mkdir -p fmcl_$(VERSION)_amd64/usr/lib/fmcl
	@mkdir -p fmcl_$(VERSION)_amd64/usr/share/applications
	@cp -R dist/FMCL/. fmcl_$(VERSION)_amd64/usr/lib/fmcl/
	@printf '#!/bin/sh\nexec /usr/lib/fmcl/FMCL "$$@"\n' > fmcl_$(VERSION)_amd64/usr/bin/fmcl
	@chmod +x fmcl_$(VERSION)_amd64/usr/bin/fmcl
	@printf '[Desktop Entry]\nName=FMCL\nComment=Fusion Minecraft Launcher\nExec=fmcl\nTerminal=false\nType=Application\nCategories=Game;\n' > fmcl_$(VERSION)_amd64/usr/share/applications/fmcl.desktop
	@printf 'Package: fmcl\nVersion: $(VERSION)\nArchitecture: amd64\nMaintainer: FMCL Team\nDescription: Fusion Minecraft Launcher\n A feature-rich Minecraft launcher.\n' > fmcl_$(VERSION)_amd64/DEBIAN/control
	@fakeroot dpkg-deb --build fmcl_$(VERSION)_amd64
	@mv fmcl_$(VERSION)_amd64.deb FMCL-$(VERSION)-linux-amd64.deb
	@rm -rf fmcl_$(VERSION)_amd64

build-appimage: build ## 构建 Linux AppImage
	@echo "Creating AppImage for version $(VERSION)..."
	@mkdir -p FMCL.AppDir/usr/bin FMCL.AppDir/usr/lib/fmcl
	@cp -R dist/FMCL/. FMCL.AppDir/usr/lib/fmcl/
	@printf '#!/bin/sh\nHERE=$$(dirname "$$(readlink -f "$$0")")\nexec "$${HERE}/../lib/fmcl/FMCL" "$$@"\n' > FMCL.AppDir/usr/bin/fmcl
	@chmod +x FMCL.AppDir/usr/bin/fmcl
	@printf '#!/bin/sh\nHERE=$$(dirname "$$(readlink -f "$$0")")\nexec "$${HERE}/usr/bin/fmcl" "$$@"\n' > FMCL.AppDir/AppRun
	@chmod +x FMCL.AppDir/AppRun
	@printf '[Desktop Entry]\nName=FMCL\nComment=Fusion Minecraft Launcher\nExec=fmcl\nTerminal=false\nType=Application\nCategories=Game;\n' > FMCL.AppDir/fmcl.desktop
	@appimagetool FMCL.AppDir FMCL-$(VERSION)-x86_64.AppImage
	@rm -rf FMCL.AppDir

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
	python -m flake8 . --exclude=.git,__pycache__,build,dist,.venv,venv,env,node_modules
	python -m mypy --ignore-missing-imports \
		main.py cli_agent.py agent_cli.py config.py downloader.py mirror.py \
		modrinth.py curseforge.py updater.py secure_storage.py \
		validation.py structured_logger.py version_utils.py \
		launcher/ minecraft_launcher_lib/ plugin_manager/ ui/ scripts/ tests/ \
		services/ app/
	-python -m mypy --ignore-missing-imports \
		curseforge.py

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
