// DialogHost.qml —— 对话框宿主：把 Dialogs 桥的请求变成屏幕上的对话框，并把答案回填。
//
// ## 它接什么、回什么
//
//   Dialogs.dialogRequested(payload)  -> 按 kind 选组件 -> 用户作答
//                                                       -> Dialogs.submitDialog(id, value)
//   Dialogs.dialogClosed(id)          -> 从队列里摘掉这条
//   Dialogs.progressChanged/Closed    -> 更新/关闭进度面板
//
// ## 三条硬边界
//
// 1. **没有对应组件也必须回填兜底值**：`enqueue()` 里 kind 认不出来时**立刻**调
//    `Dialogs.cancelDialog(id)`（桥那边用 `payload.fallback` 收尾）。什么都不做的话，
//    调用方只能干等到端口超时 —— 这是本任务最容易漏的一条，`tests/test_dialogs_qml.py`
//    有专门的用例与负例钉住。
// 2. **队列是模态的**：同一时刻只显示队首那一个（旧实现每个对话框都 `grab_set()`）。
//    worker 线程可能在第一个弹窗还没答完时又送来第二个，所以必须排队而不是覆盖。
// 3. **遮罩只负责挡输入**：整窗没有一个颜色字面量（闸门 R8），底色取自 `Theme?.bgDark ?? "transparent"`。
//
// ## 为什么不开独立窗口
//
// 旧实现每个对话框都是 `ctk.CTkToplevel`（独立窗口 + `grab_set()`）。QML 版改成
// 主窗口内的遮罩 + 卡片，理由有三条：阶段 0 实测过"悬浮窗不开 WS_EX_NOACTIVATE 会抢
// 焦点"（契约决策 3），独立窗口要重复处理这套；对话框的定位要跟着主窗口（旧实现
// `_centered_geometry` 也是优先相对父窗口居中）；测试里也能直接在同一个场景树里数委托，
// 不用去翻 `QGuiApplication.topLevelWindows()`。
//
// 用法（2.12 的 App.qml 里）：
//     DialogHost { anchors.fill: parent; z: 100 }
//     ToastHost  { anchors.fill: parent; z: 90 }

import QtQuick
import QtQuick.Controls

Item {
    id: host
    objectName: "dialogHost"

    //: 待显示的请求队列（队首就是当前显示的那个）。**每次改动都整体换一个新数组**，
    //: 这样 `head` 这类绑定才能收到变更通知（QML 不会跟踪数组内容的变化）。
    property var queue: []

    //: 当前进度快照（progressChanged 写入，progressClosed 清空）
    property var progressData: null

    // ─── 壳层自己发起的确认框（阶段 3 任务 3.4） ─────────────
    //
    // 用途：壳层要问一句"确定吗"，而**答案只在 QML 里用** —— 设置页的
    // "有未保存改动，确定要离开吗？"就是它（离开守卫由 `Nav` 发起，确认走这里）。
    //
    // 为什么是**声明式的一对属性 + 一个信号**，而不是"传一个回调函数进来"：
    // 第一版把回调塞进一个 `property var` 的映射表里，实测**答案根本没回到发起方**
    // （函数跨 QML→C++ 属性存储时不可靠），表现为"点了确定什么都没发生、也不报错"。
    // 现在发起方写 `prompt` + 置 `promptVisible`，答案经 `promptAnswered(ok)` 信号回去 ——
    // 全程只有普通数据，没有函数过境。
    //
    // 为什么不直接 `ConfirmDialog { }`：那样要自己复刻遮罩与焦点
    // （03 的纪律：不得私自新增一次性控件），而且会和桥的对话框**同时**显示。
    property bool promptVisible: false
    property var prompt: ({})
    signal promptAnswered(bool ok)

    function showPrompt(request) {
        var payload = request || ({})
        host.prompt = {
            "title": payload.title !== undefined ? String(payload.title) : "",
            "message": payload.message !== undefined ? String(payload.message) : "",
            "default": payload.default === true
        }
        host.promptVisible = true
    }

    function closePrompt() {
        host.promptVisible = false
    }

    readonly property var head: (queue.length > 0) ? queue[0] : null
    readonly property var currentDialog: dialogLoader.item

    // ─── 队列管理 ───────────────────────────────────────────

    function componentFor(kind) {
        if (kind === "confirm")
            return confirmComponent
        if (kind === "ask_text")
            return textInputComponent
        if (kind === "choose")
            return choiceComponent
        if (kind === "info" || kind === "warning" || kind === "error")
            return messageComponent
        return null
    }

    function indexOf(dialogId) {
        for (var i = 0; i < queue.length; i++) {
            if (queue[i].id === dialogId)
                return i
        }
        return -1
    }

    function enqueue(request) {
        if (!request || request.id === undefined)
            return
        if (componentFor(request.kind) === null) {
            // 边界 1：认不出的 kind 也必须有交代 —— 立刻按兜底值收尾
            console.warn("DialogHost: no component for kind '" + request.kind + "', replying fallback")
            failRequest(request, "unknown kind")
            return
        }
        if (indexOf(request.id) >= 0)
            return
        var next = queue.slice()
        next.push(request)
        queue = next
    }

    function dequeue(dialogId) {
        var next = []
        for (var i = 0; i < queue.length; i++) {
            if (queue[i].id !== dialogId)
                next.push(queue[i])
        }
        if (next.length !== queue.length)
            queue = next
    }

    //: "答不上来"的唯一出口：交给桥按 `payload.fallback` 收尾（桥那边幂等）。
    function failRequest(request, why) {
        console.warn("DialogHost: cannot show request " + request.id + " (" + why + ")")
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.cancelDialog(request.id)
    }

    function clearQueue() {
        queue = []
    }

    //: 壳层提示作答：先收起，再把答案发给发起方（顺序反了的话
    //: 处理器里读 `promptVisible` 会看到 true，重入一次显示）。
    function _onPromptAnswered(value) {
        host.promptVisible = false
        host.promptAnswered(value === true)
    }

    // ─── 与桥接线 ───────────────────────────────────────────

    Connections {
        target: (typeof Dialogs !== "undefined" && Dialogs) ? Dialogs : null

        function onDialogRequested(request) {
            host.enqueue(request)
        }

        function onDialogClosed(dialogId) {
            host.dequeue(dialogId)
        }

        function onProgressChanged(data) {
            host.progressData = data
        }

        function onProgressClosed() {
            host.progressData = null
        }
    }

    Component.onCompleted: {
        // 就绪握手：桥据此把 `is_available()` 变 True（端口才会把请求交给 QML）
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.markReady()
    }

    Component.onDestruction: {
        // 退出清理：放行还在等待的调用方、清空 Toast 队列与进度会话
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.markClosing()
    }

    // ─── 遮罩（挡住下层输入，视觉上把焦点交给对话框） ───────

    Rectangle {
        id: scrim
        objectName: "dialogScrim"
        anchors.fill: parent
        // 遮罩走派生令牌 `Theme.scrim`（黑 55% 透明）——返工 A 组加的它就是为了
        // "同一个语义只有一处取值"：以前是 `bgDark` + 本地 `opacity: 0.55` 两个来源。
        color: Theme?.scrim ?? "transparent"
        visible: host.head !== null || host.progressData !== null || host.promptVisible
        z: 1

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            // 故意不写 onClicked：模态遮罩的职责只是"点不到底下的界面"，
            // MouseArea 本身就会吃掉这些事件（不 propagate）。
        }
    }

    // ─── 进度面板（独立于对话框队列：它有取消按钮、没有超时、同一个窗口从 0% 到 100%） ───

    Loader {
        id: progressLoader
        objectName: "progressLoader"
        anchors.fill: parent
        z: 2
        active: host.progressData !== null
        sourceComponent: progressComponent

        onLoaded: {
            item.report = host.progressData ? host.progressData : ({})
        }
    }

    // ─── 对话框（只显示队首那一个） ─────────────────────────

    Loader {
        id: dialogLoader
        objectName: "dialogLoader"
        anchors.fill: parent
        z: 3
        active: host.head !== null
        sourceComponent: host.head ? host.componentFor(host.head.kind) : null

        onLoaded: {
            if (host.head !== null) {
                item.request = host.head
                item.forceActiveFocus()
            }
        }

        onStatusChanged: {
            // 组件编译/加载失败同样必须有交代（不能把调用方挂在那儿）
            if (dialogLoader.status === Loader.Error && host.head !== null)
                host.failRequest(host.head, "component load error")
        }
    }

    // ─── 壳层提示（与上面的队列**互不干扰**，所以另开一个 Loader） ───
    //
    // z 比队列高一位：守卫提示是"当前这一步必须回答"的问题，
    // 万一此刻正好有个桥的对话框在显示，它应该压在上面而不是被压住。

    Loader {
        id: promptLoader
        objectName: "promptLoader"
        anchors.fill: parent
        z: 4
        active: host.promptVisible
        sourceComponent: confirmComponent

        onLoaded: {
            item.objectName = "promptConfirmDialog"
            item.request = host.prompt
            item.answered.connect(host._onPromptAnswered)
            item.forceActiveFocus()
        }
    }

    // 进度数据变化时把新快照推给已经建好的面板
    onProgressDataChanged: {
        if (progressLoader.item !== null && progressData !== null)
            progressLoader.item.report = progressData
    }

    // ─── 组件表（kind -> 组件） ─────────────────────────────

    Component {
        id: messageComponent
        MessageDialog {}
    }

    Component {
        id: confirmComponent
        ConfirmDialog {}
    }

    Component {
        id: textInputComponent
        TextInputDialog {}
    }

    Component {
        id: choiceComponent
        ChoiceDialog {}
    }

    Component {
        id: progressComponent
        ProgressDialog {}
    }
}
