"""updater 模块测试（全部离线）"""


def _asset(name: str) -> dict:
    return {"name": name, "browser_download_url": f"https://example.com/{name}", "size": 1}


def _patch_env(monkeypatch, is_x86: bool):
    monkeypatch.setattr("updater.platform.system", lambda: "Windows")
    monkeypatch.setattr("updater._is_x86_process", lambda: is_x86)


def test_find_suitable_asset_x64_defaults_without_dotnetsdk(monkeypatch):
    """x64 进程默认选择 without-dotnetsdk 精简包"""
    from updater import find_suitable_asset

    _patch_env(monkeypatch, is_x86=False)
    assets = [
        _asset("FMCL-Setup-2.13.0.exe"),
        _asset("FMCL-Setup-2.13.0-x86.exe"),
        _asset("FMCL-Setup-2.13.0-without-dotnetsdk.exe"),
    ]
    assert find_suitable_asset(assets)["name"] == "FMCL-Setup-2.13.0-without-dotnetsdk.exe"


def test_find_suitable_asset_x64_fallback(monkeypatch):
    """无精简版时 x64 退回普通安装包（绝不含 x86 包）"""
    from updater import find_suitable_asset

    _patch_env(monkeypatch, is_x86=False)
    assets = [_asset("FMCL-Setup-2.13.0.exe"), _asset("FMCL-Setup-2.13.0-x86.exe")]
    assert find_suitable_asset(assets)["name"] == "FMCL-Setup-2.13.0.exe"


def test_find_suitable_asset_x86_process(monkeypatch):
    """x86 进程优先选择 x86 安装包（内含 x86 运行时）"""
    from updater import find_suitable_asset

    _patch_env(monkeypatch, is_x86=True)
    assets = [
        _asset("FMCL-Setup-2.13.0.exe"),
        _asset("FMCL-Setup-2.13.0-without-dotnetsdk.exe"),
        _asset("FMCL-Setup-2.13.0-x86.exe"),
    ]
    assert find_suitable_asset(assets)["name"] == "FMCL-Setup-2.13.0-x86.exe"


def test_find_suitable_asset_non_windows(monkeypatch):
    """非 Windows 平台不提供安装包"""
    from updater import find_suitable_asset

    monkeypatch.setattr("updater.platform.system", lambda: "Linux")
    assert find_suitable_asset([_asset("FMCL-Setup-2.13.0.exe")]) is None


def test_is_x86_process(monkeypatch):
    """32 位进程识别：依赖 PROCESSOR_ARCHITECTURE（platform.machine 会误报）"""
    from updater import _is_x86_process

    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "x86")
    assert _is_x86_process() is True
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    assert _is_x86_process() is False
    monkeypatch.delenv("PROCESSOR_ARCHITECTURE")
    assert _is_x86_process() is False
