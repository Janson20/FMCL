// FmAgentProviderSettings.qml —— AI 提供商 / 模型 / 思考。
import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: pane
    objectName: "agentProviderSettings"

    readonly property var providers: (typeof Agent !== "undefined" && Agent) ? Agent.providers : []
    readonly property var activeRow: {
        for (var i = 0; i < providers.length; i++) {
            if (providers[i].active)
                return providers[i]
        }
        return ({})
    }
    readonly property string activeId: String(activeRow.id || "jingdu")
    readonly property bool isCustom: activeId === "custom"
    readonly property bool needsKey: activeRow.needsKey === true

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function providerIndex() {
        for (var i = 0; i < providers.length; i++) {
            if (providers[i].active)
                return i
        }
        return 0
    }

    function modelIndex() {
        var models = activeRow.models || []
        for (var i = 0; i < models.length; i++) {
            if (String(models[i].id) === String(Agent.activeModel || ""))
                return i
        }
        return 0
    }

    function effortIndex() {
        var values = Agent.thinkingEfforts || []
        for (var i = 0; i < values.length; i++) {
            if (String(values[i]) === String(Agent.thinkingEffort || ""))
                return i
        }
        return 0
    }

    function refresh() {
        if (typeof Agent === "undefined" || !Agent)
            return
        providerCombo.model = Agent.providers
        providerCombo.currentIndex = providerIndex()
        var row = activeRow
        var models = row.models || []
        modelCombo.model = models
        modelCombo.currentIndex = modelIndex()
        keyField.text = ""
        keyField.placeholder = row.keyConfigured ? t("agent_settings_key_configured")
                                                : t("agent_settings_key_placeholder")
        urlField.text = String(row.apiUrl || "")
        customField.text = ""
        var values = []
        for (var i = 0; i < models.length; i++)
            values.push(String(models[i].id))
        customField.text = values.join("\n")
        thinkingSwitch.checked = Agent.thinkingSupported && Agent.thinkingEnabled
        effortCombo.model = Agent.thinkingEfforts
        effortCombo.currentIndex = effortIndex()
        thinkingRow.visible = Agent.thinkingSupported
        effortRow.visible = Agent.thinkingSupported && Agent.thinkingEnabled
        jingduHint.visible = activeId === "jingdu"
        statusText.text = ""
    }

    Component.onCompleted: refresh()
    onVisibleChanged: if (visible) refresh()

    Connections {
        target: (typeof Agent !== "undefined" && Agent) ? Agent : null
        function onChanged() { pane.refresh() }
    }

    Connections {
        target: (typeof Tr !== "undefined" && Tr) ? Tr : null
        function onLanguageChanged() { pane.refresh() }
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 10

            FmCard {
                Layout.fillWidth: true
                title: pane.t("agent_settings_model_title")
                subtitle: pane.t("agent_settings_model_desc")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        Layout.preferredWidth: 86
                        text: pane.t("agent_provider")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }

                    FmComboBox {
                        id: providerCombo
                        objectName: "agentProviderCombo"
                        Layout.fillWidth: true
                        textRole: "name"
                        valueRole: "id"
                        onActivated: {
                            var row = providerCombo.model[providerCombo.currentIndex] || ({})
                            if (typeof Agent !== "undefined" && Agent && row.id)
                                Agent.setProvider(String(row.id))
                            pane.refresh()
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        Layout.preferredWidth: 86
                        text: pane.t("agent_settings_model")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }

                    FmComboBox {
                        id: modelCombo
                        objectName: "agentModelCombo"
                        Layout.fillWidth: true
                        textRole: "name"
                        valueRole: "id"
                        onActivated: {
                            var row = modelCombo.model[modelCombo.currentIndex] || ({})
                            if (typeof Agent !== "undefined" && Agent && row.id)
                                Agent.setModel(Agent.activeProvider, String(row.id))
                        }
                    }
                }

                FmInfoBar {
                    id: jingduHint
                    Layout.fillWidth: true
                    level: "info"
                    text: pane.t("agent_settings_jingdu_hint")
                }

                FmTextField {
                    id: keyField
                    objectName: "agentProviderKey"
                    Layout.fillWidth: true
                    visible: pane.needsKey
                    password: true
                    label: pane.t("agent_settings_api_key")
                }

                FmTextField {
                    id: urlField
                    objectName: "agentProviderUrl"
                    Layout.fillWidth: true
                    visible: activeId !== "jingdu" || isCustom
                    label: pane.t("agent_settings_api_url")
                    placeholder: pane.t("agent_settings_api_url_placeholder")
                }

                FmTextField {
                    id: customField
                    objectName: "agentProviderCustomModels"
                    Layout.fillWidth: true
                    visible: isCustom
                    label: pane.t("agent_settings_custom_models")
                    placeholder: pane.t("agent_settings_custom_models_placeholder")
                }

                RowLayout {
                    id: thinkingRow
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmSwitch {
                        id: thinkingSwitch
                        objectName: "agentThinkingSwitch"
                        text: pane.t("agent_settings_thinking")
                        onToggled: if (typeof Agent !== "undefined" && Agent)
                                       Agent.setThinking(checked)
                    }
                }

                RowLayout {
                    id: effortRow
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        Layout.preferredWidth: 86
                        text: pane.t("agent_settings_effort")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }

                    FmComboBox {
                        id: effortCombo
                        objectName: "agentThinkingEffort"
                        Layout.fillWidth: true
                        onActivated: if (typeof Agent !== "undefined" && Agent)
                                         Agent.setThinkingEffort(String(currentText))
                    }
                }

                Text {
                    id: statusText
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "agentProviderSave"
                        text: pane.t("settings_save")
                        onClicked: {
                            if (typeof Agent === "undefined" || !Agent)
                                return
                            Agent.saveProviderConfig(Agent.activeProvider, keyField.text, urlField.text,
                                                     String((modelCombo.model[modelCombo.currentIndex] || {}).id || ""),
                                                     customField.text)
                            keyField.text = ""
                            statusText.text = pane.t("settings_saved")
                            Agent.refreshSettings()
                        }
                    }

                    FmButton {
                        objectName: "agentProviderTest"
                        primary: false
                        text: pane.t("settings_ai_test")
                        onClicked: if (typeof Agent !== "undefined" && Agent)
                                       Agent.testProvider(Agent.activeProvider)
                    }
                }
            }
        }
    }
}
