// tools 领域占位页（`02` 第 4.8 节 / 阶段 2 任务 2.12 的窗口骨架）。
//
// 这一版只剩下"把领域接到页面骨架上"：页头、三态（加载中 / 空数据 / 出错）与路由帧
// 五件套都由 `qml/components/FmPage.qml` 提供。返工 C 组之前，这 12 个页面是同一份
// 145 行代码各抄一遍（只有 `objectName` 与领域名不同），改一处要改十二处。
//
// 纪律：颜色只来自 `Theme.*`（闸门 R8）、图标走 FmIcon（闸门 R2）、文案走 `Tr.map[…]`
// （闸门 R3/R4）、自研件一律用 `qml/components` 里的 `Fm*`（闸门 R9）。
//
// 阶段 3 的改法：把 `contentState` 绑到自己桥的状态上（例如 `VersionsPage.loadState`），
// 把真实内容直接写在 `FmPage { }` 里（会自动进内容区，且只在 ready 时可见），
// 然后删掉 `demoControlsVisible` 那两行（演示按钮是给验收的人手动切三态用的）。

import "../../components"

FmPage {
    objectName: "toolsPage"

    //: 阶段 2 手动切三态；阶段 3 换成页面自己桥的状态
    contentState: "loading"
    demoControlsVisible: true
}
