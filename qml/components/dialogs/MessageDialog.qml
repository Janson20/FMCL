// MessageDialog.qml —— info / warning / error 三合一（阶段的"提示框"）。
//
// 契约（app/bridges/dialog_host.py 顶部表）：
//   payload = {title, message, level, blocking, fallback}
// 提示类**不需要作答内容**：关闭按钮 / Esc / 回车一律回填 `fallback`（端口给的是 None），
// 与 `RecordingDialogHost` 在同样输入下的回填值一致（见 tests/test_dialog_bridge.py 的对照）。
//
// 旧实现对位：`ui/dialogs.py:show_alert`（400x180、居中、单个「确定」按钮、阻塞到关闭）。
// 这里不再单独开窗口，而是作为主窗口内的模态遮罩 + 卡片（见 DialogHost.qml 的说明）。

import QtQuick
import QtQuick.Controls

Item {
    id: dialog
    objectName: "messageDialog"

    //: 桥发来的请求（含 id / kind / title / message / level / fallback）
    property var request: ({})

    readonly property string level: (request && request.level) ? String(request.level) : "info"

    // ─── 供 QML 内部与测试驱动（测试用 QMetaObject.invokeMethod 调这两个函数） ───

    function accept() {
        submit(request ? request.fallback : null)
    }

    function submit(value) {
        if (typeof Dialogs !== "undefined" && Dialogs && request && request.id !== undefined)
            Dialogs.submitDialog(request.id, value)
    }

    function iconName() {
        if (level === "warning")
            return "warning"
        if (level === "error")
            return "error"
        return "info"
    }

    function accentColor() {
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        return Theme?.accent ?? "transparent"
    }

    focus: true
    Keys.onEscapePressed: dialog.accept()
    Keys.onReturnPressed: dialog.accept()
    Keys.onEnterPressed: dialog.accept()

    Rectangle {
        id: card
        objectName: "dialogCard"
        anchors.centerIn: parent
        width: Math.min(460, Math.max(280, parent.width - 2 * Theme?.spacingXl ?? 0))
        height: layout.implicitHeight + 2 * Theme?.spacingLg ?? 0
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        Column {
            id: layout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme?.spacingLg ?? 0
            spacing: Theme?.spacingMd ?? 0

            // ─── 标题行 + 关闭按钮（等价于「确定」） ───
            Item {
                width: parent.width
                height: Math.max(titleText.implicitHeight, closeButton.height)

                Text {
                    id: titleText
                    objectName: "dialogTitle"
                    anchors.left: parent.left
                    anchors.right: closeButton.left
                    anchors.rightMargin: Theme?.spacingSm ?? 0
                    anchors.verticalCenter: parent.verticalCenter
                    text: (dialog.request && dialog.request.title) ? String(dialog.request.title) : ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    elide: Text.ElideRight
                }

                Rectangle {
                    id: closeButton
                    objectName: "closeButton"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: (Theme?.iconSize ?? 0) + Theme?.spacingSm ?? 0
                    height: width
                    radius: Theme?.radiusSm ?? 0
                    color: closeArea.containsMouse ? Theme?.bgLight ?? "transparent" : Theme?.cardBg ?? "transparent"

                    Image {
                        anchors.centerIn: parent
                        source: (typeof Runtime !== "undefined" && Runtime) ? (Runtime?.iconUrl("close") ?? "") : ""
                        sourceSize.width: Math.round((Theme?.iconSize ?? 0) * 0.7)
                        sourceSize.height: Math.round((Theme?.iconSize ?? 0) * 0.7)
                        fillMode: Image.PreserveAspectFit
                    }

                    MouseArea {
                        id: closeArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: dialog.accept()
                    }
                }
            }

            // ─── 正文：图标 + 消息 ───
            Row {
                id: body
                width: parent.width
                spacing: Theme?.spacingMd ?? 0

                Rectangle {
                    width: (Theme?.iconSize ?? 0) + Theme?.spacingLg ?? 0
                    height: width
                    radius: width / 2
                    color: Theme?.bgMedium ?? "transparent"
                    border.width: 1
                    border.color: dialog.accentColor()

                    Image {
                        objectName: "levelIcon"
                        anchors.centerIn: parent
                        source: (typeof Runtime !== "undefined" && Runtime) ? (Runtime?.iconUrl(dialog.iconName()) ?? "") : ""
                        sourceSize.width: Theme?.iconSize ?? 0
                        sourceSize.height: Theme?.iconSize ?? 0
                        fillMode: Image.PreserveAspectFit
                    }
                }

                Text {
                    id: messageText
                    objectName: "dialogMessage"
                    width: body.width - ((Theme?.iconSize ?? 0) + Theme?.spacingLg ?? 0) - Theme?.spacingMd ?? 0
                    text: (dialog.request && dialog.request.message) ? String(dialog.request.message) : ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }
            }

            // ─── 底部按钮（主按钮在左，与旧 Tk 版的 pack(side=LEFT) 顺序一致） ───
            Item {
                width: parent.width
                height: okButton.height

                Row {
                    anchors.right: parent.right
                    spacing: Theme?.spacingSm ?? 0

                    DialogButton {
                        id: okButton
                        objectName: "okButton"
                        primary: true
                        text: Tr?.map["confirm"] ?? "confirm"
                        onClicked: dialog.accept()
                    }
                }
            }
        }
    }
}
