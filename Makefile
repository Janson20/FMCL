.PHONY: help install build release clean test lint

help: ## 显示帮助信息
	@echo "可用命令:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## 安装依赖
	pip install -r requirements.txt
	npm install

build: ## 构建可执行文件
	pyinstaller build.spec --noconfirm

release: ## 创建新版本发布 (使用: make release VERSION=2.0.1)
	@python scripts/release.py $(VERSION)

clean: ## 清理构建文件
	rm -rf build/ dist/ *.spec
	rm -rf node_modules/
	find . -type d -name "__pycache__" -exec rm -rf {} +

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

docker-build: ## 构建Docker镜像
	docker build -t mcl:latest .

docker-run: ## 在Docker中运行
	docker run -it --rm -v $(PWD)/.minecraft:/app/.minecraft mcl:latest
