"""模组分类器 — 本地分类规则

仅凭 jar 内元数据即可做出确定性判断，不需要联网。
对于不确定的情况返回 "unknown"，交给远程来源处理。
"""

from launcher.mod_classifier.shared import CLIENT_ONLY_ENTRYPOINT_HINTS, Classification, LoaderType, ModMeta


def classify_local(meta: ModMeta) -> Classification:
    """基于本地元数据的分类判定

    返回 "server-keep", "client-only" 或 "unknown"（需继续远程查询）。
    """
    # ── Forge / NeoForge ──
    if meta.loader in (LoaderType.FORGE.value, LoaderType.NEOFORGE.value):
        if meta.client_side_only:
            return Classification("client-only", "local", f"{meta.loader} mods.toml: clientSideOnly=true")
        dep_sides = {s.upper() for s in meta.dependency_sides if s}
        if dep_sides == {"CLIENT"}:
            return Classification("client-only", "local", f"{meta.loader} 依赖声明 side=CLIENT")
        if dep_sides == {"SERVER"}:
            return Classification("server-keep", "local", f"{meta.loader} 依赖声明 side=SERVER")
        return Classification("unknown", "local", f"{meta.loader} 本地元数据不足")

    # ── 未知加载器 ──
    if meta.loader == LoaderType.UNKNOWN.value:
        return Classification("unknown", "local", "未知加载器，本地元数据不足")

    # ── Fabric / Quilt ──
    entrypoint_set = {ep.lower() for ep in meta.entrypoints if ep}

    # environment 明确标记
    if meta.environment == "client":
        return Classification("client-only", "local", "Fabric/Quilt environment=client")
    if meta.environment == "server":
        return Classification("server-keep", "local", "Fabric/Quilt environment=server")

    # 只有客户端专属入口点
    if entrypoint_set and entrypoint_set.issubset(CLIENT_ONLY_ENTRYPOINT_HINTS):
        return Classification("unknown", "local", "仅有客户端入口点，继续联网确认")

    # 含 main 或 server 入口点
    if "main" in entrypoint_set or "server" in entrypoint_set:
        return Classification("unknown", "local", "含 main/server 入口点，本地无法确认")

    return Classification("unknown", "local", "本地元数据不足")
