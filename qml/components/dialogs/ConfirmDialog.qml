// ConfirmDialog.qml —— 确认框。
//
// 契约（app/bridges/dialog_host.py 顶部表）：
//   payload = {title, message, default(bool), fallback(== default)}
//
// 三态与边界（任务 2.13 要求逐条对齐旧实现 `ui/dialogs.py:show_confirmation`）：
//   * 「确定」-> true；「取消」-> **false**（不是 fallback）；
//   * Esc / 右上角关闭 / 窗口被销毁 -> 与「取消」等价 -> false
//     （旧实现的 `WM_DELETE_WINDOW` / destroy 路径 result 保持 False）；
//   * `default` 只决定**默认按钮**（初始焦点 + 回车触发哪个），不改变按钮顺序：
//     顺序恒为「确定」在左、「取消」在右，与旧实现 `pack(side=ctk.LEFT)` 一致。
//   * "答不上来"（宿主没有组件、界面销毁放行）才回填 fallback —— 那条在 DialogHost.qml
//     与 DialogBridge.cancelDialog 里，不在本组件内。
//
// 注意 `fallback` 可能等于 true（default=True 时），所以本组件**不能**用
// `Dialogs.cancelDialog()` 表达"取消" —— 那会把"用户点了取消"变成"用户点了确定"。

import QtQuick
import QtQuick.Controls
import ".."

Item {
    id: dialog
    objectName: "confirmDialog"

    property var request: ({})

    readonly property bool defaultIsConfirm: !!(request && request.default === true)

    //: 本组件自己的作答信号（阶段 3 任务 3.4 新增）。
    //: 为什么需要它：`DialogHost` 现在也支持**QML 自己发起**的确认框（设置页的
    //: "有未保存改动"提示走的就是这条路）。那类请求没有桥那边的 id，
    //: 答案只能靠信号回到发起方；有 id 的请求照旧走 `Dialogs.submitDialog(…)`。
    signal answered(bool value)

    // ─── 供 QML 内部与测试驱动 ───

    function accept() {
        submit(true)
    }

    function cancel() {
        submit(false)
    }

    function submit(value) {
        answered(value)
        // 本地（QML 发起）的确认框用**负数 id**（见 DialogHost.askConfirm），
        // 那种 id 在桥里不存在，不该往桥上问一遍（否则每次都留一条"找不到待答请求"）。
        if (typeof Dialogs !== "undefined" && Dialogs && request && request.id !== undefined
                && Number(request.id) >= 0)
            Dialogs.submitDialog(request.id, value)
    }

    //: 回车落到哪个按钮上 —— 由 `default` 决定（`focus` 由 Controls 样式自己管，
    //: 所以"默认按钮"的语义在这里显式表达一次，测试也钉在这里）
    function activateDefault() {
        if (defaultIsConfirm)
            accept()
        else
            cancel()
    }

    focus: true
    Keys.onEscapePressed: dialog.cancel()
    Keys.onReturnPressed: dialog.activateDefault()
    Keys.onEnterPressed: dialog.activateDefault()

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
                id: messageText
                objectName: "dialogMessage"
                width: parent.width
                text: (dialog.request && dialog.request.message) ? String(dialog.request.message) : ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            Item {
                width: parent.width
                height: okButton.height

                Row {
                    anchors.right: parent.right
                    spacing: Theme?.spacingSm ?? 0

                    // 顺序：确定在左、取消在右（旧实现 `pack(side=ctk.LEFT)` 的先后）
                    FmButton {
                        id: okButton
                        objectName: "okButton"
                        primary: true
                        text: Tr?.map["confirm"] ?? "confirm"
                        focus: dialog.defaultIsConfirm
                        onClicked: dialog.accept()
                    }

                    FmButton {
                        id: cancelButton
                        objectName: "cancelButton"
                        text: Tr?.map["cancel"] ?? "cancel"
                        focus: !dialog.defaultIsConfirm
                        onClicked: dialog.cancel()
                    }
                }
            }
        }
    }
}
