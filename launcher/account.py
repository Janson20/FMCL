"""全局账号管理系统 - 微软正版登录、离线登录、Yggdrasil外置登录

基于 minecraft-launcher-lib 实现 fmcl 风格的账号管理：
- 微软正版登录（PKCE OAuth 流程）
- 离线登录（UUID 生成）
- Yggdrasil 外置登录（皮肤站等第三方认证）
- Token 加密存储与自动续期
- authlib-injector 自动下载与管理
- 账号导入/导出（密码加密）
"""

import base64
import enum
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import uuid as uuid_mod
import webbrowser
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, Callable, List, Dict, Tuple

import requests
from logzero import logger

import platform as _platform_mod

from secure_storage import encrypt_token, decrypt_token


def _build_ua_header() -> dict:
    return {"User-Agent": f"FMCL/1.0 ({_platform_mod.system()}; {_platform_mod.machine()})"}

MICROSOFT_CLIENT_ID = "980ebc21-3288-46a6-bfe1-fc584ba7713e"
MICROSOFT_REDIRECT_URI = "http://localhost:8080"
MICROSOFT_REDIRECT_PORTS = [8080, 8081, 8082, 8083, 8084]

AUTHLIB_INJECTOR_VERSION = "1.2.5"
AUTHLIB_INJECTOR_URL = (
    "https://pysio.online/static/mirror/authlib-injector/authlib-injector-{version}.jar"
)
AUTHLIB_INJECTOR_MIRRORS = [
    "https://authlib-injector.yushi.moe/artifact/{version}/authlib-injector-{version}.jar",
    "https://pysio.online/static/mirror/authlib-injector/authlib-injector-{version}.jar",
]

OAUTH_TIMEOUT_SECONDS = 300


class AccountType(enum.Enum):
    MICROSOFT = "microsoft"
    OFFLINE = "offline"
    YGGDRASIL = "yggdrasil"


@dataclass
class Account:
    id: str
    name: str
    account_type: AccountType
    uuid: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    client_token: Optional[str] = None
    yggdrasil_server_url: Optional[str] = None
    skins: List[Dict] = field(default_factory=list)
    created_at: Optional[str] = None
    last_login: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "account_type": self.account_type.value,
        }
        if self.uuid:
            d["uuid"] = self.uuid
        if self.access_token:
            d["access_token"] = encrypt_token(self.access_token)
        if self.refresh_token:
            d["refresh_token"] = encrypt_token(self.refresh_token)
        if self.client_token:
            d["client_token"] = self.client_token
        if self.yggdrasil_server_url:
            d["yggdrasil_server_url"] = self.yggdrasil_server_url
        if self.skins:
            d["skins"] = self.skins
        if self.created_at:
            d["created_at"] = self.created_at
        if self.last_login:
            d["last_login"] = self.last_login
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        account_type = AccountType(data.get("account_type", "offline"))
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        if access_token:
            access_token = decrypt_token(access_token)
        if refresh_token:
            refresh_token = decrypt_token(refresh_token)
        return cls(
            id=data["id"],
            name=data["name"],
            account_type=account_type,
            uuid=data.get("uuid"),
            access_token=access_token,
            refresh_token=refresh_token,
            client_token=data.get("client_token"),
            yggdrasil_server_url=data.get("yggdrasil_server_url"),
            skins=data.get("skins", []),
            created_at=data.get("created_at"),
            last_login=data.get("last_login"),
        )

    def is_token_expired(self) -> bool:
        if not self.access_token:
            return True
        if self.account_type != AccountType.MICROSOFT:
            return False
        return False

    @property
    def display_name(self) -> str:
        type_labels = {
            AccountType.MICROSOFT: "\u5FAE\u8F6F",
            AccountType.OFFLINE: "\u79BB\u7EBF",
            AccountType.YGGDRASIL: "\u5916\u7F6E",
        }
        return f"{self.name} ({type_labels.get(self.account_type, '')})"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_path.query)

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

        if "code" in query_params:
            self.server.auth_code = query_params["code"][0]
            self.server.auth_state = query_params.get("state", [None])[0]
            html = (
                "<html><head><meta charset='utf-8'></head><body>"
                "<h1>\u767B\u5F55\u6210\u529F\uFF01</h1>"
                "<p>\u60A8\u53EF\u4EE5\u5173\u95ED\u6B64\u7A97\u53E3\u8FD4\u56DE\u542F\u52A8\u5668\u3002</p>"
                "</body></html>"
            )
            self.wfile.write(html.encode("utf-8"))
        else:
            error_desc = query_params.get("error_description", ["\u672A\u77E5\u9519\u8BEF"])[0]
            html = (
                "<html><head><meta charset='utf-8'></head><body>"
                f"<h1>\u767B\u5F55\u5931\u8D25</h1><p>{error_desc}</p>"
                "</body></html>"
            )
            self.wfile.write(html.encode("utf-8"))
        self.server.shutdown_flag = True

    def log_message(self, format, *args):
        pass


class MicrosoftLoginManager:
    def __init__(self, client_id: str = MICROSOFT_CLIENT_ID):
        self.client_id = client_id
        self._redirect_port: int = MICROSOFT_REDIRECT_PORTS[0]

    def _find_available_port(self) -> int:
        import socket
        for port in MICROSOFT_REDIRECT_PORTS:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port
        return MICROSOFT_REDIRECT_PORTS[0]

    def _complete_login_with_error_handling(
        self, client_id: str, redirect_uri: str, auth_code: str,
        code_verifier: str, status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[dict]:
        import minecraft_launcher_lib.microsoft_account as microsoft

        token_resp = microsoft.get_authorization_token(
            client_id, None, redirect_uri, auth_code, code_verifier
        )
        if "access_token" not in token_resp:
            error_msg = token_resp.get("error_description") or token_resp.get("error") or str(token_resp)
            logger.error(f"微软 OAuth Token 交换失败: {error_msg}")
            if status_callback:
                status_callback(f"Token 交换失败: {error_msg}")
            return None

        return self._do_minecraft_auth(
            token_resp["access_token"], token_resp["refresh_token"], status_callback
        )

    def _do_minecraft_auth(
        self, ms_access_token: str, ms_refresh_token: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[dict]:
        import minecraft_launcher_lib.microsoft_account as microsoft
        import minecraft_launcher_lib.exceptions as mcll_exceptions

        try:
            xbl_resp = microsoft.authenticate_with_xbl(ms_access_token)
            xsts_resp = microsoft.authenticate_with_xsts(xbl_resp["Token"])
            xbl_uhs = xbl_resp["DisplayClaims"]["xui"][0]["uhs"]
            mc_resp = microsoft.authenticate_with_minecraft(xbl_uhs, xsts_resp["Token"])
        except mcll_exceptions.AccountNotOwnMinecraft:
            logger.error("该微软账号未拥有 Minecraft")
            if status_callback:
                status_callback("该微软账号未拥有 Minecraft")
            return None
        except Exception as e:
            logger.error(f"微软 XBL/XSTS/Minecraft 认证失败: {e}")
            if status_callback:
                status_callback(f"认证失败: {e}")
            return None

        if "access_token" not in mc_resp:
            logger.error("Azure 应用未获得 Minecraft API 权限，请使用已授权的 Client ID")
            if status_callback:
                status_callback("Azure 应用未获得 Minecraft API 权限")
            return None

        try:
            profile = microsoft.get_profile(mc_resp["access_token"])
        except Exception as e:
            logger.error(f"获取 Minecraft 档案失败: {e}")
            if status_callback:
                status_callback(f"获取档案失败: {e}")
            return None

        if "error" in profile and profile["error"] == "NOT_FOUND":
            logger.error("该账号未拥有 Minecraft")
            if status_callback:
                status_callback("该账号未拥有 Minecraft")
            return None

        profile["access_token"] = mc_resp["access_token"]
        profile["refresh_token"] = ms_refresh_token
        return profile

    def login(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[Account]:
        import minecraft_launcher_lib.microsoft_account as microsoft

        self._redirect_port = self._find_available_port()
        redirect_uri = f"http://localhost:{self._redirect_port}"

        if status_callback:
            status_callback("\u6B63\u5728\u83B7\u53D6\u767B\u5F55\u94FE\u63A5...")

        try:
            login_url, state, code_verifier = microsoft.get_secure_login_data(
                self.client_id, redirect_uri
            )
        except Exception as e:
            logger.error(f"\u83B7\u53D6\u5FAE\u8F6F\u767B\u5F55\u6570\u636E\u5931\u8D25: {e}")
            return None

        server = HTTPServer(("localhost", self._redirect_port), OAuthCallbackHandler)
        server.auth_code = None
        server.auth_state = None
        server.shutdown_flag = False

        if status_callback:
            status_callback("\u6B63\u5728\u6253\u5F00\u6D4F\u89C8\u5668...")
        webbrowser.open(login_url)

        def run_server():
            while not server.shutdown_flag:
                server.handle_request()

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        timeout = OAUTH_TIMEOUT_SECONDS
        start_time = time.time()
        while server.auth_code is None and (time.time() - start_time) < timeout:
            time.sleep(0.5)

        server.shutdown_flag = True
        try:
            server.server_close()
        except Exception:
            pass

        if server.auth_code is None:
            logger.error("\u5FAE\u8F6F\u767B\u5F55\u8D85\u65F6")
            if status_callback:
                status_callback("\u767B\u5F55\u8D85\u65F6\uFF0C\u8BF7\u91CD\u8BD5")
            return None

        if server.auth_state != state:
            logger.error(f"OAuth state \u4E0D\u5339\u914D: expected={state}, got={server.auth_state}")
            return None

        if status_callback:
            status_callback("\u6B63\u5728\u5B8C\u6210\u767B\u5F55...")

        try:
            login_data = self._complete_login_with_error_handling(
                self.client_id, redirect_uri, server.auth_code, code_verifier, status_callback
            )
            if login_data is None:
                return None
        except Exception as e:
            logger.error(f"\u5B8C\u6210\u5FAE\u8F6F\u767B\u5F55\u5931\u8D25: {e}")
            if status_callback:
                status_callback(f"登录失败: {e}")
            return None

        account_id = str(uuid_mod.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        return Account(
            id=account_id,
            name=login_data.get("name", "Steve"),
            account_type=AccountType.MICROSOFT,
            uuid=login_data.get("id", ""),
            access_token=login_data.get("access_token", ""),
            refresh_token=login_data.get("refresh_token", ""),
            skins=login_data.get("skins", []),
            created_at=now,
            last_login=now,
        )

    def refresh_token(self, account: Account) -> bool:
        if not account.refresh_token:
            logger.warning(f"\u5FAE\u8F6F\u8D26\u53F7 {account.name} \u6CA1\u6709 refresh_token")
            return False

        logger.info(f"\u6B63\u5728\u5237\u65B0\u5FAE\u8F6F\u8D26\u53F7 {account.name} \u7684 Token...")

        try:
            import minecraft_launcher_lib.microsoft_account as microsoft
            import minecraft_launcher_lib.exceptions as mcll_exceptions
            refresh_data = microsoft.complete_refresh(
                self.client_id,
                None,
                f"http://localhost:{self._redirect_port}",
                account.refresh_token,
            )
            account.access_token = refresh_data.get("access_token", account.access_token)
            new_refresh = refresh_data.get("refresh_token")
            if new_refresh:
                account.refresh_token = new_refresh
            account.last_login = datetime.now(timezone.utc).isoformat()
            logger.info(f"\u5FAE\u8F6F\u8D26\u53F7 {account.name} Token \u5237\u65B0\u6210\u529F")
            return True
        except Exception as e:
            logger.warning(f"\u5FAE\u8F6F\u8D26\u53F7 {account.name} Token \u5237\u65B0\u5931\u8D25: {e}")
            return False

    def login_device_code(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[Account]:
        DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
        TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
        SCOPE = "XboxLive.signin offline_access"
        DEVICE_LOGIN_PAGE = "https://microsoft.com/devicelogin"

        headers = _build_ua_header()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        if status_callback:
            status_callback("正在获取设备代码...")

        try:
            resp = requests.post(
                DEVICE_CODE_URL,
                data={"client_id": self.client_id, "scope": SCOPE},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            device_data = resp.json()
        except Exception as e:
            logger.error(f"获取设备代码失败: {e}")
            if status_callback:
                status_callback(f"获取设备代码失败: {e}")
            return None

        if "error" in device_data:
            error_msg = device_data.get("error_description") or device_data.get("error")
            logger.error(f"设备代码请求错误: {error_msg}")
            if status_callback:
                status_callback(f"设备代码错误: {error_msg}")
            return None

        user_code = device_data["user_code"]
        device_code_val = device_data["device_code"]
        expires_in = device_data.get("expires_in", 900)
        interval = device_data.get("interval", 5)

        import tkinter as tk
        try:
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(user_code)
            root.destroy()
        except Exception:
            pass

        webbrowser.open(DEVICE_LOGIN_PAGE)

        if status_callback:
            status_callback(
                f"代码 {user_code} 已复制到剪贴板，请在浏览器中打开 {DEVICE_LOGIN_PAGE} 并输入此代码"
            )

        logger.info(f"设备代码登录已启动: user_code={user_code}, 有效期 {expires_in} 秒")

        poll_payload = {
            "client_id": self.client_id,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code_val,
        }

        start_time = time.time()
        while (time.time() - start_time) < expires_in:
            time.sleep(interval)
            try:
                resp = requests.post(TOKEN_URL, data=poll_payload, headers=headers, timeout=30)
                token_data = resp.json()
            except Exception as e:
                logger.warning(f"轮询 Token 网络错误: {e}")
                continue

            if "access_token" in token_data:
                ms_access_token = token_data["access_token"]
                ms_refresh_token = token_data.get("refresh_token", "")
                logger.info("设备代码登录: 已获取 Microsoft Token")

                if status_callback:
                    status_callback("正在完成 Minecraft 认证...")

                profile = self._do_minecraft_auth(
                    ms_access_token, ms_refresh_token, status_callback
                )
                if profile is None:
                    return None

                account_id = str(uuid_mod.uuid4())
                now = datetime.now(timezone.utc).isoformat()
                return Account(
                    id=account_id,
                    name=profile.get("name", "Steve"),
                    account_type=AccountType.MICROSOFT,
                    uuid=profile.get("id", ""),
                    access_token=profile.get("access_token", ""),
                    refresh_token=profile.get("refresh_token", ""),
                    skins=profile.get("skins", []),
                    created_at=now,
                    last_login=now,
                )

            error_code = token_data.get("error", "")
            if error_code == "authorization_pending":
                continue
            elif error_code == "slow_down":
                interval += 5
                continue
            elif error_code in ("expired_token", "authorization_declined"):
                logger.error(f"设备代码登录失败: {error_code}")
                if status_callback:
                    status_callback("登录已取消或代码已过期，请重试")
                return None
            else:
                error_desc = token_data.get("error_description", error_code)
                logger.error(f"Token 请求错误: {error_desc}")
                if status_callback:
                    status_callback(f"登录失败: {error_desc}")
                return None

        logger.error("设备代码登录超时")
        if status_callback:
            status_callback("登录超时，请重试")
        return None


class YggdrasilLoginManager:
    def login(
        self,
        server_url: str,
        username: str,
        password: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[Account]:
        if not server_url or not username or not password:
            return None

        server_url = server_url.rstrip("/")
        if not server_url.endswith("/authserver"):
            auth_url = f"{server_url}/api/yggdrasil/authserver/authenticate"
        else:
            auth_url = f"{server_url}/authenticate"

        if status_callback:
            status_callback(f"\u6B63\u5728\u8FDE\u63A5: {server_url}")

        auth_payload = {
            "agent": {"name": "Minecraft", "version": 1},
            "username": username,
            "password": password,
            "requestUser": True,
        }

        try:
            resp = requests.post(
                auth_url,
                json=auth_payload,
                headers=_build_ua_header(),
                timeout=15,
            )
            if resp.status_code != 200:
                error_msg = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
                logger.error(f"Yggdrasil \u8BA4\u8BC1\u5931\u8D25: {error_msg}")
                return None

            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"Yggdrasil \u8BF7\u6C42\u5931\u8D25: {e}")
            return None
        except Exception as e:
            logger.error(f"Yggdrasil \u89E3\u6790\u5931\u8D25: {e}")
            return None

        selected = data.get("selectedProfile")
        if not selected:
            logger.error("Yggdrasil \u54CD\u5E94\u4E2D\u6CA1\u6709 selectedProfile")
            return None

        account_id = str(uuid_mod.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        return Account(
            id=account_id,
            name=selected.get("name", username),
            account_type=AccountType.YGGDRASIL,
            uuid=selected.get("id", ""),
            access_token=data.get("accessToken", ""),
            client_token=data.get("clientToken", ""),
            yggdrasil_server_url=server_url,
            skins=[],
            created_at=now,
            last_login=now,
        )


def create_offline_account(username: str) -> Account:
    offline_uuid = uuid_mod.uuid3(uuid_mod.NAMESPACE_DNS, f"offline_{username}")
    now = datetime.now(timezone.utc).isoformat()
    return Account(
        id=str(uuid_mod.uuid4()),
        name=username,
        account_type=AccountType.OFFLINE,
        uuid=str(offline_uuid),
        access_token=None,
        refresh_token=None,
        created_at=now,
        last_login=now,
    )


class AuthlibInjectorManager:
    def __init__(self, base_dir: Path):
        self._base_dir = Path(base_dir)
        self._injector_dir = self._base_dir / "authlib-injector"
        self._jar_path = self._injector_dir / f"authlib-injector-{AUTHLIB_INJECTOR_VERSION}.jar"

    @property
    def is_installed(self) -> bool:
        return self._jar_path.exists()

    @property
    def jar_path(self) -> str:
        return str(self._jar_path)

    def download(self, status_callback: Optional[Callable[[str], None]] = None) -> bool:
        if self.is_installed:
            return True

        self._injector_dir.mkdir(parents=True, exist_ok=True)

        urls = [AUTHLIB_INJECTOR_URL.format(version=AUTHLIB_INJECTOR_VERSION)]
        urls.extend(
            m.format(version=AUTHLIB_INJECTOR_VERSION) for m in AUTHLIB_INJECTOR_MIRRORS
        )

        for url in urls:
            try:
                if status_callback:
                    status_callback(f"\u6B63\u5728\u4E0B\u8F7D authlib-injector...")
                logger.info(f"\u4E0B\u8F7D authlib-injector: {url}")
                resp = requests.get(url, headers=_build_ua_header(), timeout=60, stream=True)
                if resp.status_code == 200:
                    with open(self._jar_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    logger.info(f"authlib-injector \u4E0B\u8F7D\u5B8C\u6210: {self._jar_path}")
                    return True
            except Exception as e:
                logger.debug(f"authlib-injector \u4E0B\u8F7D\u5931\u8D25 ({url}): {e}")
                continue

        logger.error("authlib-injector \u4E0B\u8F7D\u5931\u8D25\uFF0C\u6240\u6709\u955C\u50CF\u5747\u4E0D\u53EF\u7528")
        return False

    def build_launch_command(self, target_version: str, mc_dir: str, account: Account) -> List[str]:
        import minecraft_launcher_lib

        options = {
            "username": account.name,
            "uuid": account.uuid or account.id,
            "token": account.access_token or "0",
        }

        raw_command = minecraft_launcher_lib.command.get_minecraft_command(
            target_version, mc_dir, options
        )

        if not self.is_installed:
            logger.warning("authlib-injector \u672A\u5B89\u88C5\uFF0C\u65E0\u6CD5\u6CE8\u5165")
            return raw_command

        yggdrasil_url = account.yggdrasil_server_url or ""
        if not yggdrasil_url.endswith("/authserver"):
            yggdrasil_url = yggdrasil_url.rstrip("/") + "/api/yggdrasil"

        injector_args = [
            f"-javaagent:{self.jar_path}={yggdrasil_url}",
            "-Dauthlibinjector.side=client",
        ]

        main_class_index = None
        for i, arg in enumerate(raw_command):
            if arg in ("net.minecraft.client.main.Main", "net.minecraft.launchwrapper.Launch"):
                main_class_index = i
                break

        if main_class_index is not None:
            return raw_command[:main_class_index] + injector_args + raw_command[main_class_index:]

        cp_index = None
        for i, arg in enumerate(raw_command):
            if arg == "-cp" or arg == "-classpath":
                cp_index = i + 2
                break
        if cp_index is not None:
            return raw_command[:cp_index] + injector_args + raw_command[cp_index:]

        insert_idx = 1
        for i, arg in enumerate(raw_command):
            if arg in ("java", "javaw") or arg.endswith("java.exe") or arg.endswith("javaw.exe"):
                insert_idx = i + 1
                break
        return raw_command[:insert_idx] + injector_args + raw_command[insert_idx:]


class GlobalAccountSystem:
    """全局账号档案系统"""

    DEFAULT_ACCOUNTS_FILE = "accounts.json"

    def __init__(self, base_dir: Path, microsoft_client_id: Optional[str] = None):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._accounts_file = self._base_dir / self.DEFAULT_ACCOUNTS_FILE
        self._accounts: List[Account] = []
        self._current_account_id: Optional[str] = None
        self._microsoft_login = MicrosoftLoginManager(microsoft_client_id or MICROSOFT_CLIENT_ID)
        self._yggdrasil_login = YggdrasilLoginManager()
        self._authlib_manager = AuthlibInjectorManager(self._base_dir)
        self._load()

    @property
    def accounts(self) -> List[Account]:
        return list(self._accounts)

    @property
    def current_account(self) -> Optional[Account]:
        if self._current_account_id:
            return self.get_account(self._current_account_id)
        return None

    @property
    def current_account_id(self) -> Optional[str]:
        return self._current_account_id

    @property
    def microsoft_login_manager(self) -> MicrosoftLoginManager:
        return self._microsoft_login

    @property
    def yggdrasil_login_manager(self) -> YggdrasilLoginManager:
        return self._yggdrasil_login

    @property
    def authlib_injector(self) -> AuthlibInjectorManager:
        return self._authlib_manager

    def _load(self):
        if not self._accounts_file.exists():
            self._accounts = []
            return
        try:
            with open(self._accounts_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._accounts = [Account.from_dict(a) for a in data.get("accounts", [])]
            self._current_account_id = data.get("current_account_id")
            logger.info(f"\u5DF2\u52A0\u8F7D {len(self._accounts)} \u4E2A\u8D26\u53F7")
        except Exception as e:
            logger.error(f"\u52A0\u8F7D\u8D26\u53F7\u6587\u4EF6\u5931\u8D25: {e}")
            self._accounts = []

    def _save(self):
        import tempfile
        try:
            data = {
                "accounts": [a.to_dict() for a in self._accounts],
                "current_account_id": self._current_account_id,
            }
            content = json.dumps(data, indent=2, ensure_ascii=False)

            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._accounts_file.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, str(self._accounts_file))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise
            logger.info(f"\u5DF2\u4FDD\u5B58 {len(self._accounts)} \u4E2A\u8D26\u53F7")
        except Exception as e:
            logger.error(f"\u4FDD\u5B58\u8D26\u53F7\u6587\u4EF6\u5931\u8D25: {e}")

    def get_account(self, account_id: str) -> Optional[Account]:
        for acc in self._accounts:
            if acc.id == account_id:
                return acc
        return None

    def get_account_by_name(self, name: str, account_type: Optional[AccountType] = None) -> Optional[Account]:
        for acc in self._accounts:
            if acc.name == name:
                if account_type is None or acc.account_type == account_type:
                    return acc
        return None

    def get_accounts_by_type(self, account_type: AccountType) -> List[Account]:
        return [a for a in self._accounts if a.account_type == account_type]

    def add_account(self, account: Account) -> bool:
        existing = self.get_account(account.id)
        if existing:
            idx = self._accounts.index(existing)
            self._accounts[idx] = account
        else:
            self._accounts.append(account)
        self._save()
        return True

    def remove_account(self, account_id: str) -> bool:
        acc = self.get_account(account_id)
        if not acc:
            return False
        self._accounts.remove(acc)
        if self._current_account_id == account_id:
            self._current_account_id = None
        self._save()
        return True

    def set_current_account(self, account_id: str) -> bool:
        if not self.get_account(account_id):
            logger.warning(f"\u8D26\u53F7 {account_id} \u4E0D\u5B58\u5728")
            return False
        self._current_account_id = account_id
        self._save()
        logger.info(f"\u5DF2\u5207\u6362\u5F53\u524D\u8D26\u53F7: {account_id}")
        return True

    def microsoft_login(self, status_callback: Optional[Callable[[str], None]] = None) -> Optional[Account]:
        account = self._microsoft_login.login(status_callback=status_callback)
        if account:
            self.add_account(account)
            self.set_current_account(account.id)
        return account

    def microsoft_device_code_login(
        self, status_callback: Optional[Callable[[str], None]] = None
    ) -> Optional[Account]:
        account = self._microsoft_login.login_device_code(status_callback=status_callback)
        if account:
            self.add_account(account)
            self.set_current_account(account.id)
        return account

    def yggdrasil_login(
        self,
        server_url: str,
        username: str,
        password: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[Account]:
        account = self._yggdrasil_login.login(server_url, username, password, status_callback=status_callback)
        if account:
            self.add_account(account)
            self.set_current_account(account.id)
        return account

    def offline_login(self, username: str) -> Optional[Account]:
        account = create_offline_account(username)
        self.add_account(account)
        self.set_current_account(account.id)
        return account

    def refresh_account_token(self, account: Account) -> bool:
        if account.account_type != AccountType.MICROSOFT:
            return True
        success = self._microsoft_login.refresh_token(account)
        if success:
            self._save()
        return success

    def ensure_valid_token(self, account: Optional[Account] = None) -> Optional[Account]:
        target = account or self.current_account
        if not target:
            return None
        if target.account_type == AccountType.MICROSOFT:
            if not target.access_token:
                success = self._microsoft_login.refresh_token(target)
                if not success:
                    logger.warning(f"\u5FAE\u8F6F\u8D26\u53F7 {target.name} Token \u65E0\u6548\u4E14\u5237\u65B0\u5931\u8D25")
        return target

    def build_launch_options(self, account: Optional[Account] = None) -> dict:
        target = account or self.current_account
        if not target:
            return {}

        if target.account_type == AccountType.MICROSOFT:
            return {
                "username": target.name,
                "uuid": target.uuid or target.id,
                "token": target.access_token or "0",
            }
        elif target.account_type == AccountType.YGGDRASIL:
            return {
                "username": target.name,
                "uuid": target.uuid or target.id,
                "token": target.access_token or "0",
            }
        elif target.account_type == AccountType.OFFLINE:
            return {
                "username": target.name,
                "uuid": target.uuid or target.id,
            }
        return {}

    def build_launch_command(
        self,
        target_version: str,
        mc_dir: str,
        account: Optional[Account] = None,
    ) -> List[str]:
        import minecraft_launcher_lib

        target = account or self.current_account
        if not target:
            return minecraft_launcher_lib.command.get_minecraft_command(
                target_version, mc_dir, minecraft_launcher_lib.utils.generate_test_options()
            )

        if target.account_type == AccountType.YGGDRASIL and target.yggdrasil_server_url:
            if self._authlib_manager.is_installed:
                return self._authlib_manager.build_launch_command(target_version, mc_dir, target)
            else:
                logger.warning("authlib-injector \u672A\u5B89\u88C5\uFF0C\u4F7F\u7528\u666E\u901A\u65B9\u5F0F\u542F\u52A8 Yggdrasil \u8D26\u53F7")

        options = self.build_launch_options(target)
        return minecraft_launcher_lib.command.get_minecraft_command(
            target_version, mc_dir, options
        )

    def export_accounts(self, password: str, account_ids: Optional[List[str]] = None) -> Optional[bytes]:
        from cryptography.fernet import Fernet

        if not password:
            return None

        accounts_to_export = self._accounts
        if account_ids:
            accounts_to_export = [a for a in self._accounts if a.id in account_ids]

        if not accounts_to_export:
            return None

        salt = b"FMCL_ACCOUNT_EXPORT_SALT_v1"
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
        key = base64.urlsafe_b64encode(derived)

        export_data = {
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "account_count": len(accounts_to_export),
            "accounts": [a.to_dict() for a in accounts_to_export],
        }

        plaintext = json.dumps(export_data, indent=2, ensure_ascii=False).encode("utf-8")
        try:
            cipher = Fernet(key)
            encrypted = cipher.encrypt(plaintext)
            import_marker = b"FMCL_ACCOUNTS_V1\n"
            return import_marker + encrypted
        except Exception as e:
            logger.error(f"\u5BFC\u51FA\u8D26\u53F7\u52A0\u5BC6\u5931\u8D25: {e}")
            return None

    def import_accounts(self, password: str, data: bytes, merge: bool = True) -> int:
        from cryptography.fernet import Fernet, InvalidToken

        if not password or not data:
            return 0

        marker = b"FMCL_ACCOUNTS_V1\n"
        if not data.startswith(marker):
            logger.error("\u65E0\u6548\u7684\u8D26\u53F7\u5BFC\u51FA\u6587\u4EF6\u683C\u5F0F")
            return -1

        encrypted = data[len(marker):]

        salt = b"FMCL_ACCOUNT_EXPORT_SALT_v1"
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
        key = base64.urlsafe_b64encode(derived)

        try:
            cipher = Fernet(key)
            plaintext = cipher.decrypt(encrypted)
            export_data = json.loads(plaintext.decode("utf-8"))
        except InvalidToken:
            logger.error("\u5BFC\u5165\u8D26\u53F7\u5931\u8D25: \u5BC6\u7801\u9519\u8BEF")
            return -1
        except Exception as e:
            logger.error(f"\u5BFC\u5165\u8D26\u53F7\u89E3\u5BC6\u5931\u8D25: {e}")
            return -1

        imported_count = 0
        existing_ids = {a.id for a in self._accounts}
        for acc_data in export_data.get("accounts", []):
            acc = Account.from_dict(acc_data)
            if acc.id in existing_ids:
                if not merge:
                    continue
                for i, existing in enumerate(self._accounts):
                    if existing.id == acc.id:
                        self._accounts[i] = acc
                        break
            else:
                self._accounts.append(acc)
            existing_ids.add(acc.id)
            imported_count += 1

        self._save()
        logger.info(f"\u5BFC\u5165\u8D26\u53F7\u5B8C\u6210: {imported_count} \u4E2A")
        return imported_count


_global_account_system: Optional[GlobalAccountSystem] = None


def init_account_system(base_dir: Path, microsoft_client_id: Optional[str] = None) -> GlobalAccountSystem:
    global _global_account_system
    if _global_account_system is None:
        _global_account_system = GlobalAccountSystem(base_dir, microsoft_client_id)
    return _global_account_system


def get_account_system() -> Optional[GlobalAccountSystem]:
    return _global_account_system
