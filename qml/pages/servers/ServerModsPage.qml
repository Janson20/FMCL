// ServerModsPage.qml —— 服务器本地模组 / 资源包（阶段 3 任务 3.12）。
//
// 两条路由共用这一页：
//   * `servers/detail/mods`   → 模组管理（L-50）
//   * `servers/detail/packs`  → 资源包管理（02 §4.4 的 L3 页）
//
// 为什么不拆两个文件：两者的目录扫描、搜索、分页、拖拽安装、删除确认、空态与
// 进度完全同形，差别只有“允不允许 .jar”“有没有启用/禁用与更新检查”。拆开会让
// 这 80% 的界面复制两份（3.7 已经因为“搜索页和结果页重复”返工过一次）。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon（R2）、文案一律 Tr.map / Tr.tf
// （R3/R4）、控件只用 qml/components 的自研件（R9）、每一个上下文属性读取都判空。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "serverModsPage"

    readonly property bool live: (typeof ServerMods !== "undefined" && ServerMods) ? true : false
    readonly property bool modsMode: String(page.routeId || "").indexOf("/packs") < 0
    readonly property string kind: page.modsMode ? "mods" : "resourcepacks"

    readonly property var rows: page.live ? ServerMods.rows : []
    readonly property string version: page.live ? String(ServerMods.versionId || "") : ""
    readonly property string state: page.live ? String(ServerMods.loadState || "idle") : "error"
    readonly property string searchText: page.live ? String(ServerMods.search || "") : ""
    readonly property int updatesCount: page.live ? Number(ServerMods.updatesCount || 0) : 0

    property string deletePath: ""
    property string deleteName: ""
    property bool updateDialogOpen: false
    property bool reviewOpen: false
    property var reviewRows: []

    title: page.live ? page.t(String(ServerMods.kindLabelKey)) : page.t("server_mods_title")
    description: page.version
    iconName: page.live ? String(ServerMods.kindIcon) : "mods"
    //: 空文件夹**不能**让整页进 empty：那会把工具条（添加/打开目录）一起藏掉。
    //: 只有“没拿到服务器”才走整页空态。
    contentState: !page.live ? "error"
                  : (page.version.length === 0 ? "empty"
                  : (page.state === "empty" ? "ready"
                  : (page.state === "idle" || page.state === "loading" ? "loading"
                  : page.state)))
    loadingText: page.t("mod_loading_metadata")
    emptyText: page.t("server_no_version_selected")
    errorText: page.t("plugin_state_error")
    errorDetail: page.live ? String(ServerMods.loadError || "") : ""
    retryText: page.t("retry")

    onRetried: {
        if (page.live)
            ServerMods.refresh();
    }

    //: 路由参数是 createObject() 之后才写进来的，必须直接读原始 routeParams。
    function wantedVersion() {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        return String(raw.version || "");
    }

    function activateFromRoute() {
        if (page.live)
            ServerMods.activate(page.wantedVersion(), page.kind);
    }

    onRouteParamsChanged: page.activateFromRoute()
    onRouteIdChanged: page.activateFromRoute()
    onVisibleChanged: {
        if (page.visible)
            page.activateFromRoute();
    }
    Component.onCompleted: page.activateFromRoute()

    //: `Tr.map[键] ?? 键`；带参数走 `Tr.tf`（Tr.t 是 1 参槽，多给的参数会被忽略）
    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    function format(key, params) {
        return page.t(key, params);
    }

    //: 类型标签（模组 / 资源包）。析构期上下文属性会先被清空，这里兜底到页面类型的
    //: 通用键，避免退出时刷 “Cannot read property … of null”。
    function kindLabel() {
        if (page.live)
            return page.t(String(ServerMods.kindLabelKey));
        return page.t(page.modsMode ? "resource_mods" : "resource_packs");
    }

    function openBrowser() {
        if (!page.live || page.version.length === 0)
            return;
        if (typeof Nav !== "undefined" && Nav)
            Nav.push("servers/detail/mods/browser", {"version": page.version});
    }

    function requestDelete(path, name) {
        page.deletePath = String(path || "");
        page.deleteName = String(name || "");
    }

    //: 更新对话框只显示当前这一轮的结果；关掉后下次检查重新打开。
    function closeUpdates() {
        page.updateDialogOpen = false;
    }

    function updateIds() {
        var source = page.live ? ServerMods.updates : [];
        var ids = [];
        for (var i = 0; i < source.length; ++i)
            ids.push(String(source[i].modid || ""));
        return ids;
    }

    ColumnLayout {
        objectName: "serverModsContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        RowLayout {
            objectName: "serverModsToolbar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "serverModsOnlineButton"
                visible: page.modsMode
                primary: false
                iconName: "search"
                text: page.t("mod_browser_title_all")
                enabled: page.live && page.version.length > 0
                onClicked: page.openBrowser()
            }

            FmButton {
                objectName: "serverModsExportButton"
                visible: page.modsMode
                primary: false
                iconName: "copy"
                text: page.t("mod_export_list")
                enabled: page.live && !ServerMods.busy && page.rows.length > 0
                onClicked: {
                    if (page.live)
                        ServerMods.exportList();
                }
            }

            FmButton {
                objectName: "serverModsFilterButton"
                visible: page.modsMode
                primary: false
                iconName: "tool"
                text: page.t("mod_filter_client_btn")
                enabled: page.live && !ServerMods.busy && page.rows.length > 0
                onClicked: {
                    if (page.live)
                        ServerMods.filterClient();
                }
            }

            FmButton {
                objectName: "serverModsCheckUpdatesButton"
                visible: page.modsMode
                primary: false
                iconName: "download"
                text: page.t("mod_check_updates")
                enabled: page.live && !ServerMods.busy && page.rows.length > 0
                onClicked: {
                    if (page.live)
                        ServerMods.checkUpdates();
                }
            }

            Item { Layout.fillWidth: true }

            FmButton {
                objectName: "serverModsAddButton"
                primary: true
                iconName: "add"
                text: page.t("resource_add")
                enabled: page.live && page.version.length > 0
                onClicked: {
                    if (page.live)
                        ServerMods.requestAddFiles();
                }
            }

            FmButton {
                objectName: "serverModsOpenFolderButton"
                primary: false
                iconName: "folder-open"
                text: page.t("resource_open_folder")
                enabled: page.live && page.version.length > 0
                onClicked: {
                    if (page.live)
                        ServerMods.openFolder();
                }
            }

            FmButton {
                objectName: "serverModsRefreshButton"
                primary: false
                iconName: "refresh"
                text: page.t("refresh")
                enabled: page.live && !ServerMods.busy
                onClicked: {
                    if (page.live)
                        ServerMods.refresh();
                }
            }
        }

        FmSearchField {
            objectName: "serverModsSearchField"
            Layout.fillWidth: true
            placeholderText: page.live ? page.t(String(ServerMods.searchPlaceholderKey)) : ""
            text: page.searchText
            enabled: page.live && page.rows.length > 0
            onAccepted: {
                if (page.live)
                    ServerMods.setSearch(text);
            }
            onCleared: {
                if (page.live)
                    ServerMods.setSearch("");
            }
        }

        FmProgressBar {
            objectName: "serverModsProgress"
            Layout.fillWidth: true
            visible: page.live && ServerMods.progressVisible
            label: page.live ? String(ServerMods.progress.message || "") : ""
            indeterminate: page.live && Number(ServerMods.progress.total || 0) <= 0
            value: (page.live && Number(ServerMods.progress.total || 0) > 0)
                   ? (Number(ServerMods.progress.current || 0) / Number(ServerMods.progress.total || 1))
                   : 0
            showPercent: page.live && Number(ServerMods.progress.total || 0) > 0
        }

        ListView {
            id: serverModsList
            objectName: "serverModsListView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.rows.length > 0
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: page.rows
            ScrollBar.vertical: FmScrollBar {}

            delegate: Item {
                id: serverModRow
                objectName: "serverModRow"
                width: serverModsList.width
                height: rowCard.implicitHeight + (Theme?.spacingXs ?? 4)

                readonly property var row: (modelData === undefined) ? ({}) : modelData
                readonly property string pathKey: String(row.path || "")
                readonly property bool disabled: row.disabled === true

                FmCard {
                    id: rowCard
                    objectName: "serverModCard-" + serverModRow.pathKey
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    padding: Theme?.spacingMd ?? 12

                    RowLayout {
                        objectName: "serverModCardRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingMd ?? 12

                        Image {
                            objectName: "serverModIcon-" + serverModRow.pathKey
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            visible: String(serverModRow.row.icon || "").length > 0
                            source: String(serverModRow.row.icon || "")
                            sourceSize.width: 48
                            sourceSize.height: 48
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }

                        FmIcon {
                            objectName: "serverModFallback-" + serverModRow.pathKey
                            visible: String(serverModRow.row.icon || "").length === 0
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            name: String(serverModRow.row.iconName || "mods")
                            color: Theme?.textSecondary ?? "transparent"
                            size: 32
                        }

                        ColumnLayout {
                            objectName: "serverModInfo"
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                objectName: "serverModName-" + serverModRow.pathKey
                                Layout.fillWidth: true
                                text: String(serverModRow.row.display_name || "")
                                color: serverModRow.disabled ? (Theme?.textSecondary ?? "transparent")
                                                            : (Theme?.textPrimary ?? "transparent")
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "serverModAuthor-" + serverModRow.pathKey
                                Layout.fillWidth: true
                                visible: page.modsMode && String(serverModRow.row.author || "").length > 0
                                text: String(serverModRow.row.author || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "serverModDescription-" + serverModRow.pathKey
                                Layout.fillWidth: true
                                visible: page.modsMode && String(serverModRow.row.description || "").length > 0
                                text: String(serverModRow.row.description || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            RowLayout {
                                objectName: "serverModMeta"
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                FmTag {
                                    objectName: "serverModId-" + serverModRow.pathKey
                                    visible: page.modsMode && String(serverModRow.row.modid || "").length > 0
                                    text: String(serverModRow.row.modid || "")
                                    level: "accent"
                                }

                                Text {
                                    objectName: "serverModVersion-" + serverModRow.pathKey
                                    visible: page.modsMode && String(serverModRow.row.version || "").length > 0
                                    text: String(serverModRow.row.version || "").length > 0
                                          ? ("v" + String(serverModRow.row.version))
                                          : ""
                                    color: Theme?.success ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }

                                Text {
                                    objectName: "serverModFilename-" + serverModRow.pathKey
                                    Layout.fillWidth: true
                                    visible: page.modsMode
                                    text: String(serverModRow.row.filename || "")
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    elide: Text.ElideMiddle
                                }

                                Text {
                                    objectName: "serverModSize-" + serverModRow.pathKey
                                    visible: !page.modsMode && String(serverModRow.row.size || "").length > 0
                                    text: String(serverModRow.row.size || "")
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }
                            }
                        }

                        ColumnLayout {
                            objectName: "serverModActions"
                            Layout.alignment: Qt.AlignTop
                            spacing: Theme?.spacingXs ?? 4

                            FmButton {
                                objectName: "serverModToggle-" + serverModRow.pathKey
                                visible: page.modsMode
                                primary: false
                                text: serverModRow.disabled ? page.t("rm_enable") : page.t("rm_disable")
                                onClicked: {
                                    if (page.live)
                                        ServerMods.toggle(serverModRow.pathKey, serverModRow.disabled);
                                }
                            }

                            FmButton {
                                objectName: "serverModDelete-" + serverModRow.pathKey
                                danger: true
                                iconOnly: true
                                iconName: "trash"
                                onClicked: {
                                    if (page.live)
                                        page.requestDelete(serverModRow.pathKey,
                                                           String(serverModRow.row.display_name || ""));
                                }
                            }
                        }
                    }
                }
            }
        }

        FmDropZone {
            objectName: "serverModsDropZone"
            Layout.fillWidth: true
            Layout.fillHeight: page.rows.length === 0
            Layout.preferredHeight: page.rows.length === 0 ? -1 : 76
            Layout.minimumHeight: page.rows.length === 0 ? -1 : 76
            visible: page.live && page.version.length > 0
            iconName: page.modsMode ? "mods" : "resourcepack"
            text: page.rows.length === 0
                  ? (page.searchText.length > 0
                     ? page.t(page.modsMode ? "mod_search_no_results" : "rm_search_no_results")
                     : page.t("rm_folder_empty", {"label": page.kindLabel()}))
                  : page.t("rm_drop_hint")
            onDropped: function (urls) {
                if (page.live)
                    ServerMods.takeFiles(urls, true);
            }
        }

        FmPagination {
            objectName: "serverModsPagination"
            Layout.alignment: Qt.AlignLeft
            visible: page.live && Number(ServerMods.pageCount || 1) > 1
            page: page.live ? Number(ServerMods.page || 1) : 1
            pageCount: page.live ? Number(ServerMods.pageCount || 1) : 1
            onPageRequested: function (target) {
                if (page.live)
                    ServerMods.goto(Number(target));
            }
        }
    }

    // ══ 页内浮层宿主 ══════════════════════════════════════
    Item {
        id: serverModsOverlayHost
        objectName: "serverModsOverlayHost"
        anchors.fill: parent
        z: 100

        Loader {
            objectName: "serverModsDeleteLoader"
            anchors.fill: parent
            active: page.deletePath.length > 0
            sourceComponent: deleteComponent
        }

        Loader {
            objectName: "serverModsUpdateLoader"
            anchors.fill: parent
            active: page.updateDialogOpen
            sourceComponent: updateComponent
        }

        Loader {
            objectName: "serverModsReviewLoader"
            anchors.fill: parent
            active: page.reviewOpen
            sourceComponent: reviewComponent
        }
    }

    Component {
        id: deleteComponent

        ConfirmDialog {
            objectName: "serverModsDeleteDialog"
            modalScrim: true
            request: ({
                "title": page.t("rm_delete_confirm_title"),
                "message": page.t("rm_delete_confirm_msg", {"name": page.deleteName}),
                "default": true
            })
            onAnswered: function (value) {
                var path = page.deletePath;
                page.deletePath = "";
                page.deleteName = "";
                if (value === true && page.live && path.length > 0)
                    ServerMods.deletePath(path);
            }
        }
    }

    Component {
        id: updateComponent

        ResourceUpdateDialog {
            objectName: "serverModsUpdateDialog"
            rows: page.live ? ServerMods.updates : []
            titleText: page.t("mod_update_dialog_title")
            headerText: page.t("mod_update_dialog_header", {"count": page.updatesCount})
            hintText: page.t("mod_update_dialog_hint")
            cancelText: page.t("mod_update_cancel")
            allText: page.t("mod_update_all")
            selectedText: page.t("mod_update_selected")
            onCancelled: page.closeUpdates()
            onAllRequested: {
                page.closeUpdates();
                if (page.live)
                    ServerMods.applyUpdates(page.updateIds());
            }
            onSelected: function (modids) {
                page.closeUpdates();
                if (page.live)
                    ServerMods.applyUpdates(modids);
            }
        }
    }

    Component {
        id: reviewComponent

        ModReviewDialog {
            objectName: "serverModsReviewDialog"
            rows: page.reviewRows
            titleText: page.t("server_mods_title")
            hintText: page.t("mod_unknown") + " ("
                      + String((page.reviewRows || []).length) + ")"
            disableText: page.t("mod_disable")
            closeText: page.t("close")
            onDisableRequested: function (names) {
                page.reviewOpen = false;
                if (page.live)
                    ServerMods.disableMods(page.version, names);
            }
            onCancelled: page.reviewOpen = false
        }
    }

    FileDialog {
        id: addFilesDialog
        objectName: "serverModsAddFilesDialog"
        title: page.t("rm_select_file_title", {
            "label": page.kindLabel()
        })
        fileMode: FileDialog.OpenFiles
        nameFilters: page.modsMode
                     ? [page.t("resource_mods") + " (*.jar *.zip)", page.t("mp_filetype_all") + " (*)"]
                     : [page.t("resource_packs") + " (*.zip)", page.t("mp_filetype_all") + " (*)"]
        onAccepted: {
            if (page.live)
                ServerMods.takeFiles(selectedFiles, false);
        }
    }

    Connections {
        target: page.live ? ServerMods : null
        ignoreUnknownSignals: true

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level));
        }

        function onNoticeRequested(message, level) {
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(message),
                                "icon": page.modsMode ? "mods" : "resourcepack"});
        }

        function onAddFilesRequested() {
            addFilesDialog.open();
        }

        function onDeleteRequested(path, name) {
            page.requestDelete(path, name);
        }

        function onUpdatesChanged() {
            if (page.live && ServerMods.updatesCount > 0)
                page.updateDialogOpen = true;
        }

        function onUnknownModsReview(rows, count) {
            if (!rows || rows.length === 0)
                return;
            page.reviewRows = rows;
            page.reviewOpen = true;
        }
    }

    //: 在线安装成功返回本地页时刷新列表；页面可能压在栈下面，仍然要刷新。
    Connections {
        target: (typeof ServerModBrowser !== "undefined" && ServerModBrowser) ? ServerModBrowser : null
        ignoreUnknownSignals: true

        function onInstallFinished(title, success, detail) {
            if (page.live && success)
                ServerMods.refresh();
        }
    }
}
