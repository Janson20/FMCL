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
    assert (target / "My Pack.jar").is_file()
    data = json.loads((target / "My Pack.json").read_text(encoding="utf-8"))
    assert data["id"] == "My Pack"
    assert data["jar"] == "My Pack.jar"
