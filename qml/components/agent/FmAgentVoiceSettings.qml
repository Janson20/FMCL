// FmAgentVoiceSettings.qml —— 语音模型状态 / 下载 / 导入（阶段 3.23）。
import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import ".."

Item {
    id: pane
    objectName: "agentVoiceSettings"

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    readonly property bool live: (typeof Agent !== "undefined" && Agent) ? true : false

    function refresh() {
        if (!live)
            return
        statusLabel.text = Agent.voiceModelReady
                ? t("settings_voice_status_ready").replace("{ver}", String(Agent.voiceModelVersion || ""))
                : t("settings_voice_status_missing")
        dirLabel.text = String(Agent.voiceModelDir || "")
        progress.visible = Agent.voiceBusy
        progress.value = Number(Agent.voiceProgress || 0) / 100
        downloadButton.enabled = !Agent.voiceBusy && !Agent.voiceModelReady
        importButton.enabled = !Agent.voiceBusy
    }

    Component.onCompleted: refresh()
    onVisibleChanged: if (visible) refresh()

    Connections {
        target: live ? Agent : null
        function onChanged() { pane.refresh() }
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingSm ?? 8

            FmCard {
                Layout.fillWidth: true
                title: pane.t("settings_voice_section")
                subtitle: pane.t("settings_voice_desc")

                Text {
                    id: statusLabel
                    Layout.fillWidth: true
                    color: pane.live && Agent.voiceModelReady ? (Theme?.success ?? "transparent")
                                                          : (Theme?.textSecondary ?? "transparent")
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }

                FmProgressBar {
                    id: progress
                    Layout.fillWidth: true
                    visible: false
                    value: 0
                    label: pane.t("voice_downloading_model")
                }

                Text {
                    Layout.fillWidth: true
                    text: pane.t("settings_voice_netdisk")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }

                Text {
                    Layout.fillWidth: true
                    text: pane.t("settings_voice_netdisk_models")
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        id: downloadButton
                        objectName: "agentVoiceDownload"
                        text: pane.t("agent_settings_voice_download")
                        onClicked: if (pane.live) Agent.downloadVoiceModel()
                    }

                    FmButton {
                        id: importButton
                        objectName: "agentVoiceImport"
                        primary: false
                        text: pane.t("settings_voice_import_btn")
                        onClicked: importDialog.open()
                    }

                    FmButton {
                        primary: false
                        text: pane.t("settings_voice_open_dir")
                        onClicked: if (pane.live) Agent.openVoiceModelDir()
                    }
                }

                Text {
                    id: dirLabel
                    Layout.fillWidth: true
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideMiddle
                }
            }
        }
    }

    FileDialog {
        id: importDialog
        title: pane.t("settings_voice_import_btn")
        nameFilters: ["Model archive (*.zip)"]
        onAccepted: {
            var path = String(selectedFile)
            if (path.indexOf("file:///") === 0)
                path = decodeURIComponent(path.substring(8))
            if (pane.live && path.length > 0)
                Agent.importVoiceModel(path)
        }
    }
}
