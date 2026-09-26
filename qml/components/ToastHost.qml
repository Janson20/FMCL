// ToastHost.qml —— 右下角通知队列（A-16 / M-38 / M-39 的 QML 版）。
//
// ## 堆叠方向：新 toast 往上叠、y 递减（与旧实现一致）
//
// 旧实现 `ui/dialogs.py:98-110` 的算法是"先累加已有 toast 的高度，再
// `y = 父窗底边 - 高 - 16 - offset`" —— 也就是说**最早的那条钉在右下角**，
// 后来的每一条都往上放。阶段 0 第 12.5 节实测记录过这一点。本组件照抄这个方向：
//
//     slotY(slot) = height - itemMargin - itemHeight - slot * (itemHeight + itemGap)
//
// 第 0 槽（最早的一条）最靠下，槽号越大 y 越小。
//
// ## 超过 6 条怎么办（旧实现没有上限，必须新定一个）
//
// 旧实现会无限往上叠，第 7 条起就跑出窗口上沿（down 模式则会跑出下沿），
// 用户看不见、也点不到。这里的处置是 **"最多 6 条 + 桥那边挤掉最旧的一条"**
// （`DialogBridge._enforce_cap`，常量 `MAX_TOASTS`）。为什么挤最旧的、而不是
// 丢掉新来的或整体上移：
//
// * **丢新的**会丢掉最新信息，而通知的价值与"新鲜度"正相关；
// * **整体上移**（老的不动、新的往上顶）越顶越远，第 7 条照样跑出上沿，
//   只是把越界推迟了几条，没有解决问题；
// * **挤最旧的**把可见条数恒定在 6，位置恒定在右下角这块固定区域里，
//   且最旧的那条本来也最接近自己的到期时间（信息损失最小）。
//
// 上限与挤占都在 Python 侧（`DialogBridge`）：`activeToasts()` 是"当前该显示什么"的
// 唯一真值来源，界面只是它的投影 —— 这样"退出时清理队列"（A-16/M-45）、
// 超时、上限三件事都有可断言的真值，而不是散在动画回调里。
//
// ## 淡入淡出与"到点消失"的分工
//
// * **到期**：`DialogBridge` 的 QTimer 到点后把这条从队列里摘掉，并 emit `toastsChanged`；
// * **淡出**：本组件收到 `toastsChanged` 后对账 `Dialogs.activeToasts()`，发现"桥里没了
//   但界面还有"的，就把它标成 `closing` → 跑淡出动画 → 动画结束后再销毁委托；
// * **淡入**：委托创建时从 0 透明渐显。
//
// 这样只有一个定时器（Python 侧）在管寿命，动画不会和数据真值抢同一件事。
//
// ## 退出清理
//
// `Dialogs.clearToasts()`（`@Slot()`）是退出链要调的那个口子；本组件在
// `Component.onDestruction` 里也会通知桥"宿主要没了"（`markClosing()` → 清队列）。
// `main_qml` 的退出链本身是任务 2.14 的范围。
//
// 用法（2.12 的 App.qml 里）：`ToastHost { anchors.fill: parent; z: 90 }`

import QtQuick
import QtQuick.Controls

Item {
    id: toastHost
    objectName: "toastHost"

    // ─── 几何（取值抄自旧实现，便于并排比对） ───────────────
    //: 单条 toast 宽/高（`ui/dialogs.py:61` 的 `w, h = 280, 72`）
    readonly property int itemWidth: 280
    readonly property int itemHeight: 72
    //: 条间距（旧实现的 `offset += existing.winfo_height() + 8`）
    readonly property int itemGap: 8
    //: 距窗口右下角的边距（旧实现的 `- 16`）
    readonly property int itemMargin: 16

    //: 与 `DialogBridge.MAX_TOASTS` 对齐（这里只用于诊断与文档）
    readonly property int maxToasts: 6

    //: 动画时长。测试会把它们调大，好在动画中途采样（见 tests/test_dialogs_qml.py）
    property int fadeInDuration: 160
    property int fadeOutDuration: 180

    //: 当前显示的条数（不含正在淡出的）
    readonly property int liveCount: countLive()

    function countLive() {
        var n = 0
        for (var i = 0; i < toastModel.count; i++) {
            if (!toastModel.get(i).closing)
                n++
        }
        return n
    }

    //: 槽位 -> y。**槽号越大 y 越小**（新 toast 往上叠）
    function slotY(slot) {
        return height - itemMargin - itemHeight - slot * (itemHeight + itemGap)
    }

    function levelColor(level) {
        if (level === "success")
            return Theme?.success ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        return Theme?.accent ?? "transparent"
    }

    function levelIcon(level) {
        if (level === "success")
            return "success"
        if (level === "warning")
            return "warning"
        if (level === "error")
            return "error"
        return "info"
    }

    function indexOf(toastId) {
        for (var i = 0; i < toastModel.count; i++) {
            if (toastModel.get(i).toastId === toastId)
                return i
        }
        return -1
    }

    // ─── 与桥对账 ───────────────────────────────────────────

    function appendToast(data) {
        if (!data || data.id === undefined)
            return
        if (indexOf(data.id) >= 0)
            return
        toastModel.append({
            "toastId": data.id,
            "title": data.title ? String(data.title) : "",
            "message": data.message ? String(data.message) : "",
            "subtitle": data.subtitle ? String(data.subtitle) : "",
            "level": data.level ? String(data.level) : "info",
            "icon": data.icon ? String(data.icon) : "",
            "slot": countLive(),
            "closing": false
        })
    }

    //: 把每个"还在"的 toast 重新编号到 0..n-1（正在淡出的保持原槽位，免得跳位）
    function relayout() {
        var slot = 0
        for (var i = 0; i < toastModel.count; i++) {
            if (!toastModel.get(i).closing) {
                toastModel.setProperty(i, "slot", slot)
                slot++
            }
        }
    }

    function syncToasts() {
        var active = (typeof Dialogs !== "undefined" && Dialogs) ? Dialogs.activeToasts() : []
        var alive = {}
        for (var i = 0; i < active.length; i++) {
            alive[active[i].id] = true
            appendToast(active[i])
        }
        // 桥里已经没有的：先淡出，动画结束后再摘掉（不要"啪"地消失）
        for (var j = 0; j < toastModel.count; j++) {
            var entry = toastModel.get(j)
            if (!alive[entry.toastId] && !entry.closing)
                toastModel.setProperty(j, "closing", true)
        }
        relayout()
    }

    function removeToast(toastId) {
        var index = indexOf(toastId)
        if (index >= 0) {
            toastModel.remove(index)
            relayout()
        }
    }

    function dismiss(toastId) {
        // 用户点了一下：交给桥（桥是队列真值），淡出走 syncToasts 那条路
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.dismissToast(toastId)
    }

    function clearAll() {
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.clearToasts()
    }

    ListModel {
        id: toastModel
    }

    Connections {
        target: (typeof Dialogs !== "undefined" && Dialogs) ? Dialogs : null

        function onToastRequested(data) {
            toastHost.appendToast(data)
            toastHost.relayout()
        }

        function onToastsChanged() {
            toastHost.syncToasts()
        }
    }

    Component.onCompleted: {
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.markReady()
    }

    Component.onDestruction: {
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.markClosing()
    }

    Repeater {
        id: toastRepeater
        objectName: "toastRepeater"
        model: toastModel

        delegate: Rectangle {
            id: card
            objectName: "toastItem"

            required property int toastId
            required property string title
            required property string message
            required property string subtitle
            required property string level
            required property string icon
            required property int slot
            required property bool closing

            width: toastHost.itemWidth
            height: toastHost.itemHeight
            x: toastHost.width - width - toastHost.itemMargin
            y: toastHost.slotY(slot)
            radius: Theme?.radiusLg ?? 0
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: toastHost.levelColor(level)
            opacity: 0

            NumberAnimation on opacity {
                id: fadeIn
                from: 0.0
                to: 1.0
                duration: toastHost.fadeInDuration
                running: true
            }

            onClosingChanged: {
                if (closing) {
                    fadeIn.stop()
                    fadeOut.start()
                }
            }

            NumberAnimation {
                id: fadeOut
                target: card
                property: "opacity"
                to: 0.0
                duration: toastHost.fadeOutDuration
                onFinished: toastHost.removeToast(card.toastId)
            }

            // 图标（旧实现左侧 40px 宽的图标位；这里用 SVG，不用 emoji）
            Item {
                id: iconSlot
                anchors.left: parent.left
                anchors.leftMargin: Theme?.spacingSm ?? 0
                anchors.verticalCenter: parent.verticalCenter
                width: (Theme?.iconSize ?? 0) + Theme?.spacingMd ?? 0
                height: width

                Image {
                    objectName: "toastIcon"
                    anchors.centerIn: parent
                    source: (typeof Runtime !== "undefined" && Runtime)
                            ? (Runtime?.iconUrl(card.icon !== "" ? card.icon : toastHost.levelIcon(card.level)) ?? "")
                            : ""
                    sourceSize.width: Theme?.iconSize ?? 0
                    sourceSize.height: Theme?.iconSize ?? 0
                    fillMode: Image.PreserveAspectFit
                }
            }

            Column {
                anchors.left: iconSlot.right
                anchors.right: parent.right
                anchors.rightMargin: Theme?.spacingSm ?? 0
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2

                // 副标题在上、主文案在下（旧实现的排布：subtitle 小字在上、
                // title 加粗在下；端口只给 message 时主文案就是 message）
                Text {
                    objectName: "toastSubtitle"
                    width: parent.width
                    visible: text.length > 0
                    text: card.subtitle
                    color: toastHost.levelColor(card.level)
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideRight
                }

                Text {
                    objectName: "toastMessage"
                    width: parent.width
                    text: card.title !== "" ? card.title : card.message
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: true
                    elide: Text.ElideRight
                }
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: toastHost.dismiss(card.toastId)
            }
        }
    }
}
