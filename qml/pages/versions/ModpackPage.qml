// ModpackPage.qml —— 整合包页**占位**（阶段 3 任务 3.8 落地）。
//
// 为什么要有这个文件（2026-10-06 人工验收）：
// `versions/modpack` 这条路由原来指向 `VersionsPage.qml`，于是安装向导里点「安装整合包」
// 会落到**版本列表**上 —— 面包屑写着「Modpack Info」，屏幕上却是已安装版本，
// 用户一眼就看出"这不是这页该显示的东西"。
//
// 占位页的写法（与阶段 2 的领域占位页同一套）：`FmPage` 提供页头 + 路由帧 + 三态，
// 这里只把三态钉在 `empty` 上给一句实话。**不要**用 `contentState: "loading"` 占位 ——
// 那会让用户以为它一直在加载（阶段 2 的 12 个占位页就是这么写的，阶段 3 逐页替换掉）。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`（R2）、文案走 `Tr.map[…]`（R3/R4）、
// 只用 `qml/components` 里的 `Fm*`（R9）。

import "../../components"

FmPage {
    objectName: "modpackPage"

    contentState: "empty"
    emptyText: Tr?.map["modpack_info"] ?? "modpack_info"
    emptyDetail: Tr?.map["page_placeholder_hint"] ?? "page_placeholder_hint"
}
