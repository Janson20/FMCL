"""工具箱业务服务（阶段 1 任务 1.6 抽取）。

从 ``ui/app_tools.py`` （``ToolsTabMixin``，2172 行 / 57 个方法）**逐字搬运**而来，
承载 8 个工具的全部算法与 IO：

======================  ==========================================================
工具                     本模块提供的入口
======================  ==========================================================
垃圾清理                 ``parse_max_depth`` / ``scan_junk_files`` / ``group_junk_files``
                        ``delete_junk_files`` / ``is_protected_system_dir``
端口检测                 ``parse_port`` / ``probe_port``
服务器信息（查服）        ``peek_server`` / ``summarize_server_info`` / ``decode_favicon_data``
                        ``_mc_write_varint`` / ``_mc_read_varint``
坐标转换                 ``parse_coordinate`` / ``convert_coordinates``
Hash 计算器              ``hash_file`` / ``HASH_ALGORITHMS``
每日运势                 ``daily_fortune``
MC 冷知识                ``MC_FACTS`` / ``fact_of_the_day`` / ``random_fact_index``
MC 知识问答              ``QUIZ_SYSTEM_PROMPT`` / ``generate_quiz`` / ``parse_quiz_response``
                        ``load_quiz_questions`` / ``save_quiz_questions``
                        ``reindex_questions`` / ``merge_generated_questions`` / ``drop_question``
                        ``check_choice_answer`` / ``check_text_answer``
多线程下载器              ``download_multi`` / ``resolve_download_target`` / ``filename_from_url``
                        ``ensure_directory``
======================  ==========================================================

**零 UI 依赖**（由 ``scripts/check_services_purity.py`` 强制）：不 import tkinter /
customtkinter / 任何 ``ui.*`` 模块，不弹窗、不碰控件、不读控件、不写剪贴板。
确认对话框（例如"删除前是否确认"）由界面侧负责 —— 本模块只提供算法与 IO。

**线程**：本模块只在 :func:`download_multi` 内部按原样起分段下载线程（那是算法本身
的一部分）。其余函数都是同步阻塞的纯函数/IO 函数，"要不要放到后台线程"由调用方决定。

**可注入点（便于离线单测，绝不真联网 / 绝不真删用户文件）**

= 参数            函数                              默认值            用途
= ==============  ================================  ================  ==================
= ``socket_factory`` :func:`probe_port` / :func:`peek_server`  ``socket.socket``  注入假 socket
= ``session``      :func:`download_multi`            ``requests``      注入假 HTTP 会话
= ``chat``         :func:`generate_quiz`             无（必传）         注入假 AI 接口
= ``cancel_check`` :func:`download_multi`             ``lambda: False`` 注入取消判定
= ``on_progress``  :func:`download_multi`             空实现            注入进度回调
= ``today``        :func:`daily_fortune` / :func:`fact_of_the_day`  ``date.today()`` 固定日期
= ``rng``          :func:`random_fact_index`         ``random`` 模块    注入固定种子
= ``facts``        :func:`fact_of_the_day` / :func:`random_fact_index`  ``MC_FACTS``  注入题库
=

i18n：本模块通过 ``services.i18n_service._`` 取词（与 ``ui.i18n._`` 是**同一个函数
对象**，见 ``ui/i18n.py`` 的转发 shim），因此界面文案与搬运前逐字一致。

----

「必要改动」清单（除下列各点外，函数体逐字搬自 ``ui/app_tools.py``）：

1. ``self._is_protected_system_dir(...)`` → 模块级 ``is_protected_system_dir(...)``
   （原为 Mixin 方法，搬运后不再是方法）。
2. ``socket.socket(...)`` → ``factory(...)``；``sock.settimeout(5)`` → ``sock.settimeout(timeout)``
   （默认值仍是 5，等价）。
3. ``requests.head/get`` → ``http.head/get``（``http`` 默认就是 ``requests`` 模块）。
4. ``self._dl_cancel_flag`` → ``is_cancelled()``（默认 ``lambda: False``，
   而原实现里 ``_dl_cancel_flag`` 从未被置 True —— 见下方"记录现状"）。
5. 进度回调 ``self.after(0, _update)`` 改为 ``emit(downloaded, total_size, speed)``：
   原实现在 ``after`` 里读闭包变量（可能在主线程执行时已经不是当次的值），
   现在把当次的值作为参数传出，数值来源更明确，界面可见效果一致。
6. ``raise ValueError(...)`` → ``raise InvalidArgument(...)``，**message 文本逐字不变**，
   因此调用方 ``str(e)`` / ``e.message`` 拿到的字符串与搬运前完全一致
   （界面侧本来就是 ``str(e)`` 填进 ``tool_quiz_*_error`` 的）。
7. 日志器由 ``logzero.logger`` 改为 ``logging.getLogger("services.tool_service")``
   （与其它服务一致）；**日志文本逐字不变**。
8. ``_format_size`` 改为转调 ``services.monitor_service._format_bytes``（返工 E 组
   D-27）：两边原来是一份**逐字同构**的副本（同阶梯、同精度），合并成一份实现后
   返回值、精度、边界行为逐字不变，调用点也不用动
   （``ui/app_tools.py`` 的 ``_format_size = _tool_svc._format_size`` 拿到的仍是同一个对象）。
9. 进度回调不再被"时钟刻度"挡住（缺陷 **D-154**，阶段 3 首轮）：原实现把 ``emit`` 写在
   ``if elapsed > 0:``（``time.time()`` 的差值）**里面**，那层本意只是给"算速率"的除法防零，
   副作用却是**一个时钟刻度内跑完的下载一次回调都不发**（Windows 上 ``time.time()`` 步长约
   0.5 ms，内存/局域网的小文件很容易撞上）——界面进度条全程不动，而文件其实已经下好了。
   现在只把"算速率"留在判断里（没跨过刻度给 ``0.0``），``emit`` **每块都发**。
   这是本文件里**唯一一处故意与搬运前不一致的行为**，其余各点都保持逐字等价；
   实证与钉子见 ``poc/_probe_tool_progress_tick.py`` 与
   ``tests/test_tool_service.py::test_download_multi_reports_progress_even_without_a_clock_tick``。

----

「记录现状、疑为缺陷」（**未修**，按"只搬家不改逻辑"要求原样保留）：

- ``_dl_cancel_flag`` 在整个 ``ui/app_tools.py`` 里只被赋值为 ``False``，没有任何地方
  置 ``True`` —— 多线程下载器的取消机制在当前界面下**不可达**；
  ``tool_download_cancelled`` 文案因此永远显示不出来。
  :func:`download_multi` 原样保留了这套机制并把它参数化为 ``cancel_check``。
- ``save_quiz_questions`` 把 ``path.parent.mkdir(...)`` 放在 ``try`` **之外**：
  建目录失败会直接抛出，而写文件失败只记日志、静默返回。
  日志文本硬编码为 "保存 quiz.json 失败"，即使实际路径不是 quiz.json。
- 单选按钮判分用 ``selected.strip() == correct.strip()``（**大小写敏感**），
  填空题判分用 ``answer.strip().lower() == correct.strip().lower()``
  （**大小写不敏感**）—— 两处判分口径不一致，见
  :func:`check_choice_answer` / :func:`check_text_answer`。
- ``_on_quiz_next`` 里 ``self._quiz_current_index = len(...) - 1`` 之后的
  ``if self._quiz_current_index < 0`` 是**死分支**（前面已 ``return`` 掉空列表的情况）。
  该分支留在界面侧，逐字保留。
- ``ToolsTabMixin._PORT_PRESETS`` 在本仓库中**没有任何引用**（死代码），
  未搬运，原样留在 ``ui/app_tools.py``。
"""

import base64
import hashlib
import json
import logging
import os
import random
import socket
import struct
import threading
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

import requests

from services.base import Service
from services.errors import InvalidArgument
from services.i18n_service import _

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查，运行期避免循环导入
    from app.context import AppContext

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 通用：格式化 / 路径
# ═══════════════════════════════════════════════════════════════════


def _format_size(bytes_count: int) -> str:
    """把字节数格式化成可读字符串（D-27：与监控侧共用**同一份实现**）。

    这里原本是 ``services/monitor_service._format_bytes`` 的**逐字副本**（同样的
    1024 阶梯、同样的 ``.1f`` KB/MB 与 ``.2f`` GB 精度），阶段 2 返工 E 组按
    "同一件事只有一份实现"合并成转调。返回值逐字不变 ——
    ``tests/test_tool_service.py`` 的 9 个边界值（含 ``-1`` / ``1023`` /
    ``1024**3 - 1``）与 ``ui/app_tools.py`` 的 ``_format_size = _tool_svc._format_size``
    别名都原样通过。

    这里的**延迟导入**是有意的，不是漏写：``monitor_service`` 在导入期会去探测
    psutil / pynvml / keyboard / gpu-detector 这几个"随时可能缺席"的可选依赖，
    而工具箱（垃圾清理 / 下载器 / 查服 / Hash / 题库）跟 GPU 监控没有任何关系 ——
    为一个纯格式化函数让 ``import services.tool_service`` 顺带把 GPU 探测拉起来，
    是拿启动时间换一行 import。函数级 import 在本仓库是既有写法（flake8 的 E402
    就是为此放开的），每次调用的代价只是一次 ``sys.modules`` 查表。
    """
    from services.monitor_service import _format_bytes

    return _format_bytes(bytes_count)


def relative_path_from(path: str, base: str) -> str:
    """``os.path.relpath`` 的容错版（跨盘符时 ``ValueError`` → 原样返回）。

    搬运自 ``ToolsTabMixin._add_junk_file_row`` 与 ``_on_clean_junk`` 里两段**完全
    相同**的写法::

        try:
            rel = os.path.relpath(fp, base)
        except ValueError:
            rel = fp
    """
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def is_protected_system_dir(path: str) -> bool:
    """路径是否落在 ``%SystemRoot%`` 内（垃圾清理时禁止进入/删除）。"""
    sys_root = os.path.normcase(os.path.normpath(os.environ.get("SystemRoot", r"C:\Windows")))
    p = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    return p == sys_root or p.startswith(sys_root + os.sep)


def filename_from_url(url: str) -> str:
    """从 URL 推落盘文件名（去 query，无名字时用 ``downloaded_file``）。"""
    filename_from_url = url.split("/")[-1].split("?")[0]
    if not filename_from_url:
        filename_from_url = "downloaded_file"
    return filename_from_url


def resolve_download_target(url: str, save_path: str, save_dir: str) -> str:
    """决定下载落盘到哪个文件。

    搬运自 ``_on_start_download`` 的两处同名分支（两处逻辑完全相同）::

        if not save_path or os.path.isdir(save_path):
            filename = os.path.join(save_dir, <URL 文件名>)
        else:
            filename = save_path
    """
    if not save_path or os.path.isdir(save_path):
        filename = os.path.join(save_dir, filename_from_url(url))
    else:
        filename = save_path
    return filename


def ensure_directory(path: str) -> None:
    """确保目录存在（``os.makedirs(path, exist_ok=True)``）。

    ``OSError`` 原样抛出 —— 界面侧捕获取 ``str(e)`` 填进
    ``tool_download_mkdir_error``，包装成 ``ServiceError`` 会改变那条文案。
    """
    os.makedirs(path, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# 工具 1：垃圾清理
# ═══════════════════════════════════════════════════════════════════


class JunkScanResult(NamedTuple):
    """一次垃圾扫描的结果。"""

    #: 扫描根目录的绝对路径（界面用它算相对路径 / 当根节点显示名）
    base: str
    #: ``(文件路径, 字节数)``，按字节数降序
    files: List[Tuple[str, int]]
    #: 全部命中文件的字节数之和
    total_size: int


class JunkGroup(NamedTuple):
    """同一目录下的一组垃圾文件（已按组内总大小降序排好）。"""

    #: 目录绝对路径（界面用它当字典键）
    dir: str
    #: 展示用文本：相对路径，扫描根本身则显示其目录名
    display: str
    #: ``(文件路径, 字节数)``
    files: List[Tuple[str, int]]


class DeleteResult(NamedTuple):
    """批量删除的结果统计。"""

    deleted: int
    failed: int
    deleted_size: int


def parse_max_depth(text: str) -> int:
    """把输入框文本解析成扫描深度（至少 1）。

    Raises:
        InvalidArgument: 文本不是整数。界面据此显示 ``tool_clean_junk_depth_error``。
    """
    try:
        max_depth = int(text)
    except (TypeError, ValueError):
        raise InvalidArgument("扫描深度不是整数", detail=repr(text)) from None
    return max(1, max_depth)


def scan_junk_files(scan_dir: str, max_depth: int) -> JunkScanResult:
    """扫描 ``.log`` / ``.tmp`` 文件（阻塞式，应在工作线程调用）。

    搬运自 ``_on_clean_junk`` 内 ``_task`` 的扫描循环，**逐字保留**：
    跳过 ``%SystemRoot%``、按 ``max_depth`` 剪枝、``os.path.getsize`` 失败按 0 计、
    最后按大小降序排序。
    """
    base = os.path.abspath(scan_dir)
    junk_files = []
    total_size = 0

    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not is_protected_system_dir(os.path.join(root, d))]
        rel = os.path.relpath(root, base)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirs[:] = []
        for f in files:
            if f.endswith(".log") or f.endswith(".tmp"):
                fp = os.path.join(root, f)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    size = 0
                junk_files.append((fp, size))
                total_size += size

    junk_files.sort(key=lambda item: item[1], reverse=True)
    return JunkScanResult(base=base, files=junk_files, total_size=total_size)


def junk_dir_display(rel_dir: str, base: str) -> str:
    """目录行的展示文本（搬运自 ``_on_clean_junk`` 的 ``_update_ui``）。"""
    if rel_dir == ".":
        return os.path.basename(base.rstrip(os.sep)) or base
    return rel_dir


def group_junk_files(base: str, junk_files: Sequence[Tuple[str, int]]) -> List[JunkGroup]:
    """按父目录分组，组间按组内总字节数降序（搬运自 ``_update_ui`` 的分组循环）。

    排序是**稳定**排序，与原来一致 —— 大小相同时保持首次出现顺序。
    """
    dirs_map: Dict[str, List[Tuple[str, int]]] = {}
    for fp, size in junk_files:
        dirs_map.setdefault(os.path.dirname(fp), []).append((fp, size))

    groups: List[JunkGroup] = []
    for d, files in sorted(
        dirs_map.items(),
        key=lambda item: sum(s for _f, s in item[1]),
        reverse=True,
    ):
        rel_dir = relative_path_from(d, base)
        groups.append(JunkGroup(dir=d, display=junk_dir_display(rel_dir, base), files=files))
    return groups


def delete_junk_files(selected: Sequence[Tuple[Any, str, int]]) -> DeleteResult:
    """删除选中的垃圾文件，返回统计（阻塞式，应在工作线程调用）。

    搬运自 ``_on_delete_selected_junk`` 内 ``_task`` 的删除循环，**逐字保留**：
    单个文件 ``OSError`` 只记日志并计入 ``failed``，不中断其余删除。

    Args:
        selected: 三元组序列 ``(组键, 文件路径, 字节数)``。**组键不参与删除**，
            原样保留是为了让调用方不必重组界面用的数据结构
            （原实现的循环变量就是 ``for _dir_key, fp, size in selected``）。
    """
    deleted = 0
    failed = 0
    deleted_size = 0
    for _dir_key, fp, size in selected:
        try:
            os.remove(fp)
            deleted += 1
            deleted_size += size
        except OSError as e:
            logger.error(f"删除文件失败 {fp}: {e}")
            failed += 1
    return DeleteResult(deleted=deleted, failed=failed, deleted_size=deleted_size)


# ═══════════════════════════════════════════════════════════════════
# 工具 2/3：端口检测 与 服务器信息（Minecraft 协议）
# ═══════════════════════════════════════════════════════════════════


def _mc_write_varint(buf: bytearray, value: int):
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        buf.append(byte)
        if value == 0:
            break


def _mc_read_varint(sock: socket.socket) -> int:
    value = 0
    shift = 0
    while True:
        data = sock.recv(1)
        if not data:
            raise ConnectionError("Connection closed")
        byte = data[0]
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return value


def parse_port(port_str: str, *, validate_range: bool = True) -> int:
    """解析端口号；``validate_range`` 为真时额外做 1~65535 范围校验。

    .. note::
        **记录现状**：搬运前两个入口的校验口径**不一致** ——
        ``_on_test_port`` 做了范围校验，``_on_peek_server`` **只做了 ``int()``**，
        因此 ``99999`` 在"检测端口"里被拒、在"查询服务器"里却会发出去。
        疑为缺陷；按"只搬家不改逻辑"要求用 ``validate_range`` 显式区分，
        两边行为都保持原样。

    Raises:
        InvalidArgument: 不是整数，或（``validate_range=True`` 时）超出范围。
            界面据此显示 ``tool_port_invalid_port``。
    """
    try:
        port = int(port_str)
        if validate_range and (port < 1 or port > 65535):
            raise ValueError
    except (TypeError, ValueError):
        raise InvalidArgument("端口号无效", detail=repr(port_str)) from None
    return port


class PortProbeResult(NamedTuple):
    """端口连通性探测结果。

    是一个 ``tuple`` 子类，因此可以直接替换原来的 5 元组::

        entry = (host, port, status, round(elapsed * 1000, 1), err)

    界面里 ``for h, p, st, lat, e_msg in self._port_results`` 的拆包写法不受影响。
    """

    host: str
    port: int
    #: ``"open"`` / ``"closed"``
    status: str
    latency_ms: float
    #: 失败原因文本；成功为 ``None``
    error: Optional[str]


def _default_socket_factory(*args: Any, **kwargs: Any):
    return socket.socket(*args, **kwargs)


def probe_port(
    host: str,
    port: int,
    *,
    socket_factory: Optional[Callable[..., Any]] = None,
    timeout: float = 5,
) -> PortProbeResult:
    """TCP 连一下目标端口（阻塞式，应在工作线程调用）。

    搬运自 ``_on_test_port`` 内 ``_task``，**逐字保留**：任何异常都收敛成
    ``error`` 文本并把状态置 ``closed``，``latency_ms`` 始终是实际耗时。

    Args:
        socket_factory: 可注入的 ``socket`` 工厂（默认 ``socket.socket``），
            单测传假实现即可完全离线。
        timeout: ``sock.settimeout`` 的值（原为字面量 ``5``）。
    """
    factory = _default_socket_factory if socket_factory is None else socket_factory
    sock = None
    start = time.time()
    err = None
    try:
        sock = factory(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.shutdown(socket.SHUT_RDWR)
    except Exception as e:
        err = str(e)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
    elapsed = time.time() - start

    status = "open" if err is None else "closed"
    return PortProbeResult(
        host=host, port=port, status=status, latency_ms=round(elapsed * 1000, 1), error=err
    )


class PeekResult(NamedTuple):
    """服务器状态查询（Server List Ping）结果。"""

    #: 解析后的 status JSON；失败为 ``None``
    info: Optional[Dict[str, Any]]
    latency_ms: float
    #: 失败原因文本；成功为 ``None``
    error: Optional[str]


def peek_server(
    host: str,
    port: int,
    *,
    socket_factory: Optional[Callable[..., Any]] = None,
    timeout: float = 5,
) -> PeekResult:
    """发一次 Minecraft 1.20.1（协议 767）握手 + Status Request 并解析响应。

    搬运自 ``_on_peek_server`` 内 ``_task``，**逐字保留**：握手包构造、``b"\\x01\\x00"``
    状态请求、三段 varint 读取、按 ``length`` 循环收包、JSON 解析。

    Args:
        socket_factory: 可注入的 ``socket`` 工厂（默认 ``socket.socket``）。
        timeout: ``sock.settimeout`` 的值（原为字面量 ``5``）。
    """
    factory = _default_socket_factory if socket_factory is None else socket_factory
    sock = None
    err = None
    start = time.time()
    info = None
    try:
        sock = factory(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))

        host_bytes = host.encode("utf-8")
        handshake = bytearray()
        _mc_write_varint(handshake, 0x00)
        _mc_write_varint(handshake, 767)
        _mc_write_varint(handshake, len(host_bytes))
        handshake.extend(host_bytes)
        handshake.extend(struct.pack(">H", port))
        _mc_write_varint(handshake, 1)

        packet = bytearray()
        _mc_write_varint(packet, len(handshake))
        packet.extend(handshake)
        sock.sendall(packet)
        sock.sendall(b"\x01\x00")

        _mc_read_varint(sock)
        _mc_read_varint(sock)
        length = _mc_read_varint(sock)
        data = bytearray()
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                break
            data.extend(chunk)
        info = json.loads(data.decode("utf-8"))
    except Exception as e:
        err = str(e)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
    elapsed = round((time.time() - start) * 1000, 1)
    return PeekResult(info=info, latency_ms=elapsed, error=err)


def decode_favicon_data(favicon: str) -> bytes:
    """解出 favicon 的 PNG 字节（搬运自 ``_display_server_info``）。

    原写法：``base64.b64decode(favicon.split(",", 1)[-1] if "," in favicon else favicon)``
    —— 兼容 ``data:image/png;base64,....`` 与裸 base64 两种形式。
    拿去建 ``PIL`` 图片 / ``ImageTk.PhotoImage`` 是界面侧的事。
    """
    return base64.b64decode(favicon.split(",", 1)[-1] if "," in favicon else favicon)


class ServerSummary(NamedTuple):
    """服务器 status JSON 里可以直接渲染的那几个字段。"""

    #: MOTD 文本（已剥 ``§`` 颜色码、已按 60 字截断并补 ``...``；空则取 i18n 的"无 MOTD"）
    desc_text: str
    #: 版本名；``version`` 不是 dict 时取 ``str(version)``
    version_name: str
    online: int
    max_players: int
    #: 在线玩家名（最多 8 个、``" | "`` 连接、超出补 ``" ... +N"``）；无可显示项为 ``""``
    sample_text: str


def summarize_server_info(info: Dict[str, Any]) -> ServerSummary:
    """把 status JSON 解析成可直接渲染的 5 个字段（搬运自 ``_display_server_info``）。

    评估顺序与原来一致：MOTD → 截断 → 版本 → 玩家 → 样本玩家名。
    """
    description = info.get("description", {})
    if isinstance(description, dict):
        desc_text = description.get("text", "")
        extra_list = description.get("extra", [])
        if extra_list:
            parts = []
            for e in extra_list:
                if isinstance(e, dict):
                    parts.append(e.get("text", ""))
                elif isinstance(e, str):
                    parts.append(e)
            desc_text = "".join(parts) if parts else desc_text
    elif isinstance(description, str):
        desc_text = description
    else:
        desc_text = ""
    desc_text = desc_text.replace("§", "").strip() or _("tool_peek_no_motd")

    motd_len = 60
    if len(desc_text) > motd_len:
        desc_text = desc_text[:motd_len] + "..."

    ver = info.get("version", {})
    ver_name = ver.get("name", "?") if isinstance(ver, dict) else str(ver)
    players = info.get("players", {}) if isinstance(info.get("players"), dict) else {}
    online = players.get("online", 0)
    max_p = players.get("max", 0)

    sample_list = players.get("sample", [])
    sample_text = ""
    if sample_list:
        names = []
        for s in sample_list:
            if isinstance(s, dict):
                names.append(s.get("name", "?"))
            elif isinstance(s, str):
                names.append(s)
        if names:
            sample_text = " | ".join(names[:8])
            if len(sample_list) > 8:
                sample_text += f" ... +{len(sample_list) - 8}"

    return ServerSummary(
        desc_text=desc_text,
        version_name=ver_name,
        online=online,
        max_players=max_p,
        sample_text=sample_text,
    )


# ═══════════════════════════════════════════════════════════════════
# 工具 4：坐标转换器
# ═══════════════════════════════════════════════════════════════════


def parse_coordinate(text: str) -> int:
    """解析坐标输入框文本；不能解析时返回 ``0``（搬运自 ``_parse_coord``）。"""
    try:
        return int(text.strip())
    except (AttributeError, ValueError):
        return 0


class CoordinateResult(NamedTuple):
    """一次坐标转换的结果。"""

    x: int
    y: int
    z: int
    #: 转换后的 X / Z
    rx: int
    rz: int
    #: 可直接显示的说明文本（i18n）
    desc: str


def convert_coordinates(x: int, y: int, z: int, target: str) -> CoordinateResult:
    """主世界 ↔ 下界坐标换算（8:1），``target == "nether"`` 时除以 8，否则乘 8。

    搬运自 ``_on_convert_coord``，**逐字保留**（含 ``//`` 向下取整与 i18n 键）。
    """
    if target == "nether":
        rx = x // 8
        rz = z // 8
        desc = _("tool_coord_result_nether", x=x, y=y, z=z, rx=rx, rz=rz)
    else:
        rx = x * 8
        rz = z * 8
        desc = _("tool_coord_result_overworld", x=x, y=y, z=z, rx=rx, rz=rz)
    return CoordinateResult(x=x, y=y, z=z, rx=rx, rz=rz, desc=desc)


# ═══════════════════════════════════════════════════════════════════
# 工具 5：Hash 计算器
# ═══════════════════════════════════════════════════════════════════

#: 界面单选按钮取值 → ``hashlib`` 算法名（搬运自 ``_on_calc_hash`` 的 ``algo_map``）
HASH_ALGORITHMS: Dict[str, str] = {"MD5": "md5", "SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512"}


def hash_file(filepath: str, algorithm: str, *, chunk_size: int = 65536) -> str:
    """分块计算文件摘要（阻塞式，应在工作线程调用），返回十六进制字符串。

    搬运自 ``_on_calc_hash`` 内 ``_task``，**逐字保留**：循环 ``read(chunk_size)``
    直到空块，读盘异常原样抛出（界面侧 ``except Exception`` 显示
    ``tool_hash_calc_error``）。

    Args:
        algorithm: :data:`HASH_ALGORITHMS` 的键（``"MD5"`` / ``"SHA1"`` /
            ``"SHA256"`` / ``"SHA512"``）。键不存在时与搬运前一样是 ``KeyError``
            ——界面只有 4 个单选按钮，此路径不可达，故**原样保留**（记录现状）。
    """
    hasher = hashlib.new(HASH_ALGORITHMS[algorithm])
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


# ═══════════════════════════════════════════════════════════════════
# 工具 6：每日运势
# ═══════════════════════════════════════════════════════════════════


class FortuneResult(NamedTuple):
    """当日运势结果（颜色映射留在界面侧）。"""

    #: ``YYYY-MM-DD``
    today: str
    #: 0~100
    value: int
    #: i18n 键（``tool_fortune_terrible`` … ``tool_fortune_legendary``）
    level_key: str
    #: 等级对应的表情符号
    emoji: str


def daily_fortune(today: Optional[str] = None) -> FortuneResult:
    """按日期生成 0~100 的确定性运势值（搬运自 ``_on_check_fortune``）。

    算法逐字保留：``sha256("fmcl_fortune_<日期>")`` 取模 101，再按
    20/40/60/80/95 分档。颜色映射是展示细节，留在界面侧。

    Args:
        today: ``YYYY-MM-DD``；``None`` 时取 ``date.today().isoformat()``
            （供单测固定日期）。
    """
    if today is None:
        today = date.today().isoformat()
    seed_str = f"fmcl_fortune_{today}"
    seed = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    value = seed % 101

    if value <= 20:
        level_key = "tool_fortune_terrible"
        emoji = "💀"
    elif value <= 40:
        level_key = "tool_fortune_bad"
        emoji = "😟"
    elif value <= 60:
        level_key = "tool_fortune_normal"
        emoji = "😐"
    elif value <= 80:
        level_key = "tool_fortune_good"
        emoji = "😊"
    elif value <= 95:
        level_key = "tool_fortune_great"
        emoji = "🌟"
    else:
        level_key = "tool_fortune_legendary"
        emoji = "👑"

    return FortuneResult(today=today, value=value, level_key=level_key, emoji=emoji)


# ═══════════════════════════════════════════════════════════════════
# 工具 7：MC 冷知识
# ═══════════════════════════════════════════════════════════════════

#: 冷知识题库。原为 ``ToolsTabMixin._MC_FACTS``，**同一个 list 对象**：
#: ``ui/app_tools.py`` 里仍以 ``_MC_FACTS = _tool_svc.MC_FACTS`` 绑定它。
MC_FACTS = [
    "MC 最早叫 Cave Game，2009年由 Notch 用 Java 开发。",
    "爬行者最初是猪的模型 Bug 导致的，模型倒了但代码正确。",
    "Minecraft 的世界比地球大 8 倍，最大可达 6 千万×6 千万方块。",
    "狼可以被染色的项圈染色，用染料右键即可更换颜色。",
    "末影龙是 MC 第一个加入的 Boss，Herobrine 从未正式存在。",
    "金胡萝卜是游戏中回复饱食度最高的食物，性价比远超金苹果。",
    "在下界睡觉会引发爆炸，所以不要在下界放床。",
    "史莱姆不会受到摔落伤害，因为它们太Q弹了。",
    "可以用绳子拴住鸡，带它到悬崖边——但它不会飞。",
    "附魔金苹果需要使用 8 个金块和 1 个苹果合成，非常昂贵。",
    "信标的顶部可以是玻璃，不影响光柱效果。",
    "末影人碰到水会受到伤害，所以它们怕下雨。",
    "用精准采集的工具可以获取完整的草方块而不是泥土。",
    "MC 中的音乐由 C418 创作，其 Sweden 是最知名的曲目。",
    "铁傀儡会送给村民小孩罂粟花，这是 MC 最暖心的细节。",
    "在困难模式下，僵尸可以砸开木门进入房屋。",
    "使用命名牌将生物改名为 Dinnerbone 或 Grumm 会让它倒立。",
    "MC 的甘蔗不需要种在水源旁，水可以隔一格方块。",
    "下界合金装备不会在岩浆中烧毁，掉进岩浆也能捡回来。",
    "海龟壳头盔让你能在水下多呼吸 10 秒。",
    "用剪刀剪羊可获得 1-3 个羊毛，远多于直接击杀。",
    "附魔台上的符文来自银河标准字母，不是乱码。",
    "MC 中一天为 20 分钟，白天 10 分钟，夜晚 7 分钟。",
    "豹猫会吓跑爬行者，养一只在家附近可以有效防爆。",
    "堆肥桶可以通过堆肥获得骨粉，各种植物的堆肥成功率不同。",
    "MC 的 11 号唱片是一段诡异的录音，包含脚步声和逃跑声。",
    "用蜂蜜瓶可以直接合成糖，不需要甘蔗。",
    "哞菇被雷劈中后会变成棕色哞菇，再被雷劈又会变回来。",
    "MC 有超过 400 种可制造的物品。",
    "在 MC 的创造模式中，按 F3+N 可以快速切换旁观模式。",
    "海豚会带领玩家寻找海底遗迹和沉船宝藏。",
    "用胡萝卜钓竿可以控制骑着的猪走向。",
    "MC 首次发布于 2009 年 5 月 17 日，至今已超过 15 年。",
    "营火可以用来熏制食物，比熔炉更快。",
    "凋零是唯一可以由玩家建造并召唤的 Boss。",
    "海晶灯在水下提供光源，亮度与荧石相同。",
    "马的跳跃高度由隐藏的跳高属性决定，优生优育很重要。",
    "用精准采集的镐可以获取完整的末影箱。",
    "工作台的世界其实是一个不断旋转的外景天空盒。",
    "MC 中 Java 版的指令比基岩版更灵活多样。",
]


def fact_of_the_day(today: Optional[str] = None, facts: Optional[Sequence[str]] = None) -> int:
    """按日期选一条冷知识的下标（搬运自 ``_on_new_fact``）。

    ``sha256("fmcl_fact_<日期>")`` 取模题库长度。

    Args:
        today: ``YYYY-MM-DD``；``None`` 时取 ``date.today().isoformat()``。
        facts: 题库；``None`` 时用 :data:`MC_FACTS`（供单测注入小题库）。
    """
    if today is None:
        today = date.today().isoformat()
    pool = MC_FACTS if facts is None else facts
    seed_str = f"fmcl_fact_{today}"
    seed = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    index = seed % len(pool)
    return index


def random_fact_index(facts: Optional[Sequence[str]] = None, rng: Optional[Any] = None) -> int:
    """随机选一条冷知识的下标（搬运自 ``_on_random_fact``）。

    Args:
        facts: 题库；``None`` 时用 :data:`MC_FACTS`。
        rng: 提供 ``randint`` 的对象；``None`` 时用 ``random`` 模块
            （单测可传 ``random.Random(seed)`` 固定结果）。
    """
    pool = MC_FACTS if facts is None else facts
    source = random if rng is None else rng
    index = source.randint(0, len(pool) - 1)
    return index


# ═══════════════════════════════════════════════════════════════════
# 工具 8：MC 知识问答
# ═══════════════════════════════════════════════════════════════════

QUIZ_SYSTEM_PROMPT = (
    """你是一个 Minecraft 知识题库生成器。请生成 20 道 Minecraft 相关的选择题（4个选项）。"""
    """\n输出严格 JSON 数组，每个元素为：\n"""
    """{"id": 序号, "question": "问题", "options": ["A.选项1", "B.选项2", "C.选项3", "D.选项4"], "answer": "正确选项的完整文本（如 B.选项2）", "explanation": "简短解释"}\n"""
    """要求：涵盖 MC 生物、方块、合成、红石、附魔、维度、版本历史、游戏机制等各个方面。"""
)

#: 用户消息（搬运自 ``_quiz_call_ai_generate`` 里的字面量）
QUIZ_USER_PROMPT = "请生成 20 道 Minecraft 知识选择题（JSON 格式）。"

#: 可注入的 AI 对话实现：``(messages) -> {"content": str, ...}``
Chat = Callable[[List[Dict[str, str]]], Any]


def quiz_messages() -> List[Dict[str, str]]:
    """组装发给 AI 的消息列表（搬运自 ``_quiz_call_ai_generate``）。"""
    return [
        {"role": "system", "content": QUIZ_SYSTEM_PROMPT},
        {"role": "user", "content": QUIZ_USER_PROMPT},
    ]


def parse_quiz_response(content: str) -> List[Dict]:
    """从 AI 回复里抠出 JSON 数组（搬运自 ``_quiz_call_ai_generate``）。

    逐字保留原逻辑：取第一个 ``[`` 到最后一个 ``]`` 之间的切片再 ``json.loads``；
    切片不成立或解析结果不是非空 list 时报错。

    Raises:
        InvalidArgument: 未包含有效 JSON 数组 / JSON 不是非空数组。
            文本与原来的 ``ValueError`` **逐字相同**，
            界面 ``tool_quiz_gen_error`` 看到的 ``str(e)`` 不变。
        json.JSONDecodeError: 切片内容不是合法 JSON（与搬运前一样原样抛出）。
    """
    json_start = content.find("[")
    json_end = content.rfind("]") + 1
    if json_start == -1 or json_end <= json_start:
        raise InvalidArgument("AI 返回内容未包含有效的 JSON 数组")
    raw = content[json_start:json_end]
    questions = json.loads(raw)
    if not isinstance(questions, list) or len(questions) == 0:
        raise InvalidArgument("AI 返回的 JSON 格式不正确")
    return questions


def generate_quiz(token: str, *, chat: Chat) -> List[Dict]:
    """调 AI 生成一批题目（阻塞式，应在工作线程调用）。

    搬运自 ``_quiz_call_ai_generate``，**逐字保留**取词与校验顺序：
    空 token → 组装消息 → 调用 → 空内容 → 抠 JSON。

    与界面的唯一接口是 ``chat``：原实现里 ``from ui.agent.providers.jingdu import
    JingduProvider`` 是 **UI 层**的依赖（``services/`` 不得 import ``ui.*``），
    因此由界面侧构造 provider 并注入。

    Args:
        token: 净读（Jingdu）API token。
        chat: ``(messages) -> resp``；``resp.get("content", "")`` 是 AI 正文。

    Raises:
        InvalidArgument: 未登录 / AI 返回空内容 / 内容里没有合法 JSON 数组。
            文本与原来的 ``ValueError`` 逐字相同。
    """
    if not token:
        raise InvalidArgument(_("tool_quiz_not_logged_in"))

    messages = quiz_messages()
    resp = chat(messages)
    content = resp.get("content", "")
    if not content:
        raise InvalidArgument("AI 返回内容为空")

    return parse_quiz_response(content)


def check_choice_answer(selected: str, correct: str) -> bool:
    """选择题判分（搬运自 ``_on_quiz_select``）：``strip()`` 后**大小写敏感**比较。

    .. note::
        这是**记录现状**：与填空题的 :func:`check_text_answer`（大小写不敏感）
        口径不一致，疑为缺陷；按"只搬家不改逻辑"要求原样保留。
    """
    return selected.strip() == correct.strip()


def check_text_answer(answer: str, correct: str) -> bool:
    """填空题判分（搬运自 ``_on_quiz_submit_text``）：``strip().lower()`` 后比较。

    .. note::
        这是**记录现状**：与选择题的 :func:`check_choice_answer`（大小写敏感）
        口径不一致，疑为缺陷；按"只搬家不改逻辑"要求原样保留。
    """
    return answer.strip().lower() == correct.strip().lower()


def load_quiz_questions(path: Path) -> Optional[List[Dict]]:
    """读取题库文件（搬运自 ``_quiz_load_from_file``）。

    Returns:
        ``None``  —— 文件不存在。**调用方应保持现有题目不动**，与原实现一致
        （原实现只在 ``path.exists()`` 时才给 ``self._quiz_questions`` 赋值）。
        ``[]``    —— 文件存在但读取/解析失败，或内容不是 list。
        ``list``  —— 解析成功。
    """
    questions: List[Dict] = []
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                questions = data
        except Exception:
            questions = []
        return questions
    return None


def save_quiz_questions(path: Path, questions: List[Dict]) -> None:
    """写题库文件（搬运自 ``_quiz_save_to_file``）。

    **逐字保留原有错误处理**（记录现状）：
    ``path.parent.mkdir(parents=True, exist_ok=True)`` 在 ``try`` 之外 —— 建目录
    失败会抛出；写文件失败只记日志、静默返回。日志文本原本硬编码为
    "保存 quiz.json 失败"，此处原样保留。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存 quiz.json 失败: {e}")


def reindex_questions(questions: Sequence[Dict], start: int = 1) -> List[Dict]:
    """给题目重编号（搬运自 ``_on_quiz_generate`` 内 ``_task``）。

    编号从 ``start`` 起连续递增，**就地把 ``id`` 写回原 dict**，
    返回与入参同一批 dict 组成的新列表。
    """
    reindexed = []
    for i, q in enumerate(questions):
        q["id"] = start + i
        reindexed.append(q)
    return reindexed


def merge_generated_questions(existing: Sequence[Dict], new_questions: Sequence[Dict]) -> List[Dict]:
    """把新生成的题目接在现有题库后面并重编号（搬运自 ``_quiz_auto_refill`` 内 ``_task``）。

    ``max_id`` 只认 ``existing`` 里 ``isinstance(q.get("id"), int)`` 的编号，
    新题从 ``max_id + 1`` 开始编号。**不改动 ``existing``** ——
    调用方自己 ``extend(...)``（原实现就是这么写的）。
    """
    max_id = 0
    for q in existing:
        if isinstance(q.get("id"), int) and q["id"] > max_id:
            max_id = q["id"]
    reindexed = []
    for i, q in enumerate(new_questions):
        q["id"] = max_id + i + 1
        reindexed.append(q)
    return reindexed


def drop_question(questions: List[Dict], index: int) -> None:
    """原地删掉第 ``index`` 题（搬运自 ``_on_quiz_next``）。

    下标越界时**什么都不做**（原实现是 ``if index < len(...): pop(index)``）。
    """
    if index < len(questions):
        questions.pop(index)


# ═══════════════════════════════════════════════════════════════════
# 工具 9（界面里的第 8 个卡片）：多线程下载器
# ═══════════════════════════════════════════════════════════════════


class DownloadOutcome(NamedTuple):
    """一次下载的结局。"""

    #: ``"success"`` 或 ``"cancelled"``
    status: str
    #: 最终落盘路径（取消时是"本该落盘"的路径）
    path: str
    #: 总字节数
    size: int


def _never_cancelled() -> bool:
    return False


def _ignore_progress(downloaded: int, total: int, speed: float) -> None:
    return None


def download_multi(
    url: str,
    dest: str,
    *,
    user_agent: str = "FMCL/2.0",
    threads: int = 4,
    session: Optional[Any] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, int, float], None]] = None,
) -> DownloadOutcome:
    """多线程（HTTP Range 分段）下载到 ``dest``（阻塞式，应在工作线程调用）。

    搬运自 ``_on_start_download`` 内 ``_task``，**逐字保留**，只有一处**刻意偏离**：

    * **进度回调不再被时钟刻度挡住**（缺陷 D-154）。原实现在 ``if elapsed > 0:`` 里同时
      干"算速率"和"发进度"两件事，那层本意只是给除法防零，副作用却是**同一个时钟刻度内
      下完的文件一次回调都不发**（界面进度条全程不动，而文件已经落盘）。
      现在速率只在跨过刻度时才算（没跨过给 ``0.0``），``emit`` **每块都发**。

    其余步骤：

    1. ``HEAD`` 取 ``Content-Length``（超时 15s）。
    2. 拿不到长度（``0``）时退化为**单流 GET** 一次性落盘（超时 30s）。
    3. 否则切成 ``threads`` 段并发 GET（超时 60s）写 ``<dest>.partN``，
       全部结束后按序拼接并删掉分片。
    4. 取消发生在拼接之前时删掉分片并返回 ``status="cancelled"``。

    Args:
        url: 下载地址。
        dest: **最终落盘文件路径**（由调用方经 :func:`resolve_download_target` 决定）。
        user_agent: 请求头 ``User-Agent``。
        threads: 分段数。
        session: 可注入的 HTTP 会话（默认 ``requests`` 模块本身），
            需提供 ``head`` / ``get``，单测注入假实现即可完全离线。
        cancel_check: 返回是否已取消；默认永不取消。
        on_progress: ``(downloaded, total, speed) -> None``，在**分段下载的写锁内**
            按块触发（与原来 ``self.after(0, _update)`` 的位置一致）；
            **每块都发**（不因"没跨过时钟刻度"而跳过，见 D-154）；默认空实现。
            单流回退分支（拿不到 ``Content-Length``）**不发**进度 —— 原实现如此。

    Raises:
        Exception: 底层 ``requests`` / 文件 IO 异常原样向上抛，由调用方决定怎么提示
            （界面侧就是 ``except Exception as e`` → ``tool_download_error``）。
        Exception("cancelled"): 单流分支中途被取消。**这是原实现的写法，原样保留**
            ——调用方沿用 ``if str(e) == "cancelled": return`` 的静默处理。
            （记录现状：界面里没有任何地方把取消标志置 True，此路径不可达。）

    .. note::
        ``total_size < threads`` 时 ``part_size`` 为 0，除最后一段外都会退化成
        ``Range: bytes=0--1``。这是**搬运前就有的行为**，未修（记录现状）。
    """
    http = requests if session is None else session
    is_cancelled = _never_cancelled if cancel_check is None else cancel_check
    emit = _ignore_progress if on_progress is None else on_progress

    resp = http.head(url, headers={"User-Agent": user_agent}, timeout=15)
    resp.raise_for_status()

    total_size = int(resp.headers.get("Content-Length", 0))
    if total_size == 0:
        resp2 = http.get(url, headers={"User-Agent": user_agent}, stream=True, timeout=30)
        resp2.raise_for_status()
        chunks = []
        for chunk in resp2.iter_content(chunk_size=8192):
            if is_cancelled():
                resp2.close()
                raise Exception("cancelled")
            chunks.append(chunk)
        content = b"".join(chunks)
        total_size = len(content)
        with open(dest, "wb") as f:
            f.write(content)
        return DownloadOutcome(status="success", path=dest, size=total_size)

    downloaded = 0
    lock = threading.Lock()
    start_time = time.time()
    part_size = total_size // threads

    def _dl_part(start: int, end: int, idx: int):
        nonlocal downloaded
        headers = {"User-Agent": user_agent, "Range": f"bytes={start}-{end}"}
        part_file = f"{dest}.part{idx}"
        try:
            r = http.get(url, headers=headers, stream=True, timeout=60)
            r.raise_for_status()
            with open(part_file, "wb") as pf:
                for chunk in r.iter_content(chunk_size=8192):
                    if is_cancelled():
                        r.close()
                        return
                    if chunk:
                        pf.write(chunk)
                        with lock:
                            downloaded += len(chunk)
                            elapsed = time.time() - start_time
                            # D-154：`emit` 不许再被这个防除零的判断挡住 ——
                            # 一个时钟刻度内下完的文件也要报进度（理由与实测见函数文档）。
                            speed = downloaded / elapsed if elapsed > 0 else 0.0
                            emit(downloaded, total_size, speed)
        except Exception as e:
            logger.error(f"分段下载 {idx} 失败: {e}")
            raise

    threads_list = []
    for i in range(threads):
        start_byte = i * part_size
        end_byte = start_byte + part_size - 1 if i < threads - 1 else total_size - 1
        t = threading.Thread(target=_dl_part, args=(start_byte, end_byte, i))
        t.daemon = True
        threads_list.append(t)
        t.start()

    for t in threads_list:
        t.join()

    if is_cancelled():
        for i in range(threads):
            pf = f"{dest}.part{i}"
            if os.path.exists(pf):
                os.remove(pf)
        return DownloadOutcome(status="cancelled", path=dest, size=total_size)

    with open(dest, "wb") as outf:
        for i in range(threads):
            pf = f"{dest}.part{i}"
            if os.path.exists(pf):
                with open(pf, "rb") as inf:
                    outf.write(inf.read())
                os.remove(pf)

    return DownloadOutcome(status="success", path=dest, size=total_size)


# ═══════════════════════════════════════════════════════════════════
# 服务门面
# ═══════════════════════════════════════════════════════════════════


class ToolService(Service):
    """工具箱服务（``name = "tool"``）。

    每个方法都是上文模块级函数的薄转发。之所以保留实例方法这一层：界面侧统一写
    ``self._tool_service().scan_junk_files(...)``，阶段 2 接上 ``AppContext`` 后
    只要把实例换成注册表里的那一个，调用点不用动。

    本服务**不需要** ``AppContext``：``ToolService()`` 可以独立实例化并直接使用
    （阶段 1 的 Tk 界面就是这么取的），也不访问 ``self.ui`` / ``self.tasks`` /
    ``self.config`` —— 需要弹窗、确认框或后台线程时由调用方负责。

    Args:
        context: 可选 ``AppContext``。
        chat: 可选默认 AI 对话实现（供 :meth:`generate_quiz`）。
        session: 可选默认 HTTP 会话（供 :meth:`download_multi`）。
        socket_factory: 可选默认 socket 工厂（供 :meth:`probe_port` / :meth:`peek_server`）。
    """

    name = "tool"
    label = "工具箱"

    def __init__(
        self,
        context: Optional["AppContext"] = None,
        *,
        chat: Optional[Chat] = None,
        session: Optional[Any] = None,
        socket_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        super().__init__(context)
        self._chat = chat
        self._session = session
        self._socket_factory = socket_factory

    # ─── 通用 ────────────────────────────────────────────────

    def format_size(self, bytes_count: int) -> str:
        """转发 :func:`_format_size`。"""
        return _format_size(bytes_count)

    def relative_path_from(self, path: str, base: str) -> str:
        """转发 :func:`relative_path_from`。"""
        return relative_path_from(path, base)

    def is_protected_system_dir(self, path: str) -> bool:
        """转发 :func:`is_protected_system_dir`。"""
        return is_protected_system_dir(path)

    # ─── 垃圾清理 ────────────────────────────────────────────

    def parse_max_depth(self, text: str) -> int:
        """转发 :func:`parse_max_depth`。"""
        return parse_max_depth(text)

    def scan_junk_files(self, scan_dir: str, max_depth: int) -> JunkScanResult:
        """转发 :func:`scan_junk_files`。"""
        return scan_junk_files(scan_dir, max_depth)

    def group_junk_files(self, base: str, junk_files: Sequence[Tuple[str, int]]) -> List[JunkGroup]:
        """转发 :func:`group_junk_files`。"""
        return group_junk_files(base, junk_files)

    def delete_junk_files(self, selected: Sequence[Tuple[Any, str, int]]) -> DeleteResult:
        """转发 :func:`delete_junk_files`。"""
        return delete_junk_files(selected)

    # ─── 端口检测 / 服务器信息 ───────────────────────────────

    def parse_port(self, port_str: str, *, validate_range: bool = True) -> int:
        """转发 :func:`parse_port`。"""
        return parse_port(port_str, validate_range=validate_range)

    def probe_port(
        self,
        host: str,
        port: int,
        *,
        socket_factory: Optional[Callable[..., Any]] = None,
        timeout: float = 5,
    ) -> PortProbeResult:
        """转发 :func:`probe_port`（``socket_factory`` 缺省时用构造期注入的那个）。"""
        return probe_port(
            host, port, socket_factory=socket_factory or self._socket_factory, timeout=timeout
        )

    def peek_server(
        self,
        host: str,
        port: int,
        *,
        socket_factory: Optional[Callable[..., Any]] = None,
        timeout: float = 5,
    ) -> PeekResult:
        """转发 :func:`peek_server`（``socket_factory`` 缺省时用构造期注入的那个）。"""
        return peek_server(
            host, port, socket_factory=socket_factory or self._socket_factory, timeout=timeout
        )

    def decode_favicon_data(self, favicon: str) -> bytes:
        """转发 :func:`decode_favicon_data`。"""
        return decode_favicon_data(favicon)

    def summarize_server_info(self, info: Dict[str, Any]) -> ServerSummary:
        """转发 :func:`summarize_server_info`。"""
        return summarize_server_info(info)

    # ─── 坐标转换 ────────────────────────────────────────────

    def parse_coordinate(self, text: str) -> int:
        """转发 :func:`parse_coordinate`。"""
        return parse_coordinate(text)

    def convert_coordinates(self, x: int, y: int, z: int, target: str) -> CoordinateResult:
        """转发 :func:`convert_coordinates`。"""
        return convert_coordinates(x, y, z, target)

    # ─── Hash ────────────────────────────────────────────────

    def hash_file(self, filepath: str, algorithm: str) -> str:
        """转发 :func:`hash_file`。"""
        return hash_file(filepath, algorithm)

    # ─── 每日运势 / 冷知识 ───────────────────────────────────

    def daily_fortune(self, today: Optional[str] = None) -> FortuneResult:
        """转发 :func:`daily_fortune`。"""
        return daily_fortune(today)

    def fact_of_the_day(self, today: Optional[str] = None, facts: Optional[Sequence[str]] = None) -> int:
        """转发 :func:`fact_of_the_day`。"""
        return fact_of_the_day(today, facts)

    def random_fact_index(self, facts: Optional[Sequence[str]] = None, rng: Optional[Any] = None) -> int:
        """转发 :func:`random_fact_index`。"""
        return random_fact_index(facts, rng)

    # ─── 知识问答 ────────────────────────────────────────────

    def generate_quiz(self, token: str, *, chat: Optional[Chat] = None) -> List[Dict]:
        """转发 :func:`generate_quiz`（``chat`` 缺省时用构造期注入的那个）。"""
        impl = chat or self._chat
        if impl is None:
            raise InvalidArgument("未注入 AI 对话实现（chat）")
        return generate_quiz(token, chat=impl)

    def parse_quiz_response(self, content: str) -> List[Dict]:
        """转发 :func:`parse_quiz_response`。"""
        return parse_quiz_response(content)

    def check_choice_answer(self, selected: str, correct: str) -> bool:
        """转发 :func:`check_choice_answer`。"""
        return check_choice_answer(selected, correct)

    def check_text_answer(self, answer: str, correct: str) -> bool:
        """转发 :func:`check_text_answer`。"""
        return check_text_answer(answer, correct)

    def load_quiz_questions(self, path: Path) -> Optional[List[Dict]]:
        """转发 :func:`load_quiz_questions`。"""
        return load_quiz_questions(path)

    def save_quiz_questions(self, path: Path, questions: List[Dict]) -> None:
        """转发 :func:`save_quiz_questions`。"""
        save_quiz_questions(path, questions)

    def reindex_questions(self, questions: Sequence[Dict], start: int = 1) -> List[Dict]:
        """转发 :func:`reindex_questions`。"""
        return reindex_questions(questions, start)

    def merge_generated_questions(
        self, existing: Sequence[Dict], new_questions: Sequence[Dict]
    ) -> List[Dict]:
        """转发 :func:`merge_generated_questions`。"""
        return merge_generated_questions(existing, new_questions)

    def drop_question(self, questions: List[Dict], index: int) -> None:
        """转发 :func:`drop_question`。"""
        drop_question(questions, index)

    # ─── 下载器 ──────────────────────────────────────────────

    def filename_from_url(self, url: str) -> str:
        """转发 :func:`filename_from_url`。"""
        return filename_from_url(url)

    def resolve_download_target(self, url: str, save_path: str, save_dir: str) -> str:
        """转发 :func:`resolve_download_target`。"""
        return resolve_download_target(url, save_path, save_dir)

    def ensure_directory(self, path: str) -> None:
        """转发 :func:`ensure_directory`。"""
        ensure_directory(path)

    def download_multi(
        self,
        url: str,
        dest: str,
        *,
        user_agent: str = "FMCL/2.0",
        threads: int = 4,
        session: Optional[Any] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
    ) -> DownloadOutcome:
        """转发 :func:`download_multi`（``session`` 缺省时用构造期注入的那个）。"""
        return download_multi(
            url,
            dest,
            user_agent=user_agent,
            threads=threads,
            session=session or self._session,
            cancel_check=cancel_check,
            on_progress=on_progress,
        )


__all__ = [
    "Chat",
    "CoordinateResult",
    "DeleteResult",
    "DownloadOutcome",
    "FortuneResult",
    "HASH_ALGORITHMS",
    "JunkGroup",
    "JunkScanResult",
    "MC_FACTS",
    "PeekResult",
    "PortProbeResult",
    "QUIZ_SYSTEM_PROMPT",
    "QUIZ_USER_PROMPT",
    "ServerSummary",
    "ToolService",
    "check_choice_answer",
    "check_text_answer",
    "convert_coordinates",
    "daily_fortune",
    "decode_favicon_data",
    "delete_junk_files",
    "download_multi",
    "drop_question",
    "ensure_directory",
    "fact_of_the_day",
    "filename_from_url",
    "generate_quiz",
    "group_junk_files",
    "hash_file",
    "is_protected_system_dir",
    "junk_dir_display",
    "load_quiz_questions",
    "merge_generated_questions",
    "parse_coordinate",
    "parse_max_depth",
    "parse_port",
    "parse_quiz_response",
    "peek_server",
    "probe_port",
    "quiz_messages",
    "random_fact_index",
    "reindex_questions",
    "relative_path_from",
    "resolve_download_target",
    "save_quiz_questions",
    "scan_junk_files",
    "summarize_server_info",
]
