// ChoiceDialog.qml —— 多选项选择。
//
// 契约（app/bridges/dialog_host.py 顶部表）：
//   payload = {title, prompt, options(list[Choice]), default, hint, fallback(None)}
//   `options` 里的每项是 {value, label, description, disabled, data}（桥已经把 Choice 转好）。
//
// 三态与边界（对齐旧实现 `ui/ports_tk.py:_do_choose`）：
//   * 点某个选项**立即作答**（旧实现就是每个选项一个按钮 `command=_finish(option.value)`，
//     没有单独的「确定」）；
//   * `default` 对应的选项按主按钮渲染；没有 `default` 时第一个是主按钮（旧实现
//     `primary = values.index(default) if default in values else 0`）；
//   * `disabled=True` 的选项不可点（旧实现 `state="disabled"`）；
//   * Esc / 右上角关闭 -> 兜底值（choose 的 fallback 是 None）。
//
// 与旧实现的**唯一差异**：选项从"一行按钮（500x170/200 的窗口）"改成会自动换行的
// `Flow`。旧实现选项一多就会横向溢出窗口（阶段 0 记录过同类几何问题），这里换行只是
// 为了不越界，选项内容与作答语义完全一致。

import QtQuick
import QtQuick.Controls

Item {
    id: dialog
    objectName: "choiceDialog"

    property var request: ({})

    readonly property var options: (request && request.options) ? request.options : []

    //: `default` 命中的下标；没有 default（或不在列表里）时是 0 —— 与 Tk 版一致
    readonly property int primaryIndex: dialog.primaryIndexFor()

    function primaryIndexFor() {
        var list = dialog.options
        if (!request || request.default === undefined || request.default === null)
            return 0
        for (var i = 0; i < list.length; i++) {
            if (String(list[i].value) === String(request.default))
                return i
        }
        return 0
    }

    // ─── 供 QML 内部与测试驱动 ───

    function select(value) {
        submit(value)
    }

    function selectDefault() {
        var list = dialog.options
        if (list.length > 0)
            submit(list[dialog.primaryIndex].value)
    }

    function cancel() {
        if (typeof Dialogs !== "undefined" && Dialogs && request && request.id !== undefined)
            Dialogs.cancelDialog(request.id)
    }

    function submit(value) {
        if (typeof Dialogs !== "undefined" && Dialogs && request && request.id !== undefined)
            Dialogs.submitDialog(request.id, value)
    }

    focus: true
    Keys.onEscapePressed: dialog.cancel()
    Keys.onReturnPressed: dialog.selectDefault()
    Keys.onEnterPressed: dialog.selectDefault()

    Rectangle {
        id: card
        objectName: "dialogCard"
        anchors.centerIn: parent
        width: Math.min(520, Math.max(280, parent.width - 2 * Theme?.spacingXl ?? 0))
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
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignLeft
            }

            Text {
                id: hintText
                objectName: "dialogHint"
                width: parent.width
                text: (dialog.request && dialog.request.hint) ? String(dialog.request.hint) : ""
                visible: text.length > 0
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            Flow {
                id: optionFlow
                objectName: "optionFlow"
                width: parent.width
                spacing: Theme?.spacingSm ?? 0

                Repeater {
                    model: dialog.options

                    DialogButton {
                        objectName: "optionButton"
                        required property int index
                        required property var modelData

                        text: (modelData && modelData.label) ? String(modelData.label) : ""
                        primary: index === dialog.primaryIndex
                        enabled: !(modelData && modelData.disabled)
                        focus: index === dialog.primaryIndex

                        ToolTip.visible: hovered && !!(modelData && modelData.description)
                        ToolTip.text: (modelData && modelData.description) ? String(modelData.description) : ""

                        onClicked: dialog.select(modelData ? modelData.value : null)
                    }
                }
            }
        }
    }
}
