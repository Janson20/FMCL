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

- Python 3.8+
- Java (for running Minecraft)

## Installation / 安装

### 从 Release 下载（推荐）

前往 [Releases](https://github.com/Janson20/MCL/releases) 页面下载适合你平台的安装包：

| Platform | Download |
|----------|----------|
| Windows | `MCL-Setup-x.x.x.exe` (安装包) |
| macOS Intel | `MCL-x.x.x-mac-amd64.dmg` |
| macOS Apple Silicon | `MCL-x.x.x-mac-arm64.dmg` |
| Linux | `MCL-x.x.x-linux-amd64.deb` 或 `MCL-x.x.x-x86_64.AppImage` |

#### 安装说明

- **Windows**: 双击 `.exe` 安装包，按向导完成安装
- **macOS**: 双击 `.dmg` 文件，将 MCL.app 拖入 Applications 文件夹。首次打开若提示"无法验证开发者"，请在系统设置 > 安全性与隐私中点击"仍要打开"，或运行 `xattr -cr /Applications/MCL.app`
- **Linux DEB**: `sudo dpkg -i MCL-x.x.x-linux-amd64.deb`
- **Linux AppImage**: `chmod +x MCL-x.x.x-x86_64.AppImage && ./MCL-x.x.x-x86_64.AppImage`

### 从源码安装

1. 克隆仓库
   ```bash
   git clone https://github.com/Janson20/MCL.git
   cd MCL
   ```

2. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```

3. 运行启动器
   ```bash
   python main.py
   ```

## Usage / 使用方法

1. 启动器会自动检查并创建必要的目录
2. 选择是否安装新版本
3. 从列表中选择版本
4. 可选安装 Forge
5. 启动选定的版本

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
├── build.spec           # PyInstaller build config
├── installer.nsi        # Windows NSIS installer script
├── latest.log           # Log file
├── pos.txt              # Mouse position log (debug)
└── pyproject.toml       # Project configuration
```

## Development / 开发指南

### 环境设置

```bash
pip install -r requirements.txt
npm install
npm run prepare
```

### 本地构建

```bash
# 构建 PyInstaller 可执行文件
make build

# 构建系统安装包 (按平台选择)
make build-installer    # Windows (需要安装 NSIS)
make build-dmg          # macOS
make build-deb          # Linux DEB
make build-appimage     # Linux AppImage
```

### 发布流程

1. 更新版本号（`pyproject.toml` 和 `package.json`）
2. 提交变更：`git commit -m "chore: release v2.0.1"`
3. 创建标签：`git tag v2.0.1`
4. 推送：`git push origin main --tags`

GitHub Actions 会自动：
- Windows: 构建 `.exe` 安装包 (NSIS)
- macOS: 构建 `.dmg` 磁盘映像 (Intel + Apple Silicon)
- Linux: 构建 `.deb` 和 `.AppImage` 安装包
- 创建 Release 并上传所有安装包

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

### v2.0.2
- 🏗️ 重构构建流程：移除 Docker，改为构建系统原生安装包
- 🪟 Windows: 使用 NSIS 生成 `.exe` 安装包
- 🍎 macOS: 生成 `.dmg` 磁盘映像
- 🐧 Linux: 生成 `.deb` 和 `.AppImage` 安装包

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
