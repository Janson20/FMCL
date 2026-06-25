# FMCL 配置说明

> 本文档包含 FMCL 的配置文件位置（跨平台）和所有配置项说明。

---

## 配置文件位置（跨平台）

**Windows/macOS:**
- 配置文件: `config.json`（程序根目录）
- 密钥文件: `.fmcl_key`（程序根目录，用于加密存储敏感 Token）
- 日志文件: `latest.log`（程序根目录）
- Minecraft 目录: `.minecraft/`（程序根目录）

**Linux (FHS 标准):**
- 配置文件: `/etc/fmcl/config.json`
- 密钥文件: `~/.fmcl/.fmcl_key`
- 日志文件: `/var/log/fmcl/latest.log`
- Minecraft 目录: `~/.minecraft/`
- 运行时目录: `~/.fmcl/`

> 💡 **Linux 首次运行**: 
> - **日志目录** (`/var/log/fmcl/`) 会自动创建（如权限不足会提示）
> - **配置目录** (`/etc/fmcl/`) 需要手动创建或使用初始化脚本：
>   ```bash
>   chmod +x scripts/setup_linux.sh
>   ./scripts/setup_linux.sh
>   ```
>   或手动创建：
>   ```bash
>   sudo mkdir -p /etc/fmcl /var/log/fmcl
>   sudo chown $USER:$USER /etc/fmcl /var/log/fmcl
>   ```

## 配置项说明

配置文件 `config.json` 启动时自动加载：

```json
{
  "mirror_enabled": true,
  "download_threads": 4,
  "minimize_on_game_launch": false,
  "auto_check_update": true,
  "player_name": "Steve",
  "skin_path": null,
  "jdz_token": "gAAAAABm...（Fernet 加密密文）",
  "language": "zh_CN",
  "ai_privacy_consent": false,
  "terms_consent": false,
  "backup_dir": null,
  "backup_compress_level": 6,
  "backup_max_per_world": 10,
  "backup_restore_mode": "rename",
  "backup_auto_launch": false,
  "backup_auto_exit": false
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
| `language` | string | `"zh_CN"` | 界面语言（zh_CN/en_US/ja_JP/zh_TW） |
| `theme_name` | string | `"default"` | 主题名称（default/ocean/forest/lavender/sunset 或用户导入的主题名） |
| `accent_color` | string/null | `null` | 自定义强调色 Hex 值（如 `"#e94560"`，null 使用主题默认） |
| `dynamic_version_theme` | bool | `false` | 是否启用 Minecraft 版本动态主题 |
| `ai_privacy_consent` | bool | `false` | 是否已同意 AI 分析隐私说明 |
| `terms_consent` | bool | `false` | 是否已同意使用条款（Minecraft EULA + 净读协议），首次启动时弹窗确认 |
| `backup_dir` | string/null | `null` | 备份存储路径（null 则使用程序目录/backups） |
| `backup_compress_level` | int | `6` | 备份压缩等级（1=最快, 9=最小体积） |
| `backup_max_per_world` | int | `10` | 每个存档最大备份数（0=不限制） |
| `backup_restore_mode` | string | `"rename"` | 恢复时旧存档处理方式（rename/overwrite/trash） |
| `backup_auto_launch` | bool | `false` | 是否在游戏启动前自动备份 |
| `backup_auto_exit` | bool | `false` | 是否在游戏退出后自动备份 |
