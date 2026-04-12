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
xattr -cr MCL.app

# 方法3: 代码签名（需要Apple Developer账号）
codesign --deep --force --verify --verbose --sign "Developer ID Application: Your Name" MCL.app
```

### Windows 杀毒软件误报

**问题**: Windows Defender将可执行文件识别为病毒

**解决方案**:
1. **临时方案**: 添加到排除列表
   - Windows安全中心 -> 病毒和威胁防护 -> 管理设置 -> 排除项

2. **长期方案**: 购买代码签名证书并签名
   ```powershell
   # 使用signtool签名
   signtool sign /f certificate.pfx /p password /t http://timestamp.digicert.com MCL.exe
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
chmod +x MCL

# 或使用sudo运行
sudo ./MCL
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

## 获取帮助

如果以上方法都无法解决问题：

1. **查看日志**: `latest.log` 文件包含详细的错误信息
2. **搜索Issues**: [GitHub Issues](https://github.com/Janson20/MCL/issues)
3. **提交Issue**: 提供以下信息：
   - 操作系统和版本
   - Python版本
   - 错误日志
   - 复现步骤
4. **社区讨论**: [GitHub Discussions](https://github.com/Janson20/MCL/discussions)
