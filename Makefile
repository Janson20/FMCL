.PHONY: help install build release clean test lint fix

help: ## 显示帮助信息
	@echo "可用命令:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## 安装依赖
	pip install -r requirements.txt
	npm install

install-dev: ## 安装开发依赖
	pip install -r requirements-dev.txt
	npm install

build: ## 构建可执行文件
	pyinstaller build.spec --noconfirm

release: ## 创建新版本发布 (使用: make release VERSION=2.0.1)
	@python scripts/release.py $(VERSION)

clean: ## 清理构建文件
	rm -rf build/ dist/ *.spec
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

docker-build: ## 构建Docker镜像
	docker build -t mcl:latest .

docker-run: ## 在Docker中运行
	docker run -it --rm -v $(PWD)/.minecraft:/app/.minecraft mcl:latest

release-dry-run: ## 测试发布流程（不推送）
	@echo "测试发布流程..."
	@python scripts/release.py 9.9.9
	@echo "\n⚠️  这只是测试，不要推送tag v9.9.9"
	@git tag -d v9.9.9 2>/dev/null || true
	@git reset --hard HEAD~1
