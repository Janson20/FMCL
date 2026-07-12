# Linux 文件存储位置说明

本文档说明 FMCL 在 Linux 平台上的文件存储位置，遵循 XDG Base Directory 规范。

## 📁 目录结构

### Linux 平台（v3.5+）

```
~/.config/fmcl/
└── config.json              # 配置文件（镜像源、下载线程数、玩家名等）

~/.local/share/fmcl/
└── fmcl.log                 # 启动器日志

~/.minecraft/                # Minecraft 游戏目录
├── versions/                # 游戏版本
├── mods/                    # 模组（全局）
├── resourcepacks/           # 资源包
├── saves/                   # 存档
├── shaderpacks/             # 光影
├── skins/                   # 皮肤
├── crash-reports/           # 崩溃报告
└── logs/                    # 游戏日志

~/.fmcl/                     # FMCL 运行时目录
├── plugins/                 # 插件
├── accounts.json            # 账号数据（加密）
├── achievement.db           # 成就数据
└── pos.txt                  # 鼠标位置记录（调试用）
```

### Windows/macOS 平台（保持不变）

```
FMCL/                        # 程序所在目录
├── config.json              # 配置文件
├── latest.log               # 启动器日志
├── .minecraft/              # Minecraft 游戏目录
│   ├── versions/
│   ├── mods/
│   └── ...
└── pos.txt                  # 鼠标位置记录
```

## 🔧 首次运行

FMCL 会在首次启动时自动创建所有必要的目录，无需手动干预。

```bash
# 只需确保系统依赖已安装
sudo apt install python3-tk python3-venv  # Debian/Ubuntu
sudo dnf install python3-tkinter          # Fedora
```

## 🎯 设计理由

### 为什么使用 XDG Base Directory 规范？

1. **符合 Linux 社区惯例**
   - `~/.config/` — 用户配置文件
   - `~/.local/share/` — 用户应用数据
   - 与 Chromium、VS Code、Neovim 等主流应用一致

2. **无需 root 权限**
   - 所有文件都在用户主目录下
   - 首次运行即可读写，无需 `sudo`

3. **环境变量支持**
   - 可通过 `$XDG_CONFIG_HOME` 和 `$XDG_DATA_HOME` 自定义路径
   - 适合沙箱环境和容器化部署

## 🔄 从旧版本迁移（v3.4 及更早）

如果你之前使用的是 `/etc/fmcl/` 和 `/var/log/fmcl/` 路径：

```bash
# 1. 复制旧配置到新位置
mkdir -p ~/.config/fmcl
sudo cp /etc/fmcl/config.json ~/.config/fmcl/config.json 2>/dev/null || true
sudo chown $USER:$USER ~/.config/fmcl/config.json 2>/dev/null || true

# 2. 复制旧日志（诊断用）
mkdir -p ~/.local/share/fmcl
sudo cp /var/log/fmcl/latest.log ~/.local/share/fmcl/fmcl.log 2>/dev/null || true
sudo chown $USER:$USER ~/.local/share/fmcl/fmcl.log 2>/dev/null || true

# 3. （可选）清理旧目录
sudo rm -rf /etc/fmcl /var/log/fmcl
```

FMCL v3.5+ 会自动优先读取新路径，旧路径不会被清理以避免数据丢失。

## ⚠️ 已知限制

| 限制 | 说明 |
|------|------|
| 全局热键 | `keyboard` 库需要 root 权限，音乐播放器/性能监控的全局快捷键在非 root 下不可用 |
| Java 自动安装 | 自动安装命令仅支持 apt（Debian/Ubuntu），其他发行版需手动安装 Java |

## 📝 技术实现

### 平台检测逻辑

```python
import os
from pathlib import Path

system = platform.system().lower()

if system == "linux":
    xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config"))
    xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share"))

    config_file = xdg_config_home / "fmcl" / "config.json"
    log_file = xdg_data_home / "fmcl" / "fmcl.log"
    minecraft_dir = Path.home() / ".minecraft"
    base_dir = Path.home() / ".fmcl"
else:
    base_dir = Path.cwd()
    config_file = base_dir / "config.json"
    log_file = base_dir / "latest.log"
    minecraft_dir = base_dir / ".minecraft"
```

## 🔗 相关文档

- [XDG Base Directory 规范](https://specifications.freedesktop.org/basedir-spec/latest/)
- [FMCL 主文档](../README.md)
