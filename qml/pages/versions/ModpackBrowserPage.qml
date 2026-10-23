// ModpackBrowserPage.qml —— 整合包在线浏览 / 版本选择（阶段 3 任务 3.8，对照表 L-52）。
//
// 旧实现（`ui/windows/modpack_browser.py`，703 行）是一扇模态子窗口：搜索框 + 结果 +
// 分页，点「安装」再弹一个 620x540 的版本选择子窗口（按 MC 版本分组、每次追加 50 条）。
// 新界面把它拆成**同一页的两种视图**（用户裁决：不再堆第三层路由）：
//
//   * results  —— 搜索 / AI 搜索 / 结果卡片 / 分页（旧窗口那一屏）；
//   * versions —— 某个整合包的版本分组 / 加载更多 / 下载（旧版选择子窗口那一屏）。
//
// 浏览页由安装页的「从 Modrinth 下载」进入；下载完成后回到调用它的安装页
// （`versions/modpack` 或 `servers/modpack`，由路由前缀决定）。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`（R2）、文案走 `Tr.map`（R3/R4）、
// 只用 `qml/components` 的自研件（R9）、每一个上下文属性读取都判空。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"

FmPage {
    id: browserPage
    objectName: "modpackBrowserPage"

    readonly property bool live: (typeof Modpack !== "undefined" && Modpack) ? true : false
    readonly property string target: String(browserPage.routeId || "").indexOf("servers/") === 0 ? "server" : "client"

    title: browserPage.t("mp_browser_title")
    iconName: "search"
    //: 搜索工具条必须一直在（结果为空时也能改词），所以整页永远 ready
    contentState: "ready"

    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    function isForeground() {
        if (!browserPage.visible)
            return false;
        var route = String(browserPage.routeId || "");
        if (route.length === 0)
            return true;
        if (typeof Nav !== "undefined" && Nav && String(Nav.currentRoute || "").length > 0)
            return String(Nav.currentRoute) === route;
        return true;
    }

    function activateFromRoute() {
        if (!browserPage.live)
            return;
        Modpack.activateBrowser(browserPage.target);
        Qt.callLater(browserPage.autoSearch);
    }

    //: 只在"还没搜过"时自动搜一次（从版本视图返回不重复联网）
    function autoSearch() {
        if (!browserPage.live)
            return;
        if (Modpack.browserView !== "results")
            return;
        if (Modpack.loadState === "idle")
            Modpack.search();
    }

    function openVersions(row) {
        if (!browserPage.live)
            return;
        Modpack.openVersions(String(row.project_id || ""), String(row.title || ""));
    }

    function download(versionId) {
        if (!browserPage.live)
            return;
        Modpack.downloadVersion(String(versionId || ""));
    }

    function rangeText() {
        if (!browserPage.live || Modpack.loadState !== "ready")
            return "";
        return browserPage.t("mp_browser_results_count", {
            "start": Modpack.rangeStart,
            "end": Modpack.rangeEnd,
            "total": Modpack.total
        });
    }

    function versionsLoadMoreText() {
        if (!browserPage.live)
            return "";
        if (!Modpack.versionsCanLoadMore)
            return browserPage.t("mp_browser_all_loaded", {"total": Modpack.versionsTotal});
        return browserPage.t("mp_browser_load_more_2", {
            "shown": Modpack.versionsShownActual,
            "total": Modpack.versionsTotal
        });
    }

    onRouteIdChanged: browserPage.activateFromRoute()
    onRouteParamsChanged: browserPage.activateFromRoute()
    onVisibleChanged: {
        if (browserPage.visible)
            browserPage.activateFromRoute();
    }
    Component.onCompleted: browserPage.activateFromRoute()

    Connections {
        target: browserPage.live ? Modpack : null
        ignoreUnknownSignals: true

        function onStatusMessage(text, level) {
            if (browserPage.isForeground() && typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level));
        }

        function onNoticeRequested(message, level) {
            if (!browserPage.isForeground())
                return;
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(message), "icon": "modpack"});
        }

        //: 下载完成后回到调用页（旧实现是关闭下载子窗口并把路径回填给安装窗口）。
        function onFileReady(target, path) {
            if (!browserPage.isForeground())
                return;
            if (String(target) !== browserPage.target)
                return;
            if (typeof Nav !== "undefined" && Nav)
                Nav.goBack();
        }
    }

    ColumnLayout {
        objectName: "modpackBrowserContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        // ── 结果视图：搜索工具条 ──
        RowLayout {
            objectName: "modpackBrowserToolbar"
            Layout.fillWidth: true
            visible: Modpack.browserView === "results"
            spacing: Theme?.spacingSm ?? 8

            FmSearchField {
                id: searchField
                objectName: "modpackBrowserSearchField"
                Layout.fillWidth: true
                placeholderText: browserPage.t("mp_browser_search_placeholder")
                onAccepted: {
                    if (!browserPage.live)
                        return;
                    Modpack.setQuery(String(searchField.text || ""));
                    Modpack.search();
                }
            }

            FmButton {
                objectName: "modpackBrowserSearch"
                primary: true
                iconName: "search"
                text: browserPage.t("mod_browser_search")
                onClicked: {
                    if (!browserPage.live)
                        return;
                    Modpack.setQuery(String(searchField.text || ""));
                    Modpack.search();
                }
            }

            FmButton {
                objectName: "modpackBrowserAi"
                primary: false
                iconName: "bulb"
                enabled: browserPage.live && !Modpack.aiBusy
                text: (browserPage.live && Modpack.aiBusy) ? browserPage.t("ai_search_optimizing")
                                                            : browserPage.t("ai_search_btn")
                onClicked: {
                    if (!browserPage.live)
                        return;
                    Modpack.setQuery(String(searchField.text || ""));
                    Modpack.aiSearch();
                }
            }
        }

        // ── 结果视图：计数 / AI 关键词 ──
        RowLayout {
            objectName: "modpackBrowserStatusRow"
            Layout.fillWidth: true
            visible: Modpack.browserView === "results"
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "modpackBrowserCount"
                text: browserPage.rangeText()
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
            }

            Item { Layout.fillWidth: true }

            Text {
                objectName: "modpackBrowserKeywords"
                visible: String(Modpack.aiKeywords.join(", ")).length > 0
                text: Modpack.aiKeywords.join(", ")
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
                Layout.maximumWidth: 360
            }
        }

        // ── 结果视图：搜索进度 / 错误 ──
        FmProgressBar {
            objectName: "modpackBrowserSearchProgress"
            Layout.fillWidth: true
            visible: Modpack.browserView === "results" && Modpack.loadState === "loading"
            indeterminate: true
            showPercent: false
            label: browserPage.t("mp_browser_searching")
        }

        FmInfoBar {
            objectName: "modpackBrowserError"
            Layout.fillWidth: true
            visible: Modpack.browserView === "results" && Modpack.loadState === "error"
            level: "error"
            closable: false
            text: browserPage.t("mp_browser_search_error_status", {"error": Modpack.loadError})
        }

        FmInfoBar {
            objectName: "modpackBrowserAiHint"
            Layout.fillWidth: true
            visible: Modpack.browserView === "results" && !Modpack.aiAvailable
            level: "info"
            closable: false
            text: browserPage.t("ai_search_login_required")
        }

        // ── 结果列表 ──
        ListView {
            id: resultList
            objectName: "modpackBrowserResultList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: Modpack.browserView === "results" && Modpack.items.length > 0
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: Modpack.items
            ScrollBar.vertical: FmScrollBar {}

            delegate: Item {
                id: resultRow
                objectName: "modpackBrowserResult-" + String(resultRow.row.project_id || "")
                width: resultList.width
                height: resultCard.implicitHeight + (Theme?.spacingXs ?? 4)

                readonly property var row: (modelData === undefined) ? ({}) : modelData

                FmCard {
                    id: resultCard
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    padding: Theme?.spacingMd ?? 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingMd ?? 12

                        Image {
                            objectName: "modpackBrowserIcon-" + String(resultRow.row.project_id || "")
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            visible: String(resultRow.row.icon_url || "").length > 0 && status === Image.Ready
                            source: String(resultRow.row.icon_url || "")
                            sourceSize.width: 48
                            sourceSize.height: 48
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }

                        FmIcon {
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            visible: !(String(resultRow.row.icon_url || "").length > 0)
                            name: "modpack"
                            color: Theme?.textSecondary ?? "transparent"
                            size: 32
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                objectName: "modpackBrowserTitle-" + String(resultRow.row.project_id || "")
                                Layout.fillWidth: true
                                text: String(resultRow.row.title || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "modpackBrowserDesc-" + String(resultRow.row.project_id || "")
                                Layout.fillWidth: true
                                visible: String(resultRow.row.description || "").length > 0
                                text: String(resultRow.row.description || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                Text {
                                    objectName: "modpackBrowserMeta-" + String(resultRow.row.project_id || "")
                                    text: String(resultRow.row.versions_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    objectName: "modpackBrowserDownloads-" + String(resultRow.row.project_id || "")
                                    text: String(resultRow.row.downloads_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }
                            }
                        }

                        FmButton {
                            objectName: "modpackBrowserInstall-" + String(resultRow.row.project_id || "")
                            primary: true
                            iconName: "download"
                            text: browserPage.t("mp_browser_install_btn")
                            Layout.alignment: Qt.AlignTop
                            onClicked: browserPage.openVersions(resultRow.row)
                        }
                    }
                }
            }
        }

        Text {
            objectName: "modpackBrowserPlaceholder"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: Modpack.browserView === "results" && Modpack.items.length === 0 && Modpack.loadState !== "loading"
            text: Modpack.loadState === "idle" ? browserPage.t("mp_browser_ready")
                                                : browserPage.t("mp_browser_no_results")
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.WordWrap
        }

        FmPagination {
            objectName: "modpackBrowserPagination"
            Layout.alignment: Qt.AlignHCenter
            visible: Modpack.browserView === "results" && Modpack.pageCount > 1
            page: Modpack.page
            pageCount: Modpack.pageCount
            onPageRequested: function (target) {
                if (browserPage.live)
                    Modpack.goto(target);
            }
        }

        // ── 版本视图（旧版选择子窗口） ──
        ColumnLayout {
            objectName: "modpackBrowserVersions"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: Modpack.browserView === "versions"
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "modpackBrowserBack"
                    primary: false
                    iconOnly: true
                    iconName: "arrow-left"
                    text: ""
                    onClicked: {
                        if (browserPage.live)
                            Modpack.backToResults();
                    }
                }

                Text {
                    objectName: "modpackBrowserVersionsTitle"
                    Layout.fillWidth: true
                    text: Modpack.versionsTitle
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmTag {
                    objectName: "modpackBrowserVersionsTotal"
                    text: browserPage.t("mp_browser_versions_found", {"count": Modpack.versionsTotal})
                    level: "accent"
                }
            }

            FmInfoBar {
                objectName: "modpackBrowserVersionsError"
                Layout.fillWidth: true
                visible: Modpack.versionsState === "error"
                level: "error"
                closable: false
                text: browserPage.t("mp_browser_search_error_status", {"error": Modpack.versionsError})
                actionText: browserPage.t("version_install_retry")
                onActionTriggered: {
                    if (browserPage.live)
                        Modpack.retryVersions();
                }
            }

            FmProgressBar {
                objectName: "modpackBrowserVersionsLoading"
                Layout.fillWidth: true
                visible: Modpack.versionsState === "loading"
                indeterminate: true
                showPercent: false
                label: browserPage.t("mp_browser_loading")
            }

            Text {
                objectName: "modpackBrowserVersionsEmpty"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: Modpack.versionsState === "empty"
                text: browserPage.t("mp_browser_no_versions", {"title": Modpack.versionsTitle})
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.WordWrap
            }

            ListView {
                id: versionList
                objectName: "modpackBrowserVersionList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: Modpack.versionsState === "ready" && Modpack.versionRows.length > 0
                clip: true
                spacing: Theme?.spacingXs ?? 4
                model: Modpack.versionRows
                ScrollBar.vertical: FmScrollBar {}

                delegate: Item {
                    id: versionRow
                    objectName: "modpackBrowserVersionRow-" + String(versionRow.dataRow.kind || "")
                    width: versionList.width
                    height: versionRow.isHeader ? headerRow.implicitHeight + (Theme?.spacingXs ?? 4)
                                               : versionCard.implicitHeight + (Theme?.spacingXs ?? 4)

                    readonly property var dataRow: (modelData === undefined) ? ({}) : modelData
                    readonly property bool isHeader: String(versionRow.dataRow.kind) === "header"

                    RowLayout {
                        id: headerRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.rightMargin: Theme?.spacingMd ?? 12
                        anchors.top: parent.top
                        visible: versionRow.isHeader
                        spacing: Theme?.spacingSm ?? 8

                        FmIcon {
                            name: "modpack"
                            color: Theme?.success ?? "transparent"
                            size: Theme?.iconSize ?? 20
                        }

                        Text {
                            objectName: "modpackBrowserVersionHeader"
                            Layout.fillWidth: true
                            text: browserPage.t("mp_mc_version", {"version": String(versionRow.dataRow.header || "")})
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            font.bold: true
                            elide: Text.ElideRight
                        }

                        Text {
                            objectName: "modpackBrowserVersionHeaderCount"
                            text: String(versionRow.dataRow.countText || "")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }

                    FmCard {
                        id: versionCard
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        visible: !versionRow.isHeader
                        padding: Theme?.spacingMd ?? 12

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Theme?.spacingXs ?? 4

                                Text {
                                    objectName: "modpackBrowserVersionName"
                                    Layout.fillWidth: true
                                    text: String(versionRow.dataRow.version_number || "")
                                    color: Theme?.textPrimary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeBase ?? 12
                                    font.bold: true
                                    elide: Text.ElideRight
                                }

                                Text {
                                    objectName: "modpackBrowserVersionMeta"
                                    Layout.fillWidth: true
                                    text: String(versionRow.dataRow.date || "") + "  " + String(versionRow.dataRow.game_versions || "")
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    elide: Text.ElideRight
                                }
                            }

                            FmButton {
                                objectName: "modpackBrowserDownload"
                                primary: true
                                iconName: "download"
                                text: browserPage.t("mp_browser_download_btn")
                                enabled: browserPage.live && !Modpack.downloading && versionRow.dataRow.downloadable === true
                                onClicked: browserPage.download(versionRow.dataRow.id)
                            }
                        }
                    }
                }
            }

            FmProgressBar {
                objectName: "modpackBrowserDownloadProgress"
                Layout.fillWidth: true
                visible: Modpack.downloading
                indeterminate: true
                showPercent: false
                label: browserPage.t("mp_browser_loading_status")
            }

            FmButton {
                objectName: "modpackBrowserLoadMore"
                Layout.alignment: Qt.AlignHCenter
                primary: false
                iconName: "add"
                text: browserPage.versionsLoadMoreText()
                visible: Modpack.versionsState === "ready"
                enabled: Modpack.versionsCanLoadMore
                onClicked: {
                    if (browserPage.live)
                        Modpack.loadMoreVersions();
                }
            }
        }
    }
}
