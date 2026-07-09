# 贡献指南

感谢你考虑为 FMCL 贡献力量！

## 开发环境设置

### 前置要求

- Python 3.10+
- Node.js 18+（用于 Git hooks）
- Git

### 1. 克隆仓库

```bash
git clone https://github.com/Janson20/FMCL.git
cd FMCL
```

### 2. 安装依赖

```bash
# Python 依赖（推荐在虚拟环境中安装）
pip install -r requirements-dev.txt

# Node.js 依赖（用于 Git hooks）
npm install
npm run prepare

# 安装 pre-commit hooks（可选，但推荐）
pip install pre-commit
pre-commit install
```

### 3. 运行程序

```bash
python main.py
```

## 开发工作流

### 1. 创建分支

```bash
git checkout -b feat/my-feature
# 或
git checkout -b fix/my-bug-fix
```

### 2. 进行修改

请遵循以下代码规范：

- **代码风格**：使用 `black`（行宽 120）+ `isort` 自动格式化
- **类型注解**：尽可能为公共函数添加类型注解
- **国际化**：所有 UI 文本必须添加国际化支持
- **主题色**：支持主题色切换
- **错误处理**：处理所有边界情况和异常

### 3. 运行检查

在提交前运行以下命令确保代码质量：

```bash
# 代码检查
make lint

# 运行测试
make test

# 自动格式化代码
python -m black --line-length=120 .
python -m isort --profile=black --line-length=120 .
```

或者如果安装了 pre-commit hooks，提交时会自动执行代码检查。

### 4. 提交变更

本项目使用 [约定式提交](https://www.conventionalcommits.org/) 规范。

```bash
git commit -m "feat: 添加新功能"
git commit -m "fix(ui): 修复版本选择器显示错误"
git commit -m "docs: 更新安装文档"
git commit -m "refactor: 重构下载模块"
```

提交信息格式：
```
<type>(<scope>): <subject>

<body>

<footer>
```

**类型说明：**

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 Bug |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构代码 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `build` | 构建系统 |
| `ci` | CI 配置 |
| `chore` | 其他变动 |
| `revert` | 回滚提交 |

### 5. 推送并创建 PR

```bash
git push origin feat/my-feature
```

然后前往 GitHub 创建 Pull Request，确保：

- PR 标题清晰地描述变更内容
- 关联相关 Issue（如有）
- 已通过所有 CI 检查
- 已添加或更新相关测试
- 已更新相关文档

## 代码规范

### Python

- 目标 Python 版本：3.10+
- 行宽：120 字符
- 使用 `black` 作为代码格式化工具
- 使用 `isort` 管理导入顺序（profile: black）
- 使用 `flake8` 进行代码检查
- 使用 `mypy` 进行类型检查（`--ignore-missing-imports`）

### 项目结构

```
FMCL/
├── main.py                    # 主程序入口
├── config.py                  # 配置管理
├── launcher/                  # 启动器核心逻辑
├── minecraft_launcher_lib/    # Minecraft 启动库
├── plugin_manager/            # 插件系统
├── ui/                        # 用户界面
├── scripts/                   # 辅助脚本
├── tests/                     # 测试用例
├── docs/                      # 文档
└── .github/                   # GitHub 配置
    ├── workflows/             # CI/CD 工作流
    ├── ISSUE_TEMPLATE/        # Issue 模板
    └── PULL_REQUEST_TEMPLATE.md
```

## 测试

- 在 `tests/` 目录下添加测试文件
- 测试文件命名：`test_<module_name>.py`
- 测试函数命名：`test_<function_name>`
- 运行测试：`make test` 或 `python -m pytest tests/ -v`

## 文档

- 新功能必须更新 `README.md`
- 功能变更需更新 `./docs` 中的相关文档
- 必要时更新 `TERMS_OF_USE.md`

## 发布流程

版本发布由 CI 自动处理，推送 `v*.*.*` 格式的 tag 即可触发：

```bash
# 1. 更新版本号
# 修改 pyproject.toml 中的版本

# 2. 提交变更
git add .
git commit -m "chore: release v2.0.1"

# 3. 创建并推送 tag
git tag v2.0.1
git push origin main
git push origin v2.0.1
```

## 获取帮助

- 阅读 [README.md](README.md) 了解项目概况
- 阅读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 了解项目架构
- 阅读 [docs/SETUP.md](docs/SETUP.md) 了解开发环境设置
- 查看 [Issues](https://github.com/Janson20/FMCL/issues) 了解待办任务
- 在 [Discussions](https://github.com/Janson20/FMCL/discussions) 中提问
