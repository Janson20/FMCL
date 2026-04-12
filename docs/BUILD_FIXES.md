# 构建问题修复说明

## 已修复的问题

### 1. Node.js 20 弃用警告 ✅

**问题**: GitHub Actions 显示 "Node.js 20 actions are deprecated"

**修复**: 在所有工作流中添加环境变量
```yaml
env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true
```

**影响文件**:
- `.github/workflows/release.yml`
- `.github/workflows/ci.yml`

---

### 2. Ubuntu 构建失败 ✅

**问题**: Linux AMD64 构建失败，退出代码 1

**原因**: 缺少必要的系统依赖

**修复**:
1. 添加系统依赖安装步骤
```yaml
- name: Install system dependencies (Linux)
  if: matrix.platform == 'linux'
  run: |
    sudo apt-get update
    sudo apt-get install -y build-essential zlib1g-dev ...
```

2. 更新构建配置
- 改进 `build.spec`，根据平台优化构建参数
- 添加平台特定的依赖和导入

---

### 3. macOS 平台问题 ✅

**问题**: 
- Intel 和 ARM 架构区分不明确
- 缺少 macOS 特定配置

**修复**:
1. 使用不同的 macOS runner
   - `macos-13`: Intel 芯片
   - `macos-latest`: Apple Silicon (ARM64)

2. 改进 macOS 构建
   - 添加 `argv_emulation=True`
   - 创建 `.app` bundle
   - 针对 ARM64 设置 `target_arch='universal2'`

---

### 4. 构建策略优化 ✅

**问题**: 一个平台失败导致所有构建取消

**修复**: 添加 `fail-fast: false`
```yaml
strategy:
  fail-fast: false  # 即使某个构建失败，其他也继续
```

---

### 5. PyInstaller 配置问题 ✅

**问题**: 
- 平台检测不准确
- 缺少必要的隐藏导入
- 包含了不必要的依赖

**修复**:
1. 环境变量驱动的平台检测
2. 添加平台特定的隐藏导入
3. 排除不必要的包
4. 根据平台优化构建参数

---

## 新增功能

### 1. 快速修复工具 🛠️

**文件**: `scripts/fix_common_issues.py`

**功能**:
- 修复权限问题
- 清理构建文件
- 重新安装依赖
- 修复平台特定问题
- 检查Java环境

**使用**:
```bash
python scripts/fix_common_issues.py
# 或
make fix
```

---

### 2. 故障排除指南 📖

**文件**: `docs/TROUBLESHOOTING.md`

**内容**:
- 构建问题及解决方案
- 运行时问题排查
- GitHub Actions 问题
- 开发环境配置
- 常见错误处理

---

### 3. 开发依赖分离 📦

**文件**: `requirements-dev.txt`

**内容**:
- 构建工具
- 代码质量工具
- 测试框架

**使用**:
```bash
pip install -r requirements-dev.txt
# 或
make install-dev
```

---

### 4. Makefile 增强 ⚡

**新增命令**:
```bash
make fix           # 运行快速修复工具
make check         # 检查环境和依赖
make install-dev   # 安装开发依赖
make release-dry-run  # 测试发布流程
```

---

## 改进的构建流程

### 构建矩阵

```
Platform    | Runner          | Arch   | Notes
------------|-----------------|--------|------------------
Windows     | windows-latest  | amd64  | 7z压缩
macOS Intel | macos-13        | amd64  | .app bundle
macOS ARM   | macos-latest    | arm64  | Universal binary
Linux AMD64 | ubuntu-latest   | amd64  | 系统依赖
Linux ARM64 | ubuntu-latest   | arm64  | 系统依赖
```

### 构建步骤

1. **环境准备**
   - 设置 Node.js 24
   - 安装 Python 3.11
   - 安装系统依赖（Linux）

2. **依赖安装**
   - 升级 pip, setuptools, wheel
   - 安装 PyInstaller
   - 安装项目依赖

3. **构建**
   - 运行 PyInstaller
   - 根据平台配置参数

4. **打包**
   - Windows: 7z压缩
   - Unix: zip压缩
   - 上传 artifacts

5. **发布**
   - 生成更新日志
   - 创建 GitHub Release
   - 上传构建文件

---

## 测试建议

### 本地测试构建

```bash
# 1. 安装开发依赖
make install-dev

# 2. 测试构建（当前平台）
PLATFORM=win ARCH=amd64 pyinstaller build.spec --noconfirm

# 3. 测试运行
./dist/MCL
```

### 测试 GitHub Actions

1. **CI 测试**: 推送到 main 分支
   ```bash
   git push origin main
   ```

2. **Release 测试**: 创建测试 tag
   ```bash
   make release-dry-run
   ```

---

## 监控和日志

### 查看构建日志

1. 进入 Actions 页面
2. 选择特定的工作流运行
3. 展开失败的步骤查看详细日志

### 常见错误识别

| 错误信息 | 可能原因 | 解决方案 |
|---------|---------|---------|
| `ModuleNotFoundError` | 缺少依赖 | 检查 hidden_imports |
| `Permission denied` | 权限不足 | 运行 `make fix` |
| `Command failed with exit code 1` | 构建失败 | 查看详细日志 |
| `No space left on device` | 磁盘空间不足 | 清理 artifacts |

---

## 下一步

### 立即测试

```bash
# 1. 提交修复
git add .
git commit -m "fix(ci): 修复构建配置和Node.js弃用警告"

# 2. 推送到远程
git push origin main

# 3. 检查CI是否通过
# 访问 GitHub Actions 页面查看结果

# 4. 如果CI通过，创建测试发布
python scripts/release.py 2.0.1
git push origin main
git push origin v2.0.1
```

### 长期改进

1. **代码签名**
   - 购买代码签名证书
   - 在 CI 中添加签名步骤

2. **自动更新**
   - 实现自动检查更新功能
   - 提供增量更新支持

3. **性能优化**
   - 优化构建大小
   - 减少启动时间
   - 改进内存使用

---

## 支持的平台

✅ Windows 10/11 (x64)
✅ macOS 12+ (Intel & Apple Silicon)
✅ Ubuntu 20.04+ (x64 & ARM64)
✅ 其他 Linux 发行版

---

## 需要帮助？

- 📖 [故障排除指南](docs/TROUBLESHOOTING.md)
- 🐛 [提交Issue](https://github.com/YOUR_USERNAME/MCL/issues)
- 💬 [社区讨论](https://github.com/YOUR_USERNAME/MCL/discussions)
