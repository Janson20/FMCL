"""ModernApp 工具 Mixin - 工具标签页相关方法"""
import base64
import io
import json
import os
import hashlib
import socket
import struct
import threading
import time
from datetime import date
from pathlib import Path
from tkinter import filedialog
from typing import Any, Dict, Optional

import requests
import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


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


def _format_size(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


class ToolsTabMixin(object):
    """工具标签页 Mixin"""

    def _build_tools_tab_content(self):
        content = ctk.CTkScrollableFrame(self.tools_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._build_tool_clean_junk(content)
        self._build_tool_daily_fortune(content)
        self._build_tool_coordinate_converter(content)
        self._build_tool_hash_calculator(content)
        self._build_tool_port_checker(content)
        self._build_tool_minecraft_facts(content)
        self._build_tool_multi_download(content)

    def _make_tool_card(self, parent, title: str, desc: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        card.pack(fill=ctk.X, pady=(0, 12))

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill=ctk.X, padx=16, pady=(14, 0))

        ctk.CTkLabel(
            header,
            text=title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            card,
            text=desc,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=600,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, padx=16, pady=(4, 12))

        return card

    def _build_tool_clean_junk(self, parent):
        card = self._make_tool_card(parent, _("tool_clean_junk_title"), _("tool_clean_junk_desc"))

        self._clean_junk_status = ctk.CTkLabel(
            card,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 14))

        self._clean_junk_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_clean_junk_scan"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_clean_junk,
        )
        self._clean_junk_btn.pack(side=ctk.LEFT)

    def _on_clean_junk(self):
        btn = self._clean_junk_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_clean_junk_scanning"))

        def _task():
            try:
                mc_dir = self._get_minecraft_dir_for_tools()
                junk_files = []
                total_size = 0

                for root, dirs, files in os.walk(str(mc_dir)):
                    for f in files:
                        if f.endswith(".log") or f.endswith(".tmp"):
                            fp = os.path.join(root, f)
                            try:
                                size = os.path.getsize(fp)
                            except OSError:
                                size = 0
                            junk_files.append((fp, size))
                            total_size += size

                def _update_ui():
                    if not junk_files:
                        self._clean_junk_status.configure(
                            text=_("tool_clean_junk_none"),
                            text_color=COLORS["text_secondary"],
                        )
                        btn.configure(state=ctk.NORMAL, text=_("tool_clean_junk_scan"))
                        self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                    else:
                        self._clean_junk_status.configure(
                            text=_("tool_clean_junk_found", count=len(junk_files), size=_format_size(total_size)),
                            text_color=COLORS["accent"],
                        )
                        self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                        btn.configure(
                            state=ctk.NORMAL,
                            text=_("tool_clean_junk_delete"),
                            fg_color=COLORS["accent"],
                            hover_color=COLORS["accent_hover"],
                            command=self._on_delete_junk_files,
                        )
                        self._clean_junk__files = junk_files
                        self._clean_junk__total_size = total_size
                self.after(0, _update_ui)
            except Exception as e:
                logger.error(f"扫描垃圾文件失败: {e}")

                def _error_ui():
                    btn.configure(state=ctk.NORMAL, text=_("tool_clean_junk_scan"))
                    self._clean_junk_status.configure(
                        text=_("tool_clean_junk_error", error=str(e)),
                        text_color=COLORS["text_secondary"],
                    )
                    self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                self.after(0, _error_ui)

        threading.Thread(target=_task, daemon=True).start()

    # ═══════════ Minecraft 冷知识 ═══════════

    _MC_FACTS = [
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

    def _build_tool_minecraft_facts(self, parent):
        card = self._make_tool_card(parent, _("tool_fact_title"), _("tool_fact_desc"))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._fact_new_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_fact_new"),
            width=120,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_new_fact,
        )
        self._fact_new_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self._fact_random_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_fact_random"),
            width=120,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_random_fact,
        )
        self._fact_random_btn.pack(side=ctk.LEFT)

        self._fact_display_frame = ctk.CTkFrame(card, fg_color="transparent")

        self._on_new_fact()

    def _on_new_fact(self):
        today = date.today().isoformat()
        seed_str = f"fmcl_fact_{today}"
        seed = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
        index = seed % len(self._MC_FACTS)
        self._show_fact(index, _("tool_fact_today"))

    def _on_random_fact(self):
        import random as _random
        index = _random.randint(0, len(self._MC_FACTS) - 1)
        self._show_fact(index, _("tool_fact_random_title"))

    def _show_fact(self, index: int, tag: str):
        fact = self._MC_FACTS[index]

        for w in self._fact_display_frame.winfo_children():
            w.destroy()
        self._fact_display_frame.pack_forget()
        self._fact_display_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

        sep = ctk.CTkFrame(self._fact_display_frame, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, pady=(4, 8))

        ctk.CTkLabel(
            self._fact_display_frame,
            text=f"💎 {fact}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
            wraplength=560,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(0, 4))

        ctk.CTkLabel(
            self._fact_display_frame,
            text=_("tool_fact_index", index=index + 1, total=len(self._MC_FACTS)),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W)

    # ═══════════ 端口检测器 ═══════════

    _PORT_PRESETS = [
        ("", 0),
        ("localhost", 25565),
        ("localhost", 19132),
        ("localhost", 80),
        ("localhost", 443),
    ]

    def _build_tool_port_checker(self, parent):
        card = self._make_tool_card(parent, _("tool_port_title"), _("tool_port_desc"))

        host_row = ctk.CTkFrame(card, fg_color="transparent")
        host_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            host_row,
            text=_("tool_port_host"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=60,
        ).pack(side=ctk.LEFT)

        self._port_host_entry = ctk.CTkEntry(
            host_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="example.com 或 127.0.0.1",
        )
        self._port_host_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))

        ctk.CTkLabel(
            host_row,
            text=_("tool_port_port"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=40,
        ).pack(side=ctk.LEFT)

        self._port_port_entry = ctk.CTkEntry(
            host_row,
            width=80,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._port_port_entry.pack(side=ctk.LEFT)

        preset_row = ctk.CTkFrame(card, fg_color="transparent")
        preset_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            preset_row,
            text=_("tool_port_presets"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(0, 8))

        presets = [
            ("MC Java", "25565"),
            ("MC Bedrock", "19132"),
            ("HTTP", "80"),
            ("HTTPS", "443"),
        ]
        for name, port in presets:
            ctk.CTkButton(
                preset_row,
                text=f"{name} ({port})",
                width=100,
                height=26,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["bg_light"],
                hover_color=COLORS["card_border"],
                command=lambda p=port: self._on_port_preset(p),
            ).pack(side=ctk.LEFT, padx=(0, 5))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._port_test_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_port_test"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_test_port,
        )
        self._port_test_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self._port_peek_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_port_peek"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_peek_server,
        )
        self._port_peek_btn.pack(side=ctk.LEFT)

        self._port_history_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._port_results = []

        self._port_server_info_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_port_preset(self, port: str):
        self._port_port_entry.delete(0, "end")
        self._port_port_entry.insert(0, port)

    def _on_test_port(self):
        host = self._port_host_entry.get().strip()
        port_str = self._port_port_entry.get().strip()

        if not host:
            self.set_status(_("tool_port_no_host"), "error")
            return
        if not port_str:
            self.set_status(_("tool_port_no_port"), "error")
            return
        try:
            port = int(port_str)
            if port < 1 or port > 65535:
                raise ValueError
        except ValueError:
            self.set_status(_("tool_port_invalid_port"), "error")
            return

        btn = self._port_test_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_port_testing"))

        def _task():
            sock = None
            start = time.time()
            err = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
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
            entry = (host, port, status, round(elapsed * 1000, 1), err)
            self._port_results.insert(0, entry)
            if len(self._port_results) > 10:
                self._port_results = self._port_results[:10]

            def _update():
                for w in self._port_history_frame.winfo_children():
                    w.destroy()
                self._port_history_frame.pack_forget()
                self._port_history_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

                sep = ctk.CTkFrame(self._port_history_frame, fg_color=COLORS["card_border"], height=1)
                sep.pack(fill=ctk.X, pady=(4, 8))

                for h, p, st, lat, e_msg in self._port_results:
                    row = ctk.CTkFrame(self._port_history_frame, fg_color="transparent")
                    row.pack(fill=ctk.X, pady=1)

                    icon_color = "#4caf50" if st == "open" else "#cd5c5c"
                    icon_text = _("tool_port_open") if st == "open" else _("tool_port_closed")
                    lat_text = f"  {lat}ms" if st == "open" else ""
                    err_text = f"  ({e_msg})" if e_msg else ""

                    ctk.CTkLabel(
                        row,
                        text=f"{h}:{p}",
                        font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                        text_color=COLORS["text_primary"],
                    ).pack(side=ctk.LEFT, padx=(0, 10))

                    ctk.CTkLabel(
                        row,
                        text=f"{icon_text}{lat_text}{err_text}",
                        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                        text_color=icon_color,
                    ).pack(side=ctk.LEFT)

                btn.configure(state=ctk.NORMAL, text=_("tool_port_test"))
            self.after(0, _update)

        threading.Thread(target=_task, daemon=True).start()

    def _clear_server_info(self):
        for w in self._port_server_info_frame.winfo_children():
            w.destroy()
        self._port_server_info_frame.pack_forget()

    def _display_server_info(self, info: Dict[str, Any], latency_ms: int):
        self._clear_server_info()
        self._port_server_info_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

        sep = ctk.CTkFrame(self._port_server_info_frame, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, pady=(4, 8))

        info_frame = ctk.CTkFrame(self._port_server_info_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
        info_frame.pack(fill=ctk.X, pady=(0, 4))

        favicon = info.get("favicon")
        if favicon:
            try:
                img_data = base64.b64decode(favicon.split(",", 1)[-1] if "," in favicon else favicon)
                from PIL import Image as PILImage, ImageTk
                img = PILImage.open(io.BytesIO(img_data))
                img = img.resize((48, 48), PILImage.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                icon_label = ctk.CTkLabel(info_frame, image=photo, text="")
                icon_label.image = photo
            except Exception:
                icon_label = ctk.CTkLabel(info_frame, text="🖥", font=ctk.CTkFont(size=36),
                                          text_color=COLORS["text_secondary"])
        else:
            icon_label = ctk.CTkLabel(info_frame, text="🖥", font=ctk.CTkFont(size=36),
                                      text_color=COLORS["text_secondary"])
        icon_label.pack(side=ctk.LEFT, padx=(10, 12), pady=10)

        text_col = ctk.CTkFrame(info_frame, fg_color="transparent")
        text_col.pack(side=ctk.LEFT, fill=ctk.X, expand=True, pady=10)

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

        ctk.CTkLabel(
            text_col,
            text=desc_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(anchor=ctk.W)

        ver = info.get("version", {})
        ver_name = ver.get("name", "?") if isinstance(ver, dict) else str(ver)
        players = info.get("players", {}) if isinstance(info.get("players"), dict) else {}
        online = players.get("online", 0)
        max_p = players.get("max", 0)

        latency_color = "#4caf50" if latency_ms < 150 else ("#ff9800" if latency_ms < 400 else "#cd5c5c")
        detail_text = _("tool_peek_detail", version=ver_name, online=online, max_p=max_p, latency=latency_ms)
        ctk.CTkLabel(
            text_col,
            text=detail_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=latency_color,
            anchor=ctk.W,
        ).pack(anchor=ctk.W, pady=(4, 0))

        sample_list = players.get("sample", [])
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
                ctk.CTkLabel(
                    text_col,
                    text=sample_text,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                    text_color=COLORS["text_secondary"],
                    anchor=ctk.W,
                    wraplength=400,
                ).pack(anchor=ctk.W, pady=(2, 0))

    def _on_peek_server(self):
        host = self._port_host_entry.get().strip()
        port_str = self._port_port_entry.get().strip()

        if not host:
            self.set_status(_("tool_port_no_host"), "error")
            return
        if not port_str:
            port_str = "25565"

        try:
            port = int(port_str)
        except ValueError:
            self.set_status(_("tool_port_invalid_port"), "error")
            return

        peek_btn = self._port_peek_btn
        test_btn = self._port_test_btn
        peek_btn.configure(state=ctk.DISABLED, text=_("tool_peek_querying"))
        test_btn.configure(state=ctk.DISABLED)
        self._clear_server_info()

        def _task():
            sock = None
            err = None
            start = time.time()
            info = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
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

            def _update():
                if err or info is None:
                    text = _("tool_peek_error", error=err or _("tool_peek_unknown_error"))
                    self.set_status(text, "error")
                    self._port_server_info_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
                    for w in self._port_server_info_frame.winfo_children():
                        w.destroy()
                    sep = ctk.CTkFrame(self._port_server_info_frame, fg_color=COLORS["card_border"], height=1)
                    sep.pack(fill=ctk.X, pady=(4, 8))
                    ctk.CTkLabel(
                        self._port_server_info_frame,
                        text=text,
                        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                        text_color="#cd5c5c",
                        wraplength=550,
                    ).pack(anchor=ctk.W)
                else:
                    self._display_server_info(info, int(elapsed))
                peek_btn.configure(state=ctk.NORMAL, text=_("tool_port_peek"))
                test_btn.configure(state=ctk.NORMAL, text=_("tool_port_test"))
            self.after(0, _update)

        threading.Thread(target=_task, daemon=True).start()

    # ═══════════ 坐标转换器 ═══════════

    def _build_tool_coordinate_converter(self, parent):
        card = self._make_tool_card(parent, _("tool_coord_title"), _("tool_coord_desc"))

        inp_frame = ctk.CTkFrame(card, fg_color="transparent")
        inp_frame.pack(fill=ctk.X, padx=16, pady=(0, 8))

        labels = [("X:", 0), ("Y:", 1), ("Z:", 2)]
        self._coord_entries = {}
        for lbl_text, col in labels:
            sub = ctk.CTkFrame(inp_frame, fg_color="transparent")
            sub.pack(side=ctk.LEFT, padx=(0, 8) if col < 2 else (0, 0))
            ctk.CTkLabel(
                sub,
                text=lbl_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            ).pack(side=ctk.LEFT, padx=(0, 4))
            entry = ctk.CTkEntry(
                sub,
                width=100,
                height=34,
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color=COLORS["bg_medium"],
                border_color=COLORS["card_border"],
            )
            entry.pack(side=ctk.LEFT)
            self._coord_entries[lbl_text[0]] = entry

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        self._coord_to_nether_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_coord_to_nether"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=lambda: self._on_convert_coord("nether"),
        )
        self._coord_to_nether_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self._coord_to_overworld_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_coord_to_overworld"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=lambda: self._on_convert_coord("overworld"),
        )
        self._coord_to_overworld_btn.pack(side=ctk.LEFT)

        self._coord_result_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _parse_coord(self, entry) -> int:
        try:
            return int(entry.get().strip())
        except ValueError:
            return 0

    def _on_convert_coord(self, target: str):
        x = self._parse_coord(self._coord_entries["X"])
        y = self._parse_coord(self._coord_entries["Y"])
        z = self._parse_coord(self._coord_entries["Z"])

        for w in self._coord_result_frame.winfo_children():
            w.destroy()
        self._coord_result_frame.pack_forget()

        if target == "nether":
            rx = x // 8
            rz = z // 8
            desc = _("tool_coord_result_nether", x=x, y=y, z=z, rx=rx, rz=rz)
        else:
            rx = x * 8
            rz = z * 8
            desc = _("tool_coord_result_overworld", x=x, y=y, z=z, rx=rx, rz=rz)

        self._coord_result_frame.pack(fill=ctk.X, padx=16, pady=(0, 12))

        sep = ctk.CTkFrame(self._coord_result_frame, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, pady=(4, 8))

        ctk.CTkLabel(
            self._coord_result_frame,
            text=f"{rx}, {y}, {rz}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(anchor=ctk.W, pady=(0, 2))

        ctk.CTkLabel(
            self._coord_result_frame,
            text=desc,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=500,
        ).pack(anchor=ctk.W)

    # ═══════════ Hash 计算器 ═══════════

    def _build_tool_hash_calculator(self, parent):
        card = self._make_tool_card(parent, _("tool_hash_title"), _("tool_hash_desc"))

        file_row = ctk.CTkFrame(card, fg_color="transparent")
        file_row.pack(fill=ctk.X, padx=16, pady=(0, 8))

        self._hash_file_entry = ctk.CTkEntry(
            file_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("tool_hash_select_file"),
        )
        self._hash_file_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))

        ctk.CTkButton(
            file_row,
            text="📂",
            width=40,
            height=34,
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_browse_hash_file,
        ).pack(side=ctk.RIGHT)

        algo_row = ctk.CTkFrame(card, fg_color="transparent")
        algo_row.pack(fill=ctk.X, padx=16, pady=(0, 10))

        self._hash_algo_var = ctk.StringVar(value="SHA256")
        for alg in ("MD5", "SHA1", "SHA256", "SHA512"):
            ctk.CTkRadioButton(
                algo_row,
                text=alg,
                variable=self._hash_algo_var,
                value=alg,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                border_color=COLORS["text_secondary"],
                text_color=COLORS["text_primary"],
            ).pack(side=ctk.LEFT, padx=(0, 15))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._hash_calc_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_hash_calculate"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_calc_hash,
        )
        self._hash_calc_btn.pack(side=ctk.LEFT)

        self._hash_result_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_browse_hash_file(self):
        path = filedialog.askopenfilename(
            title=_("tool_hash_select_file"),
            parent=self,
        )
        if path:
            self._hash_file_entry.delete(0, "end")
            self._hash_file_entry.insert(0, path)

    def _on_calc_hash(self):
        filepath = self._hash_file_entry.get().strip()
        if not filepath or not os.path.isfile(filepath):
            self.set_status(_("tool_hash_file_error"), "error")
            return

        algo = self._hash_algo_var.get()
        algo_map = {"MD5": "md5", "SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512"}
        hasher = hashlib.new(algo_map[algo])

        btn = self._hash_calc_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_hash_calculating"))

        for w in self._hash_result_frame.winfo_children():
            w.destroy()
        self._hash_result_frame.pack_forget()

        def _task():
            try:
                with open(filepath, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        hasher.update(chunk)
                result = hasher.hexdigest()

                def _done():
                    self._hash_result_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
                    sep = ctk.CTkFrame(self._hash_result_frame, fg_color=COLORS["card_border"], height=1)
                    sep.pack(fill=ctk.X, pady=(4, 8))
                    ctk.CTkLabel(
                        self._hash_result_frame,
                        text=f"{algo}:  {result}",
                        font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                        text_color=COLORS["accent"],
                        wraplength=550,
                    ).pack(anchor=ctk.W, pady=(0, 2))
                    fname = os.path.basename(filepath)
                    ctk.CTkLabel(
                        self._hash_result_frame,
                        text=_("tool_hash_file", name=fname, size=_format_size(os.path.getsize(filepath))),
                        font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                        text_color=COLORS["text_secondary"],
                    ).pack(anchor=ctk.W)
                    btn.configure(state=ctk.NORMAL, text=_("tool_hash_calculate"))
                self.after(0, _done)
            except Exception as e:
                logger.error(f"Hash 计算失败: {e}")

                def _error():
                    btn.configure(state=ctk.NORMAL, text=_("tool_hash_calculate"))
                    self.set_status(_("tool_hash_calc_error", error=str(e)), "error")
                self.after(0, _error)

        threading.Thread(target=_task, daemon=True).start()

    def _on_delete_junk_files(self):
        files = getattr(self, "_clean_junk__files", [])
        total_size = getattr(self, "_clean_junk__total_size", 0)
        if not files:
            return

        btn = self._clean_junk_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_clean_junk_deleting"))

        def _task():
            deleted = 0
            failed = 0
            for fp, _ in files:
                try:
                    os.remove(fp)
                    deleted += 1
                except OSError as e:
                    logger.error(f"删除文件失败 {fp}: {e}")
                    failed += 1

            def _update_ui():
                self._clean_junk_status.configure(
                    text=_("tool_clean_junk_done", deleted=deleted, failed=failed, size=_format_size(total_size)),
                    text_color=COLORS["accent"] if failed == 0 else COLORS["text_secondary"],
                )
                self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                btn.configure(state=ctk.NORMAL, text=_("tool_clean_junk_scan"), fg_color=COLORS["bg_light"],
                              hover_color=COLORS["card_border"], command=self._on_clean_junk)
                self._clean_junk__files = []
                self._clean_junk__total_size = 0
            self.after(0, _update_ui)

        threading.Thread(target=_task, daemon=True).start()

    def _build_tool_daily_fortune(self, parent):
        card = self._make_tool_card(parent, _("tool_fortune_title"), _("tool_fortune_desc"))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._fortune_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_fortune_check"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_check_fortune,
        )
        self._fortune_btn.pack(side=ctk.LEFT)

        self._fortune_result_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_check_fortune(self):
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

        level_text = _(level_key)
        color_map = {
            "tool_fortune_terrible": "#8b0000",
            "tool_fortune_bad": "#cd5c5c",
            "tool_fortune_normal": "#a0a0b0",
            "tool_fortune_good": "#4caf50",
            "tool_fortune_great": "#ff9800",
            "tool_fortune_legendary": "#e94560",
        }

        for w in self._fortune_result_frame.winfo_children():
            w.destroy()
        self._fortune_result_frame.pack_forget()

        self._fortune_result_frame.pack(fill=ctk.X, padx=16, pady=(0, 12))

        value_label = ctk.CTkLabel(
            self._fortune_result_frame,
            text=f"{emoji}  {value}  {level_text}  {emoji}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=24, weight="bold"),
            text_color=color_map.get(level_key, COLORS["text_primary"]),
        )
        value_label.pack(anchor=ctk.W, pady=(4, 0))

        ctk.CTkLabel(
            self._fortune_result_frame,
            text=_("tool_fortune_date", date=today),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(2, 0))

    def _build_tool_multi_download(self, parent):
        card = self._make_tool_card(parent, _("tool_download_title"), _("tool_download_desc"))

        url_row = ctk.CTkFrame(card, fg_color="transparent")
        url_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            url_row,
            text=_("tool_download_url"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=80,
        ).pack(side=ctk.LEFT)

        self._dl_url_entry = ctk.CTkEntry(
            url_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="https://...",
        )
        self._dl_url_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        ua_row = ctk.CTkFrame(card, fg_color="transparent")
        ua_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            ua_row,
            text=_("tool_download_ua"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=80,
        ).pack(side=ctk.LEFT)

        self._dl_ua_entry = ctk.CTkEntry(
            ua_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._dl_ua_entry.insert(0, "FMCL/2.0")
        self._dl_ua_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        save_row = ctk.CTkFrame(card, fg_color="transparent")
        save_row.pack(fill=ctk.X, padx=16, pady=(0, 10))

        ctk.CTkLabel(
            save_row,
            text=_("tool_download_save_path"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=80,
        ).pack(side=ctk.LEFT)

        self._dl_save_entry = ctk.CTkEntry(
            save_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._dl_save_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))

        self._dl_browse_btn = ctk.CTkButton(
            save_row,
            text="📂",
            width=40,
            height=34,
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_browse_save_path,
        )
        self._dl_browse_btn.pack(side=ctk.RIGHT)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._dl_start_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_download_start"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_start_download,
        )
        self._dl_start_btn.pack(side=ctk.LEFT)

        self._dl_progress_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_browse_save_path(self):
        path = filedialog.asksaveasfilename(
            title=_("tool_download_save_title"),
            parent=self,
        )
        if path:
            self._dl_save_entry.delete(0, "end")
            self._dl_save_entry.insert(0, path)

    def _get_minecraft_dir_for_tools(self) -> Path:
        try:
            if self.callbacks and "get_minecraft_dir" in self.callbacks:
                return Path(self.callbacks["get_minecraft_dir"]())
        except Exception:
            pass
        try:
            from config import config
            return config.minecraft_dir
        except Exception:
            pass
        return Path(".minecraft")

    def _get_download_threads_for_tools(self) -> int:
        try:
            if self.callbacks and "get_download_threads" in self.callbacks:
                return self.callbacks["get_download_threads"]()
        except Exception:
            pass
        try:
            from config import config
            return config.download_threads
        except Exception:
            pass
        return 4

    def _on_start_download(self):
        url = self._dl_url_entry.get().strip()
        ua = self._dl_ua_entry.get().strip()
        save_path = self._dl_save_entry.get().strip()

        if not url:
            self.set_status(_("tool_download_no_url"), "error")
            return
        if not save_path:
            save_dir = filedialog.askdirectory(
                title=_("tool_download_save_title"),
                parent=self,
            )
            if not save_dir:
                return
        else:
            save_dir = os.path.dirname(save_path)
            if not save_dir:
                save_dir = "."

        if not os.path.isdir(save_dir):
            try:
                os.makedirs(save_dir, exist_ok=True)
            except OSError as e:
                self.set_status(_("tool_download_mkdir_error", error=str(e)), "error")
                return

        if not ua:
            ua = "FMCL/2.0"

        for w in self._dl_progress_frame.winfo_children():
            w.destroy()
        self._dl_progress_frame.pack_forget()
        self._dl_progress_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

        self._dl_status_label = ctk.CTkLabel(
            self._dl_progress_frame,
            text=_("tool_download_connecting"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._dl_status_label.pack(anchor=ctk.W, pady=(0, 4))

        self._dl_progress_bar = ctk.CTkProgressBar(
            self._dl_progress_frame,
            width=400,
            height=12,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        self._dl_progress_bar.pack(fill=ctk.X, pady=(0, 4))
        self._dl_progress_bar.set(0)

        self._dl_speed_label = ctk.CTkLabel(
            self._dl_progress_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._dl_speed_label.pack(anchor=ctk.W)

        self._dl_start_btn.configure(state=ctk.DISABLED, text=_("tool_download_downloading"))

        self._dl_cancel_flag = False

        def _task():
            import time as _time

            num_threads = self._get_download_threads_for_tools()
            self._dl_cancel_flag = False

            try:
                resp = requests.head(url, headers={"User-Agent": ua}, timeout=15)
                resp.raise_for_status()

                total_size = int(resp.headers.get("Content-Length", 0))
                if total_size == 0:
                    resp2 = requests.get(url, headers={"User-Agent": ua}, stream=True, timeout=30)
                    resp2.raise_for_status()
                    chunks = []
                    for chunk in resp2.iter_content(chunk_size=8192):
                        if self._dl_cancel_flag:
                            resp2.close()
                            raise Exception("cancelled")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    total_size = len(content)
                    if not save_path or os.path.isdir(save_path):
                        filename_from_url = url.split("/")[-1].split("?")[0]
                        if not filename_from_url:
                            filename_from_url = "downloaded_file"
                        filename = os.path.join(save_dir, filename_from_url)
                    else:
                        filename = save_path
                    with open(filename, "wb") as f:
                        f.write(content)

                    def _single_done():
                        self._dl_progress_bar.set(1)
                        self._dl_speed_label.configure(text="")
                        self._dl_status_label.configure(
                            text=_("tool_download_success", path=filename),
                            text_color=COLORS["accent"],
                        )
                        self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                    self.after(0, _single_done)
                    return

                if not save_path or os.path.isdir(save_path):
                    filename_from_url = url.split("/")[-1].split("?")[0]
                    if not filename_from_url:
                        filename_from_url = "downloaded_file"
                    filename = os.path.join(save_dir, filename_from_url)
                else:
                    filename = save_path

                downloaded = 0
                lock = threading.Lock()
                start_time = _time.time()
                part_size = total_size // num_threads

                def _dl_part(start: int, end: int, idx: int):
                    nonlocal downloaded
                    headers = {
                        "User-Agent": ua,
                        "Range": f"bytes={start}-{end}",
                    }
                    part_file = f"{filename}.part{idx}"
                    try:
                        r = requests.get(url, headers=headers, stream=True, timeout=60)
                        r.raise_for_status()
                        with open(part_file, "wb") as pf:
                            for chunk in r.iter_content(chunk_size=8192):
                                if self._dl_cancel_flag:
                                    r.close()
                                    return
                                if chunk:
                                    pf.write(chunk)
                                    with lock:
                                        downloaded += len(chunk)
                                        elapsed = _time.time() - start_time
                                        if elapsed > 0:
                                            speed = downloaded / elapsed

                                            def _update():
                                                if total_size > 0:
                                                    self._dl_progress_bar.set(min(downloaded / total_size, 1.0))
                                                self._dl_speed_label.configure(text=_format_size(int(speed)) + "/s")
                                                self._dl_status_label.configure(
                                                    text=_(
                                                        "tool_download_progress",
                                                        current=_format_size(downloaded),
                                                        total=_format_size(total_size),
                                                    )
                                                )
                                            self.after(0, _update)
                    except Exception as e:
                        logger.error(f"分段下载 {idx} 失败: {e}")
                        raise

                threads_list = []
                for i in range(num_threads):
                    start_byte = i * part_size
                    end_byte = start_byte + part_size - 1 if i < num_threads - 1 else total_size - 1
                    t = threading.Thread(target=_dl_part, args=(start_byte, end_byte, i))
                    t.daemon = True
                    threads_list.append(t)
                    t.start()

                for t in threads_list:
                    t.join()

                if self._dl_cancel_flag:
                    for i in range(num_threads):
                        pf = f"{filename}.part{i}"
                        if os.path.exists(pf):
                            os.remove(pf)

                    def _cancel_ui():
                        self._dl_status_label.configure(
                            text=_("tool_download_cancelled"),
                            text_color=COLORS["text_secondary"],
                        )
                        self._dl_speed_label.configure(text="")
                        self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                    self.after(0, _cancel_ui)
                    return

                with open(filename, "wb") as outf:
                    for i in range(num_threads):
                        pf = f"{filename}.part{i}"
                        if os.path.exists(pf):
                            with open(pf, "rb") as inf:
                                outf.write(inf.read())
                            os.remove(pf)

                def _done_ui():
                    self._dl_progress_bar.set(1)
                    self._dl_speed_label.configure(text="")
                    self._dl_status_label.configure(
                        text=_("tool_download_success", path=filename),
                        text_color=COLORS["accent"],
                    )
                    self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                self.after(0, _done_ui)

            except Exception as e:
                if str(e) == "cancelled":
                    return
                logger.error(f"下载失败: {e}")

                def _error_ui():
                    self._dl_status_label.configure(
                        text=_("tool_download_error", error=str(e)),
                        text_color="#cd5c5c",
                    )
                    self._dl_speed_label.configure(text="")
                    self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                self.after(0, _error_ui)

        threading.Thread(target=_task, daemon=True).start()
