"""存档备份服务的单元测试（阶段 1 任务 1.10 的验收补强）。

为什么要补：39 功能域走查表里「存档备份」那一行的"相关测试"是空的 ——
`services/backup_manager.py` 633 行、17 个方法，一个测试都没有。
备份/恢复也是**最容易造成不可逆数据损失**的一类代码（它自己会 `rmtree`、会移动目录），
所以它比别处更值得有测试。

全部离线：只用 `tmp_path` 造存档目录与假 config；不联网、不碰真实的 `.minecraft`。
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest

from services.backup_manager import (
    BackupEntry,
    BackupIndex,
    BackupManager,
    _format_size,
    _sanitize_filename,
)


class _FakeConfig:
    """只看备份逻辑用到的那几个字段。"""

    def __init__(self, base_dir: Path, mc_dir: Path, **kw) -> None:
        self.base_dir = base_dir
        self.minecraft_dir = mc_dir
        self.backup_dir = kw.pop("backup_dir", "")
        self.backup_compress_level = kw.pop("backup_compress_level", 6)
        self.backup_max_per_world = kw.pop("backup_max_per_world", 10)
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture()
def env(tmp_path: Path):
    """返回 (manager, mc_dir, backup_root)。"""
    mc = tmp_path / ".minecraft"
    (mc / "saves").mkdir(parents=True)
    cfg = _FakeConfig(tmp_path, mc, backup_dir=str(tmp_path / "bk"))
    return BackupManager(cfg), mc, tmp_path / "bk"


def _make_world(mc_dir: Path, name: str, files: dict[str, bytes] | None = None, isolated: str | None = None) -> Path:
    """造一个带 level.dat 的存档目录（可放在版本隔离目录下）。"""
    root = mc_dir / "versions" / isolated / "saves" if isolated else mc_dir / "saves"
    world = root / name
    world.mkdir(parents=True, exist_ok=True)
    (world / "level.dat").write_bytes(b"LEVELDATA")
    for rel, data in (files or {"region/r.0.0.mca": b"x" * 100}).items():
        p = world / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return world


def _mod_loader_version(mc_dir: Path, version: str) -> None:
    """写一个"装了 Forge"的版本 JSON，让 `_is_isolated_version` 判为真。"""
    vdir = mc_dir / "versions" / version
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{version}.json").write_text(
        json.dumps({"id": version, "libraries": [{"name": "net.minecraftforge:forge:1.20.4-49.0.26"}]}),
        encoding="utf-8",
    )


# ── 模块级纯函数 ──────────────────────────────────────────────


class TestSanitizeFilename:
    def test_replaces_illegal_characters(self) -> None:
        assert _sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"

    def test_keeps_normal_names_and_unicode(self) -> None:
        assert _sanitize_filename("我的世界 world-1") == "我的世界 world-1"

    def test_handles_empty(self) -> None:
        assert _sanitize_filename("") == ""


class TestFormatSize:
    @pytest.mark.parametrize(
        "value,expected",
        [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (1024**2, "1.0 MB"), (1024**3, "1.0 GB")],
    )
    def test_boundaries(self, value: int, expected: str) -> None:
        assert _format_size(value) == expected


class TestBackupEntry:
    def test_defaults_and_round_trip(self) -> None:
        e = BackupEntry({"world_name": "w", "timestamp": "2026-01-02T03:04:05+00:00", "size_bytes": 2048})
        assert e.id and e.file_name == "" and e.note == ""
        assert e.to_dict()["world_name"] == "w"
        assert BackupEntry(e.to_dict()).to_dict() == e.to_dict()

    def test_timestamp_dt_parses_or_falls_back(self) -> None:
        good = BackupEntry({"timestamp": "2026-01-02T03:04:05+00:00"})
        assert good.timestamp_dt.year == 2026
        bad = BackupEntry({"timestamp": "not-a-time"})
        assert bad.timestamp_dt is not None  # 回退成 now，不抛异常

    def test_size_display(self) -> None:
        assert BackupEntry({"size_bytes": 512}).size_display == "512 B"
        assert BackupEntry({"size_bytes": 2048}).size_display == "2.0 KB"


class TestBackupIndex:
    def test_loads_missing_corrupt_and_valid(self, tmp_path: Path) -> None:
        p = tmp_path / "idx.json"
        assert BackupIndex(p).entries == []          # 文件不存在

        p.write_text("{ 这不是 JSON", encoding="utf-8")
        assert BackupIndex(p).entries == []          # 坏 JSON 不抛异常

        p.write_text(json.dumps({"backups": [{"id": "a", "world_name": "w"}]}), encoding="utf-8")
        entries = BackupIndex(p).entries
        assert len(entries) == 1 and entries[0].id == "a"

    def test_add_remove_find_and_persist(self, tmp_path: Path) -> None:
        p = tmp_path / "idx.json"
        idx = BackupIndex(p)
        idx.add(BackupEntry({"id": "a", "world_name": "w1"}))
        idx.add(BackupEntry({"id": "b", "world_name": "w2"}))
        assert p.exists(), "add 之后必须落盘"
        assert [e.id for e in BackupIndex(p).entries] == ["a", "b"]
        assert [e.id for e in idx.find_by_world("w1")] == ["a"]
        idx.remove("a")
        assert [e.id for e in BackupIndex(p).entries] == ["b"]


# ── 目录定位 ──────────────────────────────────────────────────


class TestWorldDiscovery:
    def test_find_world_prefers_global_then_isolated(self, env) -> None:
        _make_world(env[1], "global-w")
        _make_world(env[1], "iso-w", isolated="1.20.4-forge-49.0.26")
        assert env[0]._find_world_dir("global-w") == env[1] / "saves" / "global-w"
        assert env[0]._find_world_dir("iso-w") == env[1] / "versions" / "1.20.4-forge-49.0.26" / "saves" / "iso-w"

    def test_find_world_ignores_dirs_without_level_dat(self, env) -> None:
        (env[1] / "saves" / "broken").mkdir(parents=True)
        assert env[0]._find_world_dir("broken") is None
        assert env[0]._find_world_dir("nope") is None

    def test_find_all_dedupes_and_sorts(self, env) -> None:
        a = _make_world(env[1], "same")
        b = _make_world(env[1], "same", isolated="1.20.4-forge-49.0.26")  # 同名但隔离
        older = _make_world(env[1], "older")
        import os

        # 三个目录各自钉死 mtime（不钉的话"最后创建的"必然最新，排序断言就没意义了）
        os.utime(b, (1_600_000_000, 1_600_000_000))
        os.utime(older, (1_650_000_000, 1_650_000_000))
        os.utime(a, (1_700_000_000, 1_700_000_000))
        found = {w["name"]: w for w in env[0]._find_all_world_dirs()}
        assert set(found) == {"same", "older"}, "同名存档只应出现一次"
        assert found["same"]["is_isolated"] is False, "同名时全局 saves 优先"
        names = [w["name"] for w in env[0]._find_all_world_dirs()]
        assert names == ["same", "older"], "按修改时间降序"

    def test_is_isolated_version_reads_version_json(self, env) -> None:
        _mod_loader_version(env[1], "1.20.4-forge-49.0.26")
        assert env[0]._is_isolated_version("1.20.4-forge-49.0.26") is True
        assert env[0]._is_isolated_version("1.20.4") is False

    def test_calc_dir_size_and_backup_root(self, env, tmp_path: Path) -> None:
        w = _make_world(env[1], "sized", files={"a.bin": b"x" * 10, "sub/b.bin": b"y" * 20})
        assert env[0]._calc_dir_size(w) == 9 + 10 + 20  # level.dat(9) + 10 + 20
        assert env[0]._calc_dir_size(tmp_path / "missing") == 0
        assert env[0].backup_root == tmp_path / "bk" and env[0].backup_root.exists()


# ── 备份 / 恢复 / 校验 / 导出 ─────────────────────────────────


class TestBackupCreate:
    def test_missing_world(self, env) -> None:
        ok, msg = env[0].create_backup("nope")
        assert ok is False and "未找到存档" in msg

    def test_success_writes_zip_index_and_progress(self, env) -> None:
        _make_world(env[1], "w")
        seen: list[tuple] = []
        ok, msg = env[0].create_backup("w", note="首备", progress_callback=lambda *a: seen.append(a))
        assert ok is True and "备份成功" in msg
        backups = env[0].get_backups("w")
        assert len(backups) == 1
        e = backups[0]
        assert e.world_name == "w" and e.note == "首备"
        zip_path = env[2] / "w" / e.file_name
        assert zip_path.exists() and zip_path.stat().st_size == e.size_bytes
        assert (env[2] / "w_index.json").exists(), "索引必须落盘"
        with zipfile.ZipFile(zip_path) as zf:
            assert "level.dat" in zf.namelist()
        assert seen[0][2].startswith("正在备份") and seen[-1][2] == "备份完成"

    def test_game_version_inferred_from_isolated_path(self, env) -> None:
        _make_world(env[1], "iso", isolated="1.20.4-forge-49.0.26")
        assert env[0].create_backup("iso")[0] is True
        assert env[0].get_backups("iso")[0].game_version == "1.20.4-forge-49.0.26"

    def test_disk_space_failure_is_reported(self, env, monkeypatch) -> None:
        _make_world(env[1], "w")
        monkeypatch.setattr(
            BackupManager, "_check_disk_space", lambda self, need, target: (False, "磁盘空间不足，需要 1.0 GB")
        )
        ok, msg = env[0].create_backup("w")
        assert ok is False and "磁盘空间不足" in msg

    def test_check_disk_space_branches(self, env) -> None:
        ok, msg = env[0]._check_disk_space(0, env[2])
        assert ok is True and msg == ""
        ok, msg = env[0]._check_disk_space(10**18, env[2])
        assert ok is False and "磁盘空间不足" in msg


class TestBackupRestore:
    def _backup(self, env, world: str = "w") -> str:
        _make_world(env[1], world, files={"region/r.mca": b"ORIGINAL"})
        assert env[0].create_backup(world)[0] is True
        return env[0].get_backups(world)[0].id

    def test_unknown_entry_and_missing_file(self, env) -> None:
        assert env[0].restore_backup("nope", "w") == (False, "未找到备份记录")
        entry_id = self._backup(env)
        (env[2] / "w" / env[0].get_backups("w")[0].file_name).unlink()
        ok, msg = env[0].restore_backup(entry_id, "w")
        assert ok is False and "备份文件不存在" in msg

    def test_corrupt_zip_is_rejected(self, env) -> None:
        entry_id = self._backup(env)
        (env[2] / "w" / env[0].get_backups("w")[0].file_name).write_bytes(b"not a zip")
        ok, msg = env[0].restore_backup(entry_id, "w")
        assert ok is False and "不是有效的 ZIP" in msg

    def test_restore_replaces_world_and_keeps_a_safety_copy(self, env) -> None:
        entry_id = self._backup(env)
        world = env[1] / "saves" / "w"
        (world / "region" / "r.mca").write_bytes(b"CHANGED-LATER")

        ok, msg = env[0].restore_backup(entry_id, "w")
        assert ok is True and "恢复成功" in msg
        assert (world / "region" / "r.mca").read_bytes() == b"ORIGINAL", "必须恢复成备份里的内容"
        baks = list((env[1] / "saves").glob("w_bak_*"))
        assert baks and (baks[0] / "region" / "r.mca").read_bytes() == b"CHANGED-LATER", (
            "恢复前的存档必须被改名保留，而不是删掉"
        )

    def test_restore_rolls_back_when_backup_lacks_level_dat(self, env) -> None:
        entry_id = self._backup(env)
        world = env[1] / "saves" / "w"
        # 把一个**没有 level.dat**的 zip 冒充备份
        bogus = env[2] / "w" / env[0].get_backups("w")[0].file_name
        with zipfile.ZipFile(bogus, "w") as zf:
            zf.writestr("region/r.mca", "NO-LEVEL")

        ok, msg = env[0].restore_backup(entry_id, "w")
        assert ok is False and "已回滚" in msg
        assert (world / "level.dat").exists() and (world / "region" / "r.mca").read_bytes() == b"ORIGINAL"

    def test_restore_into_isolated_dir_for_mod_loader_backup(self, env) -> None:
        _mod_loader_version(env[1], "1.20.4-forge-49.0.26")
        entry_id = self._backup(env, world="iso")
        # 备份时 game_version 为空（全局目录）→ 恢复到全局；再手动改成隔离版本走另一分支
        entry = env[0].get_backups("iso")[0]
        entry.game_version = "1.20.4-forge-49.0.26"
        env[0]._get_index("iso")._save()
        assert env[0].restore_backup(entry_id, "iso")[0] is True
        assert (env[1] / "versions" / "1.20.4-forge-49.0.26" / "saves" / "iso" / "level.dat").exists()


class TestDeleteVerifyExport:
    def test_delete_removes_file_and_entry(self, env) -> None:
        _make_world(env[1], "w")
        env[0].create_backup("w")
        e = env[0].get_backups("w")[0]
        zip_path = env[2] / "w" / e.file_name
        assert env[0].delete_backup("nope", "w") == (False, "未找到备份记录")
        ok, msg = env[0].delete_backup(e.id, "w")
        assert ok is True and not zip_path.exists() and env[0].get_backups("w") == []

    def test_verify_reports_each_failure_mode(self, env) -> None:
        _make_world(env[1], "w")
        env[0].create_backup("w")
        e = env[0].get_backups("w")[0]
        assert env[0].verify_backup("w", e.id) == (True, "备份完整")
        assert env[0].verify_backup("w", "nope") == (False, "未找到备份记录")

        zip_path = env[2] / "w" / e.file_name
        zip_path.write_bytes(b"junk")
        assert env[0].verify_backup("w", e.id) == (False, "不是有效的 ZIP 文件")

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("region/r.mca", "no level")
        ok, msg = env[0].verify_backup("w", e.id)
        assert ok is False and "缺少 level.dat" in msg

    def test_export_copies_the_archive(self, env, tmp_path: Path) -> None:
        _make_world(env[1], "w")
        env[0].create_backup("w")
        e = env[0].get_backups("w")[0]
        dest = tmp_path / "导出.zip"
        ok, msg = env[0].export_backup(e.id, "w", str(dest))
        assert ok is True and dest.read_bytes() == (env[2] / "w" / e.file_name).read_bytes()
        assert env[0].export_backup("nope", "w", str(dest))[0] is False


class TestAutoCleanupAndOrdering:
    def test_same_second_backups_do_not_overwrite_each_other(self, env) -> None:
        """D-112 回归守卫：**同一秒内的两次备份必须是两个文件**。

        修之前：文件名只有秒级时间戳，同秒第二次备份会 `unlink` 覆盖掉第一个文件，
        而索引里留下两条指向同一文件的记录 —— 删一条会连带删掉另一条的备份，
        自动清理还会把仅存的那个文件删掉。这条测试就是钉住这个行为不再回来。
        """
        _make_world(env[1], "w")
        assert env[0].create_backup("w", note="a")[0] is True
        assert env[0].create_backup("w", note="b")[0] is True
        backups = env[0].get_backups("w")
        assert len(backups) == 2
        names = {e.file_name for e in backups}
        assert len(names) == 2, f"两条备份记录指向了同一个文件: {names}"
        zips = sorted(p.name for p in (env[2] / "w").glob("*.zip"))
        assert zips == sorted(names), "索引里的文件名必须与磁盘上的文件一一对应"
        for e in backups:
            assert (env[2] / "w" / e.file_name).exists(), f"{e.file_name} 在磁盘上不存在（幽灵记录）"

    def test_deleting_one_of_two_same_second_backups_keeps_the_other(self, env) -> None:
        """同秒两条备份：删掉一条，另一条的文件必须还在（这是 D-112 的用户可见后果）。"""
        _make_world(env[1], "w")
        env[0].create_backup("w", note="a")
        env[0].create_backup("w", note="b")
        first, second = env[0].get_backups("w")
        assert env[0].delete_backup(first.id, "w")[0] is True
        assert (env[2] / "w" / second.file_name).exists(), "删一条把另一条的文件也删了"
        assert env[0].verify_backup("w", second.id) == (True, "备份完整")

    def test_keeps_only_newest_n(self, env) -> None:
        cfg = env[0].config
        cfg.backup_max_per_world = 2
        _make_world(env[1], "w")
        ids = []
        for i in range(4):
            assert env[0].create_backup("w", note=f"n{i}")[0] is True
            ids.append(env[0].get_backups("w")[0].id)
            time.sleep(0.01)  # 让 timestamp 单调，排序才确定
        backups = env[0].get_backups("w")
        assert len(backups) == 2, "超出上限的旧备份必须被自动清理"
        assert [e.note for e in backups] == ["n3", "n2"], "保留的是最新的两条（时间降序）"
        files = sorted(p.name for p in (env[2] / "w").glob("*.zip"))
        assert len(files) == 2, "被清理的备份文件也要真的删掉"

    def test_zero_means_disabled(self, env) -> None:
        env[0].config.backup_max_per_world = 0
        _make_world(env[1], "w")
        for i in range(3):
            env[0].create_backup("w", note=f"n{i}")
            time.sleep(0.01)
        assert len(env[0].get_backups("w")) == 3

    def test_get_backups_sorted_desc(self, env) -> None:
        _make_world(env[1], "w")
        idx = env[0]._get_index("w")
        for ts in ("2026-01-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"):
            idx.add(BackupEntry({"id": ts, "world_name": "w", "timestamp": ts}))
        assert [e.id for e in env[0].get_backups("w")] == [
            "2026-03-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ]


def test_service_module_has_no_ui_dependency() -> None:
    """backup_manager 属服务层：不得 import 任何 GUI 栈（与分层闸门同一判据）。"""
    import ast
    import io

    src = io.open(Path(__file__).resolve().parent.parent / "services" / "backup_manager.py", encoding="utf-8").read()
    banned = {"tkinter", "customtkinter", "PySide6", "shiboken6", "ui"}
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in banned]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in banned:
            bad.append(node.module or "")
    assert not bad, f"services/backup_manager.py 引入了界面依赖: {bad}"
