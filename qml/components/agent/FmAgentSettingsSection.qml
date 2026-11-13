// FmAgentSettingsSection.qml —— 设置 → AI 模型（阶段 3 任务 3.23）。
//
// 五个页签：模型 / 工具权限 / Skills / 语音 / 净读账号。
// 配置写入口在 Agent 桥；本件只做表单与列表展示。AI 配置与净读账号一样**立即生效**，
// 不进入设置草稿（与旧界面 AI 对话框的行为一致）。

import QtQuick
import QtQuick.Layouts
import ".."
import "../settings"

Item {
    id: section
    objectName: "fmAgentSettingsSection"

    property string tab: "model"

    readonly property var pageItems: [
        {"key": "model", "labelKey": "agent_settings_tab_model"},
        {"key": "tools", "labelKey": "agent_settings_tab_tools"},
        {"key": "skills", "labelKey": "skills_title"},
        {"key": "voice", "labelKey": "settings_voice_section"},
        {"key": "account", "labelKey": "netread_ai"}
    ]

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 10

        FmTabBar {
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            items: section.pageItems
            currentKey: section.tab
            onTabRequested: function (key) {
                section.tab = String(key)
            }
        }

        Loader {
            id: contentLoader
            objectName: "agentSettingsContentLoader"
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: section.tab === "account" ? accountComponent
                           : section.tab === "tools" ? toolsComponent
                           : section.tab === "skills" ? skillsComponent
                           : section.tab === "voice" ? voiceComponent
                           : modelComponent
        }
    }

    Component {
        id: modelComponent
        FmAgentProviderSettings {}
    }

    Component {
        id: toolsComponent
        FmAgentToolSettings {}
    }

    Component {
        id: skillsComponent
        FmAgentSkillSettings {}
    }

    Component {
        id: voiceComponent
        FmAgentVoiceSettings {}
    }

    Component {
        id: accountComponent
        FmJingduAccountSection {}
    }
}
