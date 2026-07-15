"""模组分类器 - 自动识别服务端/客户端模组

从 auto-mod-classifier 项目提取的核心分类逻辑，适配 FMCL 整合包开服流程。

使用方式:
    from launcher.mod_classifier import classify_mods_in_directory

    results = classify_mods_in_directory("path/to/mods", use_online=True)
    server_keep, client_only, unknown = results
"""

from launcher.mod_classifier.pipeline import (
    ClassificationResult,
    classify_mods,
    classify_mods_in_directory,
    filter_server_mods,
)

__all__ = ["classify_mods", "classify_mods_in_directory", "filter_server_mods", "ClassificationResult"]
