// FmIcon.qml —— 主题上色图标（阶段 2 任务 2.16；把 2.10 的"上色只能在 Python 侧做"落地）。
//
// 什么时候用它：界面上的一切图标。图标资源是 `qml/assets/icons/*.svg`（一个语义一个文件）。
// 什么时候不要用：FluentUI 控件**内部**自带的图标（那是第三方控件的实现部分，跟着控件走）；
//   需要多色/位图插画的地方（本组件只做单色描边/填充图标）。
//
// 为什么不是 `Image { source: Runtime.iconUrl(...) }`：QtSvg 把 SVG 里的 `currentColor`
//   解析成**不透明黑**，暗色主题下几乎看不见；`ColorOverlay` / `MultiEffect` 在本仓库
//   所有测试的跑法（offscreen）下**静默失效**。所以上色由 Python 侧的
//   `IconImageProvider`（`app/bridges/icon_provider.py`）渲染成目标色的位图，
//   QML 这边只拼 `image://fmcl-icon/<name>?color=%23RRGGBB`。
//   完整实测对比见 `qml/assets/icons/README.md` 第三节。
//
// 颜色**只能**来自 `Theme.*`（闸门 R8 拦十六进制字面量）。名字不合法或图片取不到时
// 退化成 2.10 的 `Runtime.iconUrl()`（能看见，但是黑的）—— `fallbackUsed` 变成 true，
// Gallery 与冒烟测试据此报警，避免"provider 忘了注册"这种静默降级没人发现。

import QtQuick

Item {
    id: root
    objectName: "fmIcon"

    //: 图标名（不带目录与扩展名，规则见 qml/assets/icons/README.md）
    property string name: ""
    //: 图标颜色（只来自 Theme.*）
    property color color: Theme?.textPrimary ?? "transparent"
    //: 边长（默认用设计令牌里的图标尺寸（Theme.*））
    property int size: Theme?.iconSize ?? 0
    //: true = provider 没注册或取图失败，当前显示的是未上色的兜底 SVG
    property bool fallbackUsed: false

    readonly property string providerUrl: "image://fmcl-icon/" + name + "?color=" + encodeURIComponent(String(color))
    readonly property string fallbackUrl: (typeof Runtime !== "undefined" && Runtime && name.length > 0)
                                          ? (Runtime?.iconUrl(name) ?? "") : ""

    implicitWidth: size
    implicitHeight: size

    Image {
        id: image
        objectName: "fmIconImage"
        anchors.fill: parent
        source: root.name.length > 0 ? root.providerUrl : ""
        // sourceSize 必须给：不给的话光栅化尺寸按 SVG 的 24 算，缩小会走缩放（2.10 实测）。
        // 真实窗口下 Qt 按设备像素比放大这个请求（DPR=1.25 时 sourceSize 20 -> 请求 25），
        // provider 按请求尺寸渲染，所以高 DPI 不糊。
        sourceSize.width: root.size
        sourceSize.height: root.size
        fillMode: Image.PreserveAspectFit
        smooth: true

        onStatusChanged: {
            if (status === Image.Error) {
                // provider 缺席或这张图取不到 -> 退回 file:// 的原始 SVG（可见但不可读）
                if (root.fallbackUrl.length > 0 && String(source) !== root.fallbackUrl) {
                    root.fallbackUsed = true
                    source = root.fallbackUrl
                }
            } else if (status === Image.Ready && String(source) === root.providerUrl) {
                root.fallbackUsed = false
            }
        }
    }
}
