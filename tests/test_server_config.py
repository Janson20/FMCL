"""服务器配置编辑器单元测试（server.properties 读写 + 配置项元数据）

覆盖：
- 四个语言文件的键完全一致
- 配置项元数据的 i18n 键都存在、没有多余的键
- server.properties 读写的注释保留、未知键保留、原地更新与追加
- 每个服务器独立的内存设置（.fmcl_server.json）与 eula.txt
- 缺失文件时按模板创建
"""

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from launcher import server_config as sc
from ui import server_config_schema as schema

LOCALES_DIR = Path(__file__).parent.parent / "ui" / "locales"


def _load_locales():
    return {f.stem: json.load(f.open(encoding="utf-8")) for f in LOCALES_DIR.glob("*.json")}


def _schema_i18n_keys():
    """配置项元数据会用到的所有 i18n 键"""
    keys = set()
    for option in schema.ALL_OPTIONS:
        keys.add(option["i18n"] + "_n")
        keys.add(option["i18n"] + "_d")
        for _raw_value, i18n_key in option.get("values") or []:
            if i18n_key:
                keys.add(i18n_key)
    for category in schema.CATEGORIES:
        keys.add(category["i18n"])
    return keys


def test_locales_have_identical_keys():
    locales = _load_locales()
    assert set(locales) == {"zh_CN", "en_US", "ja_JP", "zh_TW"}
    base = set(locales["zh_CN"])
    for name, data in locales.items():
        assert set(data) == base, f"{name} 与 zh_CN 的键不一致: {sorted(base ^ set(data))[:5]}"


def test_schema_locale_coverage():
    locales = _load_locales()
    zh = locales["zh_CN"]

    missing = sorted(k for k in _schema_i18n_keys() if k not in zh)
    assert not missing, f"缺少翻译: {missing[:5]}"

    # 代码里直接写死的 _("...") 字面量也算被使用
    used = _schema_i18n_keys()
    for src in ["ui/windows/server_config_editor.py", "ui/app_server.py"]:
        text = (Path(__file__).parent.parent / src).read_text(encoding="utf-8")
        used |= set(re.findall(r"_\(\s*[\"']([A-Za-z0-9_]+)[\"']", text))

    unused = sorted(
        k for k in zh if k.startswith(("scfg_", "server_config_")) and k not in used
    )
    assert not unused, f"未被使用的翻译键: {unused[:8]}"

    referenced = sorted(k for k in used if k.startswith(("scfg_", "server_config_")) and k not in zh)
    assert not referenced, f"代码引用了不存在的键: {referenced[:8]}"


def test_schema_integrity():
    category_ids = [c["id"] for c in schema.CATEGORIES]
    assert len(category_ids) == len(set(category_ids))

    keys = [o["key"] for o in schema.ALL_OPTIONS]
    assert len(keys) == len(set(keys)), "配置项键重复"

    for option in schema.ALL_OPTIONS:
        assert option["category"] in category_ids, option["key"]
        assert option["kind"] in (schema.BOOL, schema.INT, schema.STR, schema.ENUM, schema.MEMORY, schema.INFO)
        if option["kind"] == schema.ENUM:
            assert option["values"], f"{option['key']} 缺少可选值"
        if option["kind"] in (schema.INT, schema.BOOL, schema.STR, schema.ENUM):
            assert option["key"] in sc.DEFAULT_SERVER_PROPERTIES, f"{option['key']} 不在默认模板中"


def _make_server(tmp: Path) -> Path:
    server_dir = tmp / "1.21.4"
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "server.properties").write_text(
        "#Minecraft server properties\n"
        "# 这是注释\n"
        "motd=Hello\n"
        "max-players=20\n"
        "unknown-key=keep-me\n"
        "level-type=minecraft\\:normal\n",
        encoding="utf-8",
    )
    return server_dir


def test_server_properties_read_write():
    tmp = Path(tempfile.mkdtemp(prefix="fmcl_cfg_"))
    try:
        server_dir = _make_server(tmp)

        values = sc.read_server_properties(server_dir)
        assert values["motd"] == "Hello"
        assert values["unknown-key"] == "keep-me"
        assert values["level-type"] == "minecraft\\:normal"

        values["motd"] = "New MOTD"
        values["difficulty"] = "hard"
        ok, err = sc.write_server_properties(server_dir, values)
        assert ok, err

        text = (server_dir / "server.properties").read_text(encoding="utf-8")
        assert "# 这是注释" in text, "注释被破坏"
        assert "unknown-key=keep-me" in text, "未知键被删除"
        assert "motd=New MOTD" in text, "已有键未更新"
        assert "max-players=20" in text
        assert "difficulty=hard" in text, "新键未追加"
        assert sc.read_server_properties(server_dir)["difficulty"] == "hard"

        # 换行等非法字符不会破坏文件结构
        ok, err = sc.write_server_properties(server_dir, {"motd": "line1\nline2"})
        assert ok, err
        assert sc.read_server_properties(server_dir)["motd"] == "line1 line2"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_server_properties_missing_file():
    tmp = Path(tempfile.mkdtemp(prefix="fmcl_cfg_"))
    try:
        server_dir = tmp / "fresh"
        server_dir.mkdir()

        assert sc.read_server_properties(server_dir) == {}

        ok, err = sc.write_server_properties(server_dir, sc.DEFAULT_SERVER_PROPERTIES)
        assert ok, err
        assert (server_dir / "server.properties").exists()
        assert sc.read_server_properties(server_dir) == sc.DEFAULT_SERVER_PROPERTIES
        assert all(k in sc.DEFAULT_SERVER_PROPERTIES for k in schema.PROPERTY_KEYS)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_per_server_memory_and_eula():
    tmp = Path(tempfile.mkdtemp(prefix="fmcl_cfg_"))
    try:
        server_dir = tmp / "1.21.4"
        server_dir.mkdir()

        assert sc.get_server_launch_memory(server_dir) is None

        ok, err = sc.set_server_launch_memory(server_dir, "6G")
        assert ok, err
        assert sc.get_server_launch_memory(server_dir) == "6G"
        assert sc.get_launch_config_path(server_dir).exists()
        assert sc.read_launch_config(server_dir)["max_memory"] == "6G"

        ok, err = sc.set_server_launch_memory(server_dir, None)
        assert ok, err
        assert sc.get_server_launch_memory(server_dir) is None

        assert sc.read_eula(server_dir) is False
        ok, err = sc.write_eula(server_dir, True)
        assert ok, err
        assert sc.read_eula(server_dir) is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_config_editor_importable():
    from ui.windows.server_config_editor import ServerConfigEditorWindow

    assert issubclass(ServerConfigEditorWindow, object)
