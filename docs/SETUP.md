# 快速开始指南

## 开发环境设置

### 1. 克隆仓库

```bash
git clone https://github.com/Janson20/FMCL.git
cd FMCL
```

### 2. 安装依赖

#### Python 依赖
```bash
# 使用 pip
pip install -r requirements.txt

# 或使用 uv (推荐)
uv sync
```

#### Node.js 依赖 (用于 Git hooks)
```bash
npm install
npm run prepare
```

### 3. 运行程序

```bash
python main.py
```

## 发布流程

### 自动发布 (推荐)

使用发布脚本自动化版本更新和发布：

```bash
# 创建新版本
python scripts/release.py 2.0.1

# 推送变更和tag
git push origin main
git push origin v2.0.1
```

### 手动发布

1. 更新版本号
   - 修改 `pyproject.toml` 中的版本
   - 修改 `package.json` 中的版本

2. 提交并打标签
   ```bash
   git add .
   git commit -m "chore: release v2.0.1"
   git tag v2.0.1
   git push origin main
   git push origin v2.0.1
   ```

3. GitHub Actions 会自动：
   - 构建 Windows/macOS/Linux 的 AMD64 和 ARM64 版本
   - 生成更新日志
   - 创建 Release

## 本地构建

### 构建可执行文件

```bash
# 安装 PyInstaller
pip install pyinstaller

# 构建
pyinstaller build.spec --noconfirm
```

构建产物位于 `dist/` 目录。

### 使用 Makefile

```bash
# 查看所有可用命令
make help

# 安装依赖
make install

# 构建
make build

# 清理
make clean

# 创建发布
make release VERSION=2.0.1
```

## Docker 支持

### 构建镜像

```bash
docker build -t fmcl:latest .
```

### 运行容器

```bash
docker run -it --rm \
  -v $(pwd)/.minecraft:/app/.minecraft \
  fmcl:latest
```

## 提交规范

本项目使用约定式提交规范。请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

示例：
```bash
feat: 添加自动更新功能
fix(download): 修复大文件下载失败问题
docs: 更新安装文档
refactor: 重构启动器核心逻辑
```

## 故障排除

### 导入错误

如果遇到 `ModuleNotFoundError`，请确保已安装所有依赖：

```bash
pip install -r requirements.txt
```

### 权限错误

在 Linux/macOS 上，可能需要添加执行权限：

```bash
chmod +x dist/FMCL
```

### Windows Defender 误报

Windows 可能会误报可执行文件为病毒。这是 PyInstaller 打包的常见问题。解决方法：

1. 添加到排除列表
2. 使用代码签名证书（推荐）

## 获取帮助

- 📖 [文档](README.md)
- 🐛 [问题反馈](https://github.com/Janson20/FMCL/issues)
- 💬 [讨论区](https://github.com/Janson20/FMCL/discussions)
