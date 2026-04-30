"""预下载模块 - 启动时检测并预下载 Minecraft 资源包"""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Callable

import requests
from logzero import logger

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
            self._total_size = int(response.headers.get('Content-Length', 0))
            if self._total_size == 0:
                raise ValueError("无法获取文件大小")

            rar_path = self.minecraft_dir / "minecraft.rar"
            self._downloaded_bytes = 0

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
                self._cleanup(rar_path)
                return RESULT_CANCELLED

            self._merge_parts(rar_path)

            if self._cancel_event.is_set():
                self._cleanup(rar_path)
                return RESULT_CANCELLED

            self._extract_rar(rar_path, self.minecraft_dir)

            self._cleanup(rar_path)

            return RESULT_COMPLETED
        except Exception as e:
            logger.error(f"预下载失败: {e}")
            return RESULT_ERROR

    def _download_part(self, part_num: int, start_byte: int, end_byte: int, filepath: Path):
        headers = {'Range': f'bytes={start_byte}-{end_byte}'}
        part_file = Path(f"{filepath}.part{part_num}")
        try:
            response = requests.get(self.url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()
            with open(part_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if self._cancel_event.is_set():
                        return
                    if chunk:
                        f.write(chunk)
                        with self._lock:
                            self._downloaded_bytes += len(chunk)
                            if self._progress_callback:
                                self._progress_callback(self._downloaded_bytes, self._total_size, "download")
        except Exception as e:
            logger.error(f"下载分段 {part_num} 失败: {e}")
            self._cancel_event.set()

    def _merge_parts(self, filepath: Path):
        if self._progress_callback:
            self._progress_callback(self._total_size, self._total_size, "merge")
        with open(filepath, 'wb') as outfile:
            for i in range(self.num_threads):
                if self._cancel_event.is_set():
                    return
                part_file = Path(f"{filepath}.part{i}")
                if part_file.exists():
                    with open(part_file, 'rb') as infile:
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
                for i, name in enumerate(rf.namelist()):
                    if self._cancel_event.is_set():
                        return
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


def run_predownload_check(parent, minecraft_dir: Path, _tr) -> Optional[bool]:
    if is_predownloaded(minecraft_dir):
        return None

    result = {"action": None}

    dialog = _create_prompt_dialog(parent, _tr, result)
    if dialog is None:
        return None

    dialog.wait_window()

    create_predownloaded_marker(minecraft_dir)

    if result["action"] not in ("yes", "lite"):
        return False

    url = PREDOWNLOAD_URL if result["action"] == "yes" else PREDOWNLOAD_LITE_URL
    predownloader = Predownloader(url, minecraft_dir, num_threads=4)

    progress_win = _create_progress_window(parent, _tr, predownloader)

    def on_download_done(dl_result):
        progress_win.after(0, lambda: _finish_predownload(progress_win, parent, _tr, dl_result))

    def _run_download():
        dl_result = predownloader.run()
        if progress_win.winfo_exists():
            progress_win.after(0, lambda: on_download_done(dl_result))

    download_thread = threading.Thread(target=_run_download, daemon=True)
    download_thread.start()

    progress_win.wait_window()

    return True


def _find_rar_tool() -> Optional[str]:
    tools = []
    if sys.platform == 'win32':
        import winreg
        for key_path in [
            r"SOFTWARE\7-Zip",
            r"SOFTWARE\WOW6432Node\7-Zip",
        ]:
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
            result = subprocess.run([tool, "--help"], capture_output=True, timeout=5)
            if result.returncode <= 1:
                logger.info(f"找到 RAR 解压工具: {tool}")
                return tool
        except Exception:
            pass

    return None


def _extract_with_tool(tool: str, rar_path: Path, dest_dir: Path, cancel_event: threading.Event, progress_callback=None):
    dest_dir.mkdir(parents=True, exist_ok=True)

    if "7z" in tool.lower():
        cmd = [tool, "x", str(rar_path), f"-o{str(dest_dir)}", "-y"]
        if progress_callback:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
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
            proc.wait()
        else:
            subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    else:
        cmd = [tool, "x", str(rar_path), str(dest_dir) + os.sep, "-y"]
        subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        if progress_callback:
            progress_callback(100, 100, "extract")

def _create_prompt_dialog(parent, _tr, result):
    import tkinter as tk

    dialog = tk.Toplevel(parent)
    dialog.title(_tr("predownload_title"))
    dialog.geometry("500x200")
    dialog.resizable(False, False)
    dialog.attributes('-topmost', True)
    dialog.configure(bg='#1a1a2e')
    dialog.transient(parent)
    dialog.grab_set()

    if parent.winfo_exists():
        dialog.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 500, 200
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")

    tk.Label(
        dialog, text=_tr("predownload_prompt"),
        font=("Microsoft YaHei", 13),
        fg='#ffffff', bg='#1a1a2e',
        wraplength=400, justify=tk.CENTER
    ).pack(pady=(30, 15))

    tk.Label(
        dialog, text=_tr("predownload_hint"),
        font=("Microsoft YaHei", 10),
        fg='#a0a0b0', bg='#1a1a2e',
        wraplength=400, justify=tk.CENTER
    ).pack(pady=(0, 20))

    btn_frame = tk.Frame(dialog, bg='#1a1a2e')
    btn_frame.pack()

    def on_yes():
        result["action"] = "yes"
        dialog.grab_release()
        dialog.destroy()

    def on_lite():
        result["action"] = "lite"
        dialog.grab_release()
        dialog.destroy()

    def on_no():
        result["action"] = "no"
        dialog.grab_release()
        dialog.destroy()

    tk.Button(
        btn_frame, text=_tr("predownload_yes"),
        font=("Microsoft YaHei", 11),
        fg='#ffffff', bg='#e94560',
        activebackground='#ff6b81', activeforeground='#ffffff',
        relief='flat', cursor='hand2',
        bd=0, highlightthickness=0,
        width=12, height=1,
        command=on_yes,
    ).pack(side=tk.LEFT, padx=6)

    tk.Button(
        btn_frame, text=_tr("predownload_lite"),
        font=("Microsoft YaHei", 11),
        fg='#ffffff', bg='#0f3460',
        activebackground='#1a5276', activeforeground='#ffffff',
        relief='flat', cursor='hand2',
        bd=0, highlightthickness=0,
        width=12, height=1,
        command=on_lite,
    ).pack(side=tk.LEFT, padx=6)

    tk.Button(
        btn_frame, text=_tr("predownload_cancel"),
        font=("Microsoft YaHei", 11),
        fg='#a0a0b0', bg='#16213e',
        activebackground='#2d3a5c', activeforeground='#ffffff',
        relief='flat', cursor='hand2',
        bd=0, highlightthickness=0,
        width=12, height=1,
        command=on_no,
    ).pack(side=tk.LEFT, padx=6)

    return dialog


def _create_progress_window(parent, _tr, predownloader: Predownloader):
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(parent)
    win.title(_tr("predownload_progress_title"))
    win.geometry("480x220")
    win.resizable(False, False)
    win.attributes('-topmost', True)
    win.configure(bg='#1a1a2e')
    win.transient(parent)
    win.grab_set()

    if parent.winfo_exists():
        win.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 480, 220
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")

    tk.Label(
        win, text=_tr("predownload_progress_label"),
        font=("Microsoft YaHei", 13, 'bold'),
        fg='#ffffff', bg='#1a1a2e'
    ).pack(pady=(25, 5))

    status_label = tk.Label(
        win, text=_tr("predownload_status_downloading"),
        font=("Microsoft YaHei", 10),
        fg='#a0a0b0', bg='#1a1a2e'
    )
    status_label.pack(pady=(0, 10))

    progress_var = tk.DoubleVar(value=0)
    progress_bar = ttk.Progressbar(
        win, variable=progress_var, maximum=100,
        mode='determinate', length=420
    )
    progress_bar.pack(pady=(5, 5))

    detail_label = tk.Label(
        win, text="0 MB / 0 MB",
        font=("Microsoft YaHei", 10),
        fg='#a0a0b0', bg='#1a1a2e'
    )
    detail_label.pack(pady=(0, 10))

    cancel_btn = tk.Button(
        win, text=_tr("predownload_cancel_btn"),
        font=("Microsoft YaHei", 11),
        fg='#a0a0b0', bg='#16213e',
        activebackground='#2d3a5c', activeforeground='#ffffff',
        relief='flat', cursor='hand2',
        bd=0, highlightthickness=0,
        width=14, height=1,
        command=predownloader.cancel,
    )
    cancel_btn.pack(pady=(0, 15))

    last_update_time = [time.time()]

    def on_progress(current, total, phase):
        now = time.time()
        if now - last_update_time[0] < 0.1 and phase != "extract":
            return
        last_update_time[0] = now

        if phase == "download":
            pct = (current / total * 100) if total > 0 else 0
            progress_var.set(pct)
            mb_current = current / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            detail_label.config(text=f"{mb_current:.1f} MB / {mb_total:.1f} MB")
            status_label.config(text=_tr("predownload_status_downloading"))
        elif phase == "merge":
            progress_var.set(100)
            status_label.config(text=_tr("predownload_status_merging"))
            detail_label.config(text="")
        elif phase == "extract":
            progress_var.set(current)
            status_label.config(text=_tr("predownload_status_extracting"))
            detail_label.config(text=f"{int(current)}%")

    predownloader.set_progress_callback(on_progress)

    def on_close():
        predownloader.cancel()

    win.protocol("WM_DELETE_WINDOW", on_close)

    return win


def _finish_predownload(progress_win, parent, _tr, dl_result: str):
    import tkinter.messagebox as messagebox

    if progress_win.winfo_exists():
        progress_win.grab_release()
        progress_win.destroy()

    if dl_result == RESULT_COMPLETED:
        messagebox.showinfo(
            _tr("predownload_complete_title"),
            _tr("predownload_complete_msg"),
            parent=parent
        )
    elif dl_result == RESULT_ERROR:
        messagebox.showerror(
            _tr("predownload_error_title"),
            _tr("predownload_error_msg"),
            parent=parent
        )
