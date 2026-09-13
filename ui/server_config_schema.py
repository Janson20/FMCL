"""服务器配置编辑器 - 配置项元数据

把 server.properties 里的键整理成「人话」：分类、控件类型、取值范围与默认值。
每个配置项的名称与说明都通过 i18n 键（``scfg_<id>_n`` / ``scfg_<id>_d``）读取，
渲染逻辑见 :mod:`ui.windows.server_config_editor`。
"""

from typing import Any, Dict, List, Optional

from launcher.server_config import DEFAULT_SERVER_PROPERTIES

# ─── 控件类型 ────────────────────────────────────────────────
BOOL = "bool"  # 开关
INT = "int"  # 整数输入框
STR = "str"  # 文本输入框
ENUM = "enum"  # 下拉选择
MEMORY = "memory"  # 内存下拉（写入 .fmcl_server.json）
INFO = "info"  # 只读信息

# ─── 分类（左侧导航顺序） ─────────────────────────────────────
CATEGORIES: List[Dict[str, str]] = [
    {"id": "world", "i18n": "scfg_cat_world"},
    {"id": "players", "i18n": "scfg_cat_players"},
    {"id": "network", "i18n": "scfg_cat_network"},
    {"id": "performance", "i18n": "scfg_cat_performance"},
    {"id": "resource", "i18n": "scfg_cat_resource"},
    {"id": "startup", "i18n": "scfg_cat_startup"},
    {"id": "raw", "i18n": "scfg_cat_raw"},
]

# ─── server.properties 配置项 ─────────────────────────────────
# kind / default / min / max / values（ENUM 为 [(原始值, i18n 后缀), ...]）
_OPTIONS: List[Dict[str, Any]] = [
    # 🌍 世界与游戏
    {
        "key": "level-name",
        "category": "world",
        "i18n": "scfg_level_name",
        "kind": STR,
    },
    {
        "key": "level-type",
        "category": "world",
        "i18n": "scfg_level_type",
        "kind": ENUM,
        "values": [
            ("minecraft\\:normal", "scfg_level_type_v_normal"),
            ("minecraft\\:flat", "scfg_level_type_v_flat"),
            ("minecraft\\:large_biomes", "scfg_level_type_v_large_biomes"),
            ("minecraft\\:amplified", "scfg_level_type_v_amplified"),
            ("minecraft\\:single_biome_surface", "scfg_level_type_v_single_biome"),
        ],
    },
    {
        "key": "level-seed",
        "category": "world",
        "i18n": "scfg_level_seed",
        "kind": STR,
    },
    {
        "key": "allow-nether",
        "category": "world",
        "i18n": "scfg_allow_nether",
        "kind": BOOL,
    },
    {
        "key": "generate-structures",
        "category": "world",
        "i18n": "scfg_generate_structures",
        "kind": BOOL,
    },
    {
        "key": "spawn-monsters",
        "category": "world",
        "i18n": "scfg_spawn_monsters",
        "kind": BOOL,
    },
    {
        "key": "spawn-animals",
        "category": "world",
        "i18n": "scfg_spawn_animals",
        "kind": BOOL,
    },
    {
        "key": "spawn-npcs",
        "category": "world",
        "i18n": "scfg_spawn_npcs",
        "kind": BOOL,
    },
    {
        "key": "spawn-protection",
        "category": "world",
        "i18n": "scfg_spawn_protection",
        "kind": INT,
        "min": 0,
        "max": 10000,
    },
    {
        "key": "allow-flight",
        "category": "world",
        "i18n": "scfg_allow_flight",
        "kind": BOOL,
    },
    {
        "key": "hardcore",
        "category": "world",
        "i18n": "scfg_hardcore",
        "kind": BOOL,
    },
    {
        "key": "force-gamemode",
        "category": "world",
        "i18n": "scfg_force_gamemode",
        "kind": BOOL,
    },
    {
        "key": "max-world-size",
        "category": "world",
        "i18n": "scfg_max_world_size",
        "kind": INT,
        "min": 1,
        "max": 29999984,
        "advanced": True,
    },
    {
        "key": "generator-settings",
        "category": "world",
        "i18n": "scfg_generator_settings",
        "kind": STR,
        "advanced": True,
    },
    # 👥 玩家与规则
    {
        "key": "gamemode",
        "category": "players",
        "i18n": "scfg_gamemode",
        "kind": ENUM,
        "values": [
            ("survival", "scfg_gamemode_v_survival"),
            ("creative", "scfg_gamemode_v_creative"),
            ("adventure", "scfg_gamemode_v_adventure"),
            ("spectator", "scfg_gamemode_v_spectator"),
        ],
    },
    {
        "key": "difficulty",
        "category": "players",
        "i18n": "scfg_difficulty",
        "kind": ENUM,
        "values": [
            ("peaceful", "scfg_difficulty_v_peaceful"),
            ("easy", "scfg_difficulty_v_easy"),
            ("normal", "scfg_difficulty_v_normal"),
            ("hard", "scfg_difficulty_v_hard"),
        ],
    },
    {
        "key": "pvp",
        "category": "players",
        "i18n": "scfg_pvp",
        "kind": BOOL,
    },
    {
        "key": "max-players",
        "category": "players",
        "i18n": "scfg_max_players",
        "kind": INT,
        "min": 1,
        "max": 1000,
    },
    {
        "key": "online-mode",
        "category": "players",
        "i18n": "scfg_online_mode",
        "kind": BOOL,
    },
    {
        "key": "white-list",
        "category": "players",
        "i18n": "scfg_white_list",
        "kind": BOOL,
    },
    {
        "key": "enforce-whitelist",
        "category": "players",
        "i18n": "scfg_enforce_whitelist",
        "kind": BOOL,
    },
    {
        "key": "player-idle-timeout",
        "category": "players",
        "i18n": "scfg_player_idle_timeout",
        "kind": INT,
        "min": 0,
        "max": 1440,
    },
    {
        "key": "op-permission-level",
        "category": "players",
        "i18n": "scfg_op_permission_level",
        "kind": INT,
        "min": 1,
        "max": 4,
    },
    {
        "key": "function-permission-level",
        "category": "players",
        "i18n": "scfg_function_permission_level",
        "kind": INT,
        "min": 1,
        "max": 4,
        "advanced": True,
    },
    {
        "key": "enable-command-block",
        "category": "players",
        "i18n": "scfg_enable_command_block",
        "kind": BOOL,
    },
    {
        "key": "hide-online-players",
        "category": "players",
        "i18n": "scfg_hide_online_players",
        "kind": BOOL,
    },
    {
        "key": "enforce-secure-profile",
        "category": "players",
        "i18n": "scfg_enforce_secure_profile",
        "kind": BOOL,
        "advanced": True,
    },
    {
        "key": "prevent-proxy-connections",
        "category": "players",
        "i18n": "scfg_prevent_proxy_connections",
        "kind": BOOL,
        "advanced": True,
    },
    # 🌐 网络与连接
    {
        "key": "motd",
        "category": "network",
        "i18n": "scfg_motd",
        "kind": STR,
    },
    {
        "key": "server-port",
        "category": "network",
        "i18n": "scfg_server_port",
        "kind": INT,
        "min": 1,
        "max": 65535,
    },
    {
        "key": "enable-status",
        "category": "network",
        "i18n": "scfg_enable_status",
        "kind": BOOL,
    },
    {
        "key": "server-ip",
        "category": "network",
        "i18n": "scfg_server_ip",
        "kind": STR,
        "advanced": True,
    },
    {
        "key": "network-compression-threshold",
        "category": "network",
        "i18n": "scfg_network_compression_threshold",
        "kind": INT,
        "min": -1,
        "max": 65535,
        "advanced": True,
    },
    {
        "key": "use-native-transport",
        "category": "network",
        "i18n": "scfg_use_native_transport",
        "kind": BOOL,
        "advanced": True,
    },
    {
        "key": "enable-query",
        "category": "network",
        "i18n": "scfg_enable_query",
        "kind": BOOL,
        "advanced": True,
    },
    {
        "key": "query.port",
        "category": "network",
        "i18n": "scfg_query_port",
        "kind": INT,
        "min": 1,
        "max": 65535,
        "advanced": True,
    },
    {
        "key": "enable-rcon",
        "category": "network",
        "i18n": "scfg_enable_rcon",
        "kind": BOOL,
        "advanced": True,
    },
    {
        "key": "rcon.port",
        "category": "network",
        "i18n": "scfg_rcon_port",
        "kind": INT,
        "min": 1,
        "max": 65535,
        "advanced": True,
    },
    {
        "key": "rcon.password",
        "category": "network",
        "i18n": "scfg_rcon_password",
        "kind": STR,
        "advanced": True,
    },
    {
        "key": "broadcast-rcon-to-ops",
        "category": "network",
        "i18n": "scfg_broadcast_rcon_to_ops",
        "kind": BOOL,
        "advanced": True,
    },
    {
        "key": "broadcast-console-to-ops",
        "category": "network",
        "i18n": "scfg_broadcast_console_to_ops",
        "kind": BOOL,
        "advanced": True,
    },
    # ⚡ 性能优化
    {
        "key": "view-distance",
        "category": "performance",
        "i18n": "scfg_view_distance",
        "kind": INT,
        "min": 3,
        "max": 32,
    },
    {
        "key": "simulation-distance",
        "category": "performance",
        "i18n": "scfg_simulation_distance",
        "kind": INT,
        "min": 3,
        "max": 32,
    },
    {
        "key": "entity-broadcast-range-percentage",
        "category": "performance",
        "i18n": "scfg_entity_broadcast_range_percentage",
        "kind": INT,
        "min": 10,
        "max": 1000,
        "advanced": True,
    },
    {
        "key": "max-tick-time",
        "category": "performance",
        "i18n": "scfg_max_tick_time",
        "kind": INT,
        "min": -1,
        "max": 3600000,
        "advanced": True,
    },
    # 🎨 资源包
    {
        "key": "require-resource-pack",
        "category": "resource",
        "i18n": "scfg_require_resource_pack",
        "kind": BOOL,
        "advanced": True,
    },
    {
        "key": "resource-pack",
        "category": "resource",
        "i18n": "scfg_resource_pack",
        "kind": STR,
        "advanced": True,
    },
    {
        "key": "resource-pack-prompt",
        "category": "resource",
        "i18n": "scfg_resource_pack_prompt",
        "kind": STR,
        "advanced": True,
    },
    {
        "key": "resource-pack-sha1",
        "category": "resource",
        "i18n": "scfg_resource_pack_sha1",
        "kind": STR,
        "advanced": True,
    },
]

# ─── 🚀 启动设置（不属于 server.properties） ───────────────────
LAUNCH_OPTIONS: List[Dict[str, Any]] = [
    {
        "key": "@memory",
        "category": "startup",
        "i18n": "scfg_memory",
        "kind": MEMORY,
        "default": "2G",
        "choices": ["1G", "2G", "4G", "6G", "8G", "12G", "16G"],
    },
    {
        "key": "@eula",
        "category": "startup",
        "i18n": "scfg_eula",
        "kind": INFO,
    },
    {
        "key": "@dir",
        "category": "startup",
        "i18n": "scfg_server_dir",
        "kind": INFO,
    },
]


def _with_defaults(option: Dict[str, Any]) -> Dict[str, Any]:
    """补齐默认值与可选字段"""
    item = dict(option)
    item.setdefault("values", None)
    item.setdefault("min", None)
    item.setdefault("max", None)
    item.setdefault("advanced", False)
    item.setdefault("choices", None)
    if "default" not in item:
        item["default"] = DEFAULT_SERVER_PROPERTIES.get(item["key"], "")
    return item


CONFIG_OPTIONS: List[Dict[str, Any]] = [_with_defaults(o) for o in _OPTIONS]
LAUNCH_OPTIONS = [_with_defaults(o) for o in LAUNCH_OPTIONS]

# 所有可编辑行（server.properties + 启动设置）
ALL_OPTIONS: List[Dict[str, Any]] = CONFIG_OPTIONS + LAUNCH_OPTIONS

# server.properties 中可编辑的键集合
PROPERTY_KEYS: List[str] = [o["key"] for o in CONFIG_OPTIONS]


def get_options(category_id: str) -> List[Dict[str, Any]]:
    """获取某个分类下的所有可编辑行"""
    return [o for o in ALL_OPTIONS if o["category"] == category_id]


def get_option(key: str) -> Optional[Dict[str, Any]]:
    """按键查找配置项元数据"""
    for option in ALL_OPTIONS:
        if option["key"] == key:
            return option
    return None


def get_default(key: str) -> str:
    """获取某个 server.properties 键的项目默认值"""
    return DEFAULT_SERVER_PROPERTIES.get(key, "")
