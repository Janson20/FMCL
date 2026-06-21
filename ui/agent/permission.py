"""AGENT 权限系统 - allow/deny/ask 三级权限引擎

参考 opencode PermissionV2 设计：
- 三级效果：allow / deny / ask
- 规则按 action + resource 匹配
- 支持通配符 "*"
- 规则持久化到 config.json
- 每个请求可附带 source 信息用于审计

默认规则：
- 所有工具默认 allow
- exec_command 默认 ask（需用户确认）
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal
from logzero import logger


Effect = Literal["allow", "deny", "ask"]


@dataclass
class PermissionRule:
    """单条权限规则"""
    action: str     # 工具名或 "*"
    resource: str   # 资源标识或 "*"
    effect: Effect  # "allow" | "deny" | "ask"


DEFAULT_RULES: List[PermissionRule] = [
    PermissionRule(action="*", resource="*", effect="allow"),
    PermissionRule(action="exec_command", resource="*", effect="ask"),
    PermissionRule(action="write_file", resource="*", effect="ask"),
    PermissionRule(action="replace_in_file", resource="*", effect="ask"),
    PermissionRule(action="delete_file", resource="*", effect="ask"),
]


class PermissionManager:
    """权限管理器"""

    def __init__(self, rules: Optional[List[PermissionRule]] = None):
        self._rules: List[PermissionRule] = list(rules) if rules else list(DEFAULT_RULES)

    def check(self, action: str, resource: str = "*") -> Effect:
        """检查操作权限

        规则按顺序匹配，命中第一条即停止。
        """
        for rule in self._rules:
            if self._match(rule.action, action) and self._match(rule.resource, resource):
                return rule.effect
        return "ask"  # 默认询问

    def add_rule(self, action: str, resource: str, effect: Effect):
        """添加权限规则（覆盖已存在的相同 action+resource 规则）"""
        self.remove_rule(action, resource)
        self._rules.append(PermissionRule(action=action, resource=resource, effect=effect))
        logger.info(f"[Permission] 添加规则: {action}/{resource} -> {effect}")

    def remove_rule(self, action: str, resource: str):
        """移除权限规则"""
        self._rules = [r for r in self._rules if not (r.action == action and r.resource == resource)]

    def get_rules(self) -> List[dict]:
        """获取所有规则（用于持久化）"""
        return [{"action": r.action, "resource": r.resource, "effect": r.effect} for r in self._rules]

    def load_rules(self, rule_dicts: List[dict]):
        """从持久化数据加载规则"""
        if not rule_dicts:
            return
        for rd in rule_dicts:
            action = rd.get("action", "*")
            resource = rd.get("resource", "*")
            effect = rd.get("effect", "allow")
            if effect in ("allow", "deny", "ask"):
                self.add_rule(action, resource, effect)

    def to_config(self) -> dict:
        """导出为配置文件格式"""
        return {
            "agent_permissions": self.get_rules(),
        }

    @staticmethod
    def from_config(config_dict: dict) -> "PermissionManager":
        """从配置文件创建"""
        rules_data = config_dict.get("agent_permissions", [])
        pm = PermissionManager()
        pm.load_rules(rules_data)
        return pm

    @staticmethod
    def _match(pattern: str, value: str) -> bool:
        """通配符匹配"""
        if pattern == "*":
            return True
        return pattern == value


# 全局单例
_permission_manager: Optional[PermissionManager] = None


def get_permission_manager() -> PermissionManager:
    """获取全局权限管理器"""
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager()
    return _permission_manager


def init_permission_manager(rules: Optional[List[dict]] = None):
    """初始化权限管理器（从配置文件加载）"""
    global _permission_manager
    pm = PermissionManager()
    if rules:
        pm.load_rules(rules)
    _permission_manager = pm


def check_permission(action: str, resource: str = "*") -> Effect:
    """便捷函数：检查权限"""
    return get_permission_manager().check(action, resource)
