# 约定式提交规范

本项目采用 [约定式提交](https://www.conventionalcommits.org/) 规范。

## 提交消息格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

## 类型 (type)

| 类型 | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: 添加自动更新功能` |
| `fix` | 修复bug | `fix: 修复下载进度显示错误` |
| `docs` | 文档更新 | `docs: 更新安装文档` |
| `style` | 代码格式（不影响功能） | `style: 格式化代码` |
| `refactor` | 重构代码 | `refactor: 重构下载模块` |
| `perf` | 性能优化 | `perf: 优化下载速度` |
| `test` | 测试相关 | `test: 添加单元测试` |
| `build` | 构建系统 | `build: 更新构建配置` |
| `ci` | CI配置 | `ci: 添加GitHub Actions` |
| `chore` | 其他变动 | `chore: 更新依赖` |
| `revert` | 回滚提交 | `revert: 回滚xxx提交` |

## 范围 (scope) - 可选

指定提交影响的范围，例如：
- `feat(download): 添加断点续传功能`
- `fix(ui): 修复版本选择器bug`

## 示例

### ✅ 好的提交

```bash
feat: 添加Forge自动安装功能
fix(download): 修复大文件下载失败问题
docs: 更新README文档
refactor: 重构启动器核心逻辑
perf: 优化内存使用
```

### ❌ 不好的提交

```bash
update code
fix bug
修改了一些东西
WIP
```

## 自动发布流程

当你推送一个以 `v` 开头的 tag 时，会自动触发构建和发布：

```bash
# 1. 更新版本号
# 修改 pyproject.toml 和 package.json 中的版本号

# 2. 提交变更
git add .
git commit -m "chore: release v2.0.1"

# 3. 创建并推送tag
git tag v2.0.1
git push origin main
git push origin v2.0.1
```

GitHub Actions 会自动：
1. 构建 Windows/macOS/Linux 的 AMD64 和 ARM64 版本
2. 根据提交历史生成更新日志
3. 创建 Release 并上传所有构建文件

## 安装 Git Hooks

确保你已经安装 Node.js，然后运行：

```bash
npm install
npm run prepare
```

这会安装 Husky，并设置 Git hooks 来验证提交消息。
