// TextInputDialog.qml —— 文本输入框（含密码掩码）。
//
// 契约（app/bridges/dialog_host.py 顶部表）：
//   payload = {title, prompt, initial, password(bool), fallback(None)}
//
// 三态与边界（对齐旧实现 `ui/dialogs.py:show_input_dialog` +
// `ui/ports_tk.py:_ask_password`）：
//   * 预填 `initial` 并**全选**（旧实现的 `select_range(0, len(initial))`）；
//   * 回车 -> 确定，Esc -> 取消，右上角关闭 -> 取消；
//   * 确定返回输入内容：**明文**做 `strip()`（旧 `entry.get().strip()`），
//     **密码不做 strip**（空格可能是密码的一部分）；
//   * 取消 / Esc / 关闭 -> 兜底值（ask_text 的 fallback 是 None）。
//   * `password=true` 时输入框按掩码显示（旧实现在 `_ask_password` 里用 `show="*"`）。

import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: dialog
    objectName: "textInputDialog"

    property var request: ({})

    readonly property bool isPassword: !!(request && request.password)

    //: 当前输入内容（测试与诊断用；掩码只影响显示，不影响这里的真值）
    readonly property alias fieldText: field.text

    // ─── 供 QML 内部与测试驱动 ───

    function accept() {
        // 明文去首尾空白、密码原样返回 —— 与 Tk 版逐字一致
        submit(dialog.isPassword ? field.text : field.text.trim())
    }

    function cancel() {
        if (typeof Dialogs !== "undefined" && Dialogs && request && request.id !== undefined)
            Dialogs.cancelDialog(request.id)
    }

    function submit(value) {
        if (typeof Dialogs !== "undefined" && Dialogs && request && request.id !== undefined)
            Dialogs.submitDialog(request.id, value)
    }

    function syncInitial() {
        field.text = (request && request.initial) ? String(request.initial) : ""
        field.selectAll()
        if (dialog.visible)
            field.forceActiveFocus()
    }

    onRequestChanged: syncInitial()
    Component.onCompleted: syncInitial()

    focus: true
    Keys.onEscapePressed: dialog.cancel()

    Rectangle {
        id: card
        objectName: "dialogCard"
        anchors.centerIn: parent
        width: Math.min(480, Math.max(280, parent.width - 2 * Theme?.spacingXl ?? 0))
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
                        onClicked: dialog.cancel()
                    }
                }
            }

            Text {
                id: promptText
                objectName: "dialogPrompt"
                width: parent.width
                text: (dialog.request && dialog.request.prompt) ? String(dialog.request.prompt) : ""
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            // 返工 C 组：原生 TextField 换成 FmTextField（闸门 R9）。密码态用它自己的
            // `password` 属性表达（内部就是 TextInput.Password），页面上不再直接引用
            // QtQuick.Controls 的枚举；`id` 与 `objectName` 保持不变（本文件别处与
            // `tests/test_dialogs_qml.py` 都按 `inputField` / `field.text` 取值）。
            FmTextField {
                id: field
                objectName: "inputField"
                width: parent.width
                password: dialog.isPassword
                onAccepted: dialog.accept()
            }

            Item {
                width: parent.width
                height: okButton.height

                Row {
                    anchors.right: parent.right
                    spacing: Theme?.spacingSm ?? 0

                    FmButton {
                        id: okButton
                        objectName: "okButton"
                        primary: true
                        text: Tr?.map["confirm"] ?? "confirm"
                        onClicked: dialog.accept()
                    }

                    FmButton {
                        id: cancelButton
                        objectName: "cancelButton"
                        text: Tr?.map["cancel"] ?? "cancel"
                        onClicked: dialog.cancel()
                    }
                }
            }
        }
    }
}
