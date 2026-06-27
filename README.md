# FMCL - Fusion Minecraft Launcher

一个功能丰富的 Minecraft 启动器，基于 CustomTkinter 现代化 UI，支持国内镜像加速、多模组加载器安装与版本管理。

---

## 功能特点

| 分类 | 简介 | 详情 |
|------|------|------|
| 🎮 版本管理 | 浏览/安装/删除 Minecraft 版本，支持正式版与测试版 | 每页 20 个，支持一键选择、快捷操作 |
| 🔧 多模组加载器 | Forge / Fabric / NeoForge / Quilt 一站式安装，版本隔离 | 与原版并行安装，自动适配 YY.D.H 新版本格式 |
| ⚡ 国内镜像加速 | 内置 BMCLAPI 镜像源，国内下载速度大幅提升 | 一键开关，覆盖版本清单/资源/库/安装器 |
| 🧩 模组管理 | Modrinth + CurseForge 在线搜索安装，AI 搜索 | 自动匹配版本和加载器，支持依赖递归安装 |
| 📦 整合包 | 支持 .mrpack 安装与开服，Modrinth 在线下载 | 并行安装优化，分段进度显示 |
| 🖥 服务器管理 | 一键安装/启动 MC 服务器，实时日志与命令交互 | 自动同意 EULA，智能 Java 管理，1G~16G 内存 |
| 🌐 陶瓦联机 | 基于 EasyTier P2P 虚拟组网，局域网广播模拟 | Base34 大厅编号，TCP 端口转发，成员管理 |
| 💾 存档备份 | 手动/自动备份，一键恢复，压缩/校验/导出 | 支持版本隔离目录扫描，备份索引记录 |
| 🤖 AGENT 助手 | 自然语言控制启动器，33 个工具可供 AI 调用 | 多模型支持，流式 SSE 输出，50 轮工具循环 |
| 🎵 音乐播放器 | 本地文件夹播放，全局快捷键，Windows SMTC | 支持 MP3/FLAC/OGG/WAV 等，四种播放模式，迷你窗口 |
| ⏱ 性能监控 | Ctrl+Shift+M 悬浮窗，CPU/内存/GPU 实时监控 | 半透明置顶窗口，拖拽移动，GPU 温度显存显示 |
| 🎨 动态主题 | 5 种预设 + 自定义 Hex + 版本动态颜色 | 支持导入 .json 主题，主题持久化保存 |
| 🏆 成就系统 | 47 项成就，9 大分类，Toast 通知，云存档同步 | 多阶段成就，进度条可视化，每日签到 |
| 💥 崩溃分析 | 智能诊断 + 净读 AI 深度分析 | 识别 11 种崩溃类型，支持导出报告和 AI 修复建议 |
| 🔒 安全 | SSL 验证、Fernet 加密、输入防注入、SHA 校验 | 密钥文件可迁移，断点续传，原子写入，安装回滚 |
| 🌐 多语言 | 简体中文 / English / 繁體中文 / 日本語 | 自动检测系统语言，设置内一键切换即时生效 |
| ⬆ 自动更新 | GitHub Release 检测 + 静默安装 | 自动识别平台下载对应安装包，可配置开关 |

> 完整功能列表详见 [docs/FEATURES.md](docs/FEATURES.md)

---

## 界面预览

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ⛏ FMCL   Minecraft Launcher   ⬆ 更新  🔄 刷新  ⚙ 设置  │
│  [🎮 游戏] [💾 备份] [🖥 开服] [🔗 链接] [🌐 联机] [🎵 音乐] [🤖 AGENT] [🏆 成就] [📜]│
├────────────┬──────────────────────────────┬──────────────────────────────────────┤
│ 👤 账号  │  📦 已安装版本  ⚙  3 个版本 │  📥 安装新版本               │
│ ⭐ Steve │  ─────────────────────────── │  ──────────────────────      │
│            │  │ 1.20.4          🧩⚙[X] │  │  版本 ID:  [1.20.4      ]   │
│ 🎨 皮肤   │  │ 1.20.4-forge-49🧩⚙[X] │  │  模组加载器: [无      ▼]     │
│ ✅ skin.png│  │ fabric-loader-0🧩⚙[X] │  │  提示: 安装 Forge 会同时... │
│ [选择][🗑] │  │                        │  │  [📥 安装版本]              │
│            │  │                        │  │                              │
│ 📋 日志   │  │                        │  │  📋 快速选择                 │
│ [14:30:01] │  │                        │  │  ──────────────────          │
│ 环境初始化 │  │                        │  │  📦 正式版  🔬 测试版        │
│ 完成       │  │                        │  │  [📦 1.21.4] [📦 1.21.3]    │
│            │  └────────────────────────┘  │  [📦 1.21.2] [📦 1.21.1]    │
│            │  [🚀 启动游戏] [⏹]           │  ◀  1/12  ▶                 │
│            │                              │                              │
│ [清空日志] │                              │                              │
├────────────┴──────────────────────────────┴──────────────────────────────┤
│  ✅ 已安装 3 个 | 正式版 842 个 | 测试版 312 个    ████░░ 45% │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 环境要求

| 依赖 | 说明 |
|------|------|
| Python 3.10+ | 运行启动器 |
| Java 8+ | 运行 Minecraft（启动器自动扫描系统 Java，推荐版本由 MC 版本决定） |

---

## 安装

### 方式一：从 Release 下载（推荐）

前往 [Releases](https://github.com/Janson20/FMCL/releases) 页面下载适合你平台的安装包：

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows | `FMCL-Setup-x.x.x.exe` | NSIS 安装包，双击运行 |

> **注意**：自 **v2.10.3** 起，不再通过 GitHub Actions 构建并发布 macOS（`.dmg`）与 Linux（`.deb` / `.AppImage`）二进制包。Linux 用户请使用下方「方式二：Linux 一键安装」脚本进行安装；macOS 用户请参考「方式三：从源码运行」。

#### 安装说明

- **Windows**: 双击 `.exe` 安装包，按向导完成安装。安装包已内置 7-Zip（24.09 版本），无需联网即可自动安装
- **Linux**: 请使用下方「方式二：Linux 一键安装」脚本
- **macOS**: 请参考下方「方式三：从源码运行」

### 方式二：Linux 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/Janson20/FMCL/main/scripts/install.sh | bash
```

脚本自动完成：安装系统依赖 → 安装 uv → 克隆项目 → uv sync → 注册 `fmcl` 命令。

支持 Debian / Ubuntu / Fedora / RHEL / CentOS / Arch / openSUSE，安装完成后直接运行 `fmcl` 即可启动。
自定义安装目录：`./scripts/install.sh ~/.local/share/fmcl`

### 方式三：从源码运行

```bash
# 克隆仓库
git clone https://github.com/Janson20/FMCL.git
cd FMCL

# 安装 Python 依赖（Windows）
pip install -r requirements-windows.txt

# 安装 Python 依赖（macOS / Linux）
pip install -r requirements-unix.txt

# 运行启动器（GUI 模式）
python main.py

# 运行 Agent CLI 模式
python main.py --agent "帮我安装最新版"
python main.py -A              # 交互模式
```

> 💡 建议使用虚拟环境：`python -m venv .venv && source .venv/bin/activate`
> Linux 用户请参阅 [docs/LINUX_FILE_LOCATIONS.md](docs/LINUX_FILE_LOCATIONS.md) 了解 FHS 标准下的文件存储路径

---

## 快速开始

### 安装版本

1. 在右侧面板输入版本号（如 `1.20.4`）
2. 选择模组加载器（可选）：Forge / Fabric / NeoForge / 无
3. 点击「📥 安装版本」，等待完成

### 启动游戏

1. 在左侧「已安装版本」列表中点击要启动的版本
2. 点击底部「🚀 启动游戏」

### 常用操作

- **安装模组**：已安装加载器的版本右侧点击 🧩 按钮，搜索安装 Modrinth 模组
- **安装整合包**：点击「📦 安装整合包」，选择 .mrpack 文件或从 Modrinth 下载
- **开服**：切换到"🖥 开服"标签页，安装并启动 Minecraft 服务器
- **备份存档**：切换到"💾 备份"标签页，手动或自动备份存档

> 完整使用说明详见 [docs/USAGE.md](docs/USAGE.md)
> 配置项说明详见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

---

## 项目结构

```
FMCL/
├── main.py                # 程序入口
├── launcher/              # 启动器核心逻辑
├── ui/                    # CustomTkinter 界面
│   ├── app.py             # 主窗口（Mixin 组合模式）
│   ├── agent/             # AI 智能助手子系统
│   └── windows/           # 独立子窗口
├── downloader.py          # 多线程/异步下载器
├── modrinth.py            # Modrinth API 集成
├── curseforge.py          # CurseForge API 集成
├── mirror.py              # BMCLAPI 镜像源
├── backup_manager.py      # 存档备份管理
├── secure_storage.py      # 安全存储（Fernet 加密）
├── config.py              # 跨平台配置管理
├── validation.py          # 输入验证
├── updater.py             # 自动更新
├── scripts/               # 构建/发布脚本
├── tests/                 # 测试
└── docs/                  # 文档
    ├── FEATURES.md        # 完整功能列表
    ├── USAGE.md           # 详细使用指南
    ├── ARCHITECTURE.md    # 项目架构与技术栈
    ├── CONFIGURATION.md   # 配置说明
    └── ...
```

> 完整项目结构与模块依赖关系详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## 开发指南

### 环境设置

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装开发依赖（Linux CI）
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

> 构建问题排查请参考 [docs/BUILD_FIXES.md](docs/BUILD_FIXES.md)

### 发布流程

1. 更新 `pyproject.toml` 和 `package.json` 中的版本号
2. 提交变更：`git commit -m "chore: release vX.X.X"`
3. 创建标签：`git tag vX.X.X`
4. 推送：`git push origin main --tags`

GitHub Actions 会自动构建 Windows 安装包并创建 Release（自 v2.10.3 起不再自动构建 macOS / Linux 二进制包，如需可使用上方 `make build-dmg` / `make build-deb` / `make build-appimage` 在对应平台本地构建）。

详见：[CONTRIBUTING.md](CONTRIBUTING.md) | [docs/SETUP.md](docs/SETUP.md)

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

---

## 故障排除

查看完整指南：[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

| 问题 | 解决方案 |
|------|----------|
| 镜像源连接失败 | 尝试关闭「国内镜像」开关使用 Mojang 官方源 |
| 版本安装失败 | 检查网络连接，查看 `latest.log` 日志 |
| 游戏启动失败 | 确保已安装 Java 运行时 |
| macOS 提示无法验证开发者 | `xattr -cr FMCL.app` 移除隔离属性 |
| Linux 配置目录权限错误 | `sudo mkdir -p /etc/fmcl && sudo chown $USER:$USER /etc/fmcl` |
| Linux 无图形环境崩溃 | WSL/无头服务器下鼠标检测线程会自动跳过，不会崩溃 |

---

## 许可证

- **v2.8.4 及以前版本**：使用 [MIT License](LICENSE)
- **v2.8.4 以后版本**：使用 [GNU General Public License v3.0](LICENSE)

Copyright (c) 2026 FMCL Team
