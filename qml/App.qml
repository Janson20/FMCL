// FMCL QML 根窗口 —— 阶段 2 任务 2.2 的**最小骨架**。
//
// 谁负责什么：
//   * 任务 2.2（本文件当前形态）：证明"入口 → 引擎 → 桥接 → 根组件"这条链路通了。
//   * 任务 2.8 / 2.9：Theme / Tr 单例（届时本文件改为 `import FMCL 1.0` 并绑定它们的色键与文案）。
//   * 任务 2.12：真正的窗口骨架（标题栏 + 一级导航 + 页面栈 + 底部状态条）。
//   * 任务 2.14：启动画面与"协议 → 公告 → 预下载"链条。
//
// 因此本文件刻意**不硬编码任何颜色、不写任何中文**：
//   * 颜色留给 2.8 的 Theme 单例（现在用窗口默认色，不制造"手工登记颜色"的先例）；
//   * 文案留给 2.9 的 Tr 单例（闸门 R3 禁止中文字面量，R4 要求绑定必须走 `Tr.map[...]`）。
//     下面的英文占位串是**临时**的，2.12 会把它们整段替换掉。
//
// 约束见 docs/refactor/11-phase2-contract.md 第七节（闸门 R1~R7）：
// 禁渐变/亚克力（R1）、禁 emoji（R2）、禁中文字面量（R3）、绑定不得用 `Tr.t(`（R4）、
// `overlays/` 下不得用 `color: "transparent"`（R6）。

import QtQuick
import QtQuick.Controls

Window {
    id: app

    // `Runtime` 是上下文属性（app/bridges/runtime_bridge.py），不需要 import。
    // 只读：应用名/版本、Python/Qt 版本、数据目录、FluentUI 模块是否就绪、桥接注册结果。
    //
    // **为什么每个绑定都要判空**：测试实测过 —— 引擎析构时上下文属性先被清掉，而那时
    // 还有绑定的求值排在队列里，于是退出阶段会刷一串
    // `TypeError: Cannot read property 'appName' of null`。它不影响功能，但会污染日志，
    // 也会让"日志里有没有 QML 报错"这条判据（冒烟测试要用）永远为真。
    readonly property string runtimeLabel: Runtime
                                           ? (Runtime.appName + " " + Runtime.appVersion)
                                           : ""

    width: 1280
    height: 800
    minimumWidth: 960
    minimumHeight: 640
    visible: true
    title: runtimeLabel

    // 留给 2.12 的骨架：页面栈先建出来，页面由阶段 3 逐页填。
    StackView {
        id: pageStack
        objectName: "pageStack"
        anchors.fill: parent
        initialItem: placeholderPage
    }

    Component {
        id: placeholderPage

        Item {
            objectName: "placeholderPage"

            Column {
                anchors.centerIn: parent
                spacing: 8

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: app.runtimeLabel
                    font.pixelSize: 28
                }

                Text {
                    objectName: "bridgeStatusText"
                    anchors.horizontalCenter: parent.horizontalCenter
                    // 桥接注册结果：B/C/D 组落地前会显示缺哪些桥 —— 这是**可断言**的接线证据，
                    // 而不是"看起来能跑"。
                    text: Runtime ? Runtime.bridgeStatus : ""
                    font.pixelSize: 14
                }

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "QML shell placeholder (task 2.12 builds the real shell)"
                    font.pixelSize: 12
                    opacity: 0.6
                }
            }
        }
    }
}
