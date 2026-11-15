"""mrpack 自定义实例名的落盘回归（2026-10-23 用户需求）。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import minecraft_launcher_lib
from launcher.mrpack import MrpackMixin


class DummyMrpack(MrpackMixin):
    def __init__(self, minecraft_dir: Path) -> None:
        self.minecraft_dir = str(minecraft_dir)
        self._mcllib = minecraft_launcher_lib
        self._mp_progress = {}

    def _get_callback(self):
        return {}

    def _download_mrpack_files_parallel(self, *args, **kwargs):
        return []

    def _resolve_java_for_version(self, version_id):
        return "java"

    def _check_and_warn_missing_java(self, version_id, java_path):
        return None


def test_install_mrpack_custom_name_creates_own_version(tmp_path, monkeypatch) -> None:
    mc_dir = tmp_path / "mc"
    pack = tmp_path / "demo.mrpack"
    with zipfile.ZipFile(pack, "w") as zf:
        zf.writestr(
            "modrinth.index.json",
            json.dumps(
                {
                    "formatVersion": 1,
                    "name": "Demo Pack",
                    "versionId": "1.0",
                    "dependencies": {"minecraft": "1.20.1"},
                    "files": [],
                }
            ),
        )

    def fake_install(version, directory, callback=None):
        target = Path(directory) / "versions" / version
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{version}.json").write_text(
            json.dumps({"id": version, "jar": version, "libraries": []}), encoding="utf-8"
        )
        (target / f"{version}.jar").write_bytes(b"jar")

    monkeypatch.setattr(minecraft_launcher_lib.install, "install_minecraft_version", fake_install)
    dummy = DummyMrpack(mc_dir)

    ok, result = dummy.install_mrpack(str(pack), instance_name="My Pack")

    assert ok, result
    assert result == "My Pack"
    target = mc_dir / "versions" / "My Pack"
    assert (target / "My Pack.json").is_file()
    data = json.loads((target / "My Pack.json").read_text(encoding="utf-8"))
    assert data["id"] == "My Pack"
    #: `jar` 是**版本 ID**（classpath 拼的是 versions/<jar>/<jar>.jar），不是文件名。
    #: 旧实现写 `jar: "My Pack.jar"`，classpath 于是指到 `versions/My Pack.jar/` ——
    #: 那个不存在的版本，同时还会在 versions/ 下留一个空壳目录（版本列表多一行 error）。
    assert data["jar"] == "1.20.1", "jar 必须指向真实存在的版本，而不是一个带 .jar 后缀的假版本"
    assert (mc_dir / "versions" / "1.20.1" / "1.20.1.jar").is_file(), "jar 指向的文件必须真的在"
    assert not (mc_dir / "versions" / "My Pack.jar").exists(), "不许再产生幽灵版本目录"
    #: 实例目录里的 jar 只在**来源目录那份不可用**时才复制；这里来源（原版 1.20.1）的
    #: jar 就在原地，没必要再抄一份 20MB 到实例目录。
    assert not (target / "My Pack.jar").exists()
