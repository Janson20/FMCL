"""预下载模块 - 启动时检测并预下载 Minecraft 资源包

界面交互（询问、进度窗口、完成/失败提示）全部通过 ``app.ports.UIPort`` 完成，
本模块不再 import tkinter（分层约束见 ``scripts/check_services_purity.py``）。
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests
from logzero import logger

from app.ports import Choice, ProgressReport, UIPort

PREDOWNLOAD_URL = "https://jingdu.qzz.io/static/fmcl/minecraft.rar"
PREDOWNLOAD_LITE_URL = "https://jingdu.qzz.io/static/fmcl/lite.rar"
PREDOWNLOAD_MARKER = "predownloaded"

RESULT_CANCELLED = "cancelled"
RESULT_COMPLETED = "completed"
RESULT_ERROR = "error"


def is_predownloaded(minecraft_dir: Path) -> bool:
    return (minecraft_dir / PREDOWNLOAD_MARKER).exists()


def create_predownloaded_marker(minecraft_dir: Path):
    minecraft_dir.mkdir(parents=True, exist_ok=True)
    (minecraft_dir / PREDOWNLOAD_MARKER).touch()


class Predownloader:
    def __init__(self, url: str, minecraft_dir: Path, num_threads: int = 4, chunk_size: int = 8192):
        self.url = url
        self.minecraft_dir = minecraft_dir
        self.num_threads = num_threads
        self.chunk_size = chunk_size
        self._progress_callback: Optional[Callable] = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._downloaded_bytes = 0
        self._total_size = 0
        self._range_supported = True
        #: D-116（第 12 轮）：某个分段**因为异常**（网络/HTTP）失败，而不是用户按了取消。
        #: 两者都会置 `_cancel_event`，必须分开记账，否则"网络断了"会被当成"用户取消"
        #: 而一声不吭（界面把 cancelled 定义成"只关窗、不弹提示"）。
        self._part_failed = False

    def set_progress_callback(self, callback: Callable):
        self._progress_callback = callback

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def cancel(self):
        self._cancel_event.set()

    def run(self) -> str:
        try:
            response = requests.head(self.url, timeout=10)
            response.raise_for_status()
            self._total_size = int(response.headers.get("Content-Length", 0))
            if self._total_size == 0:
                raise ValueError("无法获取文件大小")

            rar_path = self.minecraft_dir / "minecraft.rar"
            self._downloaded_bytes = 0
            self._part_failed = False

            if self.num_threads > 1:
                self._range_supported = True
                part_size = self._total_size // self.num_threads
                threads = []

                for i in range(self.num_threads):
                    start_byte = i * part_size
                    end_byte = start_byte + part_size - 1 if i < self.num_threads - 1 else self._total_size - 1
                    t = threading.Thread(target=self._download_part, args=(i, start_byte, end_byte, rar_path))
                    threads.append(t)
                    t.start()

                for t in threads:
                    t.join()

                if self._cancel_event.is_set():
                    if not self._range_supported:
                        logger.info("服务器不支持 Range 请求，回退到单线程下载")
                        self._cleanup(rar_path)
                        self._cancel_event.clear()
                        result = self._download_single_thread(rar_path)
                        if result != RESULT_COMPLETED:
                            return result
                    elif self._part_failed:
                        # D-116（第 12 轮，**行为变更**）：分段因为网络/HTTP 异常而失败，
                        # 改前会返回 RESULT_CANCELLED，而界面把"取消"定义成"只关窗、
                        # 不弹任何提示" —— 用户看到进度窗自己消失、没有任何解释，
                        # 而单线程路径同样情况返回的是 RESULT_ERROR（会弹错误框）。
                        # 现在两条路径一致：失败要报失败。
                        logger.error("分段下载失败，本次预下载按失败处理（不是用户取消）")
                        self._cleanup(rar_path)
                        return RESULT_ERROR
                    else:
                        self._cleanup(rar_path)
                        return RESULT_CANCELLED
                else:
                    self._merge_parts(rar_path)
            else:
                result = self._download_single_thread(rar_path)
                if result != RESULT_COMPLETED:
                    return result

            if self._cancel_event.is_set():
                self._cleanup(rar_path)
                return RESULT_CANCELLED

            self._extract_rar(rar_path, self.minecraft_dir)

            self._cleanup(rar_path)

            return RESULT_COMPLETED
        except Exception as e:
            logger.error(f"预下载失败: {e}")
            return RESULT_ERROR

    def _download_single_thread(self, rar_path: Path) -> str:
        self._downloaded_bytes = 0
        try:
            response = requests.get(self.url, stream=True, timeout=60)
            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            if content_length:
                self._total_size = int(content_length)

            with open(rar_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if self._cancel_event.is_set():
                        return RESULT_CANCELLED
                    if chunk:
                        f.write(chunk)
                        with self._lock:
                            self._downloaded_bytes += len(chunk)
                            clamped = (
                                min(self._downloaded_bytes, self._total_size)
                                if self._total_size > 0
                                else self._downloaded_bytes
                            )
                            if self._progress_callback:
                                self._progress_callback(clamped, max(self._total_size, clamped), "download")
            return RESULT_COMPLETED
        except Exception as e:
            logger.error(f"单线程下载失败: {e}")
            return RESULT_ERROR

    def _download_part(self, part_num: int, start_byte: int, end_byte: int, filepath: Path):
        headers = {"Range": f"bytes={start_byte}-{end_byte}"}
        part_file = Path(f"{filepath}.part{part_num}")
        try:
            response = requests.get(self.url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()

            if response.status_code != 206:
                logger.warning(f"服务器返回 {response.status_code} 而非 206，不支持 Range 分块下载")
                self._range_supported = False
                self._cancel_event.set()
                return

            with open(part_file, "wb") as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if self._cancel_event.is_set():
                        return
                    if chunk:
                        f.write(chunk)
                        with self._lock:
                            self._downloaded_bytes += len(chunk)
                            if self._progress_callback:
                                display_bytes = min(self._downloaded_bytes, self._total_size)
                                display_total = max(self._total_size, 1)
                                self._progress_callback(display_bytes, display_total, "download")
        except Exception as e:
            logger.error(f"下载分段 {part_num} 失败: {e}")
            self._part_failed = True  # D-116：与"用户取消"区分开
            self._cancel_event.set()

    def _merge_parts(self, filepath: Path):
        if self._progress_callback:
            self._progress_callback(self._total_size, self._total_size, "merge")
        with open(filepath, "wb") as outfile:
            for i in range(self.num_threads):
                if self._cancel_event.is_set():
                    return
                part_file = Path(f"{filepath}.part{i}")
                if part_file.exists():
                    with open(part_file, "rb") as infile:
                        outfile.write(infile.read())
                    part_file.unlink()

    def _extract_rar(self, rar_path: Path, dest_dir: Path):
        if self._progress_callback:
            self._progress_callback(0, 100, "extract")

        try:
            import rarfile

            tool = _find_rar_tool()
            if tool:
                rarfile.UNRAR_TOOL = tool

            with rarfile.RarFile(str(rar_path)) as rf:
                total_files = len(rf.namelist())
                abs_dest = os.path.abspath(str(dest_dir))
                for i, name in enumerate(rf.namelist()):
                    if self._cancel_event.is_set():
                        return
                    extracted_path = os.path.abspath(os.path.join(str(dest_dir), name))
                    if not extracted_path.startswith(abs_dest + os.sep) and extracted_path != abs_dest:
                        logger.warning(f"跳过越界文件: {name}")
                        continue
                    rf.extract(name, str(dest_dir))
                    if self._progress_callback:
                        pct = int((i + 1) / total_files * 100)
                        self._progress_callback(pct, 100, "extract")
            logger.info(f"rarfile 解压成功: {rar_path}")
            return
        except ImportError:
            logger.debug("rarfile 未安装，尝试系统工具")
        except Exception as e:
            logger.warning(f"rarfile 解压失败: {e}，尝试系统工具")

        tool = _find_rar_tool()
        if tool:
            _extract_with_tool(tool, rar_path, dest_dir, self._cancel_event, self._progress_callback)
            logger.info(f"使用 {tool} 解压成功")
            return

        raise RuntimeError(
            "未找到可用的 RAR 解压工具。\n"
            "请安装 7-Zip (https://7-zip.org/) 或 WinRAR (https://www.win-rar.com/)\n"
            "安装后重启启动器即可。"
        )

    def _cleanup(self, rar_path: Path):
        if rar_path.exists():
            try:
                rar_path.unlink()
            except Exception:
                pass
        for i in range(self.num_threads):
            part_file = Path(f"{rar_path}.part{i}")
            if part_file.exists():
                try:
                    part_file.unlink()
                except Exception:
                    pass


def run_predownload_check(ui: UIPort, minecraft_dir: Path, _tr) -> Optional[bool]:
    """预下载检查入口（原来的 ``parent`` 参数换成 UI 能力端口）。

    Args:
        ui: UI 能力端口：问答 / 进度窗口 / 提示全部走它，本模块不碰 tkinter。
        minecraft_dir: .minecraft 目录。
        _tr: i18n 取词函数。

    Returns:
        ``None``  —— 已经预下载过，直接返回；
        ``False`` —— 用户放弃预下载（此时仍会写"已询问过"标记，与原实现一致）；
        ``True``  —— 已发起下载（返回时进度窗口已按原 ``wait_window`` 语义关闭）。

    线程约定：与原实现一致 —— 本函数由主线程（``app.after`` 回调）调用，
    下载仍然跑在 ``_run_download`` 线程里，没有把阻塞工作搬到新线程。
    """
    if is_predownloaded(minecraft_dir):
        return None

    # 原来的自建 tk 三按钮弹窗换成端口问答：yes=完整版 / lite=轻量版 / no=取消。
    # 窗口被关闭、超时或没有回答时 choose 返回 None，与原实现里 result["action"]
    # 为 None 的路径一致（写标记 + 返回 False）。
    action = ui.choose(
        _tr("predownload_title"),
        _tr("predownload_prompt"),
        [
            Choice("yes", _tr("predownload_yes")),
            Choice("lite", _tr("predownload_lite")),
            Choice("no", _tr("predownload_cancel")),
        ],
        default=None,
        hint=_tr("predownload_hint"),
    )

    create_predownloaded_marker(minecraft_dir)

    if action not in ("yes", "lite"):
        return False

    url = PREDOWNLOAD_URL if action == "yes" else PREDOWNLOAD_LITE_URL
    predownloader = Predownloader(url, minecraft_dir, num_threads=4)
    predownloader.set_progress_callback(_make_progress_callback(ui, _tr))

    finished = threading.Event()

    def _run_download():
        dl_result = predownloader.run()
        # 收尾在主线程之外执行：关闭进度窗/弹提示都由端口负责切主线程
        _finish_predownload(ui, _tr, dl_result)
        finished.set()

    download_thread = threading.Thread(target=_run_download, daemon=True)
    download_thread.start()

    if finished.is_set():
        # 极端情况：下载在建窗之前就结束了（例如 head 请求直接失败）。
        # 此时 close_progress 已经发过，再建窗就没人会关它，直接收工。
        logger.warning("预下载在建窗之前就结束了，跳过进度窗口")
        return True

    # 建窗 + 模态等待，等价原来的
    #   progress_win = _create_progress_window(...) ; progress_win.wait_window()
    # 先起线程再建窗是刻意的：modal 等待会在主线程阻塞，而端口只在主线程就地建窗
    # （worker 的上报只能入队），所以窗口必须由这一次调用创建。
    # wait_event 是兜底：等待"窗口关闭"或"下载线程结束"二者先到 —— 就算
    # close_progress 抢在窗口之前被轮询消费掉，主线程也不会死等。
    ui.show_progress(
        ProgressReport(
            title=_tr("predownload_progress_title"),
            message=_tr("predownload_status_downloading"),
            current=0,
            total=100,
        ),
        heading=_tr("predownload_progress_label"),
        detail="0 MB / 0 MB",
        cancel_label=_tr("predownload_cancel_btn"),
        on_cancel=predownloader.cancel,
        modal=True,
        wait_event=finished,
    )
    # 兜底：若上面是因 wait_event 提前结束的，确保残留的进度窗口被关掉
    # （正常路径下这里是无操作：窗口已由 close_progress 关闭）
    ui.close_progress(_tr("predownload_progress_title"))

    return True


def _find_rar_tool() -> Optional[str]:
    tools = []
    if sys.platform == "win32":
        import winreg

        for key_path in [r"SOFTWARE\7-Zip", r"SOFTWARE\WOW6432Node\7-Zip"]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    path = winreg.QueryValueEx(key, "Path")[0]
                    exe = os.path.join(path, "7z.exe")
                    if os.path.exists(exe):
                        tools.append(exe)
            except OSError:
                pass
        for base in [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
            r"C:\Program Files\WinRAR\UnRAR.exe",
            r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
        ]:
            if os.path.exists(base):
                tools.append(base)

    for cmd in ("7z", "7z.exe", "unrar", "unrar.exe", "unar", "bsdtar"):
        if shutil.which(cmd):
            tools.append(cmd)

    for tool in tools:
        try:
            result = subprocess.run(
                [tool, "--help"],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode <= 1:
                logger.info(f"找到 RAR 解压工具: {tool}")
                return tool
        except Exception:
            pass

    return None


def _extract_with_tool(
    tool: str, rar_path: Path, dest_dir: Path, cancel_event: threading.Event, progress_callback=None
):
    dest_dir.mkdir(parents=True, exist_ok=True)

    if "7z" in tool.lower():
        cmd = [tool, "x", str(rar_path), f"-o{str(dest_dir)}", "-y"]
        if progress_callback:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            for line in proc.stdout:
                if cancel_event.is_set():
                    proc.terminate()
                    return
                if "%" in line:
                    try:
                        pct_str = line.strip().split()[-1].replace("%", "")
                        pct = int(pct_str)
                        progress_callback(pct, 100, "extract")
                    except ValueError:
                        pass
            returncode = proc.wait()
            # D-115（第 12 轮，**行为变更**）：这一段以前只 `proc.wait()` 就返回，
            # **返回码从不检查** —— 解压失败会被当成成功（调用方还会打一行
            # "使用 xxx 解压成功" 并报"预下载完成"），用户看到完成提示、磁盘上
            # 却一个文件都没解出来。同函数的另一个分支用的是
            # `subprocess.run(..., check=True)`，两边判据本来就不一致；
            # 现在统一成"非 0 即失败"。
            if returncode != 0:
                raise RuntimeError(f"{tool} 解压失败（返回码 {returncode}）")
        else:
            subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    else:
        cmd = [tool, "x", str(rar_path), str(dest_dir) + os.sep, "-y"]
        subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if progress_callback:
            progress_callback(100, 100, "extract")


def _make_progress_callback(ui: UIPort, _tr) -> Callable:
    """构造 ``Predownloader`` 的进度回调。

    保留原实现的节流（0.1 秒一次，解压阶段不节流）与三阶段文案，差异只在于
    不再直接改 Tk 部件，而是把 ``ProgressReport`` 交给 UI 端口 —— 这些回调
    实际是在下载/解压线程里跑的，端口负责切回主线程。

    ``detail`` 是端口认识的扩展参数，用来复用原来的明细文案
    （下载时 "x.x MB / y.y MB"、合并时留空、解压时 "nn%"）。
    """
    last_update_time = [time.time()]

    def on_progress(current, total, phase):
        now = time.time()
        if now - last_update_time[0] < 0.1 and phase != "extract":
            return
        last_update_time[0] = now

        if phase == "download":
            mb_current = current / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            ui.show_progress(
                ProgressReport(
                    message=_tr("predownload_status_downloading"),
                    current=max(0, int(current)),
                    total=int(total) if total > 0 else 1,
                ),
                detail=f"{mb_current:.1f} MB / {mb_total:.1f} MB",
            )
        elif phase == "merge":
            ui.show_progress(
                ProgressReport(message=_tr("predownload_status_merging"), current=1, total=1),
                detail="",
            )
        elif phase == "extract":
            ui.show_progress(
                ProgressReport(message=_tr("predownload_status_extracting"), current=int(current), total=100),
                detail=f"{int(current)}%",
            )

    return on_progress


def _finish_predownload(ui: UIPort, _tr, dl_result: str) -> None:
    """收尾：关闭进度窗口，按结果弹完成/失败提示。

    本函数在下载线程里被调用，切主线程由 UI 端口负责。
    与原实现一致：``RESULT_CANCELLED``（用户取消 / 关窗）只关窗，不弹任何提示。
    """
    ui.close_progress(_tr("predownload_progress_title"))

    if dl_result == RESULT_COMPLETED:
        ui.show_info(_tr("predownload_complete_title"), _tr("predownload_complete_msg"))
    elif dl_result == RESULT_ERROR:
        ui.show_error(_tr("predownload_error_title"), _tr("predownload_error_msg"))
