// FmAgentToolSettings.qml —— 工具清单与三级权限（阶段 3.23）。
import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: pane
    objectName: "agentToolSettings"

    property var rows: []
    property var effectOptions: []

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function effectIndex(value) {
        for (var i = 0; i < effectOptions.length; i++) {
            if (String(effectOptions[i].value) === String(value))
                return i
        }
        return 0
    }

    function refresh() {
        if (typeof Agent === "undefined" || !Agent)
            return
        rows = Agent.tools
        effectOptions = [
            {"value": "allow", "label": t("agent_permission_allow")},
            {"value": "ask", "label": t("agent_permission_ask")},
            {"value": "deny", "label": t("agent_permission_deny")}
        ]
        for (var i = 0; i < rows.length; i++)
            rows[i].effectIndex = effectIndex(rows[i].effect)
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
            spacing: Theme?.spacingSm ?? 8

            FmCard {
                Layout.fillWidth: true
                title: pane.t("agent_settings_tools_title")
                subtitle: pane.t("agent_settings_tools_desc")

                Repeater {
                    model: pane.rows

                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.displayName || modelData.name || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.description || "")
                                color: Theme?.textTertiary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }
                        }

                        FmComboBox {
                            Layout.preferredWidth: 116
                            model: pane.effectOptions
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: pane.effectIndex(modelData.effect)
                            onActivated: {
                                var row = model[currentIndex] || ({})
                                if (typeof Agent !== "undefined" && Agent && row.value)
                                    Agent.setToolEffect(String(modelData.name), String(row.value))
                            }
                        }
                    }
                }
            }
        }
    }
}
