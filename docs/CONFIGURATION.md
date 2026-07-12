# FMCL 配置说明

> 本文档包含 FMCL 的配置文件位置（跨平台）和所有配置项说明。

---

## 配置文件位置（跨平台）

**Windows/macOS:**
- 配置文件: `config.json`（程序根目录）
- 密钥文件: `.fmcl_key`（程序根目录，用于加密存储敏感 Token）
- 日志文件: `latest.log`（程序根目录）
- Minecraft 目录: `.minecraft/`（程序根目录）

**Linux (XDG Base Directory 规范):**
- 配置文件: `~/.config/fmcl/config.json`
- 密钥文件: `~/.fmcl/.fmcl_key`
- 日志文件: `~/.local/share/fmcl/fmcl.log`
- Minecraft 目录: `~/.minecraft/`
- 运行时目录: `~/.fmcl/`

> **Linux 首次运行**：启动器会自动创建上述目录，无需手动干预。
> 确保已安装系统依赖：`sudo apt install python3-tk python3-venv`（Debian/Ubuntu）

## 配置项说明

配置文件 `config.json` 启动时自动加载，共 26 项持久化配置：

```json
{
  "mirror_enabled": true,
  "download_threads": 4,
  "minimize_on_game_launch": false,
  "auto_check_update": true,
  "player_name": "Steve",
  "skin_path": null,
  "jdz_token": "gAAAAABm...（Fernet 加密密文）",
  "jdz_username": "gAAAAABm...（Fernet 加密密文）",
  "language": "zh_CN",
  "theme_name": "default",
  "accent_color": null,
  "dynamic_version_theme": false,
  "ai_privacy_consent": false,
  "terms_consent": false,
  "java_mode": "auto",
  "java_custom_path": null,
  "backup_dir": null,
  "backup_compress_level": 6,
  "backup_max_per_world": 10,
  "backup_restore_mode": "rename",
  "backup_auto_launch": false,
  "backup_auto_exit": false,
  "accounts_file": null,
  "current_account_id": null,
  "account_migration_done": false,
  "music_state": {}
}
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `mirror_enabled` | bool | `true` | 是否启用 BMCLAPI 国内镜像 |
| `download_threads` | int | `4` | 多线程下载的线程数 |
| `minimize_on_game_launch` | bool | `false` | 游戏启动后是否最小化启动器窗口 |
| `auto_check_update` | bool | `true` | 启动时是否自动检查更新 |
| `player_name` | string | `"Steve"` | （旧版兼容）自定义游戏角色名，现在由账号系统自动管理 |
| `skin_path` | string/null | `null` | 自定义皮肤文件路径 |
| `jdz_token` | string/null | `null` | 净读 AI Token（Fernet 加密存储，不可手动编辑） |
| `jdz_username` | string/null | `null` | 净读 AI 用户名（Fernet 加密存储，不可手动编辑） |
| `language` | string | `"zh_CN"` | 界面语言（zh_CN/en_US/ja_JP/zh_TW） |
| `theme_name` | string | `"default"` | 主题名称（default/ocean/forest/lavender/sunset 或用户导入的主题名） |
| `accent_color` | string/null | `null` | 自定义强调色 Hex 值（如 `"#e94560"`，null 使用主题默认） |
| `dynamic_version_theme` | bool | `false` | 是否启用 Minecraft 版本动态主题 |
| `ai_privacy_consent` | bool | `false` | 是否已同意 AI 分析隐私说明 |
| `terms_consent` | bool | `false` | 是否已同意使用条款（Minecraft EULA + 净读协议），首次启动时弹窗确认 |
| `java_mode` | string | `"auto"` | Java 模式：`auto`（自动扫描）、`system`（系统默认）、`custom`（自定义路径） |
| `java_custom_path` | string/null | `null` | 自定义 Java 可执行文件路径（`java_mode` 为 `custom` 时使用） |
| `backup_dir` | string/null | `null` | 备份存储路径（null 则使用程序目录/backups） |
| `backup_compress_level` | int | `6` | 备份压缩等级（1=最快, 9=最小体积） |
| `backup_max_per_world` | int | `10` | 每个存档最大备份数（0=不限制） |
| `backup_restore_mode` | string | `"rename"` | 恢复时旧存档处理方式：`rename`（重命名为 _bak_时间戳）、`overwrite`（直接覆盖）、`trash`（移至回收站） |
| `backup_auto_launch` | bool | `false` | 是否在游戏启动前自动备份 |
| `backup_auto_exit` | bool | `false` | 是否在游戏退出后自动备份 |
| `accounts_file` | string/null | `null` | 账号存储文件路径（null 则使用程序目录/accounts.json） |
| `current_account_id` | string/null | `null` | 当前选中账号的 UUID |
| `account_migration_done` | bool | `false` | 旧版 `player_name` 是否已迁移为离线账号（首次启动自动执行） |
| `music_state` | object | `{}` | 音乐播放器状态持久化（包含音量、播放模式、上次打开的文件夹路径等） |
