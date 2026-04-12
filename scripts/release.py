#!/usr/bin/env python3
"""
版本发布脚本
自动更新版本号并创建git tag
"""
import re
import sys
from pathlib import Path
import subprocess


def get_current_version():
    """从pyproject.toml获取当前版本"""
    pyproject_path = Path("pyproject.toml")
    content = pyproject_path.read_text(encoding='utf-8')
    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise ValueError("无法在pyproject.toml中找到版本号")
    return match.group(1)


def update_version(new_version):
    """更新所有文件中的版本号"""
    # (文件路径, 正则模式, 替换模板)
    files = [
        ("pyproject.toml", r'version\s*=\s*["\']([^"\']+)["\']', 'version = "{v}"'),
        ("package.json", r'"version":\s*"([^"]+)"', '"version": "{v}"'),
        ("launcher.py", r'(self\.options\["launcherVersion"\]\s*=\s*)"[^"]*"', r'\1"{v}"'),
    ]
    
    for file_path, pattern, replacement in files:
        path = Path(file_path)
        if not path.exists():
            continue
            
        content = path.read_text(encoding='utf-8')
        new_content = re.sub(pattern, replacement.format(v=new_version), content)
        if new_content != content:
            path.write_text(new_content, encoding='utf-8')
            print(f"✅ 已更新 {file_path}")
        else:
            print(f"⏭️  {file_path} 无需更新")


def main():
    if len(sys.argv) < 2:
        print("使用方法: python scripts/release.py <new_version>")
        print("示例: python scripts/release.py 2.0.1")
        sys.exit(1)
    
    new_version = sys.argv[1]
    
    # 验证版本号格式
    if not re.match(r'^\d+\.\d+\.\d+$', new_version):
        print(f"❌ 无效的版本号格式: {new_version}")
        print("版本号应为: major.minor.patch (例如: 2.0.1)")
        sys.exit(1)
    
    current_version = get_current_version()
    print(f"当前版本: {current_version}")
    print(f"目标版本: {new_version}")
    
    # 检查是否有未提交的变更
    result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
    if result.stdout.strip():
        print("❌ 存在未提交的变更，请先提交所有变更")
        sys.exit(1)
    
    # 更新版本号
    print("\n📝 更新版本号...")
    update_version(new_version)
    
    # 提交变更
    print("\n💾 提交变更...")
    subprocess.run(['git', 'add', '.'])
    subprocess.run(['git', 'commit', '-m', f'chore: release v{new_version}'])
    
    # 创建tag
    print(f"\n🏷️  创建tag v{new_version}...")
    subprocess.run(['git', 'tag', f'v{new_version}'], check=True)

    # 推送提交
    print("\n🚀 推送提交...")
    subprocess.run(['git', 'push', 'origin', 'main'], check=True)

    # 推送tag
    print(f"🚀 推送tag v{new_version}...")
    subprocess.run(['git', 'push', 'origin', f'v{new_version}'], check=True)

    print("\n✅ 发布完成！GitHub Actions 将自动构建并发布 Release。")


if __name__ == "__main__":
    main()
