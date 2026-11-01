"""版本号（唯一来源）。

由 ``scripts/release.py`` 自动更新；发布流水线在打包前也会依据 git tag
覆写一次，保证产物里的版本与 tag 一致。不要手工编辑。
"""

APP_VERSION = "1.0.9"
