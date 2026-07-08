# 常见问题排查

## 构建问题

### Linux 构建失败

**问题**: PyInstaller在Linux上构建失败，提示缺少依赖

**解决方案**:
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y build-essential zlib1g-dev libncurses5-dev \
    libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev \
    libsqlite3-dev libbz2-dev liblzma-dev tk-dev uuid-dev

# Fedora/RHEL
sudo dnf groupinstall "Development Tools"
sudo dnf install python3-devel tk-devel
```

### macOS 签名问题

**问题**: macOS提示"无法打开，因为无法验证开发者"

**解决方案**:
```bash
# 方法1: 在系统偏好设置中允许
# 系统偏好设置 -> 安全性与隐私 -> 通用 -> 仍要打开

# 方法2: 移除隔离属性
xattr -cr FMCL.app

# 方法3: 代码签名（需要Apple Developer账号）
codesign --deep --force --verify --verbose --sign "Developer ID Application: Your Name" FMCL.app
```

### Windows 杀毒软件误报

**问题**: Windows Defender将可执行文件识别为病毒

**解决方案**:
1. **临时方案**: 添加到排除列表
   - Windows安全中心 -> 病毒和威胁防护 -> 管理设置 -> 排除项

2. **长期方案**: 购买代码签名证书并签名
   ```powershell
   # 使用signtool签名
   signtool sign /f certificate.pfx /p password /t http://timestamp.digicert.com FMCL.exe
   ```

### ARM64 构建问题

**问题**: 在ARM64平台上构建失败

**解决方案**:
- **macOS**: 使用Xcode命令行工具
  ```bash
  xcode-select --install
  ```
- **Linux**: 确保安装了ARM64交叉编译工具链
  ```bash
  sudo apt-get install gcc-aarch64-linux-gnu g++-aarch64-linux-gnu
  ```

## 运行时问题

### 导入错误

**问题**: `ModuleNotFoundError: No module named 'xxx'`

**解决方案**:
```bash
# 重新安装依赖
pip install -r requirements.txt --force-reinstall

# 或使用uv
uv sync --reinstall
```

### 权限错误

**问题**: Linux/macOS上提示权限不足

**解决方案**:
```bash
# 添加执行权限
chmod +x FMCL

# 或使用sudo运行
sudo ./FMCL
```

### Minecraft 启动失败

**问题**: 游戏无法启动或闪退

**排查步骤**:
1. 检查Java版本
   ```bash
   java -version
   ```
   Minecraft 1.17+需要Java 17+

2. 检查.minecraft目录权限
   ```bash
   ls -la .minecraft
   ```

3. 查看日志
   ```bash
   cat latest.log
   ```

4. 检查可用磁盘空间
   ```bash
   df -h
   ```

### 下载速度慢

**问题**: 文件下载速度很慢

**解决方案**:
1. 使用多线程下载（已内置）
2. 更换镜像源（编辑`config.py`）
3. 使用代理

### GUI 界面不显示

**问题**: 启动后没有界面显示

**解决方案**:
- **Linux**: 确保安装了图形界面
  ```bash
  sudo apt-get install xvfb
  xvfb-run python main.py
  ```
- **macOS**: 检查是否在终端中运行，尝试双击.app文件
- **Windows**: 检查是否有杀毒软件拦截

## GitHub Actions 问题

### 构建超时

**问题**: GitHub Actions构建超时

**解决方案**:
1. 减少构建目标（编辑`.github/workflows/release.yml`）
2. 优化依赖安装
3. 使用缓存

### Node.js 弃用警告

**问题**: "Node.js 20 actions are deprecated"

**解决方案**: 已在工作流中设置
```yaml
env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true
```

### Artifacts 上传失败

**问题**: 上传artifacts时失败

**解决方案**:
1. 检查文件是否存在
2. 检查文件大小（最大2GB）
3. 查看Actions日志获取详细错误

## 开发环境问题

### Git Hooks 不工作

**问题**: 提交时没有验证

**解决方案**:
```bash
# 重新安装Git hooks
npm install
npm run prepare

# 手动安装
npx husky install
```

### commitlint 报错

**问题**: 提交消息格式错误

**解决方案**:
使用正确的提交格式：
```bash
git commit -m "feat: 添加新功能"
git commit -m "fix: 修复bug"
git commit -m "docs: 更新文档"
```

### 依赖冲突

**问题**: 依赖版本冲突

**解决方案**:
```bash
# 清理并重新安装
pip uninstall -y -r requirements.txt
pip cache purge
pip install -r requirements.txt

# 或使用虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

## AI Agent 问题

### Agent 无法连接 AI 服务

**问题**: AGENT 标签页显示连接失败或超时

**解决方案**:
1. 确保已在设置中登录净读 AI 账号
2. 检查网络连接是否正常
3. 如使用自定义 OpenAI 端点，确保端点地址和 API Key 正确
4. 查看 `latest.log` 中的 API 调用错误详情

### Agent 工具执行失败

**问题**: AI 回复提示工具执行错误

**解决方案**:
1. 确认工具所需的权限已授予（首次使用时会弹窗确认）
2. 文件操作工具（写/改/删）需要在确认弹窗中预览 diff 后点击确认
3. 检查任务面板中是否有前置任务未完成
4. 查看右侧任务面板的状态提示了解具体错误原因

### 模型调用返回空响应

**问题**: AI 返回空内容或连接超时

**解决方案**:
1. 切换其他模型（如从 DeepSeek V4 Flash 切换到 V4 Pro）
2. 检查 API 密钥余额是否充足
3. 净读 AI 用户请在净读平台确认账号状态

## 插件系统问题

### 插件安装失败

**问题**: 安装 `.fmpl` 文件时提示错误

**解决方案**:
1. 确认文件是有效的 `.fmpl` 格式（本质为 ZIP 压缩包，包含 `plugin.json` 和 `__init__.py`）
2. 检查 `plugin.json` 中的 `min_fmcl_version` 是否与当前 FMCL 版本兼容
3. 检查是否已安装同 ID 的插件（插件 ID 必须全局唯一）
4. 查看 `latest.log` 中的详细错误信息

### 插件启用后报错

**问题**: 插件状态变为 ERROR 或启动器功能异常

**解决方案**:
1. 在「设置 → 插件管理」中禁用该插件
2. 检查插件请求的权限是否被拒绝（高风险权限如 `core.process`、`network.socket` 可能被限制）
3. 查看 `latest.log` 中的 Python 异常堆栈
4. 联系插件开发者获取支持

### 插件冲突

**问题**: 启用多个插件后功能异常

**解决方案**:
1. 逐一禁用插件，确定冲突源
2. 检查 `plugin.json` 中的 `conflicts` 字段是否声明了冲突
3. 相同钩子点的多个插件可能产生预期外的交互

## 陶瓦联机问题

### EasyTier 无法启动

**问题**: 创建/加入大厅时 EasyTier 启动失败

**解决方案**:
1. 检查网络连接：EasyTier 需要访问 STUN/TURN 服务器
2. 确认系统防火墙未阻止 EasyTier 进程
3. 关闭 VPN 或代理软件，可能干扰 P2P 连接
4. 查看联机标签页的日志面板获取详细错误信息

### 无法加入好友大厅

**问题**: 输入陶瓦编号后连接超时

**解决方案**:
1. 确认陶瓦编号输入正确（注意大小写和 `U/` 前缀）
2. 确认好友的大厅仍在运行（主机离线时大厅自动销毁）
3. 检查 NAT 类型：对称 NAT 环境可能无法直连，EasyTier 会自动尝试中继
4. 尝试关闭防火墙或添加例外规则

### 联机延迟过高

**问题**: 游戏中卡顿、延迟高

**解决方案**:
1. EasyTier 默认启用 `--latency-first` 模式，会自动选择最优路径
2. 检查成员列表底部的延迟显示，确认与主机的网络质量
3. 避免通过移动热点或高延迟网络联机

## 音乐播放器问题

### 在线搜索无结果

**问题**: 搜索关键词后返回空列表

**解决方案**:
1. 尝试切换音源（如从酷狗切换到 QQ 音乐）
2. 个别平台接口可能变动导致暂时不可用
3. 尝试使用不同的搜索关键词

### 在线播放失败

**问题**: 点击播放后无声音或报错

**解决方案**:
1. 尝试切换音质（如从 FLAC 切换到 128K）
2. 音源自动降级重试 3 次，若仍失败则换源播放
3. 检查网络连接，部分平台需要访问特定的 CDN 域名

### 桌面歌词不显示

**问题**: 桌面歌词窗口打开后无内容

**解决方案**:
1. 确认当前播放的歌曲包含歌词元数据
2. 调整歌词窗口透明度，可能过于透明导致看不见
3. 解锁拖拽后移动窗口位置

## 获取帮助

如果以上方法都无法解决问题：

1. **查看日志**: `latest.log` 文件包含详细的错误信息
2. **搜索Issues**: [GitHub Issues](https://github.com/Janson20/FMCL/issues)
3. **提交Issue**: 提供以下信息：
   - 操作系统和版本
   - Python版本
   - 错误日志
   - 复现步骤
4. **社区讨论**: [GitHub Discussions](https://github.com/Janson20/FMCL/discussions)
