"""存档备份管理模块

提供 Minecraft 存档的备份、恢复、删除等功能。
支持手动备份/恢复、自动备份（游戏启动前/退出后）、备份索引管理等。
"""
import json
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any, Tuple
from uuid import uuid4

# zipfile.ZIP_DEFLATE = 8，直接使用整数值避免某些环境导入异常
_ZIP_DEFLATE = getattr(zipfile, 'ZIP_DEFLATE', 8)

from logzero import logger

from config import Config, _json_loads, _json_dumps
from structured_logger import slog


class BackupEntry:
    """单条备份记录"""

    def __init__(self, data: Dict[str, Any]):
        self.id: str = data.get("id", str(uuid4()))
        self.world_name: str = data.get("world_name", "")
        self.timestamp: str = data.get("timestamp", "")
        self.size_bytes: int = data.get("size_bytes", 0)
        self.game_version: str = data.get("game_version", "")
        self.note: str = data.get("note", "")
        self.file_name: str = data.get("file_name", "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "world_name": self.world_name,
            "timestamp": self.timestamp,
            "size_bytes": self.size_bytes,
            "game_version": self.game_version,
            "note": self.note,
            "file_name": self.file_name,
        }

    @property
    def timestamp_dt(self) -> datetime:
        try:
            return datetime.fromisoformat(self.timestamp)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    @property
    def size_display(self) -> str:
        b = self.size_bytes
        if b >= 1024 ** 3:
            return f"{b / (1024 ** 3):.1f} GB"
        if b >= 1024 ** 2:
            return f"{b / (1024 ** 2):.1f} MB"
        if b >= 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b} B"


class BackupIndex:
    """备份索引文件管理"""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self._entries: List[BackupEntry] = []
        self._load()

    def _load(self):
        if self.index_path.exists():
            try:
                with open(self.index_path, "rb") as f:
                    data = _json_loads(f.read())
                self._entries = [BackupEntry(e) for e in data.get("backups", [])]
            except Exception as e:
                logger.error(f"加载备份索引失败: {e}")
                self._entries = []

    def _save(self):
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"backups": [e.to_dict() for e in self._entries]}
            with open(self.index_path, "w", encoding="utf-8") as f:
                f.write(_json_dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"保存备份索引失败: {e}")

    @property
    def entries(self) -> List[BackupEntry]:
        return list(self._entries)

    def add(self, entry: BackupEntry):
        self._entries.append(entry)
        self._save()

    def remove(self, entry_id: str):
        self._entries = [e for e in self._entries if e.id != entry_id]
        self._save()

    def find_by_world(self, world_name: str) -> List[BackupEntry]:
        return [e for e in self._entries if e.world_name == world_name]


class BackupManager:
    """存档备份管理器"""

    def __init__(self, config: Config):
        self.config = config
        self._backup_root: Optional[Path] = None
        self._indices: Dict[str, BackupIndex] = {}

    # ─── 路径管理 ───────────────────────────────────────────

    @property
    def backup_root(self) -> Path:
        """获取备份根目录"""
        if self._backup_root is None:
            backup_dir = getattr(self.config, "backup_dir", None)
            if backup_dir:
                self._backup_root = Path(backup_dir)
            else:
                self._backup_root = self.config.base_dir / "backups"
            self._backup_root.mkdir(parents=True, exist_ok=True)
        return self._backup_root

    def _world_backup_dir(self, world_name: str) -> Path:
        """获取某个存档的备份目录"""
        d = self.backup_root / world_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _world_index_path(self, world_name: str) -> Path:
        """获取某个存档的索引文件路径"""
        return self.backup_root / f"{world_name}_index.json"

    def _get_index(self, world_name: str) -> BackupIndex:
        """获取或创建存档索引"""
        if world_name not in self._indices:
            self._indices[world_name] = BackupIndex(self._world_index_path(world_name))
        return self._indices[world_name]

    # ─── 存档目录定位 ───────────────────────────────────────

    @staticmethod
    def _is_isolated_version(version_id: str) -> bool:
        """判断版本是否为模组加载器版本（需要版本隔离）"""
        v = version_id.lower()
        return any(loader in v for loader in ("forge", "fabric", "neoforge"))

    def _find_world_dir(self, world_name: str) -> Optional[Path]:
        """
        查找存档目录，优先在版本隔离目录中查找，回退到全局 saves 目录。
        """
        mc_dir = self.config.minecraft_dir

        # 1. 全局 saves 目录
        global_saves = mc_dir / "saves" / world_name
        if global_saves.exists() and (global_saves / "level.dat").exists():
            return global_saves

        # 2. 版本隔离目录中查找 (versions/<version>/saves/<world_name>)
        versions_dir = mc_dir / "versions"
        if versions_dir.exists():
            for ver_dir in versions_dir.iterdir():
                if ver_dir.is_dir():
                    isolated_save = ver_dir / "saves" / world_name
                    if isolated_save.exists() and (isolated_save / "level.dat").exists():
                        return isolated_save

        return None

    def _find_all_world_dirs(self) -> List[Dict[str, Any]]:
        """扫描所有可用的存档目录"""
        worlds = []
        seen = set()
        mc_dir = self.config.minecraft_dir

        # 扫描全局 saves
        global_saves = mc_dir / "saves"
        if global_saves.exists():
            for d in global_saves.iterdir():
                if d.is_dir() and (d / "level.dat").exists():
                    name = d.name
                    if name not in seen:
                        seen.add(name)
                        worlds.append({
                            "name": name,
                            "path": d,
                            "is_isolated": False,
                            "last_modified": d.stat().st_mtime if d.exists() else 0,
                        })

        # 扫描版本隔离目录
        versions_dir = mc_dir / "versions"
        if versions_dir.exists():
            for ver_dir in versions_dir.iterdir():
                if ver_dir.is_dir():
                    saves_dir = ver_dir / "saves"
                    if saves_dir.exists():
                        for d in saves_dir.iterdir():
                            if d.is_dir() and (d / "level.dat").exists():
                                name = d.name
                                if name not in seen:
                                    seen.add(name)
                                    worlds.append({
                                        "name": name,
                                        "path": d,
                                        "is_isolated": True,
                                        "last_modified": d.stat().st_mtime if d.exists() else 0,
                                    })

        # 按修改时间降序排列
        worlds.sort(key=lambda w: w.get("last_modified", 0), reverse=True)
        return worlds

    # ─── 文件大小计算 ───────────────────────────────────────

    def _calc_dir_size(self, path: Path) -> int:
        """递归计算目录大小（字节）"""
        total = 0
        try:
            for f in path.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def _check_disk_space(self, required_bytes: int, target_dir: Path) -> Tuple[bool, str]:
        """检查磁盘空间是否足够"""
        try:
            import platform as _platform
            if _platform.system() == "Windows":
                import ctypes
                free_bytes = ctypes.c_ulonglong(0)
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    str(target_dir), None, None, ctypes.pointer(free_bytes)
                )
                available = free_bytes.value
            else:
                stat = os.statvfs(str(target_dir))
                available = stat.f_bavail * stat.f_frsize
            if available < required_bytes:
                return False, f"磁盘空间不足，需要 {_format_size(required_bytes)}，可用 {_format_size(available)}"
            return True, ""
        except Exception as e:
            logger.warning(f"检查磁盘空间失败: {e}")
            return True, ""

    # ─── 备份操作 ───────────────────────────────────────────

    def create_backup(
        self,
        world_name: str,
        note: str = "",
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        创建存档备份

        Args:
            world_name: 存档名称
            note: 备份备注
            progress_callback: 进度回调 (current, total, status_text)

        Returns:
            (是否成功, 消息) 元组
        """
        world_dir = self._find_world_dir(world_name)
        if not world_dir:
            return False, f"未找到存档: {world_name}"

        # 检查 level.dat
        if not (world_dir / "level.dat").exists():
            return False, f"存档无效: 缺少 level.dat ({world_name})"

        # 估算大小并检查空间
        est_size = self._calc_dir_size(world_dir)
        needed = int(est_size * 1.2) + (10 * 1024 * 1024)  # 预留 20% + 10MB
        target = self._world_backup_dir(world_name)
        ok, msg = self._check_disk_space(needed, target)
        if not ok:
            slog.error("backup_failed", world_name=world_name, reason="disk_full",
                       required_bytes=needed)
            return False, msg

        # 收集文件列表
        files = []
        for f in world_dir.rglob("*"):
            if f.is_file():
                files.append(f)

        total_files = len(files)
        total_size = sum(f.stat().st_size for f in files if f.exists())
        if total_files == 0:
            return False, "存档为空，无需备份"

        # 生成备份文件名
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = _sanitize_filename(world_name)
        file_name = f"{safe_name}_{ts}.zip"

        # 尝试检测游戏版本（从存档路径推断）
        game_version = ""
        if "versions" in world_dir.parts:
            idx = world_dir.parts.index("versions")
            if idx + 1 < len(world_dir.parts):
                game_version = world_dir.parts[idx + 1]

        if progress_callback:
            progress_callback(0, total_size, f"正在备份 {world_name}...")

        # 在临时目录创建压缩包
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            os.close(tmp_fd)

            processed = 0
            with zipfile.ZipFile(
                tmp_path, "w", _ZIP_DEFLATE,
                compresslevel=getattr(self.config, "backup_compress_level", 6),
            ) as zf:
                for f in files:
                    try:
                        arcname = f.relative_to(world_dir)
                        zf.write(str(f), str(arcname))
                        processed += f.stat().st_size
                        if progress_callback:
                            progress_callback(processed, total_size, f"正在压缩 {arcname}")
                    except (PermissionError, OSError) as e:
                        logger.warning(f"跳过文件 {f}: {e}")

            # 移动到目标目录
            dest = target / file_name
            if dest.exists():
                dest.unlink()
            shutil.move(tmp_path, str(dest))

            # 获取最终文件大小
            actual_size = dest.stat().st_size

            # 更新索引
            entry = BackupEntry({
                "id": str(uuid4()),
                "world_name": world_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "size_bytes": actual_size,
                "game_version": game_version,
                "note": note,
                "file_name": file_name,
            })
            self._get_index(world_name).add(entry)

            # 自动清理旧备份
            self._auto_cleanup(world_name)

            if progress_callback:
                progress_callback(total_size, total_size, "备份完成")

            slog.info("backup_start", world_name=world_name, backup_name=file_name,
                      size_bytes=actual_size, compress_level=getattr(self.config, "backup_compress_level", 6))
            return True, f"备份成功: {file_name}"

        except Exception as e:
            logger.error(f"备份失败: {e}")
            slog.error("backup_failed", world_name=world_name, reason=str(e)[:200])
            # 清理临时文件
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass
            return False, f"备份失败: {e}"

    def restore_backup(
        self,
        entry_id: str,
        world_name: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        恢复存档备份

        Args:
            entry_id: 备份记录 ID
            world_name: 存档名称
            progress_callback: 进度回调 (current, total, status_text)

        Returns:
            (是否成功, 消息) 元组
        """
        index = self._get_index(world_name)
        entry = None
        for e in index.entries:
            if e.id == entry_id:
                entry = e
                break

        if not entry:
            return False, "未找到备份记录"

        zip_path = self._world_backup_dir(world_name) / entry.file_name
        if not zip_path.exists():
            return False, f"备份文件不存在: {entry.file_name}"

        # 校验 zip 完整性
        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                bad = zf.testzip()
                if bad:
                    return False, f"备份文件已损坏: {bad}"
                total_files = len(zf.namelist())
        except zipfile.BadZipFile:
            return False, "备份文件不是有效的 ZIP 文件"

        # 确定目标存档目录
        # 如果备份来自版本隔离目录（game_version 字段非空且含模组加载器关键字），
        # 则恢复到对应的版本隔离目录；否则恢复到全局 saves/
        if entry.game_version and self._is_isolated_version(entry.game_version):
            saves_dir = self.config.minecraft_dir / "versions" / entry.game_version / "saves"
        else:
            saves_dir = self.config.minecraft_dir / "saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        target_dir = saves_dir / world_name

        # 保护当前存档
        if target_dir.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak_name = f"{world_name}_bak_{ts}"
            bak_dir = target_dir.parent / bak_name
            try:
                shutil.move(str(target_dir), str(bak_dir))
                logger.info(f"当前存档已重命名: {bak_name}")
            except Exception as e:
                return False, f"无法保护当前存档: {e}"

        if progress_callback:
            progress_callback(0, total_files, "正在恢复存档...")

        try:
            # 解压备份
            processed = 0
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                for member in zf.namelist():
                    try:
                        target_file = target_dir / member
                        if member.endswith("/"):
                            target_file.mkdir(parents=True, exist_ok=True)
                        else:
                            target_file.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as src, open(target_file, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                        processed += 1
                        if progress_callback:
                            progress_callback(processed, total_files, f"正在恢复 {member}")
                    except Exception as e:
                        logger.warning(f"跳过 {member}: {e}")

            # 验证恢复结果
            if not (target_dir / "level.dat").exists():
                # 回滚
                if target_dir.exists():
                    shutil.rmtree(str(target_dir))
                if bak_dir.exists():
                    shutil.move(str(bak_dir), str(target_dir))
                return False, "恢复后未找到 level.dat，已回滚"

            if progress_callback:
                progress_callback(total_files, total_files, "恢复完成")

            logger.info(f"存档 {world_name} 已从备份恢复")
            slog.info("backup_restored", world_name=world_name, backup_entry=entry_id,
                      size_bytes=entry.size_bytes)
            return True, f"恢复成功: {world_name}"

        except Exception as e:
            logger.error(f"恢复失败: {e}")
            slog.error("backup_restore_failed", world_name=world_name,
                       reason=str(e)[:200])
            # 回滚
            try:
                if target_dir.exists():
                    shutil.rmtree(str(target_dir))
                if bak_dir.exists():
                    shutil.move(str(bak_dir), str(target_dir))
            except Exception:
                pass
            return False, f"恢复失败: {e}"

    def delete_backup(self, entry_id: str, world_name: str) -> Tuple[bool, str]:
        """删除一条备份记录及其文件"""
        index = self._get_index(world_name)
        entry = None
        for e in index.entries:
            if e.id == entry_id:
                entry = e
                break

        if not entry:
            return False, "未找到备份记录"

        zip_path = self._world_backup_dir(world_name) / entry.file_name
        if zip_path.exists():
            try:
                zip_path.unlink()
            except Exception as e:
                return False, f"删除备份文件失败: {e}"

        index.remove(entry_id)
        return True, f"已删除备份: {entry.file_name}"

    def get_backups(self, world_name: str) -> List[BackupEntry]:
        """获取某存档的所有备份，按时间降序排列"""
        index = self._get_index(world_name)
        entries = index.find_by_world(world_name)
        entries.sort(key=lambda e: e.timestamp_dt, reverse=True)
        return entries

    # ─── 自动清理 ───────────────────────────────────────────

    def _auto_cleanup(self, world_name: str):
        """根据策略自动清理旧备份"""
        max_backups = getattr(self.config, "backup_max_per_world", 10)
        if max_backups <= 0:
            return

        backups = self.get_backups(world_name)
        if len(backups) <= max_backups:
            return

        # 删除超出数量的最旧备份
        to_delete = backups[max_backups:]
        for entry in to_delete:
            zip_path = self._world_backup_dir(world_name) / entry.file_name
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except Exception:
                    pass
            self._get_index(world_name).remove(entry.id)
            logger.info(f"自动清理旧备份: {entry.file_name}")

    # ─── 完整性校验 ─────────────────────────────────────────

    def verify_backup(self, world_name: str, entry_id: str) -> Tuple[bool, str]:
        """校验备份文件完整性"""
        index = self._get_index(world_name)
        entry = None
        for e in index.entries:
            if e.id == entry_id:
                entry = e
                break

        if not entry:
            return False, "未找到备份记录"

        zip_path = self._world_backup_dir(world_name) / entry.file_name
        if not zip_path.exists():
            return False, "备份文件不存在"

        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                bad = zf.testzip()
                if bad:
                    return False, f"文件损坏: {bad}"
                has_level = any("level.dat" in n for n in zf.namelist())
                if not has_level:
                    return False, "备份中缺少 level.dat"
            return True, "备份完整"
        except zipfile.BadZipFile:
            return False, "不是有效的 ZIP 文件"
        except Exception as e:
            return False, f"校验失败: {e}"

    # ─── 导出备份 ───────────────────────────────────────────

    def export_backup(self, entry_id: str, world_name: str, dest_path: str) -> Tuple[bool, str]:
        """导出备份到指定路径"""
        index = self._get_index(world_name)
        entry = None
        for e in index.entries:
            if e.id == entry_id:
                entry = e
                break

        if not entry:
            return False, "未找到备份记录"

        src = self._world_backup_dir(world_name) / entry.file_name
        if not src.exists():
            return False, "备份文件不存在"

        try:
            shutil.copy2(str(src), dest_path)
            return True, f"已导出到: {dest_path}"
        except Exception as e:
            return False, f"导出失败: {e}"


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    import re
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)


def _format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"
