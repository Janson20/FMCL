"""依赖解析器 - SemVer 约束解析 + 拓扑排序 + 循环检测"""

import re
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional

from logzero import logger


@dataclass
class VersionConstraint:
    """单个版本约束，如 '>=1.0.0' 或 '<2.0.0'"""
    op: str          # >=, <=, >, <, ==, !=
    version: str     # 1.0.0


@dataclass
class PluginDependency:
    """解析后的依赖项"""
    plugin_id: str
    constraints: List[VersionConstraint]


def _parse_version(version: str) -> Tuple[int, int, int, Tuple[str, ...]]:
    """将 SemVer 版本号解析为可比较的元组

    Args:
        version: 如 "1.2.3" 或 "1.2.3-beta.1"

    Returns:
        (major, minor, patch, (pre_release_parts))
    """
    # 分离 pre-release
    core, _, pre = version.partition("-")
    # 分离 build metadata
    pre = pre.split("+")[0]

    core_parts = core.split(".")
    major = int(core_parts[0]) if len(core_parts) > 0 else 0
    minor = int(core_parts[1]) if len(core_parts) > 1 else 0
    patch = int(core_parts[2]) if len(core_parts) > 2 else 0

    # pre-release 部分
    pre_tuple: Tuple[str, ...] = ()
    if pre:
        pre_tuple = tuple(pre.split("."))

    return (major, minor, patch, pre_tuple)


def _compare_semver(v1: str, v2: str) -> int:
    """语义化版本比较

    Returns:
        -1: v1 < v2
         0: v1 == v2
         1: v1 > v2
    """
    a = _parse_version(v1)
    b = _parse_version(v2)

    # 比较核心版本号
    for i in range(3):
        if a[i] < b[i]:
            return -1
        if a[i] > b[i]:
            return 1

    # 比较 pre-release (有 pre-release 的版本 < 无 pre-release 的版本)
    a_pre = a[3]
    b_pre = b[3]

    if not a_pre and not b_pre:
        return 0
    if not a_pre and b_pre:
        return 1    # 1.0.0 > 1.0.0-beta
    if a_pre and not b_pre:
        return -1   # 1.0.0-beta < 1.0.0

    # 两个都有 pre-release
    max_len = max(len(a_pre), len(b_pre))
    for i in range(max_len):
        part_a = a_pre[i] if i < len(a_pre) else ""
        part_b = b_pre[i] if i < len(b_pre) else ""

        # 尝试数字比较
        if part_a.isdigit() and part_b.isdigit():
            diff = int(part_a) - int(part_b)
            if diff != 0:
                return diff
        elif part_a.isdigit():
            return -1   # 数字优先
        elif part_b.isdigit():
            return 1
        else:
            if part_a < part_b:
                return -1
            if part_a > part_b:
                return 1

    return 0


def _parse_constraint(constraint_str: str) -> List[VersionConstraint]:
    """解析约束字符串，如 '>=1.0,<2.0' 或 '==1.5.0'"""
    constraints = []
    # 匹配操作符 + 版本号
    pattern = re.compile(r'(>=|<=|!=|==|>|<)\s*([\w.\-+]+)')
    for match in pattern.finditer(constraint_str):
        constraints.append(VersionConstraint(
            op=match.group(1),
            version=match.group(2),
        ))
    if not constraints:
        # 尝试作为简单版本号处理 (隐含 ==)
        clean = constraint_str.strip()
        if clean:
            constraints.append(VersionConstraint(op="==", version=clean))
    return constraints


def _check_constraint(constraints: List[VersionConstraint], version: str) -> bool:
    """检查版本是否满足一组约束"""
    for c in constraints:
        cmp = _compare_semver(version, c.version)
        if c.op == ">=" and cmp < 0:
            return False
        if c.op == "<=" and cmp > 0:
            return False
        if c.op == ">" and cmp <= 0:
            return False
        if c.op == "<" and cmp >= 0:
            return False
        if c.op == "==" and cmp != 0:
            return False
        if c.op == "!=" and cmp == 0:
            return False
    return True


def parse_dependencies(raw_deps: Dict[str, str]) -> List[PluginDependency]:
    """解析原始依赖字典为 PluginDependency 列表"""
    result = []
    for plugin_id, constraint_str in raw_deps.items():
        constraints = _parse_constraint(constraint_str)
        if constraints:
            result.append(PluginDependency(
                plugin_id=plugin_id,
                constraints=constraints,
            ))
    return result


class DependencyResolver:
    """依赖解析器

    提供:
        - 拓扑排序（返回安全的加载顺序）
        - 循环依赖检测
        - 版本兼容性检查
    """

    def __init__(self):
        pass

    def resolve_load_order(
        self,
        plugins: Dict[str, Tuple[str, Dict[str, str]]],
    ) -> Tuple[List[str], List[str]]:
        """解析加载顺序

        Args:
            plugins: {plugin_id: (version, {dep_id: constraint_str})}

        Returns:
            (load_order, errors)
            - load_order: 拓扑排序后的插件 ID 列表
            - errors: 错误信息列表（包含循环依赖等）
        """
        errors: List[str] = []
        sorted_order: List[str] = []

        # 构建邻接表和入度
        in_degree: Dict[str, int] = {pid: 0 for pid in plugins}
        adjacency: Dict[str, List[str]] = {pid: [] for pid in plugins}

        for pid, (_, deps) in plugins.items():
            dep_list = parse_dependencies(deps)
            for dep in dep_list:
                if dep.plugin_id not in plugins:
                    # 依赖的外部模块，跳过（可在运行时检查）
                    continue
                adjacency[dep.plugin_id].append(pid)
                in_degree[pid] = in_degree.get(pid, 0) + 1

        # Kahn 算法
        queue: List[str] = [pid for pid, deg in in_degree.items() if deg == 0]

        while queue:
            # 按字母序出队，保证确定性
            queue.sort()
            node = queue.pop(0)
            sorted_order.append(node)

            for neighbor in adjacency.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 检查是否有未输出的节点 → 存在循环依赖
        remaining = [pid for pid in plugins if pid not in sorted_order]
        if remaining:
            errors.append(f"检测到循环依赖，涉及插件: {', '.join(remaining)}")

        return sorted_order, errors

    def check_version_compatibility(
        self,
        deps: Dict[str, str],
        installed: Dict[str, str],
    ) -> Tuple[bool, List[str]]:
        """检查版本兼容性

        Args:
            deps: 依赖插件的版本约束，如 {"com.a": ">=1.0,<2.0"}
            installed: 已安装的插件版本，如 {"com.a": "1.5.0"}

        Returns:
            (兼容性, 错误列表)
        """
        errors = []
        dep_list = parse_dependencies(deps)

        for dep in dep_list:
            installed_ver = installed.get(dep.plugin_id)
            if installed_ver is None:
                errors.append(f"缺少依赖: {dep.plugin_id}")
                continue
            if not _check_constraint(dep.constraints, installed_ver):
                constraint_str = ", ".join(
                    f"{c.op}{c.version}" for c in dep.constraints
                )
                errors.append(
                    f"{dep.plugin_id} 版本 {installed_ver} 不满足约束 {constraint_str}"
                )

        return len(errors) == 0, errors

    def check_conflicts(
        self,
        conflicts: Dict[str, str],
        installed: Dict[str, str],
    ) -> Tuple[bool, List[str]]:
        """检查冲突

        Args:
            conflicts: 冲突的插件及版本约束
            installed: 已安装的插件版本

        Returns:
            (无冲突, 错误列表)
        """
        errors = []
        conflict_list = parse_dependencies(conflicts)

        for conflict in conflict_list:
            installed_ver = installed.get(conflict.plugin_id)
            if installed_ver is None:
                continue
            if _check_constraint(conflict.constraints, installed_ver):
                errors.append(f"与已安装插件 '{conflict.plugin_id}@{installed_ver}' 冲突")

        return len(errors) == 0, errors

    @staticmethod
    def compare_versions(v1: str, v2: str) -> int:
        """比较两个 SemVer 版本号"""
        return _compare_semver(v1, v2)
