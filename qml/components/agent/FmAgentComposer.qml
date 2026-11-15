// FmAgentComposer.qml —— AGENT 输入区。
//
// 什么时候用它：Agent 页底部。包含多行输入、发送/停止、语音按钮、免责声明。
// 什么时候不要用：快速提问框（顶栏用 FmTextField；这里带语音与停止语义）。
//
// 快捷键：Enter 发送，Shift+Enter 换行；输入框自己把 Enter 交给 submitOnEnter
// （qml/components/FmTextArea.qml 的通用开关），不在页面里和 TextArea 抢按键。

import QtQuick
import QtQuick.Layouts
import ".."

Column {
    id: root
    objectName: "fmAgentComposer"

    property alias text: input.text
    property bool busy: false
    property bool loggedIn: true
    property string voiceState: "idle"
    property string voiceError: ""
    property int credits: 0
    property int tokenEstimate: 0

    signal sendRequested(string text)
    signal stopRequested()
    signal voiceRequested()

    spacing: Theme?.spacingXs ?? 4

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function focusInput() {
        input.forceActiveFocus()
    }

    function submit() {
        if (root.busy || !root.loggedIn)
            return
        var value = String(input.text || "").trim()
        if (value.length === 0)
            return
        root.sendRequested(value)
    }

    function voiceLabel() {
        if (voiceState === "recording")
            return t("agent_voice_listening")
        if (voiceState === "recognizing")
            return t("agent_voice_recognizing")
        if (voiceState === "loading")
            return t("voice_loading_model")
        if (voiceState === "unavailable")
            return t("agent_voice_unavailable")
        return ""
    }

    RowLayout {
        width: parent.width
        spacing: Theme?.spacingSm ?? 8

        FmTextArea {
            id: input
            objectName: "agentComposerInput"
            Layout.fillWidth: true
            lines: 2
            placeholder: root.t("agent_input_placeholder")
            submitOnEnter: true
            onSubmitted: root.submit()
        }

        ColumnLayout {
            Layout.alignment: Qt.AlignBottom
            spacing: 2

            RowLayout {
                spacing: Theme?.spacingXs ?? 4

                FmToolButton {
                    objectName: "agentVoiceButton"
                    iconName: root.voiceState === "recording" ? "stop" : "microphone"
                    iconColor: root.voiceState === "recording"
                               ? (Theme?.error ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                    selected: root.voiceState === "recording"
                    side: 34
                    onClicked: root.voiceRequested()
                }

                FmButton {
                    objectName: "agentSendButton"
                    primary: true
                    visible: !root.busy
                    iconName: "arrow-right"
                    text: root.t("agent_send")
                    enabled: root.loggedIn && String(input.text || "").trim().length > 0
                    onClicked: root.submit()
                }

                FmButton {
                    objectName: "agentStopButton"
                    primary: false
                    danger: true
                    visible: root.busy
                    iconName: "stop"
                    text: root.t("agent_stop")
                    onClicked: root.stopRequested()
                }
            }

            Text {
                Layout.alignment: Qt.AlignRight
                visible: root.voiceLabel().length > 0
                text: root.voiceLabel()
                color: root.voiceState === "unavailable"
                       ? (Theme?.warning ?? "transparent")
                       : (Theme?.textSecondary ?? "transparent")
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }
        }
    }

    RowLayout {
        width: parent.width
        spacing: Theme?.spacingSm ?? 8

        Text {
            Layout.fillWidth: true
            text: root.t("agent_disclaimer")
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            wrapMode: Text.WordWrap
        }

        Text {
            objectName: "agentTokenLabel"
            visible: root.tokenEstimate > 0
            text: root.t("agent_token_estimate").replace("{tokens}", String(root.tokenEstimate))
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        Text {
            objectName: "agentCreditsLabel"
            visible: root.credits > 0
            text: root.t("agent_ai_credits").replace("{credits}", String(root.credits))
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }
    }
}
