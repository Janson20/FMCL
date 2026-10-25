// ModpackPage.qml —— 整合包安装 / 开服（阶段 3 任务 3.8，对照表 L-53 / L-54）。
//
// 同一份界面承担客户端与服务器两种模式（用户裁决，见 `app/bridges/modpack_bridge.py`
// 的文件头）：两者的文件选择、信息展示、可选组件、进度与结果完全同形，只有安装入口、
// 服务器名与成功文案不同。模式由路由决定：
//
//   * `versions/modpack` → client（安装向导里的「安装整合包」入口，B-12）；
//   * `servers/modpack`  → server（旧服务器页的「整合包开服」入口）。
//
// 浏览在独立页 `ModpackBrowserPage.qml`：它是旧实现那个模态子窗口的对应物，
// 下载完成后把文件交给本页（桥里按模式暂存），本页再解析元数据。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`（R2）、文案走 `Tr.map`（R3/R4）、
// 只用 `qml/components` 的自研件（R9）、每一个上下文属性读取都判空。

import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "modpackPage"

    //: 桥缺席时整页走错误态，而不是绑一堆 undefined 报错
    readonly property bool live: (typeof Modpack !== "undefined" && Modpack) ? true : false
    readonly property string mode: String(page.routeId || "").indexOf("servers/") === 0 ? "server" : "client"
    readonly property bool serverMode: page.mode === "server"

    //: 服务端安装完成后，核心回报“待人工确认”的模组。旧实现只写日志，用户看不到；
    //: 现在弹一个列表，确认后直接进 3.12 的模组管理页处理。
    property bool modsReviewOpen: false
    property var modsReviewRows: []
    property string modsReviewVersion: ""

    title: page.serverMode ? page.t("mp_server_title") : page.t("mp_install_header")
    description: page.serverMode ? page.t("mp_server_subtitle") : page.t("mp_install_subtitle")
    iconName: "modpack"
    //: 工具区一直在：文件没选时也要能选文件 / 去浏览器下载，所以三态画在内部
    contentState: "ready"

    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    //: 当前页是否在前台（StackView 切页动画期间两页都 visible，必须看路由）
    function isForeground() {
        if (!page.visible)
            return false;
        var route = String(page.routeId || "");
        if (route.length === 0)
            return true;
        if (typeof Nav !== "undefined" && Nav && String(Nav.currentRoute || "").length > 0)
            return String(Nav.currentRoute) === route;
        return true;
    }

    function browseRoute() {
        return page.serverMode ? "servers/modpack/browse" : "versions/modpack/browse";
    }

    function activateFromRoute() {
        if (!page.live)
            return;
        Modpack.activate(page.mode);
    }

    onRouteIdChanged: page.activateFromRoute()
    onRouteParamsChanged: page.activateFromRoute()
    onVisibleChanged: {
        if (page.visible)
            page.activateFromRoute();
    }
    Component.onCompleted: page.activateFromRoute()

    function openFilePicker() {
        if (!page.live || Modpack.busy)
            return;
        fileDialog.open();
    }

    function pickComponent(name) {
        if (page.live)
            Modpack.toggleComponent(String(name));
    }

    function openModsPage() {
        var version = page.modsReviewVersion;
        page.modsReviewOpen = false;
        if (version.length > 0 && typeof Nav !== "undefined" && Nav)
            Nav.push("servers/detail/mods", {"version": version});
    }

    Connections {
        target: page.live ? Modpack : null
        ignoreUnknownSignals: true

        function onStatusMessage(text, level) {
            if (page.isForeground() && typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level));
        }

        function onNoticeRequested(message, level) {
            if (!page.isForeground())
                return;
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(message), "icon": "modpack"});
        }

        //: 浏览器下载完成后返回本页：若当前页已在栈里，activateFromRoute 会认领文件；
        //: 这里再主动调一次，覆盖"页面还没收到 visibleChanged"的时序。
        function onFileReady(target, path) {
            if (String(target) === page.mode && page.isForeground())
                page.activateFromRoute();
        }

        //: 3.12 修复：服务端模组筛选有 unknown 时弹窗。安装本身已经成功，
        //: 这里不重跑筛选、不自动禁用；用户确认后去模组管理逐条判断。
        function onServerModsNeedReview(version, rows, count) {
            if (!page.serverMode || !page.isForeground())
                return;
            if (!rows || rows.length === 0)
                return;
            page.modsReviewVersion = String(version || "");
            page.modsReviewRows = rows;
            page.modsReviewOpen = true;
        }
    }

    FmScrollView {
        id: pageScroll
        anchors.fill: parent
        verticalBarEnabled: true

        ColumnLayout {
            objectName: "modpackContent"
            width: pageScroll.availableWidth
            spacing: Theme?.spacingMd ?? 12

            FmInfoBar {
                objectName: "modpackUnavailableBar"
                Layout.fillWidth: true
                visible: !page.live
                level: "error"
                closable: false
                text: page.t("plugin_state_error")
            }

            // ── 文件选择卡片 ──
            FmCard {
                objectName: "modpackFileCard"
                Layout.fillWidth: true
                title: page.serverMode ? page.t("mp_server_title") : page.t("mp_install_header")
                subtitle: page.serverMode ? page.t("mp_server_subtitle") : page.t("mp_install_subtitle")

                // 没选文件：拖放区 + 两个入口
                ColumnLayout {
                    objectName: "modpackEmptyFileArea"
                    Layout.fillWidth: true
                    visible: page.live && !Modpack.hasFile
                    spacing: Theme?.spacingSm ?? 8

                    FmDropZone {
                        objectName: "modpackFileDrop"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 128
                        iconName: "modpack"
                        text: page.t("mp_no_file_selected")
                        onDropped: function (urls) {
                            if (page.live && urls && urls.length > 0)
                                Modpack.setFileUrl(urls[0]);
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmButton {
                            objectName: "modpackSelectFile"
                            primary: true
                            iconName: "folder"
                            text: page.t("mp_select_file_btn")
                            onClicked: page.openFilePicker()
                        }

                        FmButton {
                            objectName: "modpackBrowseButton"
                            primary: false
                            iconName: "search"
                            text: page.t("mp_download_modrinth")
                            onClicked: {
                                if (page.live)
                                    Nav.push(page.browseRoute(), {"target": page.mode});
                            }
                        }

                        Item { Layout.fillWidth: true }

                        FmTag {
                            objectName: "modpackModeTag"
                            text: page.serverMode ? page.t("mp_server_title") : page.t("mp_install_title")
                            level: page.serverMode ? "warning" : "accent"
                        }
                    }
                }

                // 已选文件：文件名 + 格式 + 操作
                ColumnLayout {
                    objectName: "modpackSelectedFileArea"
                    Layout.fillWidth: true
                    visible: page.live && Modpack.hasFile
                    spacing: Theme?.spacingSm ?? 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmIcon {
                            name: "file"
                            color: Theme?.accent ?? "transparent"
                            size: Theme?.iconSize ?? 20
                            Layout.alignment: Qt.AlignVCenter
                        }

                        Text {
                            objectName: "modpackFileName"
                            Layout.fillWidth: true
                            text: Modpack.fileName
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            elide: Text.ElideMiddle
                        }

                        FmTag {
                            objectName: "modpackFileFormat"
                            visible: Modpack.formatText.length > 0
                            text: Modpack.formatText
                            level: "accent"
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmButton {
                            objectName: "modpackChangeFile"
                            primary: false
                            iconName: "folder"
                            text: page.t("mp_select_file_btn")
                            enabled: page.live && !Modpack.busy
                            onClicked: page.openFilePicker()
                        }

                        FmButton {
                            objectName: "modpackBrowseFromFile"
                            primary: false
                            iconName: "search"
                            text: page.t("mp_download_modrinth")
                            enabled: page.live && !Modpack.busy
                            onClicked: {
                                if (page.live)
                                    Nav.push(page.browseRoute(), {"target": page.mode});
                            }
                        }

                        FmButton {
                            objectName: "modpackClearFile"
                            primary: false
                            iconName: "trash"
                            text: page.t("agent_clear")
                            enabled: page.live && !Modpack.busy
                            onClicked: {
                                if (page.live)
                                    Modpack.clearFile();
                            }
                        }

                        Item { Layout.fillWidth: true }
                    }

                    FmProgressBar {
                        objectName: "modpackInfoLoading"
                        Layout.fillWidth: true
                        visible: Modpack.fileState === "loading"
                        indeterminate: true
                        showPercent: false
                        label: page.t("mp_reading_info")
                    }

                    FmInfoBar {
                        objectName: "modpackInfoError"
                        Layout.fillWidth: true
                        visible: Modpack.fileState === "error"
                        level: "error"
                        closable: false
                        text: page.t("mp_read_error", {"error": Modpack.fileError})
                        actionText: page.t("version_install_retry")
                        onActionTriggered: {
                            if (page.live)
                                Modpack.retryInfo();
                        }
                    }
                }
            }

            // ── 实例命名（正式安装前的第二步）──
            FmCard {
                objectName: "modpackNamingCard"
                Layout.fillWidth: true
                visible: page.live && Modpack.naming && !page.serverMode
                title: page.t("modpack_name")

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmTextField {
                        objectName: "modpackInstanceName"
                        Layout.fillWidth: true
                        placeholder: page.t("rename_instance_prompt")
                        text: Modpack.instanceName
                        errorText: Modpack.instanceNameError
                        onEdited: function (value) {
                            if (page.live)
                                Modpack.setInstanceName(String(value));
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmButton {
                            objectName: "modpackNamingBack"
                            primary: false
                            iconName: "arrow-left"
                            text: page.t("mod_browser_prev_page")
                            onClicked: {
                                if (page.live)
                                    Modpack.backToInfo();
                            }
                        }

                        FmButton {
                            objectName: "modpackNamingNext"
                            primary: true
                            iconName: "download"
                            text: page.t("modpack_start_install")
                            enabled: page.live && Modpack.instanceNameValid
                            onClicked: {
                                if (page.live)
                                    Modpack.install();
                            }
                        }

                        Item { Layout.fillWidth: true }
                    }
                }
            }

            // ── 进度 / 结果 ──
            FmCard {
                objectName: "modpackProgressCard"
                Layout.fillWidth: true
                visible: page.live && Modpack.installTarget === page.mode
                         && (Modpack.busy || String(Modpack.installState).length > 0)
                title: page.t("mp_install_progress_title")

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmProgressBar {
                        objectName: "modpackProgressBar"
                        Layout.fillWidth: true
                        value: Modpack.progress
                        indeterminate: Modpack.busy && Modpack.progress <= 0
                        showPercent: !(Modpack.busy && Modpack.progress <= 0)
                        label: Modpack.progressText
                    }

                    Text {
                        objectName: "modpackMrpackProgress"
                        Layout.fillWidth: true
                        visible: String(Modpack.mrpackText).length > 0
                        text: Modpack.mrpackText
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }

                    Text {
                        objectName: "modpackVanillaProgress"
                        Layout.fillWidth: true
                        visible: String(Modpack.vanillaText).length > 0
                        text: Modpack.vanillaText
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }

                    FmInfoBar {
                        objectName: "modpackResultBar"
                        Layout.fillWidth: true
                        visible: String(Modpack.resultText).length > 0
                        level: String(Modpack.resultLevel || "info")
                        closable: false
                        text: Modpack.resultText
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmButton {
                            objectName: "modpackRetryButton"
                            primary: false
                            iconName: "refresh"
                            text: page.serverMode ? page.t("mp_server_install_retry") : page.t("mp_install_retry")
                            visible: Modpack.resultLevel === "error" && !Modpack.busy
                            onClicked: {
                                if (!page.live)
                                    return;
                                Modpack.resetInstall();
                                Modpack.install();
                            }
                        }

                        FmButton {
                            objectName: "modpackAnotherButton"
                            primary: false
                            iconName: "add"
                            text: page.t("mp_select_file_btn")
                            visible: Modpack.installState === "done" && !Modpack.busy
                            onClicked: {
                                if (!page.live)
                                    return;
                                Modpack.resetInstall();
                                Modpack.clearFile();
                            }
                        }

                        Item { Layout.fillWidth: true }
                    }
                }
            }

            // ── 整合包信息 ──
            FmCard {
                objectName: "modpackInfoCard"
                Layout.fillWidth: true
                visible: page.live && Modpack.hasFile && Modpack.fileState === "ready"
                title: page.t("modpack_info")

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        objectName: "modpackPackName"
                        Layout.fillWidth: true
                        text: Modpack.packName
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    Text {
                        objectName: "modpackSummary"
                        Layout.fillWidth: true
                        visible: Modpack.packInfo.summary !== undefined && String(Modpack.packInfo.summary || "").length > 0
                        text: String(Modpack.packInfo.summary || "")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        Text {
                            objectName: "modpackMcVersion"
                            text: Modpack.mcVersionText
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                        }

                        FmTag {
                            objectName: "modpackFormatTag"
                            visible: Modpack.formatText.length > 0
                            text: Modpack.formatText
                            level: "accent"
                        }

                        Text {
                            objectName: "modpackLoaderText"
                            visible: Modpack.loaderText.length > 0
                            text: Modpack.loaderText
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        Item { Layout.fillWidth: true }
                    }

                    Text {
                        objectName: "modpackDescription"
                        Layout.fillWidth: true
                        visible: Modpack.packInfo.description !== undefined && String(Modpack.packInfo.description || "").length > 0
                        text: String(Modpack.packInfo.description || "")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        wrapMode: Text.WordWrap
                    }

                    // CurseForge 的必装/可选计数（旧实现只显示这一行，不列组件）
                    FmInfoBar {
                        objectName: "modpackCfFilesInfo"
                        Layout.fillWidth: true
                        visible: Number(Modpack.packInfo.required_count || 0) > 0 || Number(Modpack.packInfo.optional_count || 0) > 0
                        level: "info"
                        closable: false
                        text: page.t("mp_cf_files_info", {
                            "required": Number(Modpack.packInfo.required_count || 0),
                            "optional": Number(Modpack.packInfo.optional_count || 0)
                        })
                    }

                    Text {
                        objectName: "modpackComponentsTitle"
                        Layout.fillWidth: true
                        visible: Modpack.components.length > 0
                        text: page.t("mp_optional_components")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    ColumnLayout {
                        objectName: "modpackComponentList"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4
                        visible: Modpack.components.length > 0

                        Repeater {
                            model: Modpack.components

                            delegate: ColumnLayout {
                                id: componentRow
                                Layout.fillWidth: true
                                Layout.maximumHeight: componentRow.implicitHeight
                                spacing: Theme?.spacingXs ?? 4

                                FmCheckBox {
                                    objectName: "modpackComponentCheck-" + String(modelData.name)
                                    Layout.fillWidth: true
                                    visible: modelData.selectable === true
                                    text: String(modelData.name)
                                    checked: modelData.selected === true
                                    onClicked: page.pickComponent(modelData.name)
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    visible: modelData.selectable !== true
                                    spacing: Theme?.spacingSm ?? 8

                                    FmIcon {
                                        name: "file"
                                        color: Theme?.textSecondary ?? "transparent"
                                        size: Theme?.fontSizeSmall ?? 10
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: String(modelData.name)
                                        color: Theme?.textPrimary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeBase ?? 12
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }

                    // ── 服务器模式：名称 + 加载器提示 ──
                    Rectangle {
                        objectName: "modpackServerDivider"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        visible: page.serverMode
                        color: Theme?.cardBorder ?? "transparent"
                    }

                    FmTextField {
                        objectName: "modpackServerName"
                        Layout.fillWidth: true
                        visible: page.serverMode
                        label: page.t("mp_server_name_label")
                        placeholder: page.t("mp_server_name_placeholder")
                        text: Modpack.serverName
                        onEdited: function (value) {
                            if (page.live)
                                Modpack.setServerName(String(value));
                        }
                    }

                    Text {
                        objectName: "modpackServerHint"
                        Layout.fillWidth: true
                        visible: page.serverMode
                        text: page.t("mp_server_loader_hint")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmButton {
                            objectName: "modpackInstallButton"
                            primary: true
                            iconName: "download"
                            text: page.serverMode ? page.t("mp_server_start_install") : page.t("mod_browser_next_page")
                            visible: !(page.live && Modpack.naming)
                            enabled: page.live && Modpack.canInstall
                            loading: Modpack.busy
                            onClicked: {
                                if (!page.live)
                                    return;
                                if (page.serverMode)
                                    Modpack.install();
                                else
                                    Modpack.beginInstallNaming();
                            }
                        }

                        Item { Layout.fillWidth: true }
                    }
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    //: 待确认模组弹窗的宿主（页内浮层；Loader 控制存在性，关掉后不占焦点）
    Item {
        id: reviewOverlay
        objectName: "modpackReviewOverlay"
        anchors.fill: parent
        z: 100

        Loader {
            objectName: "modpackReviewLoader"
            anchors.fill: parent
            active: page.modsReviewOpen
            sourceComponent: reviewComponent
        }
    }

    Component {
        id: reviewComponent

        ModReviewDialog {
            objectName: "modpackModsReviewDialog"
            rows: page.modsReviewRows
            titleText: page.t("server_mods_title")
            hintText: page.t("mod_unknown") + " ("
                      + String((page.modsReviewRows || []).length) + ")"
            disableText: page.t("mod_disable")
            manageText: page.t("server_mod_manage_btn")
            closeText: page.t("close")
            onDisableRequested: function (names) {
                page.modsReviewOpen = false;
                if (page.live && typeof ServerMods !== "undefined" && ServerMods)
                    ServerMods.disableMods(page.modsReviewVersion, names);
            }
            onManageRequested: page.openModsPage()
            onCancelled: page.modsReviewOpen = false
        }
    }

    //: 待确认弹窗里的“禁用”动作走 ServerMods 桥；提示也由它发出。
    //: 只有当前页在前台时才转成 Toast，避免在别的页面重复弹。
    Connections {
        target: (typeof ServerMods !== "undefined" && ServerMods) ? ServerMods : null
        ignoreUnknownSignals: true

        function onNoticeRequested(message, level) {
            if (!page.isForeground())
                return;
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(message), "icon": "mods"});
        }
    }

    FileDialog {
        id: fileDialog
        objectName: "modpackFileDialog"
        title: page.t("mp_select_dialog_title")
        fileMode: FileDialog.OpenFile
        nameFilters: page.serverMode
            ? [page.t("mp_filetype_all_supported") + " (*.mrpack)", page.t("mp_filetype_all") + " (*)"]
            : [page.t("mp_filetype_all_supported") + " (*.mrpack *.zip)",
               "Modrinth (*.mrpack)",
               page.t("mp_filetype_zip") + " (*.zip)",
               page.t("mp_filetype_all") + " (*)"]
        onAccepted: {
            if (page.live)
                Modpack.setFileUrl(selectedFile);
        }
    }
}
