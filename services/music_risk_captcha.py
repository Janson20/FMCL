"""B站风控验证码交互 - 本地验证页 + 系统浏览器

gaia-vgate 的 v_voucher 风控需要用户完成 geetest 滑块验证（无法自动通过）。
本模块起一个仅监听 127.0.0.1 的临时 HTTP 服务提供验证页，用系统浏览器打开，
geetest 完成回调经本地接口回传，供调用方换取 grisk_id。

安全设计:
    - 只绑定 127.0.0.1 + 随机端口
    - 页面内嵌一次性随机 token，回调接口校验，防本机其他程序伪造
    - 固定超时（默认 300 秒），支持外部取消
"""

import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional

# geetest 官方初始化脚本（v0.4.8，PiliPlus 同款）
GEETEST_JS_URL = "https://static.geetest.com/static/tools/gt.js"
# 验证交互超时（秒）
DEFAULT_TIMEOUT = 300

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>B站安全验证</title>
<style>
  body {{ font-family: "Microsoft YaHei", sans-serif; background: #f5f6f7;
        display: flex; flex-direction: column; align-items: center;
        justify-content: center; min-height: 90vh; margin: 0; }}
  h3 {{ color: #333; }}
  .hint {{ color: #888; font-size: 13px; margin-top: 4px; }}
</style>
<script src="{gtjs}"></script>
</head>
<body>
<h3>B站触发安全验证</h3>
<p class="hint">请在下方完成滑块验证，完成后自动继续，可返回 FMCL</p>
<div id="captcha" style="width: 100%; max-width: 360px;"></div>
<script>
initGeetest({{
  gt: "{gt}",
  challenge: "{challenge}",
  offline: false,
  new_captcha: true,
  product: "bind",
  width: "100%"
}}, function (captchaObj) {{
  captchaObj.onReady(function () {{
    captchaObj.verify();
  }});
  captchaObj.onSuccess(function () {{
    var r = captchaObj.getValidate();
    fetch("{callback_url}", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        token: "{callback_token}",
        challenge: r.geetest_challenge,
        seccode: r.geetest_seccode,
        validate: r.geetest_validate
      }})
    }}).then(function (resp) {{
      if (resp.ok) {{
        document.body.innerHTML = "<h3 style='color:#00a65a'>验证通过，请返回 FMCL</h3>";
      }} else {{
        document.body.innerHTML = "<h3 style='color:#dd4b39'>验证失败，请关闭页面重试</h3>";
      }}
    }});
  }});
}});
</script>
</body>
</html>
"""


def build_captcha_page(gt: str, challenge: str, callback_token: str, port: int) -> str:
    """生成验证页 HTML（含 geetest 初始化与回调上报）"""
    callback_url = f"http://127.0.0.1:{port}/callback"
    return _PAGE_TEMPLATE.format(
        gtjs=GEETEST_JS_URL,
        gt=gt,
        challenge=challenge,
        callback_url=callback_url,
        callback_token=callback_token,
    )


def run_captcha_flow(
    gt: str,
    challenge: str,
    on_status: Optional[Callable[[str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[Dict]:
    """打开浏览器验证页并等待 geetest 完成回调。

    Args:
        gt: geetest gt 参数
        challenge: geetest challenge 参数
        on_status: 状态回调（在调用线程同步调用，GUI 场景需自行调度到主线程）
        stop_event: 外部取消标志（置位后立即返回 None）
        timeout: 等待超时秒数

    Returns:
        {"geetest_challenge", "geetest_seccode", "geetest_validate"} 或 None（取消/超时/失败）
    """
    callback_token = secrets.token_hex(16)
    result_box: Dict = {}
    received = threading.Event()
    stop = stop_event or threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str = ""):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/") == "/captcha":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                page = build_captcha_page(gt, challenge, callback_token, self.server.server_address[1])
                data = page.encode()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send(404)

        def do_POST(self):
            if self.path.rstrip("/") != "/callback":
                self._send(404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
            except Exception:
                self._send(400, "bad request")
                return
            if body.get("token") != callback_token:
                self._send(403, "forbidden")
                return
            payload = {
                "geetest_challenge": str(body.get("challenge", "")),
                "geetest_seccode": str(body.get("seccode", "")),
                "geetest_validate": str(body.get("validate", "")),
            }
            if not all(payload.values()):
                self._send(400, "missing fields")
                return
            result_box["payload"] = payload
            received.set()
            self._send(200, "ok")

        def log_message(self, *args):
            pass  # 静默访问日志

    server = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
    except OSError as e:
        if on_status:
            on_status(f"启动验证服务失败: {e}")
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if on_status:
        on_status("正在打开浏览器...")
    webbrowser.open(f"http://127.0.0.1:{port}/captcha")
    try:
        # 等待回调，期间响应取消事件
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if received.is_set():
                if on_status:
                    on_status("验证通过，正在继续...")
                return result_box.get("payload")
            if stop.is_set():
                return None
            received.wait(timeout=0.5)
        if on_status:
            on_status("等待验证超时")
        return None
    finally:
        server.shutdown()
        server.server_close()
        if stop.is_set() and on_status:
            on_status("验证已取消")
