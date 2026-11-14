// FmPluginCard.qml —— 插件管理器里的单张插件卡片（阶段 3 任务 3.27，M-30）。
//
// 字段全部来自 Plugins 桥的 plugins 列表（item 是普通 JS 对象）：
//   id / name / version / author / description（多语言字典）/ state / error /
//   permissions / permission_details（按风险排好序）/ ui_api / ui_pages /
//   ui_unsupported / has_update / latest_version。
//
// 与旧实现（ui/windows/plugin_manager.py:215-385）逐项对应：名称 + 状态、版本 +
// 作者、描述截断、权限标签最多 3 个 + N、错误信息、权限管理 / 启用禁用 / 卸载。
// 新增两项（3.27 的 UI API v2）：界面页面数、旧 UI API 的"界面不受支持"标记。
//
// 卡片只发信号，不调桥 —— 权限弹窗、确认框、启停与卸载都由管理器页编排。

import QtQuick
import QtQuick.Layouts
import ".."

FmCard {
    id: card
    objectName: "pluginCard_" + String(card.item.id || "")

    property var item: ({})

    signal managePermissions()
    signal toggleRequested()
    signal uninstallRequested()

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[key] ?? key) : key
    }

    function templateText(key, params) {
        var text = (typeof Tr !== "undefined" && Tr) ? (Tr.map[key] ?? key) : key
        if (params) {
            for (var name in params)
                text = text.replace("{" + name + "}", String(params[name]))
        }
        return text
    }

    //: 描述字典按当前语言取，缺键回落到 zh_CN / en_US / 第一个可用语言。
    function descriptionText() {
        var desc = card.item.description
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

    function stateKey(state) {
        var text = String(state || "scanned")
        if (text === "uninstalling")
            return "plugin_state_loading"
        return "plugin_state_" + text
    }

    function stateLevel(state) {
        var text = String(state || "")
        if (text === "enabled" || text === "running")
            return "success"
        if (text === "error" || text === "init_error")
            return "error"
        if (text === "loading" || text === "incompatible")
            return "warning"
        return "neutral"
    }

    function permissionLevel(risk) {
        var text = String(risk || "")
        if (text === "high")
            return "error"
        if (text === "medium")
            return "warning"
        return "neutral"
    }

    function isActive() {
        var text = String(card.item.state || "")
        return text === "enabled" || text === "running"
    }

    function canToggle() {
        var text = String(card.item.state || "")
        return text === "enabled" || text === "running" || text === "disabled"
                || text === "scanned" || text === "loaded"
    }

    function shownPermissions() {
        var list = card.item.permission_details
        return (list === undefined || list === null) ? [] : list.slice(0, 3)
    }

    function hiddenPermissionCount() {
        var list = card.item.permission_details
        if (list === undefined || list === null)
            return 0
        return Math.max(0, list.length - 3)
    }

    Layout.fillWidth: true

    // ── 名称 + 状态 ──
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme?.spacingSm ?? 8

        Text {
            objectName: "pluginName"
            Layout.fillWidth: true
            text: String(card.item.name || card.item.id || "")
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeLarge ?? 14
            font.bold: true
            elide: Text.ElideRight
        }

        FmTag {
            objectName: "pluginStateTag"
            text: card.t(card.stateKey(card.item.state))
            level: card.stateLevel(card.item.state)
        }
    }

    // ── 版本 + 作者 + 更新提示 ──
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme?.spacingSm ?? 8

        Text {
            objectName: "pluginVersion"
            text: "v" + String(card.item.version || "?")
            color: card.item.has_update ? (Theme?.warning ?? "transparent") : (Theme?.accent ?? "transparent")
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        FmTag {
            objectName: "pluginUpdateTag"
            visible: card.item.has_update === true
            text: card.t("plugin_update_available_tag")
            level: "warning"
        }

        Text {
            objectName: "pluginUpdateHint"
            visible: card.item.has_update === true
            text: "v" + String(card.item.version || "?") + " -> v" + String(card.item.latest_version || "?")
            color: Theme?.warning ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        Item {
            Layout.fillWidth: true
        }

        Text {
            objectName: "pluginAuthor"
            text: String(card.item.author || "")
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }
    }

    // ── 描述 ──
    Text {
        objectName: "pluginDescription"
        Layout.fillWidth: true
        visible: text.length > 0
        text: card.descriptionText()
        color: Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
        wrapMode: Text.WordWrap
        maximumLineCount: 3
        elide: Text.ElideRight
    }

    // ── 权限标签（最多 3 个 + N）──
    RowLayout {
        Layout.fillWidth: true
        visible: card.item.permissions !== undefined && card.item.permissions.length > 0
        spacing: Theme?.spacingXs ?? 4

        Repeater {
            model: card.shownPermissions()

            FmTag {
                text: card.t(String(modelData.label_key || ""))
                level: card.permissionLevel(String(modelData.risk || ""))
            }
        }

        Text {
            objectName: "pluginPermissionMore"
            visible: card.hiddenPermissionCount() > 0
            text: "+" + card.hiddenPermissionCount()
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        Item {
            Layout.fillWidth: true
        }

        // ── UI API v2：页面数 / 旧接口标记 ──
        FmTag {
            objectName: "pluginUiPagesTag"
            visible: Number(card.item.ui_pages || 0) > 0
            text: card.templateText("plugin_ui_pages", {"count": Number(card.item.ui_pages || 0)})
            level: "accent"
        }

        FmTag {
            objectName: "pluginUiUnsupportedTag"
            visible: card.item.ui_unsupported === true
            text: card.t("plugin_ui_unsupported")
            level: "warning"
        }
    }

    // ── 旧 UI API 说明（不支持的界面只影响页面，非 UI 功能照常）──
    Text {
        objectName: "pluginUiUnsupportedDetail"
        Layout.fillWidth: true
        visible: card.item.ui_unsupported === true
        text: card.t("plugin_ui_unsupported_detail")
        color: Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
        wrapMode: Text.WordWrap
    }

    // ── 错误信息 ──
    Text {
        objectName: "pluginError"
        Layout.fillWidth: true
        visible: text.length > 0
        text: (card.item.error !== undefined && String(card.item.error).length > 0)
              ? (card.t("error") + ": " + String(card.item.error))
              : ""
        color: Theme?.error ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
        wrapMode: Text.WordWrap
    }

    footer: RowLayout {
        objectName: "pluginCardActions"
        spacing: Theme?.spacingSm ?? 8

        FmButton {
            objectName: "pluginPermissionsButton"
            primary: false
            visible: card.item.permissions !== undefined && card.item.permissions.length > 0
            text: card.t("plugin_permissions_manage")
            onClicked: card.managePermissions()
        }

        FmButton {
            objectName: "pluginToggleButton"
            primary: false
            visible: card.canToggle()
            text: card.isActive() ? card.t("disable") : card.t("enable")
            onClicked: card.toggleRequested()
        }

        FmButton {
            objectName: "pluginUninstallButton"
            primary: false
            danger: true
            text: card.t("uninstall")
            onClicked: card.uninstallRequested()
        }
    }
}
