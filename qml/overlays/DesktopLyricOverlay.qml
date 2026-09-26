// 桌面歌词悬浮窗 —— 阶段 2 任务 2.15。
//
// 对齐旧实现（ui/music_desktop_lyric.py）：无边框 / 置顶 / 整窗 0.85 半透明 / 可拖拽 /
// 锁定后禁止拖拽 / 默认屏幕下方居中；差别只有一处**有意偏差**：旧实现用 emoji
// 表示锁定态，本项目禁止 emoji（D-07），改为 `(Runtime?.iconUrl("lock"/"unlock") ?? "")` 的图标
// （`qml/assets/icons/emoji-map.json` 就是这么规定的：那颗 emoji 的家是 lock.svg）。
//
// 窗口语义与 MonitorOverlay 完全一致（原生 Window + flags、实色 + opacity、增量拖拽、
// 显示前补 WS_EX_NOACTIVATE、坐标一律逻辑像素）—— 为什么这么做见 MonitorOverlay.qml 文件头。
import QtQuick
import QtQuick.Window

Window {
    id: lyricWin

    // 与 OverlayBridge.OBJECT_NAMES 里的约定一致
    objectName: "lyricOverlay"

    readonly property bool bridgeReady: typeof Overlay !== "undefined" && Overlay !== null
    readonly property bool i18nReady: typeof Tr !== "undefined" && Tr !== null
    readonly property bool iconReady: typeof Runtime !== "undefined" && Runtime !== null
    readonly property var overlayProps: bridgeReady ? Overlay.lyricProps : ({})
    // 锁定态：桥里存的锁定规则来自 services.desktop_lyric.toggle_lock
    readonly property bool locked: bridgeReady ? Overlay.lyricLocked : false

    // 歌词两行。**桥不解析歌词**（红线 2）：文本由阶段 3 的歌词服务推过来，
    // 这里先给占位；旧 Tk 实现的 prev/next 初始也是空串，行为一致。
    property string currentLine: ""
    property string nextLine: ""

    // 两段式可见性：onCompleted 里打完 Win32 补丁才允许显示
    property bool hostReady: false

    title: lyricWin.i18nReady ? Tr?.map["music_desktop_lyric"] : ""

    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
           | (lyricWin.overlayProps.topMost === false ? 0 : Qt.WindowStaysOnTopHint)
    width: lyricWin.overlayProps.width !== undefined ? lyricWin.overlayProps.width : 600
    height: lyricWin.overlayProps.height !== undefined ? lyricWin.overlayProps.height : 140
    opacity: lyricWin.overlayProps.opacity !== undefined ? lyricWin.overlayProps.opacity : 0.85
    color: Theme?.bgDark ?? "transparent"
    visible: lyricWin.hostReady && lyricWin.bridgeReady && Overlay.lyricVisible

    function opacityPercent() {
        return Math.round(lyricWin.opacity * 100)
    }

    Component.onCompleted: {
        if (!lyricWin.bridgeReady) {
            return
        }
        Overlay.setupWindow("lyric", lyricWin)
        var geo = Overlay.geometryFor("lyric")
        lyricWin.x = geo.x
        lyricWin.y = geo.y
        Overlay.applyNoActivate("lyric")
        lyricWin.hostReady = true
    }

    onVisibleChanged: {
        if (visible && lyricWin.bridgeReady) {
            Overlay.applyNoActivate("lyric")
        }
    }

    onXChanged: {
        if (lyricWin.hostReady && lyricWin.bridgeReady) {
            Overlay.reportPosition("lyric", x, y)
        }
    }
    onYChanged: {
        if (lyricWin.hostReady && lyricWin.bridgeReady) {
            Overlay.reportPosition("lyric", x, y)
        }
    }

    Connections {
        target: lyricWin.bridgeReady ? Overlay : null

        function onOverlayMoved(kind, x, y) {
            if (kind !== "lyric") {
                return
            }
            if (lyricWin.x !== x) {
                lyricWin.x = x
            }
            if (lyricWin.y !== y) {
                lyricWin.y = y
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Theme?.bgDark ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        // ── 整窗拖拽区（z 序最低）：锁定时整体失效 ──
        MouseArea {
            id: dragArea
            anchors.fill: parent
            enabled: !lyricWin.locked
            acceptedButtons: Qt.LeftButton

            property real lastX: 0
            property real lastY: 0
            property bool moved: false

            onPressed: (mouse) => {
                lastX = mouse.x
                lastY = mouse.y
                moved = false
            }

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
                lyricWin.x = lyricWin.x + dx
                lyricWin.y = lyricWin.y + dy
                moved = true
            }

            onReleased: {
                if (moved && lyricWin.bridgeReady) {
                    Overlay.savePosition()
                }
                moved = false
            }
            onCanceled: moved = false
        }

        Column {
            id: content
            anchors.fill: parent
            anchors.margins: 4
            spacing: 2

            // ── 控制栏：自带 MouseArea 的控件，z 序高于拖拽区，优先拿到点击 ──
            Rectangle {
                id: controlBar
                width: parent.width
                height: 22
                color: Theme?.bgMedium ?? "transparent"

                Row {
                    anchors.left: parent.left
                    anchors.leftMargin: 2
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4

                    // 锁定开关：图标来自 emoji-map 指定的 lock.svg / unlock.svg
                    Rectangle {
                        id: lockButton
                        objectName: "lyricLockButton"
                        width: 22
                        height: 18
                        color: lyricWin.locked ? Theme?.accent ?? "transparent" : Theme?.bgLight ?? "transparent"

                        Image {
                            id: lockIcon
                            objectName: "lyricLockIcon"
                            anchors.centerIn: parent
                            width: 12
                            height: 12
                            fillMode: Image.PreserveAspectFit
                            source: lyricWin.iconReady
                                    ? (Runtime?.iconUrl(lyricWin.locked ? "lock" : "unlock") ?? "") : ""
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                if (lyricWin.bridgeReady) {
                                    Overlay.toggleLyricLock()
                                }
                            }
                        }
                    }

                    // 透明度 +/-：边界与步长都在 services.desktop_lyric 里，界面不重算
                    Rectangle {
                        id: opacityDown
                        objectName: "lyricOpacityDown"
                        width: 22
                        height: 18
                        color: Theme?.bgLight ?? "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "-"
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                if (lyricWin.bridgeReady) {
                                    Overlay.decreaseOpacity("lyric")
                                }
                            }
                        }
                    }
                    Rectangle {
                        id: opacityUp
                        objectName: "lyricOpacityUp"
                        width: 22
                        height: 18
                        color: Theme?.bgLight ?? "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "+"
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                if (lyricWin.bridgeReady) {
                                    Overlay.increaseOpacity("lyric")
                                }
                            }
                        }
                    }

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: (lyricWin.i18nReady ? Tr?.map["music_desktop_lyric"] : "")
                              + "  " + lyricWin.opacityPercent() + " %"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: (Theme?.fontSizeSmall ?? 10) - 1
                    }
                }

                Rectangle {
                    id: closeButton
                    objectName: "lyricCloseButton"
                    anchors.right: parent.right
                    anchors.rightMargin: 2
                    anchors.verticalCenter: parent.verticalCenter
                    width: 22
                    height: 18
                    color: Theme?.bgLight ?? "transparent"

                    Image {
                        id: closeIcon
                        objectName: "lyricCloseIcon"
                        anchors.centerIn: parent
                        width: 12
                        height: 12
                        fillMode: Image.PreserveAspectFit
                        source: lyricWin.iconReady ? (Runtime?.iconUrl("close") ?? "") : ""
                    }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            if (lyricWin.bridgeReady) {
                                Overlay.hideLyric()
                            }
                        }
                    }
                }
            }

            // ── 歌词第一行：当前行（没有数据时显示 i18n 占位）──
            Text {
                id: currentLineText
                objectName: "lyricCurrentLine"
                width: parent.width
                height: 46
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                text: lyricWin.currentLine !== ""
                      ? lyricWin.currentLine
                      : (lyricWin.i18nReady ? Tr?.map["music_lyric_loading"] : "")
                color: Theme?.accent ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
                elide: Text.ElideRight
            }

            // ── 歌词第二行：下一行（占位；等歌词服务推送）──
            Text {
                id: nextLineText
                objectName: "lyricNextLine"
                width: parent.width
                height: 32
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                text: lyricWin.nextLine
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
            }
        }
    }
}
