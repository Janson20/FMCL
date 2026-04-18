# Linux 文件存储位置说明

本文档说明 FMCL 在 Linux 平台上的文件存储位置调整，遵循 FHS (Filesystem Hierarchy Standard) 标准。

## 📁 目录结构

### Linux 平台（v2.3.1+）

```
/etc/fmcl/
└── config.json              # 配置文件（镜像源、下载线程数、玩家名等）

/var/log/fmcl/
├── latest.log               # 启动器日志
└── debug.log                # 调试日志（如有）

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

## 🔧 首次运行设置

### 方法一：使用初始化脚本（推荐）

```bash
cd /path/to/FMCL
chmod +x scripts/setup_linux.sh
./scripts/setup_linux.sh
```

该脚本会自动：
- 创建 `/etc/fmcl` 和 `/var/log/fmcl` 目录
- 设置正确的所有权和权限
- 创建 `~/.minecraft` 和 `~/.fmcl` 目录

### 方法二：手动设置

```bash
# 创建配置目录
sudo mkdir -p /etc/fmcl
sudo chown $USER:$USER /etc/fmcl
sudo chmod 755 /etc/fmcl

# 创建日志目录
sudo mkdir -p /var/log/fmcl
sudo chown $USER:$USER /var/log/fmcl
sudo chmod 755 /var/log/fmcl

# 创建 Minecraft 目录
mkdir -p ~/.minecraft

# 创建 FMCL 运行时目录
mkdir -p ~/.fmcl
```

## 🎯 设计理由

### 为什么使用 FHS 标准？

1. **符合 Linux 惯例**
   - `/etc/` - 系统配置文件
   - `/var/log/` - 可变日志文件
   - `~/` - 用户数据

2. **便于系统管理**
   - 日志可被 logrotate 统一管理
   - 配置可被备份工具自动包含
   - 符合系统管理员预期

3. **多用户支持**
   - 每个用户有独立的 `~/.minecraft`
   - 共享系统级配置框架

4. **安全性**
   - 配置文件权限可控
   - 日志目录独立，便于审计

## 🔄 迁移指南

### 从旧版本迁移

如果你之前使用的是当前工作目录模式：

```bash
# 1. 备份旧配置
cp config.json ~/config.json.backup

# 2. 运行初始化脚本
./scripts/setup_linux.sh

# 3. 恢复配置（可选）
cp ~/config.json.backup /etc/fmcl/config.json

# 4. 移动 Minecraft 数据（如果需要）
mv .minecraft/* ~/.minecraft/

# 5. 清理旧文件
rm -rf .minecraft config.json latest.log
```

## ⚠️ 注意事项

### 权限问题

- `/etc/fmcl` 和 `/var/log/fmcl` 需要适当的权限
- 首次运行可能需要 `sudo` 创建目录
- 之后普通用户即可读写

### 日志轮转

建议配置 logrotate 管理日志：

```bash
# /etc/logrotate.d/fmcl
/var/log/fmcl/*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 0644 $USER $USER
}
```

### 备份策略

```bash
# 备份配置
tar czf fmcl-config-backup.tar.gz /etc/fmcl/

# 备份 Minecraft 数据
tar czf minecraft-backup.tar.gz ~/.minecraft/

# 备份日志（诊断用）
tar czf fmcl-logs-backup.tar.gz /var/log/fmcl/
```

## 🐛 故障排除

### 问题：无法写入配置文件

**症状**: `Permission denied: /etc/fmcl/config.json`

**解决**:
```bash
sudo chown $USER:$USER /etc/fmcl
sudo chmod 755 /etc/fmcl
```

### 问题：无法写入日志

**症状**: `Permission denied: /var/log/fmcl/latest.log`

**解决**:
```bash
sudo chown $USER:$USER /var/log/fmcl
sudo chmod 755 /var/log/fmcl
```

### 问题：找不到之前的 Minecraft 数据

**检查**:
```bash
# 查看当前 Minecraft 目录
ls -la ~/.minecraft/

# 如果数据在旧位置
ls -la ./FMCL/.minecraft/

# 移动数据
mv ./FMCL/.minecraft/* ~/.minecraft/
```

## 📝 技术实现

### 代码变更摘要

1. **config.py**
   - 新增 `_get_platform_paths()` 函数
   - 根据 `platform.system()` 返回不同路径
   - Linux 使用 FHS 标准路径
   - Windows/macOS 保持原有行为

2. **ui.py**
   - 更新 `_collect_crash_info()` 方法
   - Linux 从 `/var/log/fmcl/` 读取日志
   - 其他平台从项目根目录读取

3. **main.py**
   - 无需修改（使用 `config.log_file` 自动适配）

### 平台检测逻辑

```python
import platform

system = platform.system().lower()

if system == "linux":
    # FHS 标准路径
    config_file = Path("/etc/fmcl/config.json")
    log_file = Path("/var/log/fmcl/latest.log")
    minecraft_dir = Path.home() / ".minecraft"
else:
    # 当前工作目录
    base_dir = Path.cwd()
    config_file = base_dir / "config.json"
    log_file = base_dir / "latest.log"
    minecraft_dir = base_dir / ".minecraft"
```

## 🔗 相关文档

- [FHS 标准](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/index.html)
- [Linux 目录结构说明](https://tldp.org/LDP/Linux-Filesystem-Hierarchy/html/)
- [FMCL 主文档](../README.md)

## 📅 版本历史

- **v2.3.1** (2026-04-18): 初始实现 Linux FHS 标准路径支持
