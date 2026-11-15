// FmAgentSkillSettings.qml —— Skills 管理。
import QtQuick
import QtQuick.Layouts
import "../dialogs"
import ".."

Item {
    id: pane
    objectName: "agentSkillSettings"

    property var rows: []
    property string newName: ""

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function refresh() {
        if (typeof Agent === "undefined" || !Agent)
            return
        rows = Agent.skills
        pathText.text = String(Agent.skillsDir || "")
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
                title: pane.t("skills_title")
                subtitle: pane.t("skills_desc")

                Text {
                    Layout.fillWidth: true
                    text: pane.t("skills_empty")
                    visible: pane.rows.length === 0
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }

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
                                text: String(modelData.name || "")
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

                        FmButton {
                            primary: false
                            text: pane.t("skills_open")
                            onClicked: if (typeof Agent !== "undefined" && Agent)
                                           Agent.openSkill(String(modelData.name))
                        }

                        FmButton {
                            primary: false
                            text: pane.t("skills_edit")
                            onClicked: if (typeof Agent !== "undefined" && Agent)
                                           Agent.openSkillFile(String(modelData.name))
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "agentSkillNew"
                        text: pane.t("skills_new")
                        onClicked: {
                            newDialog.request = {"title": pane.t("skills_new"), "prompt": pane.t("skills_name_prompt")}
                            newDialog.visible = true
                        }
                    }

                    FmButton {
                        primary: false
                        text: pane.t("skills_open_dir")
                        onClicked: if (typeof Agent !== "undefined" && Agent)
                                       Agent.openSkillsDir()
                    }
                }

                Text {
                    id: pathText
                    Layout.fillWidth: true
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideMiddle
                }
            }
        }
    }

    TextInputDialog {
        id: newDialog
        anchors.fill: parent
        visible: false
        modalScrim: true
        z: 90
        request: ({"title": pane.t("skills_new"), "prompt": pane.t("skills_name_prompt")})
        onSubmitted: function (value) {
            var name = String(value || "").trim()
            if (name.length > 0 && typeof Agent !== "undefined" && Agent)
                Agent.createSkill(name)
            newDialog.visible = false
        }
        onCancelled: newDialog.visible = false

    }
}
