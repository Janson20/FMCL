// BedrockPage.qml —— 基岩版（阶段 3 任务 3.15）。
//
// 三条路由共用这一页：
//   * bedrock            -> 已安装 + 下载面板
//   * bedrock/download   -> 聚焦下载面板（深链/面包屑）
//   * bedrock/detail     -> 选中版本的详情浮层
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon/FmToolButton（R2）、文案一律
// page.t/page.tf（R3/R4）、控件只用 qml/components 的自研件（R9）、所有上下文
// 属性读取先判空，页面销毁期绑定用 page && page.live 短路。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "bedrockPage"

    readonly property bool live: (typeof Bedrock !== "undefined" && Bedrock) ? true : false
    readonly property string routeName: String(page.routeId || "")
    readonly property bool detailRoute: page.routeName.indexOf("/detail") >= 0
    readonly property bool downloadRoute: page.routeName.indexOf("/download") >= 0

    property string deleteName: ""
    property string deleteVersion: ""
    property string deleteBuildType: ""

    title: page.detailRoute ? page.t("bedrock_detail_title") : page.t("bedrock_installed")
    iconName: "bedrock"
    contentState: !(page && page.live) ? "error" : "ready"
    loadingText: page.t("loading_installed")
    emptyText: page.t("bedrock_no_installed")
    errorText: page.t("plugin_state_error")
    errorDetail: (page && page.live) ? String(Bedrock.installedError || "") : ""
    retryText: page.t("refresh")

    onRetried: {
        if (page && page.live)
            Bedrock.refresh();
    }

    // ─── 文案 / 路由 / 小工具 ───────────────────────────────

    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    function tf(key, params) {
        return page.t(key, params);
    }

    function routeParam(name) {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        return String(raw[name] || "");
    }

    function activateRoute() {
        if (!(page && page.live))
            return;
        Bedrock.activate(String(page.routeId || ""), page.routeParam("name"));
        page.syncFromBridge();
        if (page.downloadRoute) {
            Qt.callLater(function () {
                if (page && page.live && searchField)
                    searchField.forceActiveFocus();
            });
        }
    }

    function syncFromBridge() {
        if (!(page && page.live))
            return;
        if (filterCombo && filterCombo.currentIndex !== Bedrock.filterIndex)
            filterCombo.currentIndex = Bedrock.filterIndex;
        if (searchField && !searchField.activeFocus && searchField.text !== Bedrock.searchText)
            searchField.text = Bedrock.searchText;
    }

    function openDetail(name) {
        if (!(page && page.live) || String(name || "").length === 0)
            return;
        if (typeof Nav !== "undefined" && Nav)
            Nav.push("bedrock/detail", {"name": String(name)});
    }

    function closeDetail() {
        if (!(page && page.live))
            return;
        if (page.detailRoute) {
            if (typeof Nav !== "undefined" && Nav) {
                if (!Nav.goBack())
                    Nav.push("bedrock");
            }
        } else {
            Bedrock.clearDetail();
        }
    }

    function requestDelete(row) {
        page.deleteName = String((row && row.name) || "");
        page.deleteVersion = String((row && row.version) || "");
        page.deleteBuildType = String((row && row.buildType) || "");
    }

    function confirmDelete() {
        var name = page.deleteName;
        page.deleteName = "";
        if (name.length > 0 && (page && page.live))
            Bedrock.remove(name);
    }

    function cancelDelete() {
        page.deleteName = "";
    }

    Component.onCompleted: Qt.callLater(function () {
        if (page)
            page.activateRoute();
    })

    onRouteIdChanged: Qt.callLater(function () {
        if (page)
            page.activateRoute();
    })

    onRouteParamsChanged: Qt.callLater(function () {
        if (page)
            page.activateRoute();
    })

    onVisibleChanged: {
        if (page && page.visible)
            Qt.callLater(function () {
                if (page)
                    page.activateRoute();
            });
    }

    Connections {
        target: (typeof Bedrock !== "undefined" && Bedrock) ? Bedrock : null

        function onViewChanged() {
            Qt.callLater(function () {
                if (page && page.live)
                    page.syncFromBridge();
            });
        }

        function onDetailClosed() {
            if (page && page.live && page.detailRoute)
                page.closeDetail();
        }
    }

    // ─── 内容 ───────────────────────────────────────────────

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 0

        FmInfoBar {
            objectName: "bedrockCoreBar"
            Layout.fillWidth: true
            visible: (page && page.live) && !Bedrock.coreReady
            level: "warning"
            text: page.t("bedrock_core_not_ready")
            actionText: page.t("refresh")
            onActionTriggered: {
                if (page && page.live)
                    Bedrock.refresh();
            }
        }

        Rectangle {
            id: msaCard
            objectName: "bedrockMsaCard"
            Layout.fillWidth: true
            visible: (page && page.live) && Bedrock.msaPending
            implicitHeight: msaColumn.implicitHeight + 2 * (Theme?.spacingSm ?? 0)
            color: Theme?.bgLight ?? "transparent"
            radius: Theme?.radiusMd ?? 0
            border.width: 1
            border.color: Theme?.accent ?? "transparent"

            ColumnLayout {
                id: msaColumn
                anchors.fill: parent
                anchors.margins: Theme?.spacingSm ?? 0
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    FmIcon {
                        name: "account"
                        color: Theme?.accent ?? "transparent"
                        size: Theme?.iconSize ?? 0
                    }

                    Text {
                        text: page.t("bedrock_msa_title")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Item { Layout.fillWidth: true }

                    FmButton {
                        primary: false
                        iconName: "copy"
                        text: page.t("bedrock_msa_copy_code")
                        onClicked: {
                            if (page && page.live && String(Bedrock.msaCode).length > 0
                                    && typeof Dialogs !== "undefined" && Dialogs)
                                Dialogs.set_clipboard(String(Bedrock.msaCode));
                        }
                    }

                    FmButton {
                        primary: false
                        iconName: "link"
                        text: page.t("open_link")
                        onClicked: {
                            if (page && page.live && String(Bedrock.msaUri).length > 0)
                                Qt.openUrlExternally(String(Bedrock.msaUri));
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: page.t("bedrock_msa_instruction")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }

                Text {
                    Layout.fillWidth: true
                    text: (page && page.live) ? String(Bedrock.msaCode || "") : ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    font.letterSpacing: 2
                }

                Text {
                    Layout.fillWidth: true
                    text: (page && page.live) ? String(Bedrock.msaUri || "") : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideRight
                }
            }
        }

        RowLayout {
            id: mainRow
            objectName: "bedrockMainRow"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme?.spacingMd ?? 0

            // ─── 左：已安装 ─────────────────────────────────
            Rectangle {
                objectName: "bedrockInstalledPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme?.cardBg ?? "transparent"
                radius: Theme?.radiusMd ?? 0
                border.width: 1
                border.color: Theme?.cardBorder ?? "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme?.spacingSm ?? 0
                    spacing: Theme?.spacingSm ?? 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 0

                        Text {
                            text: page.t("bedrock_installed")
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            font.bold: true
                        }

                        Text {
                            text: (page && page.live)
                                  ? page.tf("bedrock_count", {"count": Bedrock.installedCount})
                                  : ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        Item { Layout.fillWidth: true }

                        FmToolButton {
                            objectName: "bedrockOpenFolderButton"
                            iconName: "folder"
                            side: 28
                            enabled: (page && page.live) && Bedrock.coreReady
                            onClicked: {
                                if (page && page.live)
                                    Bedrock.openFolder();
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Theme?.cardBorder ?? "transparent"
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        ListView {
                            id: installedList
                            objectName: "bedrockInstalledList"
                            anchors.fill: parent
                            clip: true
                            spacing: Theme?.spacingXs ?? 0
                            visible: (page && page.live) && Bedrock.installedState === "ready"
                                     && Bedrock.installedCount > 0
                            model: (page && page.live) ? Bedrock.installed : []
                            ScrollBar.vertical: FmScrollBar {}

                            delegate: Rectangle {
                                id: installedCard
                                required property var modelData
                                width: ListView.view.width
                                height: installedColumn.implicitHeight + 2 * (Theme?.spacingSm ?? 0)
                                color: installedMouse.containsMouse
                                       ? (Theme?.cardHover ?? Theme?.bgLight ?? "transparent")
                                       : (Theme?.bgLight ?? "transparent")
                                radius: Theme?.radiusMd ?? 0
                                border.width: 1
                                border.color: Theme?.cardBorder ?? "transparent"

                                MouseArea {
                                    id: installedMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: page.openDetail(String(modelData.name || ""))
                                }

                                Column {
                                    id: installedColumn
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: Theme?.spacingSm ?? 0
                                    spacing: Theme?.spacingXs ?? 0

                                    RowLayout {
                                        width: parent.width
                                        spacing: Theme?.spacingXs ?? 0

                                        Text {
                                            Layout.fillWidth: true
                                            text: String(modelData.name || "")
                                            color: Theme?.textPrimary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeBase ?? 12
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }

                                        FmTag {
                                            text: String(modelData.buildType || "UWP")
                                            level: "neutral"
                                        }

                                        FmTag {
                                            text: String(modelData.typeText || "")
                                            level: "accent"
                                        }
                                    }

                                    Text {
                                        width: parent.width
                                        text: String(modelData.version || "")
                                        color: Theme?.textSecondary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                                        elide: Text.ElideRight
                                    }

                                    RowLayout {
                                        width: parent.width
                                        spacing: Theme?.spacingXs ?? 0

                                        FmButton {
                                            primary: false
                                            iconName: "play"
                                            text: page.t("bedrock_launch")
                                            enabled: (page && page.live) && Bedrock.coreReady && !Bedrock.busy
                                            loading: (page && page.live) && Bedrock.busyKind === "launch"
                                                     && Bedrock.busyName === String(modelData.name || "")
                                            onClicked: {
                                                if (page && page.live)
                                                    Bedrock.launch(String(modelData.name || ""));
                                            }
                                        }

                                        Item { Layout.fillWidth: true }

                                        FmButton {
                                            primary: false
                                            danger: true
                                            iconName: "trash"
                                            text: page.t("bedrock_delete")
                                            enabled: (page && page.live) && Bedrock.coreReady && !Bedrock.busy
                                            onClicked: page.requestDelete(modelData)
                                        }
                                    }
                                }
                            }
                        }

                        FmLoadingState {
                            anchors.fill: parent
                            visible: (page && page.live)
                                     && (Bedrock.installedState === "idle" || Bedrock.installedState === "loading")
                            title: page.t("loading_installed")
                        }

                        FmEmptyState {
                            anchors.fill: parent
                            visible: (page && page.live) && Bedrock.installedState === "ready"
                                     && Bedrock.installedCount === 0
                            iconName: "bedrock"
                            title: page.t("bedrock_no_installed")
                        }

                        FmErrorState {
                            anchors.fill: parent
                            visible: (page && page.live) && Bedrock.installedState === "error"
                            title: page.t("plugin_state_error")
                            description: (page && page.live)
                                         ? page.t("bedrock_load_error", {"error": Bedrock.installedError})
                                         : ""
                            detailText: (page && page.live) ? String(Bedrock.installedError || "") : ""
                            retryText: page.t("refresh")
                            onRetried: {
                                if (page && page.live)
                                    Bedrock.refresh();
                            }
                        }
                    }
                }
            }


            // ─── 右：下载 ───────────────────────────────────
            Rectangle {
                objectName: "bedrockDownloadPanel"
                Layout.preferredWidth: 430
                Layout.fillHeight: true
                color: Theme?.cardBg ?? "transparent"
                radius: Theme?.radiusMd ?? 0
                border.width: 1
                border.color: Theme?.cardBorder ?? "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme?.spacingSm ?? 0
                    spacing: Theme?.spacingSm ?? 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 0

                        Text {
                            text: page.t("bedrock_available")
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            font.bold: true
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            text: (page && page.live)
                                  ? page.tf("bedrock_available_status", {
                                        "total": Bedrock.availableCount,
                                        "release": Bedrock.releaseCount
                                    })
                                  : ""
                            visible: (page && page.live) && Bedrock.coreReady
                                     && Bedrock.availableState === "ready"
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            elide: Text.ElideRight
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 0

                        FmSearchField {
                            id: searchField
                            objectName: "bedrockSearchField"
                            Layout.fillWidth: true
                            placeholderText: page.t("bedrock_search_placeholder")
                            text: (page && page.live) ? Bedrock.searchText : ""
                            onTextChanged: {
                                if (page && page.live && text !== Bedrock.searchText)
                                    Bedrock.setSearch(text);
                            }
                        }

                        FmComboBox {
                            id: filterCombo
                            objectName: "bedrockFilterCombo"
                            Layout.preferredWidth: 128
                            model: (page && page.live) ? Bedrock.filters : []
                            textRole: "label"
                            valueRole: "key"
                            onActivated: {
                                if (page && page.live)
                                    Bedrock.setBuildFilter(String(currentValue));
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Theme?.cardBorder ?? "transparent"
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        ListView {
                            id: versionList
                            objectName: "bedrockVersionList"
                            anchors.fill: parent
                            clip: true
                            spacing: Theme?.spacingXs ?? 0
                            visible: (page && page.live) && Bedrock.availableState === "ready"
                                     && Bedrock.filteredCount > 0
                            model: (page && page.live) ? Bedrock.versions : []
                            ScrollBar.vertical: FmScrollBar {}

                            delegate: Rectangle {
                                id: versionCard
                                required property var modelData
                                width: ListView.view.width
                                height: versionColumn.implicitHeight + 2 * (Theme?.spacingSm ?? 0)
                                color: String(Bedrock.selectedVersion) === String(modelData.version || "")
                                       ? (Theme?.accentSoft ?? Theme?.bgLight ?? "transparent")
                                       : (versionMouse.containsMouse
                                          ? (Theme?.cardHover ?? Theme?.bgLight ?? "transparent")
                                          : (Theme?.bgLight ?? "transparent"))
                                radius: Theme?.radiusMd ?? 0
                                border.width: 1
                                border.color: String(Bedrock.selectedVersion) === String(modelData.version || "")
                                              ? (Theme?.accent ?? "transparent")
                                              : (Theme?.cardBorder ?? "transparent")

                                MouseArea {
                                    id: versionMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (page && page.live)
                                            Bedrock.selectVersion(String(modelData.version || ""));
                                    }
                                }

                                Rectangle {
                                    width: 3
                                    height: parent.height - 2 * (Theme?.spacingXs ?? 0)
                                    anchors.left: parent.left
                                    anchors.leftMargin: Theme?.spacingXs ?? 0
                                    anchors.verticalCenter: parent.verticalCenter
                                    radius: width / 2
                                    color: Theme?.accent ?? "transparent"
                                    visible: String(Bedrock.selectedVersion) === String(modelData.version || "")
                                }

                                Column {
                                    id: versionColumn
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: Theme?.spacingSm ?? 0
                                    spacing: Theme?.spacingXs ?? 0

                                    RowLayout {
                                        width: parent.width
                                        spacing: Theme?.spacingXs ?? 0

                                        Text {
                                            Layout.fillWidth: true
                                            text: String(modelData.version || "")
                                            color: Theme?.textPrimary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeBase ?? 12
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }

                                        FmTag {
                                            text: String(modelData.buildType || "UWP")
                                            level: "neutral"
                                        }

                                        FmTag {
                                            text: String(modelData.typeText || "")
                                            level: "accent"
                                        }
                                    }

                                    Text {
                                        width: parent.width
                                        text: String(modelData.date || "")
                                        color: Theme?.textSecondary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }

                        FmLoadingState {
                            anchors.fill: parent
                            visible: (page && page.live)
                                     && (Bedrock.availableState === "idle" || Bedrock.availableState === "loading")
                            title: page.t("loading_available")
                        }

                        FmEmptyState {
                            anchors.fill: parent
                            visible: (page && page.live) && Bedrock.availableState === "ready"
                                     && Bedrock.availableCount === 0
                            iconName: "download"
                            title: page.t("bedrock_no_available")
                        }

                        FmEmptyState {
                            anchors.fill: parent
                            visible: (page && page.live) && Bedrock.availableState === "ready"
                                     && Bedrock.availableCount > 0 && Bedrock.filteredCount === 0
                            iconName: "search"
                            title: page.t("bedrock_no_match")
                        }

                        FmErrorState {
                            anchors.fill: parent
                            visible: (page && page.live) && Bedrock.availableState === "error"
                            title: page.t("plugin_state_error")
                            description: (page && page.live)
                                         ? page.t("bedrock_load_error", {"error": Bedrock.availableError})
                                         : ""
                            detailText: (page && page.live) ? String(Bedrock.availableError || "") : ""
                            retryText: page.t("refresh")
                            onRetried: {
                                if (page && page.live)
                                    Bedrock.refresh();
                            }
                        }
                    }

                    FmProgressBar {
                        objectName: "bedrockInstallProgress"
                        Layout.fillWidth: true
                        visible: (page && page.live) && Bedrock.busy && Bedrock.busyKind === "install"
                        label: (page && page.live) ? String(Bedrock.progress.message || "") : ""
                        value: (page && page.live) ? Number(Bedrock.progress.fraction || 0) : 0
                        indeterminate: (page && page.live) ? !!Bedrock.progress.indeterminate : true
                    }

                    FmPagination {
                        objectName: "bedrockPagination"
                        Layout.alignment: Qt.AlignHCenter
                        visible: (page && page.live) && Bedrock.availableState === "ready"
                                 && Bedrock.filteredCount > 0 && Bedrock.pageCount > 1
                        enabled: (page && page.live) && !Bedrock.busy
                        page: (page && page.live) ? Bedrock.page : 1
                        pageCount: (page && page.live) ? Bedrock.pageCount : 1
                        onPageRequested: function (target) {
                            if (page && page.live)
                                Bedrock.goToPage(target);
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 0

                        FmButton {
                            objectName: "bedrockInstallButton"
                            primary: true
                            iconName: "download"
                            Layout.fillWidth: true
                            text: (page && page.live) ? page.t("bedrock_install") : ""
                            enabled: (page && page.live) && Bedrock.canInstall
                            loading: (page && page.live) && Bedrock.busyKind === "install"
                            onClicked: {
                                if (page && page.live)
                                    Bedrock.install();
                            }
                        }

                        FmButton {
                            objectName: "bedrockRefreshButton"
                            primary: false
                            iconName: "refresh"
                            text: page.t("bedrock_refresh")
                            enabled: (page && page.live) && !Bedrock.busy
                            onClicked: {
                                if (page && page.live)
                                    Bedrock.refresh();
                            }
                        }
                    }
                }
            }
        }
    }

    // ─── 详情浮层 ───────────────────────────────────────────

    Loader {
        id: detailLoader
        objectName: "bedrockDetailLoader"
        anchors.fill: parent
        z: 80
        active: (page && page.live) && page.detailRoute
        sourceComponent: detailComponent
    }

    Component {
        id: detailComponent

        Item {
            id: detailRoot
            anchors.fill: parent
            focus: true
            Keys.onEscapePressed: page.closeDetail()

            Rectangle {
                anchors.fill: parent
                color: Theme?.scrim ?? "transparent"

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.AllButtons
                    onClicked: page.closeDetail()
                }
            }

            FmCard {
                id: detailCard
                objectName: "bedrockDetailCard"
                anchors.centerIn: parent
                width: Math.min(560, detailRoot.width - 2 * (Theme?.spacingLg ?? 0))
                title: page.t("bedrock_detail_title")
                subtitle: (page && page.live) && String(Bedrock.detail.name || "").length > 0
                          ? String(Bedrock.detail.name)
                          : ""
                focus: true
                Keys.onEscapePressed: page.closeDetail()

                FmLoadingState {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 120
                    visible: (page && page.live)
                             && (Bedrock.installedState === "idle" || Bedrock.installedState === "loading")
                    title: page.t("loading_installed")
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: (page && page.live) && String(Bedrock.detail.name || "").length > 0
                    spacing: Theme?.spacingSm ?? 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 0

                        FmTag {
                            text: String(Bedrock.detail.buildType || "UWP")
                            level: "neutral"
                        }

                        FmTag {
                            text: String(Bedrock.detail.typeText || "")
                            level: "accent"
                        }

                        Item { Layout.fillWidth: true }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: String(Bedrock.detail.version || "")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 0

                        Text {
                            text: page.t("version_field_path")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        Text {
                            Layout.fillWidth: true
                            text: String(Bedrock.detail.path || "")
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            wrapMode: Text.WrapAnywhere
                        }
                    }
                }

                FmEmptyState {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 160
                    visible: (page && page.live) && Bedrock.installedState === "ready"
                             && String(Bedrock.detail.name || "").length === 0
                    iconName: "bedrock"
                    title: page.t("version_detail_missing")
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    FmButton {
                        primary: false
                        text: page.t("close")
                        onClicked: page.closeDetail()
                    }

                    Item { Layout.fillWidth: true }

                    FmButton {
                        primary: false
                        iconName: "folder"
                        text: page.t("version_open_folder")
                        enabled: (page && page.live) && Bedrock.coreReady
                        onClicked: {
                            if (page && page.live)
                                Bedrock.openFolder();
                        }
                    }

                    FmButton {
                        primary: false
                        danger: true
                        iconName: "trash"
                        text: page.t("bedrock_delete")
                        enabled: (page && page.live) && Bedrock.coreReady && !Bedrock.busy
                                 && String(Bedrock.detail.name || "").length > 0
                        onClicked: page.requestDelete(Bedrock.detail)
                    }

                    FmButton {
                        primary: true
                        iconName: "play"
                        text: page.t("bedrock_launch")
                        enabled: (page && page.live) && Bedrock.coreReady && !Bedrock.busy
                                 && String(Bedrock.detail.name || "").length > 0
                        onClicked: {
                            if (page && page.live)
                                Bedrock.launch(String(Bedrock.detail.name || ""));
                        }
                    }
                }
            }
        }
    }

    // ─── 条款 / .NET / 组件同意 / 删除确认 ─────────────────

    Loader {
        objectName: "bedrockTermsLoader"
        anchors.fill: parent
        z: 90
        active: (page && page.live) && String(Bedrock.pendingTermsVersion).length > 0
        sourceComponent: termsComponent
    }

    Component {
        id: termsComponent

        ConfirmDialog {
            objectName: "bedrockTermsDialog"
            modalScrim: true
            request: ({
                "title": page.t("bedrock_terms_title"),
                "message": page.t("bedrock_terms_msg"),
                "default": false
            })
            onAnswered: function (value) {
                if (value)
                    Bedrock.confirmTerms();
                else
                    Bedrock.cancelTerms();
            }
        }
    }

    Loader {
        objectName: "bedrockDotnetLoader"
        anchors.fill: parent
        z: 90
        active: (page && page.live) && String(Bedrock.pendingDotnetUrl).length > 0
        sourceComponent: dotnetComponent
    }

    Component {
        id: dotnetComponent

        ConfirmDialog {
            objectName: "bedrockDotnetDialog"
            modalScrim: true
            request: ({
                "title": page.t("bedrock_dotnet_title"),
                "message": page.t("bedrock_dotnet_msg"),
                "default": false
            })
            onAnswered: function (value) {
                var url = String(Bedrock.pendingDotnetUrl);
                Bedrock.dismissDotnet();
                if (value && url.length > 0)
                    Qt.openUrlExternally(url);
            }
        }
    }

    Loader {
        objectName: "bedrockComponentLoader"
        anchors.fill: parent
        z: 90
        active: (page && page.live) && String(Bedrock.pendingComponentName).length > 0
        sourceComponent: componentConsentComponent
    }

    Component {
        id: componentConsentComponent

        ConfirmDialog {
            objectName: "bedrockComponentDialog"
            modalScrim: true
            request: ({
                "title": page.t("bedrock_component_title"),
                "message": page.t("bedrock_component_msg"),
                "default": false
            })
            onAnswered: function (value) {
                if (value)
                    Bedrock.confirmComponents();
                else
                    Bedrock.declineComponents();
            }
        }
    }

    Loader {
        objectName: "bedrockDeleteLoader"
        anchors.fill: parent
        z: 90
        active: (page && page.live) && page.deleteName.length > 0
        sourceComponent: deleteComponent
    }

    Component {
        id: deleteComponent

        ConfirmDialog {
            objectName: "bedrockDeleteDialog"
            modalScrim: true
            request: ({
                "title": page.t("confirm_delete"),
                "message": page.tf("bedrock_confirm_delete", {"name": page.deleteName}),
                "default": false
            })
            onAnswered: function (value) {
                if (value)
                    page.confirmDelete();
                else
                    page.cancelDelete();
            }
        }
    }
}

