# Minecraft Launcher / Minecraft启动器

## Description / 描述

A feature-rich Minecraft launcher with multi-threaded download support, Forge installation, and GUI version selection.

一个功能丰富的Minecraft启动器，支持多线程下载、Forge安装和GUI版本选择。

## Features / 功能特点

- **Multi-threaded Downloading** - Faster downloads using parallel threads with progress tracking  
  **多线程下载** - 使用并行线程实现更快的下载速度,带进度跟踪
- **Forge Support** - Easy installation of Forge mod loader  
  **Forge支持** - 轻松安装Forge模组加载器
- **Version Management** - Browse and install different Minecraft versions with GUI  
  **版本管理** - 通过图形界面浏览和安装不同的Minecraft版本
- **Progress Tracking** - Real-time download progress with progress bar  
  **进度跟踪** - 带有进度条的实时下载进度显示
- **GUI Interface** - User-friendly version selection interface  
  **图形界面** - 用户友好的版本选择界面
- **Logging System** - Detailed operation logs for debugging  
  **日志系统** - 详细的操作日志记录,便于调试
- **Modular Architecture** - Clean, maintainable code structure  
  **模块化架构** - 清晰、可维护的代码结构
- **Configuration Management** - Centralized configuration system  
  **配置管理** - 集中的配置管理系统
- **Screenshot Tool** - Built-in screenshot functionality with hotkey support  
  **截图工具** - 内置截图功能,支持快捷键

## Requirements / 需求

- Python 3.7+  
- Required packages (see `pyproject.toml`)  
  所需包（见`pyproject.toml`）

## Installation / 安装

### 从 Release 下载（推荐）

前往 [Releases](https://github.com/Janson20/MCL/releases) 页面下载适合你平台的预编译版本：

| Platform | Architecture | Download |
|----------|--------------|----------|
| Windows | x64 | `MCL-win-amd64.zip` |
| macOS | Intel | `MCL-mac-amd64.zip` |
| macOS | Apple Silicon | `MCL-mac-arm64.zip` |
| Linux | x64 | `MCL-linux-amd64.zip` |
| Linux | ARM64 | `MCL-linux-arm64.zip` |

### 从源码安装

1. Clone this repository  
   克隆本仓库
   ```bash
   git clone https://github.com/Janson20/MCL.git
   cd MCL
   ```

2. Install dependencies using uv (recommended)  
   使用 uv 安装依赖(推荐)
   ```bash
   uv sync
   ```
   Or using pip  
   或使用 pip
   ```bash
   pip install -r requirements.txt
   ```

3. Run the launcher  
   运行启动器
   ```bash
   python main.py
   ```

### 使用 Docker

```bash
# 构建镜像
docker build -t mcl:latest .

# 运行容器
docker run -it --rm -v $(pwd)/.minecraft:/app/.minecraft mcl:latest
```

## Usage / 使用方法

1. The launcher will automatically check and create necessary directories  
   启动器会自动检查并创建必要的目录
2. Choose whether to install a new version  
   选择是否安装新版本
3. Select a version from the list  
   从列表中选择版本
4. Optionally install Forge  
   可选安装Forge
5. Launch the selected version  
   启动选定的版本

## File Structure / 文件结构

```
.
├── .minecraft/          # Minecraft game directory
│   └── versions/        # Installed versions
├── config.py            # Configuration management
├── downloader.py        # Multi-threaded downloader
├── launcher.py          # Launcher core logic
├── ui.py                # UI components
├── main.py              # Main program entry
├── screen_shot.py       # Screenshot tool
├── latest.log           # Log file
├── pos.txt              # Mouse position log (debug)
└── pyproject.toml       # Project configuration
```

## Notes / 注意事项

- First run will download the latest stable Minecraft version  
  首次运行会下载最新的稳定版Minecraft
- Forge installation requires manual execution of the downloaded installer  
  Forge安装需要手动执行下载的安装程序
- Logs are saved in `latest.log`  
  日志保存在`latest.log`中
- Screenshot tool can be activated with `Ctrl+Alt+T` (run `screen_shot.py` separately)  
  截图工具可通过 `Ctrl+Alt+T` 激活(需单独运行 `screen_shot.py`)

## Architecture / 架构说明

This project follows a modular architecture for better maintainability:

本项目采用模块化架构以提高可维护性:

- **config.py** - Configuration management and path handling  
  配置管理和路径处理
- **downloader.py** - Multi-threaded download functionality  
  多线程下载功能
- **launcher.py** - Core launcher logic and game management  
  核心启动器逻辑和游戏管理
- **ui.py** - User interface components and dialogs  
  用户界面组件和对话框
- **main.py** - Application entry point and initialization  
  应用程序入口点和初始化

## Development / 开发指南

### 环境设置

```bash
# 安装Python依赖
pip install -r requirements.txt

# 安装Node.js依赖（用于Git hooks）
npm install
npm run prepare
```

### 本地构建

```bash
# 使用PyInstaller构建
pyinstaller build.spec --noconfirm

# 或使用Makefile
make build
```

### 发布流程

项目使用 GitHub Actions 自动构建和发布：

1. 更新版本号（`pyproject.toml` 和 `package.json`）
2. 提交变更：`git commit -m "chore: release v2.0.1"`
3. 创建标签：`git tag v2.0.1`
4. 推送：`git push origin main --tags`

GitHub Actions 会自动：
- ✅ 构建 Windows/macOS/Linux 的 AMD64 和 ARM64 版本
- ✅ 根据约定式提交生成更新日志
- ✅ 创建 Release 并上传构建文件

详细文档：[SETUP.md](docs/SETUP.md)

### 提交规范

本项目使用 [约定式提交](https://www.conventionalcommits.org/) 规范：

```bash
feat: 添加新功能
fix: 修复bug
docs: 更新文档
refactor: 重构代码
perf: 性能优化
```

详见：[CONTRIBUTING.md](CONTRIBUTING.md)

## 故障排除

遇到问题？查看 [故障排除指南](docs/TROUBLESHOOTING.md)

常见问题：
- **构建失败**: 确保安装了所有系统依赖
- **macOS签名问题**: 使用 `xattr -cr MCL.app` 移除隔离属性
- **Windows杀毒误报**: 添加到排除列表或使用代码签名
- **依赖问题**: 重新安装依赖 `pip install -r requirements.txt --force-reinstall`

## Changelog / 更新日志

### v2.0.1
- 🐛 Fixed GitHub Actions build failures
- 🐛 Fixed Node.js 20 deprecation warnings
- 🐛 Fixed Linux build missing dependencies
- 🐛 Fixed macOS Intel/ARM architecture detection
- ✨ Added quick fix tool (`make fix`)
- ✨ Added comprehensive troubleshooting guide
- 📚 Improved build configuration and documentation

### v2.0
- 🎉 Complete refactoring with modular architecture
- 🐛 Fixed CPU 100% usage issue in screenshot tool
- 🐛 Fixed path concatenation issues
- 🐛 Fixed duplicate imports
- ✨ Added proper error handling and logging
- ✨ Added type hints for better code quality
- ✨ Improved configuration management
- ✨ Added multi-platform CI/CD pipeline
- ✨ Added automatic release workflow
- 📚 Updated documentation

### v1.4 and earlier
- Basic launcher functionality
- Forge support
- Mouse detection
- UI improvements

**构建修复详情**: 查看 [BUILD_FIXES.md](docs/BUILD_FIXES.md)

## License / 许可证

This project is licensed under the MIT License.  
本项目采用MIT许可证。