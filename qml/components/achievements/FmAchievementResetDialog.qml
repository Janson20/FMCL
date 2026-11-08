// FmAchievementResetDialog.qml —— 成就重置的两步确认框（阶段 3.21）。
//
// 什么时候用它：重置云存档 / 重置本地成就。旧实现是"按钮 → 危险确认 → 输入指定短语"
// 三步；本件负责后两步，且**输入不匹配时留在原地**，不再弹第三个错误框。
// 什么时候不要用：普通删除确认（用 ConfirmDialog）；可以由 Dialogs 队列回答的请求。
//
// 页面内浮层必须由 Loader.active 控制存在性；本件不自己可见性管理，只提供 open()/close()。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "fmAchievementResetDialog"

    property string step1Title: ""
    property string step1Message: ""
    property string step2Title: ""
    property string step2Message: ""
    property string phrase: ""
    property string continueText: ""
    property string confirmText: ""
    property string cancelText: ""
    property string mismatchText: ""
    property int step: 1
    property string errorText: ""

    signal confirmed()
    signal cancelled()

    visible: true
    focus: dialogVisible
    Keys.onEscapePressed: dialog.cancel()

    readonly property bool dialogVisible: overlay.visible

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function open() {
        dialog.step = 1
        dialog.errorText = ""
        if (phraseField)
            phraseField.text = ""
        overlay.visible = true
        Qt.callLater(dialog.forceActiveFocus)
    }

    function close() {
        overlay.visible = false
    }

    function cancel() {
        dialog.close()
        dialog.cancelled()
    }

    function advance() {
        dialog.step = 2
        dialog.errorText = ""
        if (phraseField) {
            phraseField.text = ""
            Qt.callLater(phraseField.forceActiveFocus)
        }
    }

    function submit() {
        if (dialog.step !== 2)
            return
        var typed = phraseField ? String(phraseField.text || "").trim() : ""
        if (typed !== String(dialog.phrase || "")) {
            dialog.errorText = dialog.mismatchText
            if (phraseField)
                phraseField.forceActiveFocus()
            return
        }
        dialog.close()
        dialog.confirmed()
    }

    Rectangle {
        id: overlay
        objectName: "achievementResetScrim"
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"
        visible: false
        z: 30

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
        }

        Rectangle {
            id: card
            objectName: "achievementResetCard"
            anchors.centerIn: parent
            width: Math.min(460, Math.max(300, parent.width - 2 * (Theme?.spacingXl ?? 24)))
            height: layout.implicitHeight + 2 * (Theme?.spacingLg ?? 16)
            radius: Theme?.radiusLg ?? 12
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"

            ColumnLayout {
                id: layout
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Theme?.spacingLg ?? 16
                spacing: Theme?.spacingMd ?? 12

                Text {
                    objectName: "achievementResetTitle"
                    Layout.fillWidth: true
                    text: dialog.step === 1 ? dialog.step1Title : dialog.step2Title
                    color: Theme?.error ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    wrapMode: Text.WordWrap
                }

                Text {
                    objectName: "achievementResetMessage"
                    Layout.fillWidth: true
                    text: dialog.step === 1 ? dialog.step1Message : dialog.step2Message
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }

                FmTextField {
                    id: phraseField
                    objectName: "achievementResetPhrase"
                    Layout.fillWidth: true
                    visible: dialog.step === 2
                    label: dialog.phrase
                    errorText: dialog.errorText
                    onEdited: function(value) {
                        if (dialog.errorText.length > 0)
                            dialog.errorText = ""
                    }
                    onAccepted: dialog.submit()
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Item { Layout.fillWidth: true }

                    FmButton {
                        objectName: "achievementResetContinue"
                        visible: dialog.step === 1
                        danger: true
                        text: dialog.continueText
                        onClicked: dialog.advance()
                    }

                    FmButton {
                        objectName: "achievementResetConfirm"
                        visible: dialog.step === 2
                        danger: true
                        text: dialog.confirmText
                        onClicked: dialog.submit()
                    }

                    FmButton {
                        objectName: "achievementResetCancel"
                        primary: false
                        text: dialog.cancelText
                        onClicked: dialog.cancel()
                    }
                }
            }
        }
    }
}
