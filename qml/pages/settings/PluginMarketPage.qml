// PluginMarketPage.qml —— 插件市场。
//
// 内容：标题 / 刷新 / 搜索 / 标签筛选 / 状态栏 / 卡片（更新标记、已安装标记、
// 权限统计 H:M:L、许可证）/ 下载安装 / 更新 / 启用 / 每页 10 条 / 焦点重载。
//
// 两条要点：
//   * 刷新按钮恒传 force=True —— "点刷新没反应"是因为受 1 小时缓存约束；
//   * 权限统计复用 services/browse_common.permission_summary（与 plugin_manager
//     的权限风险表同一真值来源）。
//
// 线程：索引拉取 / 安装 / 更新都在 Plugins 桥的 TaskRunner 里；本页只发意图。

import QtQuick
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "pluginMarketPage"

    readonly property bool live: (typeof Plugins !== "undefined" && Plugins) ? true : false
    readonly property bool busy: (page !== null && page.live) && Plugins.busy

    title: (page !== null ? page.t("plugin_market_title") : "")
    description: (page !== null ? page.t("plugin_market_desc") : "")
    iconName: "download"
    //: 工具条永远在（结果为空也要能改关键词 / 刷新），三态画在结果区内部。
    contentState: "ready"

    property string marketPluginId: ""
    property string marketPluginName: ""
    property var marketGroups: []
    //: install = 未安装（安装前确认权限）；enable = 已安装但禁用（启用前确认）
    property string marketMode: "install"

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[key] ?? key) : key
    }

    function templateText(key, params) {
        var text = page.t(key)
        if (params) {
            for (var name in params)
                text = text.replace("{" + name + "}", String(params[name]))
        }
        return text
    }

    function descriptionText(item) {
        var desc = item.description
        if (desc === undefined || desc === null)
            return ""
        if (typeof desc === "string")
            return desc
        var lang = (typeof Tr !== "undefined" && Tr) ? String(Tr.language || "zh_CN") : "zh_CN"
        var keys = [lang, "zh_CN", "en_US"]
        for (var i = 0; i < keys.length; i++) {
            if (desc[keys[i]] !== undefined && desc[keys[i]] !== null && String(desc[keys[i]]).length > 0)
                return String(desc[keys[i]])
        }
        for (var key in desc) {
            if (desc[key] !== undefined && desc[key] !== null && String(desc[key]).length > 0)
                return String(desc[key])
        }
        return ""
    }

    function statusText() {
        if (!(page !== null && page.live))
            return (page !== null ? page.t("plugin_market_unavailable") : "")
        if (Plugins.marketLoading)
            return (page !== null ? page.t("plugin_market_loading") : "")
        if (Plugins.marketError.length > 0 && Plugins.marketTotal === 0)
            return (page !== null ? page.t("plugin_market_unavailable") : "")
        return page.templateText("plugin_market_total", {"total": Plugins.marketTotal})
    }

    function tagLabel(tag) {
        return String(tag.label || tag.id || "")
    }

    function openPermissionDialog(item, mode) {
        page.marketPluginId = String(item.id || "")
        page.marketPluginName = String(item.name || page.marketPluginId)
        page.marketMode = mode
        page.marketGroups = mode === "install"
                ? (item.permission_groups || [])
                : Plugins.permissionGroups(page.marketPluginId)
        permissionDialog.pluginName = page.marketPluginName
        permissionDialog.visible = true
    }

    function requestInstall(item) {
        if (!(page !== null && page.live) || !item || (page !== null && page.busy))
            return
        var pid = String(item.id || "")
        var permissions = item.permissions || []
        if (permissions.length > 0) {
            page.openPermissionDialog(item, "install")
        } else {
            Plugins.installMarket(pid)
        }
    }

    function requestEnable(item) {
        if (!(page !== null && page.live) || !item || (page !== null && page.busy))
            return
        var pid = String(item.id || "")
        //: 已授权（或没有声明权限）时直接启用；否则先让用户确认一遍权限。
        if (Plugins.hasUngranted(pid))
            page.openPermissionDialog(item, "enable")
        else
            Plugins.enableMarket(pid)
    }

    function onMarketApproved() {
        var pid = page.marketPluginId
        var mode = page.marketMode
        permissionDialog.visible = false
        if (!(page !== null && page.live) || pid.length === 0)
            return
        if (mode === "enable") {
            Plugins.grantPermissions(pid)
            Plugins.enableMarket(pid)
        } else {
            Plugins.installMarket(pid)
        }
    }

    Component.onCompleted: {
        if (!(page !== null && page.live))
            return
        Plugins.initialize()
        Plugins.loadMarket(false)
    }

    Connections {
        target: (page !== null && page.live) ? Plugins : null

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level))
        }

        function onNotified(pluginId, title, message, level) {
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"title": String(title), "message": String(message),
                                "level": String(level), "icon": "plugin"})
        }
    }

    ColumnLayout {
        objectName: "pluginMarketContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        // ── 搜索 + 刷新 ──
        RowLayout {
            objectName: "pluginMarketToolbar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmSearchField {
                objectName: "pluginMarketSearch"
                Layout.fillWidth: true
                placeholderText: (page !== null ? page.t("plugin_market_search_placeholder") : "")
                onTextChanged: {
                    if (page.live)
                        Plugins.setMarketQuery(text)
                }
            }

            FmButton {
                objectName: "pluginMarketRefreshButton"
                primary: false
                text: (page !== null ? page.t("refresh") : "")
                iconName: "refresh"
                loading: (page !== null && page.live) && Plugins.marketLoading
                enabled: (page !== null && page.live) && !Plugins.marketLoading
                onClicked: {
                    if (page.live)
                        Plugins.loadMarket(true)
                }
            }
        }

        // ── 标签筛选 ──
        Flow {
            objectName: "pluginMarketTags"
            Layout.fillWidth: true
            visible: (page !== null && page.live) && Plugins.marketTags.length > 0
            spacing: Theme?.spacingXs ?? 4

            FmButton {
                objectName: "pluginMarketTagAll"
                primary: (page !== null && page.live) && String(Plugins.marketTag).length === 0
                text: (page !== null ? page.t("all") : "")
                onClicked: {
                    if (page.live)
                        Plugins.setMarketTag("")
                }
            }

            Repeater {
                model: (page !== null && page.live) ? Plugins.marketTags : []

                FmButton {
                    objectName: "pluginMarketTag_" + String(modelData.id)
                    primary: (page !== null && page.live) && String(Plugins.marketTag) === String(modelData.id)
                    text: (page !== null ? page.tagLabel(modelData) : "")
                    onClicked: {
                        if (page.live)
                            Plugins.setMarketTag(String(modelData.id))
                    }
                }
            }
        }

        // ── 状态栏 ──
        Text {
            objectName: "pluginMarketStatus"
            Layout.fillWidth: true
            text: (page !== null ? page.statusText() : "")
            color: ((page !== null && page.live) && Plugins.marketError.length > 0 && Plugins.marketTotal === 0)
                   ? (Theme?.error ?? "transparent")
                   : (Theme?.textSecondary ?? "transparent")
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            wrapMode: Text.WordWrap
        }

        FmProgressBar {
            objectName: "pluginMarketProgress"
            Layout.fillWidth: true
            visible: (page !== null && page.busy)
            value: (page !== null && page.live) ? Plugins.progress : 0
            indeterminate: (page !== null && page.live) ? Plugins.progress < 0 : true
            label: (page !== null && page.live) ? Plugins.busyText : ""
        }

        // ── 结果 ──
        FmScrollView {
            id: scroller
            objectName: "pluginMarketScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth

            ColumnLayout {
                width: scroller.availableWidth
                spacing: Theme?.spacingMd ?? 12

                Text {
                    objectName: "pluginMarketEmpty"
                    Layout.fillWidth: true
                    visible: (page !== null && page.live) && !Plugins.marketLoading && Plugins.marketTotal === 0
                    text: (page !== null ? page.t("plugin_market_no_results") : "")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    horizontalAlignment: Text.AlignHCenter
                    topPadding: Theme?.spacingXl ?? 24
                    bottomPadding: Theme?.spacingXl ?? 24
                }

                Repeater {
                    model: (page !== null && page.live) ? Plugins.marketItems : []

                    FmCard {
                        id: marketCard
                        property var item: modelData
                        Layout.fillWidth: true
                        objectName: "pluginMarketCard_" + String(item.id || "")

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                objectName: "pluginMarketName"
                                Layout.fillWidth: true
                                text: String(marketCard.item.name || marketCard.item.id || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeLarge ?? 14
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            FmTag {
                                objectName: "pluginMarketUpdateTag"
                                visible: marketCard.item.has_update === true
                                text: Tr?.map["plugin_update_available_tag"] ?? "plugin_update_available_tag"
                                level: "warning"
                            }

                            FmTag {
                                objectName: "pluginMarketInstalledTag"
                                visible: marketCard.item.installed === true
                                text: String(marketCard.item.installed_state) === "enabled"
                                      ? (Tr?.map["installed"] ?? "installed")
                                      : (Tr?.map["disabled"] ?? "disabled")
                                level: String(marketCard.item.installed_state) === "enabled" ? "success" : "neutral"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                objectName: "pluginMarketVersion"
                                text: marketCard.item.has_update === true
                                      ? ("v" + String(marketCard.item.installed_version || "?")
                                         + " -> v" + String(marketCard.item.latest_version || "?"))
                                      : ("v" + String(marketCard.item.version || "?"))
                                color: marketCard.item.has_update === true
                                       ? (Theme?.warning ?? "transparent") : (Theme?.accent ?? "transparent")
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            Text {
                                objectName: "pluginMarketAuthor"
                                text: String(marketCard.item.author || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "pluginMarketLicense"
                                visible: String(marketCard.item.license || "").length > 0
                                text: String(marketCard.item.license || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }
                        }

                        Text {
                            objectName: "pluginMarketDescription"
                            Layout.fillWidth: true
                            visible: text.length > 0
                            text: {
                                var desc = marketCard.item.description
                                if (desc === undefined || desc === null)
                                    return ""
                                if (typeof desc === "string")
                                    return desc
                                var lang = (typeof Tr !== "undefined" && Tr) ? String(Tr.language || "zh_CN") : "zh_CN"
                                var keys = [lang, "zh_CN", "en_US"]
                                for (var i = 0; i < keys.length; i++) {
                                    if (desc[keys[i]] !== undefined && desc[keys[i]] !== null
                                            && String(desc[keys[i]]).length > 0)
                                        return String(desc[keys[i]])
                                }
                                for (var key in desc) {
                                    if (desc[key] !== undefined && desc[key] !== null
                                            && String(desc[key]).length > 0)
                                        return String(desc[key])
                                }
                                return ""
                            }
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Repeater {
                                model: (marketCard.item.tags || []).slice(0, 3)

                                FmTag {
                                    text: String(modelData)
                                    level: "neutral"
                                }
                            }

                            Text {
                                objectName: "pluginMarketPermissionSummary"
                                visible: String(marketCard.item.permission_summary || "").length > 0
                                text: String(marketCard.item.permission_summary || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            FmButton {
                                objectName: "pluginMarketAction"
                                primary: true
                                enabled: (typeof Plugins !== "undefined" && Plugins) ? !Plugins.busy : false
                                visible: !(marketCard.item.installed === true
                                           && String(marketCard.item.installed_state) === "enabled")
                                text: {
                                    if (marketCard.item.has_update === true)
                                        return Tr?.map["plugin_update"] ?? "plugin_update"
                                    if (marketCard.item.installed === true)
                                        return Tr?.map["enable"] ?? "enable"
                                    return Tr?.map["install"] ?? "install"
                                }
                                onClicked: {
                                    if (!(page !== null && page.live))
                                        return
                                    var pid = String(marketCard.item.id || "")
                                    if (marketCard.item.has_update === true)
                                        Plugins.updateMarket(pid)
                                    else if (marketCard.item.installed === true)
                                        page.requestEnable(marketCard.item)
                                    else
                                        page.requestInstall(marketCard.item)
                                }
                            }
                        }
                    }
                }
            }
        }

        // ── 分页 ──
        RowLayout {
            objectName: "pluginMarketPager"
            Layout.fillWidth: true
            visible: (page !== null && page.live) && Plugins.marketPageCount > 1

            Item {
                Layout.fillWidth: true
            }

            FmPagination {
                objectName: "pluginMarketPagination"
                page: (page !== null && page.live) ? Plugins.marketPage : 1
                pageCount: (page !== null && page.live) ? Plugins.marketPageCount : 1
                onPageRequested: function(p) {
                    if (page.live)
                        Plugins.setMarketPage(p)
                }
            }
        }
    }

    FmPluginPermissionDialog {
        id: permissionDialog
        objectName: "pluginMarketPermissionDialog"
        groups: (page !== null ? page.marketGroups : [])
        busy: (page !== null && page.busy)
        onApproved: page.onMarketApproved()
        onDenied: {
            permissionDialog.visible = false
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus((page !== null ? page.t("plugin_permission_denied") : ""), "error")
        }
    }
}
