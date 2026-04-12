# MCL - Minecraft Launcher

一个功能丰富的 Minecraft 启动器，基于 CustomTkinter 现代化 UI，支持国内镜像加速、多模组加载器安装与版本管理。

---

## 功能特点

### 🎮 版本管理
- 浏览并安装所有 Minecraft 版本（正式版 + 测试版）
- 已安装版本列表，支持一键选择和启动
- 版本删除，释放磁盘空间
- 分页浏览可用版本列表（每页 20 个）

### ⚡ 国内镜像加速
- 内置 [BMCLAPI](https://bmclapi2.bangbang93.com/) 镜像源（by bangbang93），国内下载速度大幅提升
- 一键开关镜像源，切换即时生效
- 自动连接测试，状态栏显示连接结果
- 覆盖范围：版本清单、游戏资源、库文件、Forge/Fabric/NeoForge 安装器

### 🔧 多模组加载器
- **Forge** - 最广泛使用的模组加载器
- **Fabric** - 轻量级模组加载器
- **NeoForge** - Forge 的社区分支
- 安装模组加载器时自动安装原版 Minecraft，无需重复操作

### 🖥️ 现代化界面
- 基于 CustomTkinter 的深色主题，流畅美观
- 左右双栏布局：左侧已安装版本，右侧操作面板
- 异步操作：所有网络与安装任务在后台线程执行，UI 不卡顿
- 实时进度条与状态提示

### 📸 截图工具
- 内置区域截图工具，框选屏幕区域即可保存
- 快捷键 `Ctrl+Alt+T` 随时触发

### 📋 日志系统
- 基于 logzero 的完整日志记录
- 日志文件 `latest.log` 保留在程序目录，便于排查问题

---

## 界面预览

```
┌─────────────────────────────────────────────────────────────┐
│  ⛏ MCL   Minecraft Launcher           🇨🇳 国内镜像  🔄 刷新  │
├──────────────────────────────┬──────────────────────────────┤
│  📦 已安装版本        3 个版本 │  📥 安装新版本               │
│  ─────────────────────────── │  ──────────────────────      │
│  ┌────────────────────────┐  │  版本 ID:  [1.20.4      ]   │
│  │ 1.20.4            [X]  │  │  模组加载器: [无      ▼]     │
│  │ 1.20.4-forge-49.0 [X]  │  │  提示: 安装 Forge 会同时... │
│  │ fabric-loader-0.15 [X] │  │  [📥 安装版本]              │
│  │                        │  │                              │
│  │                        │  │  📋 快速选择                 │
│  │                        │  │  ──────────────────          │
│  │                        │  │  📦 正式版  🔬 测试版        │
│  │                        │  │  [📦 1.21.4] [📦 1.21.3]    │
│  │                        │  │  [📦 1.21.2] [📦 1.21.1]    │
│  │                        │  │  ◀  1/12  ▶                 │
│  └────────────────────────┘  │                              │
│  [🚀 启动游戏]               │                              │
├──────────────────────────────┴──────────────────────────────┤
│  ✅ 已安装 3 个 | 正式版 842 个 | 测试版 312 个    ████░░ 45% │
└─────────────────────────────────────────────────────────────┘
```

---

## 环境要求

| 依赖 | 说明 |
|------|------|
| Python 3.10+ | 运行启动器 |
| Java 8+ | 运行 Minecraft（启动器会自动检测系统 Java） |

---

## 安装

### 方式一：从 Release 下载（推荐）

前往 [Releases](https://github.com/Janson20/MCL/releases) 页面下载适合你平台的安装包：

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows | `MCL-Setup-x.x.x.exe` | NSIS 安装包，双击运行 |
| macOS Intel | `MCL-x.x.x-mac-amd64.dmg` | Intel 芯片 |
| macOS Apple Silicon | `MCL-x.x.x-mac-arm64.dmg` | M1/M2/M3 芯片 |
| Linux | `MCL-x.x.x-linux-amd64.deb` | Debian/Ubuntu 等发行版 |
| Linux | `MCL-x.x.x-x86_64.AppImage` | 通用 Linux 格式 |

#### 安装说明

- **Windows**: 双击 `.exe` 安装包，按向导完成安装
- **macOS**: 双击 `.dmg` 文件，将 MCL.app 拖入 Applications 文件夹。首次打开若提示"无法验证开发者"，请在系统设置 > 安全性与隐私中点击"仍要打开"，或运行：
  ```bash
  xattr -cr /Applications/MCL.app
  ```
- **Linux DEB**:
  ```bash
  sudo dpkg -i MCL-x.x.x-linux-amd64.deb
  ```
- **Linux AppImage**:
  ```bash
  chmod +x MCL-x.x.x-x86_64.AppImage
  ./MCL-x.x.x-x86_64.AppImage
  ```

### 方式二：从源码运行

```bash
# 克隆仓库
git clone https://github.com/Janson20/MCL.git
cd MCL

# 安装 Python 依赖
pip install -r requirements.txt

# 运行启动器
python main.py
```

> 💡 建议使用虚拟环境：`python -m venv .venv && source .venv/bin/activate`

---

## 使用方法

### 首次启动

1. 启动程序后自动检查运行环境
2. 首次运行会自动下载最新正式版 Minecraft
3. 下载完成后即可启动游戏

### 日常使用

#### 安装新版本

1. 在右侧面板「版本 ID」输入框中输入版本号（如 `1.20.4`）
2. 可选择模组加载器：
   - **无** - 仅安装原版 Minecraft
   - **Forge** - 安装 Forge 模组加载器（自动安装原版）
   - **Fabric** - 安装 Fabric 模组加载器（自动安装原版）
   - **NeoForge** - 安装 NeoForge 模组加载器（自动安装原版）
3. 点击「📥 安装版本」，等待进度条完成

> 💡 安装模组加载器时，原版 Minecraft 会自动安装，无需重复操作。安装完成后两者均可独立启动。

#### 快速选择版本

1. 在右侧「📋 快速选择」区域浏览可用版本
2. 点击「📦 正式版」/「🔬 测试版」切换版本类型
3. 点击版本条目自动填入版本 ID 输入框
4. 使用底部分页控件（◀ ▶）翻页浏览更多版本

#### 启动游戏

1. 在左侧「📦 已安装版本」面板中点击要启动的版本
2. 选中版本会高亮显示
3. 点击底部「🚀 启动游戏」按钮

#### 删除版本

- 点击已安装版本条目右侧的 `X` 按钮
- 确认删除后版本将被移除（不可恢复）

#### 镜像源切换

- 右上角「🇨🇳 国内镜像」开关控制是否使用 BMCLAPI 镜像
- 默认开启，国内用户建议保持开启
- 切换后自动测试连接，状态栏显示结果
- 镜像源设置会持久化到 `config.json`

#### 刷新版本列表

- 点击右上角「🔄 刷新」按钮
- 先加载已安装版本（本地，快速），再加载可用版本列表（需要网络）

---

## 项目结构

```
MCL/
├── main.py                # 主程序入口，日志配置、UI 创建、线程管理
├── launcher.py            # 启动器核心逻辑
│   ├── 环境检查与初始化
│   ├── 版本安装（原版 + 模组加载器）
│   ├── 版本删除
│   ├── 游戏启动（支持模糊匹配版本 ID）
│   └── 镜像源管理
├── ui.py                  # CustomTkinter 现代化 UI
│   ├── ModernApp          # 主窗口（双栏布局 + 状态栏）
│   ├── VersionSelectorDialog  # 版本选择弹出对话框
│   └── 辅助函数           # show_confirmation / show_alert
├── downloader.py          # 多线程下载器 & 模组加载器安装
│   ├── MultiThreadDownloader  # 多线程分段下载 + 文件合并
│   └── install_mod_loader # Forge/Fabric/NeoForge 统一安装
├── mirror.py              # BMCLAPI 国内镜像源模块
│   ├── MirrorSource       # 镜像源管理器
│   ├── URL 重写规则       # 官方 URL -> BMCLAPI 映射
│   └── Monkey Patch       # minecraft_launcher_lib 补丁
├── config.py              # 配置管理
│   └── Config             # 配置类（持久化到 config.json）
├── screen_shot.py         # 截图工具（Ctrl+Alt+T 触发）
├── config.json            # 用户配置（镜像源开关、下载线程数等）
├── requirements.txt       # Python 依赖
├── pyproject.toml         # 项目元数据（版本号等）
├── build.spec             # PyInstaller 构建配置
├── installer.nsi          # Windows NSIS 安装脚本
├── Makefile               # 构建/开发命令集合
├── package.json           # Node.js 开发工具配置
├── scripts/
│   ├── release.py         # 自动发布脚本
│   └── fix_common_issues.py  # 常见问题修复工具
└── docs/
    ├── SETUP.md           # 构建设置详细文档
    ├── BUILD_FIXES.md     # 构建问题修复记录
    └── TROUBLESHOOTING.md # 故障排除指南
```

### 模块依赖关系

```
main.py
  ├── config.py (全局配置)
  ├── launcher.py (核心逻辑)
  │   ├── mirror.py (镜像源)
  │   └── downloader.py (下载器 & 模组加载器)
  └── ui.py (界面)
      └── launcher.get_callbacks() (通过回调与核心逻辑交互)
```

---

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| UI 框架 | CustomTkinter | 现代 Tkinter 封装，深色主题 |
| Minecraft 库 | minecraft-launcher-lib | 版本安装、启动命令生成 |
| 镜像源 | BMCLAPI (bangbang93) | 国内加速下载 |
| 日志 | logzero | 轻量级日志框架 |
| HTTP | requests | API 请求与文件下载 |
| 下载 | 多线程分段下载 | 大文件并行下载加速 |
| 截图 | pyautogui + keyboard | 区域截图 + 快捷键监听 |
| 构建打包 | PyInstaller + NSIS | 可执行文件 + Windows 安装包 |
| CI/CD | GitHub Actions | 多平台自动构建与发布 |
| 提交规范 | Husky + Commitlint | 约定式提交自动校验 |

---

## 配置说明

配置文件 `config.json` 位于程序根目录，启动时自动加载：

```json
{
  "mirror_enabled": true,
  "download_threads": 4
}
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `mirror_enabled` | bool | `true` | 是否启用 BMCLAPI 国内镜像 |
| `download_threads` | int | `4` | 多线程下载的线程数 |

---

## 开发指南

### 环境设置

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装开发依赖（可选）
pip install -r requirements-dev.txt

# 安装 Git hooks (Husky + Commitlint)
npm install
npm run prepare
```

### 常用命令

```bash
make dev              # 开发模式运行
make run              # 运行程序
make check            # 检查环境和依赖
make lint             # 代码检查 (flake8 + mypy)
make fix              # 运行常见问题修复工具
make clean            # 清理构建文件
```

### 构建

```bash
make build            # PyInstaller 构建可执行文件
make build-installer  # Windows NSIS 安装包 (需要 NSIS)
make build-dmg        # macOS DMG 磁盘映像 (仅 macOS)
make build-deb        # Linux DEB 包
make build-appimage   # Linux AppImage
```

### 发布流程

1. 更新 `pyproject.toml` 和 `package.json` 中的版本号
2. 提交变更：`git commit -m "chore: release vX.X.X"`
3. 创建标签：`git tag vX.X.X`
4. 推送：`git push origin main --tags`

GitHub Actions 会自动：
- Windows: 构建 `.exe` 安装包 (NSIS)
- macOS: 构建 `.dmg` 磁盘映像 (Intel + Apple Silicon)
- Linux: 构建 `.deb` 和 `.AppImage` 安装包
- 创建 Release 并上传所有安装包

详见：[CONTRIBUTING.md](CONTRIBUTING.md) | [SETUP.md](docs/SETUP.md)

### 提交规范

本项目使用 [约定式提交](https://www.conventionalcommits.org/)：

```bash
feat: 添加新功能
fix: 修复 bug
docs: 更新文档
refactor: 重构代码
perf: 性能优化
chore: 构建/工具变动
```

详见：[CONTRIBUTING.md](CONTRIBUTING.md)

---

## 故障排除

查看完整指南：[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

| 问题 | 解决方案 |
|------|----------|
| 镜像源连接失败 | 尝试关闭「国内镜像」开关使用 Mojang 官方源 |
| 版本安装失败 | 检查网络连接，查看 `latest.log` 日志 |
| 游戏启动失败 | 确保已安装 Java 运行时 |
| macOS 提示无法验证开发者 | `xattr -cr MCL.app` 移除隔离属性 |
| Windows 杀毒误报 | 添加到杀毒软件排除列表 |
| 依赖安装失败 | `pip install -r requirements.txt --force-reinstall` |

---

## 许可证

[MIT License](LICENSE) - Copyright (c) 2026 MCL Team
