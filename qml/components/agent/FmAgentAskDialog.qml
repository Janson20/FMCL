// FmAgentAskDialog.qml —— ask_user 的多问题选择浮层（阶段 3 任务 3.22）。
//
// 工具循环在 worker 里等这个答案；关闭/拒绝都必须发 answered(false, [])。
// 支持 1~4 个问题、单选/多选、自定义输入、上一题/下一题；选项数据完全来自
// 工具返回的 questions（label/description 是 AI 生成的普通字符串）。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "fmAgentAskDialog"

    property var request: ({})
    property int currentIndex: 0
    property var answers: []
    property var selections: []
    property string errorText: ""

    signal answered(bool accepted, var answers)

    readonly property var questions: request && request.questions ? request.questions : []
    readonly property var question: (currentIndex >= 0 && currentIndex < questions.length)
                                    ? questions[currentIndex] : ({})
    readonly property bool multi: !!(question && question.multiSelect)
    readonly property bool allowCustom: !question || question.custom === undefined ? true : !!question.custom
    readonly property bool lastQuestion: currentIndex >= questions.length - 1

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function selectionAt(index) {
        var value = selections[index]
        return value instanceof Array ? value : []
    }

    function isSelected(label) {
        return selectionAt(currentIndex).indexOf(String(label)) >= 0
    }

    function setSelected(label, checked) {
        var next = selections.slice()
        var current = selectionAt(currentIndex).slice()
        var value = String(label)
        var position = current.indexOf(value)
        if (checked && position < 0)
            current.push(value)
        if (!checked && position >= 0)
            current.splice(position, 1)
        next[currentIndex] = current
        selections = next
    }

    function customField() {
        return customInput.text
    }

    function reset() {
        currentIndex = 0
        answers = []
        selections = []
        errorText = ""
        customInput.text = ""
    }

    function loadQuestion() {
        errorText = ""
        var saved = answers[currentIndex]
        customInput.text = saved !== undefined && !multi ? String(saved) : ""
        selections = selections.slice()
    }

    function collect() {
        var custom = allowCustom ? String(customInput.text || "").trim() : ""
        var picked = selectionAt(currentIndex)
        if (custom.length > 0)
            return custom
        if (picked.length > 0)
            return picked.join(", ")
        return ""
    }

    function saveCurrent(requireAnswer) {
        var value = collect()
        if (requireAnswer && value.length === 0) {
            errorText = t("agent_ask_empty")
            return false
        }
        var next = answers.slice()
        next[currentIndex] = value
        answers = next
        errorText = ""
        return true
    }

    function goPrevious() {
        if (currentIndex <= 0)
            return
        if (!saveCurrent(false))
            return
        currentIndex -= 1
        loadQuestion()
    }

    function goNext() {
        if (!saveCurrent(true))
            return
        if (!lastQuestion) {
            currentIndex += 1
            loadQuestion()
            return
        }
        submit()
    }

    function submit() {
        if (!saveCurrent(true))
            return
        var cleaned = []
        for (var i = 0; i < questions.length; i++) {
            var value = answers[i]
            cleaned.push(value === undefined ? "" : String(value))
        }
        dialog.answered(true, cleaned)
    }

    function cancel() {
        dialog.answered(false, [])
    }

    onRequestChanged: reset()
    Component.onCompleted: reset()

    focus: visible
    Keys.onEscapePressed: dialog.cancel()

    Rectangle {
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
    }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(640, Math.max(340, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: Math.min(600, layout.implicitHeight + 2 * (Theme?.spacingLg ?? 16))
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        ColumnLayout {
            id: layout
            anchors.fill: parent
            anchors.margins: Theme?.spacingLg ?? 16
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Text {
                    Layout.fillWidth: true
                    text: dialog.t("agent_ask_title")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    elide: Text.ElideRight
                }

                Text {
                    visible: dialog.questions.length > 1
                    text: String(dialog.currentIndex + 1) + "/" + String(dialog.questions.length)
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }

                FmToolButton {
                    objectName: "agentAskClose"
                    iconName: "close"
                    side: 28
                    onClicked: dialog.cancel()
                }
            }

            Text {
                Layout.fillWidth: true
                visible: text.length > 0
                text: dialog.question && dialog.question.header ? String(dialog.question.header) : ""
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                font.bold: true
            }

            Text {
                Layout.fillWidth: true
                text: dialog.question && dialog.question.question ? String(dialog.question.question) : ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                visible: dialog.multi
                text: dialog.t("agent_multi_select_hint")
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            Flickable {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(300, Math.max(80, optionsColumn.implicitHeight))
                clip: true
                contentWidth: width
                contentHeight: optionsColumn.implicitHeight

                ScrollBar.vertical: FmScrollBar {
                    thickness: 5
                }

                Column {
                    id: optionsColumn
                    width: parent.width
                    spacing: Theme?.spacingXs ?? 4

                    Repeater {
                        model: dialog.question && dialog.question.options ? dialog.question.options : []

                        Column {
                            required property var modelData

                            width: optionsColumn.width
                            spacing: 0

                            FmCheckBox {
                                visible: dialog.multi
                                width: parent.width
                                text: String(modelData && modelData.label || "")
                                checked: dialog.isSelected(String(modelData && modelData.label || ""))
                                onToggled: dialog.setSelected(String(modelData && modelData.label || ""), checked)
                            }

                            FmRadio {
                                visible: !dialog.multi
                                width: parent.width
                                autoExclusive: false
                                text: String(modelData && modelData.label || "")
                                checked: dialog.isSelected(String(modelData && modelData.label || ""))
                                onClicked: dialog.setSelected(String(modelData && modelData.label || ""), true)
                            }

                            Text {
                                width: parent.width - 24
                                x: 24
                                visible: text.length > 0
                                text: String(modelData && modelData.description || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            FmTextField {
                id: customInput
                objectName: "agentAskCustom"
                Layout.fillWidth: true
                visible: dialog.allowCustom
                placeholder: dialog.t("agent_ask_custom")
            }

            Text {
                Layout.fillWidth: true
                visible: dialog.errorText.length > 0
                text: dialog.errorText
                color: Theme?.error ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "agentAskPrev"
                    primary: false
                    text: dialog.t("agent_ask_prev")
                    enabled: dialog.currentIndex > 0
                    onClicked: dialog.goPrevious()
                }

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "agentAskNext"
                    visible: !dialog.lastQuestion
                    primary: true
                    text: dialog.t("agent_ask_next")
                    onClicked: dialog.goNext()
                }

                FmButton {
                    objectName: "agentAskSubmit"
                    visible: dialog.lastQuestion
                    primary: true
                    text: dialog.t("agent_ask_submit")
                    onClicked: dialog.submit()
                }
            }
        }
    }
}
