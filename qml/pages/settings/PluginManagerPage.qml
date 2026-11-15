// PluginManagerPage.qml —— 插件管理器。
//
// 旧实现是 ui/windows/plugin_manager.py（528 行 Tk Toplevel）：从文件安装、
// 打开市场、刷新、状态栏、焦点自动刷新、插件卡片（9 种状态 / 版本 / 作者 /
// 描述截断 / 权限标签最多 3+N / 错误信息）、更新提示、权限管理、启用禁用、
// 卸载（二次确认）。
//
// 新界面把它做成设置域的一条二级路由（settings/plugin/manager）：
//   * "焦点自动刷新"在 QML 里对应"每次进入页面重新初始化/检查更新"；
//   * "打开市场"改成跳 settings/plugin/market；
//   * 权限确认、卸载确认都复用页面内的自研对话框（FmPluginPermissionDialog /
//     ConfirmDialog），不新开窗口。
//
// 线程：安装是后台任务（Plugins 桥走 TaskRunner），其余启停/卸载是主线程的
// 短操作；本页只发意图、只读桥属性。

import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"
import "../../components/settings"

FmPage {
    id: page
    objectName: "pluginManagerPage"

    readonly property bool live: (typeof Plugins !== "undefined" && Plugins) ? true : false

    title: (page !== null ? page.t("plugin_manager_title") : "")
    description: (page !== null ? page.t("plugin_manager_desc") : "")
    iconName: "plugin"

    contentState: !(page !== null && page.live) ? "error"
                  : (!Plugins.initialized
                     ? (Plugins.initError.length > 0 ? "error" : "loading")
                     : (Plugins.plugins.length === 0 ? "empty" : "ready"))
    loadingText: (page !== null ? page.t("plugin_state_loading") : "")
    emptyText: (page !== null ? page.t("plugin_manager_empty") : "")
    emptyActionText: (page !== null ? page.t("plugin_install_from_file") : "")
    emptyActionPrimary: true
    errorText: (page !== null ? page.t("plugin_state_error") : "")
    errorDetail: (page !== null && page.live) ? (Plugins.initError.length > 0 ? Plugins.initError : (page !== null ? page.t("plugin_not_initialized") : "")) : ""
    retryText: (page !== null ? page.t("refresh") : "")

    //: 安装/更新任务在跑时禁用动作按钮（桥的 busy 是全局的）
    readonly property bool busy: (page !== null && page.live) && Plugins.busy

    //: 待安装的 .fmpl（inspectPackage 的结果）
    property var pendingPackage: ({})
    property string pendingPluginId: ""
    property string pendingName: ""

    //: 权限弹窗的状态
    property string permissionPluginId: ""
    property string permissionMode: "install" // install | manage | enable
    property var permissionGroups: []

    //: 待卸载的插件
    property string uninstallPluginId: ""
    property string uninstallName: ""

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

    function summaryText() {
        if (!(page !== null && page.live) || !Plugins.initialized)
            return ""
        return page.templateText("plugin_status_summary", {
            "enabled": Number(Plugins.summary ? Plugins.summary.enabled : 0),
            "total": Number(Plugins.summary ? Plugins.summary.total : 0)
        })
    }

    function localPath(url) {
        var text = String(url)
        text = text.replace(/^file:\/{2,3}/, "")
        try {
            return decodeURIComponent(text)
        } catch (e) {
            return text
        }
    }

    function inspectInstall(url) {
        if (!(page !== null && page.live))
            return
        var info = Plugins.inspectPackage(page.localPath(url))
        if (!info || info.ok !== true) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus((page !== null ? page.t("error") : "") + ": " + (page !== null ? page.t("plugin_invalid_fmpl") : ""), "error")
            return
        }
        page.pendingPackage = info
        page.pendingPluginId = String(info.id || "")
        page.pendingName = String(info.name || info.id || "")
        var permissions = info.permissions || []
        if (permissions.length > 0) {
            page.permissionPluginId = page.pendingPluginId
            page.permissionMode = "install"
            page.permissionGroups = info.permission_groups || []
            permissionDialog.pluginName = page.pendingName
            permissionDialog.visible = true
        } else {
            page.installPending()
        }
    }

    function installPending() {
        if (page.pendingPluginId.length === 0)
            return
        (page !== null && page.live) && Plugins.installFromFile(String(page.pendingPackage.path || ""), page.pendingPluginId)
        page.pendingPackage = ({})
        page.pendingPluginId = ""
        page.pendingName = ""
    }

    function openPermissionDialog(item, mode) {
        if (!(page !== null && page.live) || !item)
            return
        page.permissionPluginId = String(item.id || "")
        page.permissionMode = mode
        page.permissionGroups = Plugins.permissionGroups(page.permissionPluginId)
        permissionDialog.pluginName = String(item.name || page.permissionPluginId)
        permissionDialog.visible = true
    }

    function onPermissionApproved() {
        var pid = page.permissionPluginId
        var mode = page.permissionMode
        permissionDialog.visible = false
        if (!(page !== null && page.live) || pid.length === 0)
            return
        if (mode === "install") {
            page.installPending()
        } else if (mode === "manage") {
            Plugins.grantPermissions(pid)
        } else if (mode === "enable") {
            Plugins.grantPermissions(pid)
            Plugins.setEnabled(pid, true)
        }
    }

    function onPermissionDenied() {
        var mode = page.permissionMode
        permissionDialog.visible = false
        if (typeof Shell === "undefined" || !Shell)
            return
        if (mode === "install")
            Shell.setStatus((page !== null ? page.t("plugin_permission_denied") : ""), "error")
        else
            Shell.setStatus((page !== null ? page.t("plugin_permission_unchanged") : ""), "info")
    }

    function requestToggle(item) {
        if (!(page !== null && page.live) || !item || (page !== null && page.busy))
            return
        var pid = String(item.id || "")
        var state = String(item.state || "")
        if (state === "enabled" || state === "running") {
            Plugins.setEnabled(pid, false)
            return
        }
        if (Plugins.hasUngranted(pid) && (item.permissions || []).length > 0)
            page.openPermissionDialog(item, "enable")
        else
            Plugins.setEnabled(pid, true)
    }

    function requestUninstall(item) {
        if (!(page !== null && page.live) || !item || (page !== null && page.busy))
            return
        page.uninstallPluginId = String(item.id || "")
        page.uninstallName = String(item.name || item.id || "")
        uninstallDialog.visible = true
    }

    onRetried: {
        if (!(page !== null && page.live))
            return
        Plugins.initialize()
        Plugins.refreshPlugins()
    }

    onEmptyActionTriggered: {
        if ((page !== null && page.live) && !(page !== null && page.busy))
            installDialog.open()
    }

    Component.onCompleted: {
        if (!(page !== null && page.live))
            return
        Plugins.initialize()
        Plugins.checkUpdates()
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

        function onPluginsChanged() {
            // 列表刷新后弹窗里的插件可能已经不存在（卸载/禁用）—— 收起旧弹窗。
            if (permissionDialog.visible
                    && Plugins.permissionGroups(page.permissionPluginId).length === 0
                    && page.permissionMode !== "install")
                permissionDialog.visible = false
        }
    }

    // ── 工具条（一直显示：空态也要能安装）──────────────────────
    ColumnLayout {
        objectName: "pluginManagerContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        RowLayout {
            objectName: "pluginManagerToolbar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "pluginInstallFileButton"
                primary: true
                text: (page !== null ? page.t("plugin_install_from_file") : "")
                iconName: "download"
                enabled: (page !== null && page.live) && !(page !== null && page.busy)
                onClicked: installDialog.open()
            }

            FmButton {
                objectName: "pluginManagerMarketButton"
                primary: false
                text: (page !== null ? page.t("plugin_market_title") : "")
                iconName: "plugin"
                onClicked: {
                    if (typeof Nav !== "undefined" && Nav)
                        Nav.push("settings/plugin/market")
                }
            }

            FmButton {
                objectName: "pluginRefreshButton"
                primary: false
                text: (page !== null ? page.t("refresh") : "")
                iconName: "refresh"
                loading: (page !== null && page.busy)
                enabled: (page !== null && page.live) && !(page !== null && page.busy)
                onClicked: {
                    if (page.live)
                        Plugins.refreshPlugins()
                }
            }

            Item {
                Layout.fillWidth: true
            }

            Text {
                objectName: "pluginManagerSummary"
                text: (page !== null ? page.summaryText() : "")
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }
        }

        FmProgressBar {
            objectName: "pluginManagerProgress"
            Layout.fillWidth: true
            visible: (page !== null && page.busy)
            value: (page !== null && page.live) ? Plugins.progress : 0
            indeterminate: (page !== null && page.live) ? Plugins.progress < 0 : true
            label: (page !== null && page.live) ? Plugins.busyText : ""
        }

        FmScrollView {
            id: scroller
            objectName: "pluginManagerScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth

            ColumnLayout {
                width: scroller.availableWidth
                spacing: Theme?.spacingMd ?? 12

                Repeater {
                    model: (page !== null && page.live) ? Plugins.plugins : []

                    FmPluginCard {
                        id: card
                        item: modelData

                        onManagePermissions: page.openPermissionDialog(card.item, "manage")
                        onToggleRequested: page.requestToggle(card.item)
                        onUninstallRequested: page.requestUninstall(card.item)
                    }
                }
            }
        }
    }

    // ── 对话框 ─────────────────────────────────────────────
    FileDialog {
        id: installDialog
        objectName: "pluginInstallDialog"
        title: (page !== null ? page.t("plugin_install_from_file") : "")
        nameFilters: [(page !== null ? page.t("plugin_file_type") : "") + " (*.fmpl)", (page !== null ? page.t("all_files") : "") + " (*)"]
        onAccepted: page.inspectInstall(selectedFile)
    }

    FmPluginPermissionDialog {
        id: permissionDialog
        objectName: "pluginPermissionDialog"
        groups: (page !== null ? page.permissionGroups : [])
        busy: (page !== null && page.busy)
        onApproved: page.onPermissionApproved()
        onDenied: page.onPermissionDenied()
    }

    ConfirmDialog {
        id: uninstallDialog
        objectName: "pluginUninstallDialog"
        visible: false
        modalScrim: true
        request: ({
            "title": (page !== null ? page.t("confirm_delete") : ""),
            "message": (page !== null ? page.templateText("plugin_uninstall_confirm", {"name": page.uninstallName}) : ""),
            "default": false
        })
        onAnswered: function(ok) {
            uninstallDialog.visible = false
            if (ok && (page !== null && page.live) && page.uninstallPluginId.length > 0)
                Plugins.uninstall(page.uninstallPluginId)
        }
    }
}
