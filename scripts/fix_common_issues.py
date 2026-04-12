#!/usr/bin/env python3
"""
快速修复脚本 - 解决常见构建和运行问题
"""
import os
import subprocess
import sys
from pathlib import Path


def run_command(cmd, description):
    """运行命令并显示结果"""
    print(f"\n🔧 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ {description}成功")
            if result.stdout:
                print(result.stdout)
            return True
        else:
            print(f"❌ {description}失败")
            if result.stderr:
                print(result.stderr)
            return False
    except Exception as e:
        print(f"❌ 执行出错: {str(e)}")
        return False


def fix_permissions():
    """修复权限问题"""
    print("\n📁 修复文件权限...")
    
    # Linux/macOS
    if sys.platform != 'win32':
        run_command("chmod +x scripts/*.py", "设置脚本执行权限")
        if Path("dist/MCL").exists():
            run_command("chmod +x dist/MCL", "设置可执行文件权限")
    
    # 修复.minecraft目录权限
    minecraft_dir = Path(".minecraft")
    if minecraft_dir.exists():
        if sys.platform != 'win32':
            run_command("chmod -R 755 .minecraft", "修复.minecraft目录权限")


def clean_build():
    """清理构建文件"""
    print("\n🧹 清理构建文件...")
    
    dirs_to_clean = ['build', 'dist', '__pycache__', '.pytest_cache', '.mypy_cache']
    files_to_clean = ['*.spec', '*.pyc', '*.log']
    
    for dir_name in dirs_to_clean:
        dir_path = Path(dir_name)
        if dir_path.exists():
            run_command(f"rm -rf {dir_name}", f"删除 {dir_name}")
    
    for pattern in files_to_clean:
        run_command(f"find . -name '{pattern}' -delete", f"删除 {pattern} 文件")


def reinstall_dependencies():
    """重新安装依赖"""
    print("\n📦 重新安装依赖...")
    
    # 卸载现有依赖
    run_command("pip uninstall -y -r requirements.txt", "卸载现有依赖")
    
    # 清理缓存
    run_command("pip cache purge", "清理pip缓存")
    
    # 重新安装
    run_command("pip install --upgrade pip setuptools wheel", "升级pip和setuptools")
    run_command("pip install -r requirements.txt", "安装项目依赖")


def fix_macos_issues():
    """修复macOS特定问题"""
    if sys.platform != 'darwin':
        return
    
    print("\n🍎 修复macOS问题...")
    
    # 移除隔离属性
    if Path("dist/MCL.app").exists():
        run_command("xattr -cr dist/MCL.app", "移除应用隔离属性")
    
    # 安装Xcode命令行工具
    run_command("xcode-select --install", "安装Xcode命令行工具")


def fix_linux_issues():
    """修复Linux特定问题"""
    if sys.platform != 'linux':
        return
    
    print("\n🐧 修复Linux问题...")
    
    # 检测包管理器并安装依赖
    if Path("/usr/bin/apt-get").exists():
        run_command(
            "sudo apt-get update && sudo apt-get install -y "
            "build-essential zlib1g-dev libncurses5-dev libgdbm-dev "
            "libnss3-dev libssl-dev libreadline-dev libffi-dev "
            "libsqlite3-dev libbz2-dev liblzma-dev tk-dev uuid-dev",
            "安装系统依赖 (Debian/Ubuntu)"
        )
    elif Path("/usr/bin/dnf").exists():
        run_command(
            "sudo dnf groupinstall -y 'Development Tools' && "
            "sudo dnf install -y python3-devel tk-devel",
            "安装系统依赖 (Fedora/RHEL)"
        )


def check_java():
    """检查Java安装"""
    print("\n☕ 检查Java环境...")
    result = subprocess.run("java -version", shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Java已安装")
        print(result.stderr)
    else:
        print("❌ Java未安装")
        print("请安装Java 17或更高版本")
        print("下载地址: https://adoptium.net/")


def main():
    """主函数"""
    print("=" * 60)
    print("MCL 快速修复工具")
    print("=" * 60)
    
    print("\n可用选项:")
    print("1. 修复权限问题")
    print("2. 清理构建文件")
    print("3. 重新安装依赖")
    print("4. 修复macOS问题")
    print("5. 修复Linux问题")
    print("6. 检查Java环境")
    print("7. 全部执行")
    print("0. 退出")
    
    while True:
        choice = input("\n请选择操作 (0-7): ").strip()
        
        if choice == '0':
            print("\n👋 退出")
            break
        elif choice == '1':
            fix_permissions()
        elif choice == '2':
            clean_build()
        elif choice == '3':
            reinstall_dependencies()
        elif choice == '4':
            fix_macos_issues()
        elif choice == '5':
            fix_linux_issues()
        elif choice == '6':
            check_java()
        elif choice == '7':
            print("\n🔧 执行所有修复...")
            fix_permissions()
            clean_build()
            reinstall_dependencies()
            fix_macos_issues()
            fix_linux_issues()
            check_java()
        else:
            print("❌ 无效选择，请重试")
        
        if choice in ['1', '2', '3', '4', '5', '6', '7']:
            print("\n✅ 操作完成")
    
    print("\n" + "=" * 60)
    print("修复完成！如有问题，请查看 docs/TROUBLESHOOTING.md")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 操作已取消")
        sys.exit(0)
