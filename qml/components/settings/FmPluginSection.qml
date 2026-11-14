// FmPluginSection.qml —— 设置 → 插件分区的概况页（阶段 3 任务 3.27，M-23）。
//
// 旧实现是 Tk 设置窗的「插件」标签页（ui/windows/launcher_settings.py:2095-2178）：
// 标题 + 说明 + 「打开插件管理」+ 概况摘要（已启用/总数）+ 三条使用提示 +
// 「浏览插件市场」。迁移到 QML 后，两个动作变成两条二级路由：
// settings/plugin/manager 与 settings/plugin/market（见 02 §4.12 / 04 M-23）。
//
// 数据只来自 Plugins 桥（available / initialized / summary）；本分区不碰服务，
// 也不缓存任何业务状态 —— 语言切换由 Tr.map 绑定自动重算。
//
// 纪律：颜色只来自 Theme.*、图标走 FmIcon/FmButton、文案走 Tr.map。

import QtQuick
import QtQuick.Layouts
import ".."

FmScrollView {
    id: section
    objectName: "fmPluginSection"

    contentWidth: availableWidth

    readonly property bool live: (typeof Plugins !== "undefined" && Plugins) ? true : false

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[key] ?? key) : key
    }

    //: 已启用/总数。桥缺席或尚未初始化时退回「插件系统未初始化」。
    readonly property string summaryText: {
        if (!section.live || !Plugins.initialized)
            return section.t("plugin_not_initialized")
        var enabled = Number(Plugins.summary ? Plugins.summary.enabled : 0)
        var total = Number(Plugins.summary ? Plugins.summary.total : 0)
        var template = (typeof Tr !== "undefined" && Tr)
                ? (Tr.map["plugin_status_summary"] ?? "plugin_status_summary")
                : "plugin_status_summary"
        return template.replace("{enabled}", String(enabled)).replace("{total}", String(total))
    }

    //: 三条使用提示的键（与旧实现逐字相同的三条）
    readonly property var tipKeys: ["plugin_tip_1", "plugin_tip_2", "plugin_tip_3"]

    Component.onCompleted: {
        if (section.live)
            Plugins.initialize()
    }


    ColumnLayout {
        width: section.availableWidth
        spacing: Theme?.spacingMd ?? 12

        Text {
            objectName: "pluginSectionTitle"
            Layout.fillWidth: true
            text: section.t("plugin_manager_title")
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeTitle ?? 18
            font.bold: true
            elide: Text.ElideRight
        }

        Text {
            objectName: "pluginSectionDesc"
            Layout.fillWidth: true
            text: section.t("plugin_manager_desc")
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            wrapMode: Text.WordWrap
        }

        FmButton {
            objectName: "pluginOpenManagerButton"
            Layout.fillWidth: true
            primary: true
            text: section.t("plugin_open_manager")
            iconName: "plugin"
            onClicked: {
                if (typeof Nav !== "undefined" && Nav)
                    Nav.push("settings/plugin/manager")
            }
        }

        FmCard {
            objectName: "pluginSummaryCard"
            Layout.fillWidth: true
            title: section.t("plugin_quick_info")

            Text {
                objectName: "pluginSummaryText"
                Layout.fillWidth: true
                text: section.summaryText
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 11
                wrapMode: Text.WordWrap
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 4

                Repeater {
                    model: section.tipKeys

                    Text {
                        objectName: "pluginTip_" + modelData
                        Layout.fillWidth: true
                        text: "  " + section.t(modelData)
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        FmButton {
            objectName: "pluginOpenMarketButton"
            Layout.fillWidth: true
            primary: false
            text: section.t("plugin_market_open")
            iconName: "download"
            onClicked: {
                if (typeof Nav !== "undefined" && Nav)
                    Nav.push("settings/plugin/market")
            }
        }
    }
}
