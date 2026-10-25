// ServerModBrowserPage.qml —— 服务端模组在线浏览（阶段 3 任务 3.12 / L-51）。
//
// 旧实现 ``ui/windows/server_mod_browser.py`` 是 800x680 的模态子窗口：搜索框 +
// AI 搜索 + 结果卡片 + 后端分页。新界面按 02 §4.4 把它挂在
// `servers/detail/mods/browser`（L3，服务器模组页的下一层），不占一级导航。
//
// 与 3.7 客户端浏览页刻意不同：
//   * **只有服务端模组**，没有模组/资源包/光影三页签 —— 旧服务端窗口也只有一种；
//   * 不分 CurseForge（旧服务端搜索只走 Modrinth 的 `search_server_mods`）；
//   * 安装目标固定为当前服务器的 `mods/`，不问目录。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon（R2）、文案走 Tr.map / Tr.tf
// （R3/R4）、控件只用 qml/components 的自研件（R9）、每个上下文属性读取都判空。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "serverModBrowserPage"

    readonly property bool live: (typeof ServerModBrowser !== "undefined" && ServerModBrowser) ? true : false
    readonly property var rows: page.live ? ServerModBrowser.items : []
    readonly property string version: page.live ? String(ServerModBrowser.versionId || "") : ""
    readonly property string gameVersion: page.live ? String(ServerModBrowser.gameVersion || "") : ""
    readonly property string loader: page.live ? String(ServerModBrowser.loader || "") : ""

    title: page.live
           ? page.t("server_mod_browser_title", {"version": page.version})
           : page.t("server_mod_browser_title", {"version": ""})
    description: page.t("server_mod_browser_server_only")
    iconName: "search"
    //: 工具条必须一直在：搜索失败/无结果都画在结果区内部（与 3.7 浏览页同一理由）
    contentState: "ready"

    function wantedVersion() {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        return String(raw.version || "");
    }

    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

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

    function activateFromRoute() {
        if (!page.live)
            return;
        ServerModBrowser.activate(page.wantedVersion());
        //: 页面创建与 routeParams 写入是两步；callLater 合并同一轮的两次激活。
        Qt.callLater(page.autoSearch);
    }

    function autoSearch() {
        if (page.live && ServerModBrowser.loadState === "idle")
            ServerModBrowser.search("");
    }

    function contextText() {
        var parts = [];
        if (page.gameVersion.length > 0)
            parts.push("MC " + page.gameVersion);
        if (page.loader.length > 0)
            parts.push(page.loader);
        parts.push(page.t("server_mod_browser_server_only"));
        return parts.join(" | ");
    }

    function rangeText() {
        if (!page.live || ServerModBrowser.loadState !== "ready" || ServerModBrowser.total <= 0)
            return "";
        return page.t("mod_browser_result_range", {
            "start": ServerModBrowser.rangeStart,
            "end": ServerModBrowser.rangeEnd,
            "total": ServerModBrowser.total
        });
    }

    function placeholderText() {
        if (!page.live)
            return "";
        if (ServerModBrowser.loadState === "idle")
            return page.t("browse_search_hint");
        if (ServerModBrowser.loadState === "empty")
            return page.t("mod_browser_no_mods");
        return "";
    }

    function research(ai) {
        if (!page.live)
            return;
        if (ai === true)
            ServerModBrowser.aiSearch();
        else
            ServerModBrowser.search(String(searchField.text || ""));
    }

    onRouteParamsChanged: page.activateFromRoute()
    onRouteIdChanged: page.activateFromRoute()
    onVisibleChanged: {
        if (page.visible)
            page.activateFromRoute();
    }
    Component.onCompleted: page.activateFromRoute()

    Connections {
        target: page.live ? ServerModBrowser : null
        ignoreUnknownSignals: true

        function onStatusMessage(text, level) {
            if (page.isForeground() && typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level));
        }

        function onNoticeRequested(message, level) {
            if (!page.isForeground())
                return;
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(message), "icon": "mods"});
        }
    }

    ColumnLayout {
        objectName: "serverModBrowserContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        RowLayout {
            objectName: "serverModBrowserContextRow"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmIcon {
                objectName: "serverModBrowserContextIcon"
                name: "info"
                color: Theme?.textSecondary ?? "transparent"
                size: Theme?.fontSizeBase ?? 12
                Layout.alignment: Qt.AlignVCenter
            }

            Text {
                objectName: "serverModBrowserContextText"
                Layout.fillWidth: true
                text: page.contextText()
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }
        }

        RowLayout {
            objectName: "serverModBrowserToolbar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmSearchField {
                id: searchField
                objectName: "serverModBrowserSearchField"
                Layout.fillWidth: true
                placeholderText: page.t("server_mod_browser_search_placeholder")
                onAccepted: page.research(false)
            }

            FmButton {
                objectName: "serverModBrowserSearchButton"
                primary: true
                iconName: "search"
                text: page.t("mod_browser_search")
                enabled: page.live && !ServerModBrowser.busy
                onClicked: page.research(false)
            }

            FmButton {
                objectName: "serverModBrowserAiButton"
                primary: false
                iconName: "bulb"
                text: (page.live && ServerModBrowser.aiBusy)
                      ? page.t("ai_search_optimizing")
                      : page.t("browse_ai_search")
                enabled: page.live && !ServerModBrowser.busy
                onClicked: page.research(true)
            }
        }

        RowLayout {
            objectName: "serverModBrowserStatusRow"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "serverModBrowserRange"
                text: page.rangeText()
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            Text {
                objectName: "serverModBrowserKeywords"
                visible: page.live && ServerModBrowser.aiKeywords.length > 0
                text: page.live ? ServerModBrowser.aiKeywords.join(", ") : ""
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
                Layout.maximumWidth: 320
                Layout.alignment: Qt.AlignVCenter
            }
        }

        FmProgressBar {
            objectName: "serverModBrowserProgress"
            Layout.fillWidth: true
            visible: page.live && (ServerModBrowser.searching || ServerModBrowser.aiBusy
                                   || ServerModBrowser.installing || ServerModBrowser.progressVisible)
            indeterminate: page.live && (ServerModBrowser.searching || ServerModBrowser.aiBusy)
            value: 0
            label: page.live
                   ? (ServerModBrowser.installing
                      ? (ServerModBrowser.progressText.length > 0
                         ? ServerModBrowser.progressText
                         : page.t("mod_installing"))
                      : (ServerModBrowser.aiBusy ? page.t("ai_search_optimizing")
                                                 : page.t("mod_browser_searching")))
                   : ""
        }

        FmInfoBar {
            objectName: "serverModBrowserErrorBar"
            Layout.fillWidth: true
            visible: page.live && ServerModBrowser.loadState === "error"
            level: "error"
            closable: false
            text: page.t("mod_browser_search_failed_status", {
                "error": page.live ? String(ServerModBrowser.loadError || "") : ""
            })
        }

        FmInfoBar {
            objectName: "serverModBrowserTargetBar"
            Layout.fillWidth: true
            visible: page.live && !ServerModBrowser.canInstall
            level: "warning"
            closable: false
            text: page.gameVersion.length === 0
                  ? page.t("mod_browser_unknown_version")
                  : page.t("mod_browser_unknown_loader")
        }

        FmInfoBar {
            objectName: "serverModBrowserAiHint"
            Layout.fillWidth: true
            visible: page.live && !ServerModBrowser.aiAvailable
            level: "info"
            closable: false
            text: page.t("ai_search_login_required")
        }

        ListView {
            id: serverModBrowserList
            objectName: "serverModBrowserList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Theme?.spacingXs ?? 4
            visible: page.live && ServerModBrowser.items.length > 0
            model: page.live ? ServerModBrowser.items : []
            ScrollBar.vertical: FmScrollBar {}

            delegate: Item {
                id: browserRow
                objectName: "serverModBrowserCard-" + String(browserRow.row.project_id || "")
                width: serverModBrowserList.width
                height: browserCard.implicitHeight + (Theme?.spacingXs ?? 4)

                readonly property var row: (modelData === undefined) ? ({}) : modelData

                FmCard {
                    id: browserCard
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    padding: Theme?.spacingMd ?? 12

                    RowLayout {
                        objectName: "serverModBrowserCardRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingMd ?? 12

                        Image {
                            objectName: "serverModBrowserIcon-" + String(browserRow.row.project_id || "")
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            visible: String(browserRow.row.icon_url || "").length > 0 && status === Image.Ready
                            source: String(browserRow.row.icon_url || "")
                            sourceSize.width: 48
                            sourceSize.height: 48
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }

                        FmIcon {
                            objectName: "serverModBrowserFallbackIcon-" + String(browserRow.row.project_id || "")
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            visible: !(String(browserRow.row.icon_url || "").length > 0)
                            name: String(browserRow.row.type_icon || "mods")
                            color: Theme?.textSecondary ?? "transparent"
                            size: 32
                        }

                        ColumnLayout {
                            objectName: "serverModBrowserInfo"
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                objectName: "serverModBrowserTitle-" + String(browserRow.row.project_id || "")
                                Layout.fillWidth: true
                                text: String(browserRow.row.title || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "serverModBrowserDescription-" + String(browserRow.row.project_id || "")
                                Layout.fillWidth: true
                                visible: String(browserRow.row.description || "").length > 0
                                text: String(browserRow.row.description || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            RowLayout {
                                objectName: "serverModBrowserMeta"
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                Text {
                                    objectName: "serverModBrowserLoaders-" + String(browserRow.row.project_id || "")
                                    visible: String(browserRow.row.loaders_text || "").length > 0
                                    text: String(browserRow.row.loaders_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }

                                Text {
                                    objectName: "serverModBrowserVersions-" + String(browserRow.row.project_id || "")
                                    visible: String(browserRow.row.versions_text || "").length > 0
                                    text: String(browserRow.row.versions_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    objectName: "serverModBrowserDownloads-" + String(browserRow.row.project_id || "")
                                    text: String(browserRow.row.downloads_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }
                            }
                        }

                        FmButton {
                            objectName: "serverModBrowserInstall-" + String(browserRow.row.project_id || "")
                            primary: true
                            iconName: "download"
                            text: page.t("mod_browser_install")
                            enabled: page.live && !ServerModBrowser.busy && ServerModBrowser.canInstall
                            onClicked: {
                                if (page.live)
                                    ServerModBrowser.install(String(browserRow.row.project_id || ""),
                                                             String(browserRow.row.title || ""));
                            }
                        }
                    }
                }
            }
        }

        Text {
            objectName: "serverModBrowserPlaceholder"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.live && ServerModBrowser.items.length === 0
                     && ServerModBrowser.loadState !== "loading"
            text: page.placeholderText()
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.WordWrap
        }

        FmPagination {
            objectName: "serverModBrowserPagination"
            Layout.alignment: Qt.AlignHCenter
            visible: page.live && ServerModBrowser.pageCount > 1
            page: page.live ? ServerModBrowser.page : 1
            pageCount: page.live ? ServerModBrowser.pageCount : 1
            onPageRequested: function (target) {
                if (page.live)
                    ServerModBrowser.goto(Number(target));
            }
        }
    }
}
