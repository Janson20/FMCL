// 性能监控悬浮窗 —— 阶段 2 任务 2.15。
//
// 窗口语义全部照阶段 0 的实测配方（docs/refactor/08-phase0-execution-log.md 第 11/12 节）：
//   1. 原生 Window + flags，**不用 FluWindow**（它不设 Frameless/StaysOnTop、会加 30px
//      appBar，且只在 Windows 生效）；
//   2. 实色背景 + `Window.opacity`，**不用** `color: "transparent"`（无合成器的 X11
//      会黑屏、鼠标穿透语义也不同；闸门 R6 同样会拦）；
//   3. 拖拽用增量累加（决策 4），不要"按下记绝对值再算差值"；
//   4. 拖拽区声明在背景矩形最前面（z 序最低），自带 MouseArea 的控件声明在后面；
//   5. `x`/`y` 是**逻辑像素**，位置回报与持久化都用这一套（Win32 的物理像素不落盘）；
//   6. 显示之前必须先 `Overlay.applyNoActivate("monitor")` 补 Win32 WS_EX_NOACTIVATE：
//      所以这里用 hostReady 两段式可见性 —— `visible` 在 Component.onCompleted 跑完、
//      补丁打完之前恒为 false，杜绝"先抢焦点再被改回去"的闪烁。
import QtQuick
import QtQuick.Window

Window {
    id: monitorWin

    // 与 OverlayBridge.OBJECT_NAMES 里的约定一致（桥找不到登记表时靠它兜底）
    objectName: "monitorOverlay"

    readonly property bool bridgeReady: typeof Overlay !== "undefined" && Overlay !== null
    readonly property bool i18nReady: typeof Tr !== "undefined" && Tr !== null
    readonly property bool iconReady: typeof Runtime !== "undefined" && Runtime !== null
    // `Overlay.monitorProps` 是带 NOTIFY 的属性，读它即建立依赖；configure() 一改就重算
    readonly property var overlayProps: bridgeReady ? Overlay.monitorProps : ({})
    readonly property var metrics: bridgeReady ? Overlay.monitorMetrics : ({})
    // 采集不到（或本来就没有来源，如 FPS）时的占位文案走 i18n，不写死中文
    readonly property string unknownText: i18nReady ? Tr?.map["unknown"] : ""

    // 两段式可见性：onCompleted 里打完补丁才允许显示（见文件头第 6 条）
    property bool hostReady: false

    title: monitorWin.i18nReady ? Tr?.map["monitor_title"] : ""

    // 无边框 + 置顶 + 不接受焦点（后者在 Windows 上还要靠 applyNoActivate 兜底）
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
           | (monitorWin.overlayProps.topMost === false ? 0 : Qt.WindowStaysOnTopHint)
    width: monitorWin.overlayProps.width !== undefined ? monitorWin.overlayProps.width : 400
    height: monitorWin.overlayProps.height !== undefined ? monitorWin.overlayProps.height : 200
    opacity: monitorWin.overlayProps.opacity !== undefined ? monitorWin.overlayProps.opacity : 0.80
    color: Theme?.bgDark ?? "transparent"
    visible: monitorWin.hostReady && monitorWin.bridgeReady && Overlay.monitorVisible

    // ── 指标分片（供进度条宽度用；没有数据时归零，不画假的百分比） ──
    readonly property real cpuFraction: metrics.cpu_percent !== undefined
                                        ? Math.max(0, Math.min(1, metrics.cpu_percent / 100)) : 0
    readonly property real memFraction: metrics.mem_percent !== undefined
                                        ? Math.max(0, Math.min(1, metrics.mem_percent / 100)) : 0

    Component.onCompleted: {
        if (!monitorWin.bridgeReady) {
            return
        }
        // 顺序有意义：先登记窗口（桥才拿得到 QWindow），再取几何，最后补 Win32 样式
        Overlay.setupWindow("monitor", monitorWin)
        var geo = Overlay.geometryFor("monitor")
        monitorWin.x = geo.x
        monitorWin.y = geo.y
        // 必须在这一步之后才可能显示（hostReady 在最后一行才置真）
        Overlay.applyNoActivate("monitor")
        monitorWin.hostReady = true
    }

    // 每次重新显示都补一次（幂等：已置位时只是读回校验）—— 覆盖"建窗时就已经可见"的边角
    onVisibleChanged: {
        if (visible && monitorWin.bridgeReady) {
            Overlay.applyNoActivate("monitor")
        }
    }

    // 位置回报：拖动过程中每帧都报（桥只更新内存 + 发信号），**不写盘**
    onXChanged: {
        if (monitorWin.hostReady && monitorWin.bridgeReady) {
            Overlay.reportPosition("monitor", x, y)
        }
    }
    onYChanged: {
        if (monitorWin.hostReady && monitorWin.bridgeReady) {
            Overlay.reportPosition("monitor", x, y)
        }
    }

    // 桥要求窗口移动时（restorePosition / configure 的 x,y）才动窗口；
    // 等值判断是必须的：否则"赋值 -> 回报 -> 再赋值"会成环
    Connections {
        target: monitorWin.bridgeReady ? Overlay : null

        function onOverlayMoved(kind, x, y) {
            if (kind !== "monitor") {
                return
            }
            if (monitorWin.x !== x) {
                monitorWin.x = x
            }
            if (monitorWin.y !== y) {
                monitorWin.y = y
            }
        }
    }

    // 纯色底：项目 UI 规范禁止渐变（闸门 R1），也禁止 transparent（闸门 R6）
    Rectangle {
        anchors.fill: parent
        color: Theme?.bgDark ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        // ── 整窗拖拽区：声明在背景矩形的最前面（z 序最低）──
        // 普通 Item 不吃鼠标按下事件，所以事件会落到这里；后面的控件 z 序更高，
        // 自带 MouseArea 的按钮仍然优先拿到点击。
        MouseArea {
            id: dragArea
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton

            property real lastX: 0
            property real lastY: 0
            property bool moved: false

            onPressed: (mouse) => {
                lastX = mouse.x
                lastY = mouse.y
                moved = false
            }

            // 增量累加（阶段 0 决策 4）：窗口移动后 Qt 会用新原点重算坐标，天然收敛
            onPositionChanged: (mouse) => {
                if (!pressed) {
                    return
                }
                const dx = mouse.x - lastX
                const dy = mouse.y - lastY
                lastX = mouse.x
                lastY = mouse.y
                if (dx === 0 && dy === 0) {
                    return
                }
                monitorWin.x = monitorWin.x + dx
                monitorWin.y = monitorWin.y + dy
                moved = true
            }

            // **只在拖拽真的发生过时**落盘：拖动过程中一次都不写（避免配置读写放大）
            onReleased: {
                if (moved && monitorWin.bridgeReady) {
                    Overlay.savePosition()
                }
                moved = false
            }
            onCanceled: moved = false
        }

        Column {
            id: content
            anchors.fill: parent
            anchors.margins: 1
            spacing: 0

            // ── 标题条：图标 + 热键提示（旧实现的标题里那颗 emoji 按 D-07 换成图标）──
            Rectangle {
                width: parent.width
                height: 24
                color: Theme?.bgMedium ?? "transparent"

                Image {
                    id: titleIcon
                    objectName: "monitorTitleIcon"
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    width: 14
                    height: 14
                    fillMode: Image.PreserveAspectFit
                    source: monitorWin.iconReady ? (Runtime?.iconUrl("monitor") ?? "") : ""
                }
                Text {
                    anchors.left: titleIcon.right
                    anchors.leftMargin: 6
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: monitorWin.i18nReady ? Tr?.map["monitor_hint"] : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: (Theme?.fontSizeSmall ?? 10) - 1
                    elide: Text.ElideRight
                }
            }

            // ── CPU ──
            Item {
                width: parent.width
                height: 42
                Text {
                    x: 10
                    y: 5
                    text: monitorWin.i18nReady ? Tr?.map["monitor_cpu"] : ""
                    color: Theme?.accent ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    font.bold: true
                }
                Text {
                    objectName: "monitorCpuValue"
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    y: 5
                    text: monitorWin.metrics.cpu_percent !== undefined
                          ? Math.round(monitorWin.metrics.cpu_percent) + " %  "
                            + (monitorWin.metrics.cpu_freq || "")
                          : monitorWin.unknownText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
                Rectangle {
                    x: 10
                    y: 25
                    width: parent.width - 20
                    height: 6
                    color: Theme?.bgLight ?? "transparent"
                    Rectangle {
                        width: parent.width * monitorWin.cpuFraction
                        height: parent.height
                        color: Theme?.accent ?? "transparent"
                    }
                }
            }

            // ── 内存 ──
            Item {
                width: parent.width
                height: 42
                Text {
                    x: 10
                    y: 5
                    text: monitorWin.i18nReady ? Tr?.map["monitor_memory"] : ""
                    color: Theme?.warning ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    font.bold: true
                }
                Text {
                    objectName: "monitorMemValue"
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    y: 5
                    text: monitorWin.metrics.mem_percent !== undefined
                          ? Math.round(monitorWin.metrics.mem_percent) + " %  "
                            + (monitorWin.metrics.mem_used || "") + " / "
                            + (monitorWin.metrics.mem_total || "")
                          : monitorWin.unknownText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
                Rectangle {
                    x: 10
                    y: 25
                    width: parent.width - 20
                    height: 6
                    color: Theme?.bgLight ?? "transparent"
                    Rectangle {
                        width: parent.width * monitorWin.memFraction
                        height: parent.height
                        color: Theme?.warning ?? "transparent"
                    }
                }
            }

            // ── GPU（采样值是服务格式化好的字符串，界面不重算）──
            Item {
                width: parent.width
                height: 42
                Text {
                    x: 10
                    y: 5
                    text: monitorWin.i18nReady ? Tr?.map["monitor_gpu"] : ""
                    color: Theme?.success ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    font.bold: true
                }
                Text {
                    objectName: "monitorGpuValue"
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    y: 5
                    text: (monitorWin.metrics.gpu_util !== undefined && monitorWin.metrics.gpu_util !== "")
                          ? monitorWin.metrics.gpu_util + "  " + (monitorWin.metrics.gpu_temp || "")
                          : monitorWin.unknownText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
                Text {
                    x: 10
                    y: 24
                    width: parent.width - 20
                    text: (monitorWin.metrics.gpu_mem !== undefined && monitorWin.metrics.gpu_mem !== "")
                          ? monitorWin.metrics.gpu_mem
                          : (monitorWin.i18nReady ? Tr?.map["monitor_gpu_na"] : "")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: (Theme?.fontSizeSmall ?? 10) - 1
                    elide: Text.ElideRight
                }
            }

            // ── FPS ──
            // 现状：`services/monitor_service` **没有**帧率来源（旧 Tk 监控窗也没有这一行）。
            // 这里保留第四行占位（占位文案走 i18n 的 unknown），接入真正的帧率采集
            // 属于阶段 3 的服务层工作 —— 桥里不做指标计算。
            Item {
                width: parent.width
                height: 42
                Text {
                    x: 10
                    y: 5
                    text: "FPS"
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    font.bold: true
                }
                Text {
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    y: 5
                    text: monitorWin.unknownText
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
                Rectangle {
                    x: 10
                    y: 25
                    width: parent.width - 20
                    height: 6
                    color: Theme?.bgLight ?? "transparent"
                }
            }
        }
    }
}
