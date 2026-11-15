# FMCL 配置说明

> 本文列出配置文件位置、平台差异与全部持久化配置项。配置由启动器自动读写，
> 通常不需要手工编辑；需要修改时请先退出启动器。

---

## 1. 配置文件位置

### Windows / macOS

启动器优先使用程序所在目录（便携模式）：

| 内容 | 路径 |
|---|---|
| 配置 | `config.json` |
| 密钥 | `.fmcl_key` |
| 日志 | `latest.log` |
| 结构化日志 | `latest_structured.log` |
| Minecraft | `.minecraft/` |

程序目录不可写时（例如安装在系统程序目录），自动回退到用户数据目录：

- Windows：`%LOCALAPPDATA%\FMCL`
- macOS：`~/Library/Application Support/FMCL`

首次回退时会把原目录中已有的配置、账号、存档、歌单等数据复制到新目录，原目录保留不删。

### Linux

遵循 XDG Base Directory：

| 内容 | 路径 |
|---|---|
| 配置 | `~/.config/fmcl/config.json` |
| 密钥 | `~/.fmcl/.fmcl_key` |
| 日志 | `~/.local/share/fmcl/fmcl.log` |
| 运行时数据 | `~/.fmcl/` |
| Minecraft | `~/.minecraft/` |

首次运行会自动创建目录。源码运行时需要 Python 3.10–3.11 与 Qt 6 运行库；
构建依赖见 [SETUP.md](SETUP.md)。

---

## 2. 配置项

`config.json` 当前保存 **32 项**配置。写入使用临时文件加原子替换，避免中断时留下半个文件。

```json
{
  "mirror_enabled": true,
  "download_threads": 4,
  "minimize_on_game_launch": false,
  "auto_check_update": true,
  "player_name": "Steve",
  "skin_path": null,
  "jdz_token": null,
  "jdz_username": null,
  "language": "zh_CN",
  "language_chosen": false,
  "theme_name": "default",
  "accent_color": null,
  "dynamic_version_theme": false,
  "ai_privacy_consent": false,
  "terms_consent": false,
  "bedrock_terms_accepted": false,
  "bedrock_components_consented": false,
  "backup_dir": null,
  "backup_compress_level": 6,
  "backup_max_per_world": 10,
  "backup_restore_mode": "rename",
  "backup_auto_launch": false,
  "backup_auto_exit": false,
  "java_mode": "auto",
  "java_custom_path": null,
  "accounts_file": null,
  "current_account_id": null,
  "account_migration_done": false,
  "music_state": {},
  "overlay_geometry": {},
  "music_baike_original_enabled": true,
  "wy_cookie": null
}
```

### 下载与更新

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `mirror_enabled` | bool | `true` | 使用 BMCLAPI 国内镜像 |
| `download_threads` | int | `4` | 多线程下载的线程数 |
| `auto_check_update` | bool | `true` | 启动后静默检查更新 |
| `minimize_on_game_launch` | bool | `false` | 游戏启动后最小化启动器 |

### 账号与玩家

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `player_name` | string | `"Steve"` | 旧版玩家名，仅用于首次迁移为离线账号 |
| `skin_path` | string/null | `null` | 当前皮肤文件路径 |
| `accounts_file` | string/null | `null` | 账号文件路径；`null` 使用数据目录下的 `accounts.json` |
| `current_account_id` | string/null | `null` | 当前账号 id |
| `account_migration_done` | bool | `false` | 旧玩家名是否已迁移，内部标记 |

### 界面与语言

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `language` | string | `"zh_CN"` | 界面语言：`zh_CN` / `zh_TW` / `en_US` / `ja_JP` |
| `language_chosen` | bool | `false` | 是否已完成首次语言选择 |
| `theme_name` | string | `"default"` | 主题名：`default` / `ocean` / `forest` / `lavender` / `sunset`，或导入主题名 |
| `accent_color` | string/null | `null` | 自定义强调色 Hex 值；`null` 使用主题默认 |
| `dynamic_version_theme` | bool | `false` | 按当前 Minecraft 版本调整强调色 |
| `overlay_geometry` | object | `{}` | 桌面歌词与性能悬浮窗的位置、尺寸、透明度，键为窗口名 |

### 协议与隐私

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `terms_consent` | bool | `false` | 是否已同意使用条款与 Minecraft EULA |
| `ai_privacy_consent` | bool | `false` | 是否已同意 AI 分析的日志上传说明 |
| `bedrock_terms_accepted` | bool | `false` | 是否已接受基岩版相关条款 |
| `bedrock_components_consented` | bool | `false` | 是否同意按需下载 GDK 认证组件 |

### Java

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `java_mode` | string | `"auto"` | `auto` 自动扫描 / `system` 系统 Java / `custom` 自定义路径 |
| `java_custom_path` | string/null | `null` | 自定义 Java 可执行文件路径 |

### 备份

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `backup_dir` | string/null | `null` | 备份目录；`null` 使用数据目录下的 `backups/` |
| `backup_compress_level` | int | `6` | ZIP 压缩等级，1 最快、9 体积最小 |
| `backup_max_per_world` | int | `10` | 每个存档保留的备份数，0 表示不限制 |
| `backup_restore_mode` | string | `"rename"` | 恢复时旧存档处理：`rename` / `overwrite` / `trash` |
| `backup_auto_launch` | bool | `false` | 游戏启动前自动备份 |
| `backup_auto_exit` | bool | `false` | 游戏退出后自动备份 |

### 音乐与凭据

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `music_state` | object | `{}` | 音量、播放模式、上次目录等播放状态 |
| `music_baike_original_enabled` | bool | `true` | 无法判定原唱时查询百科兜底 |
| `wy_cookie` | string/null | `null` | 网易云登录 Cookie，Fernet 加密后保存 |
| `jdz_token` | string/null | `null` | 净读 AI Token，Fernet 加密后保存 |
| `jdz_username` | string/null | `null` | 净读 AI 用户名，Fernet 加密后保存 |

> `jdz_token`、`jdz_username`、`wy_cookie` 在文件中是加密密文，不能直接复制使用；
> 修改后启动器会解密失败并按未登录处理。请通过界面重新登录。

---

## 3. 运行时目录

以下目录不在 `config.json` 中配置，由数据目录推导：

| 内容 | 位置 |
|---|---|
| 游戏版本 | `.minecraft/versions/` |
| 模组与资源 | 对应版本的 `mods/`、`resourcepacks/`、`shaderpacks/`、`saves/` |
| 账号 | `accounts.json` |
| 成就 | `achievements.db` |
| 插件 | `plugins/`（配置与数据按插件 id 分目录） |
| 音乐 | `data/music/` |
| 备份 | `backups/` 或 `backup_dir` |
| 主题 | `themes/` |
| 日志 | `latest.log`、`latest_structured.log` |

---

## 4. 恢复默认

- 单项设置：在「设置」对应分区点「恢复默认」或改回默认值。
- 全部设置：退出启动器，备份 `config.json` 后删除它；下次启动会重新生成默认配置。
- 账号、成就、存档与歌单不在 `config.json` 中，删除配置不会删除这些数据。
