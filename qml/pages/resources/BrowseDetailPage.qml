// BrowseDetailPage.qml —— 在线资源详情（L2；阶段 3 任务 3.7，对照表 L-56）。
//
// ## 这一页是新功能
//
// 旧实现**没有**详情页：结果卡片上直接「安装」，装的是"最新兼容版本"。
// 用户 2026-10-17 裁决「三页 + 详情页本轮一并做」，于是本页承担：
//
//   * 项目信息（图标 / 标题 / 来源 / 作者 / 简介 / 下载量）；
//   * **版本选择**：该项目在当前游戏版本 + 加载器下的文件列表（`ModBrowser.detailVersions`）；
//   * **依赖预览**：所选版本的必需 / 可选依赖（`ModBrowser.detailDeps`，名字由服务层限量解析）；
//   * 「安装此版本」：装**选中的那个版本**（含依赖递归；旧实现只能装最新版本）。
//
// ## 路由参数
//
// `source` / `projectId` / `tab` / `title` / `version` / `sourceLoader`。
// 与另两页同一纪律：参数在 `Component.onCompleted` 之后才写进来，只能读**原始**
// `routeParams`（`16` §二十六）。
//
// ## 边界
//
// * 拿不到项目（被下架 / 网络失败）→ 错误态 + 重试；
// * 项目没有任何兼容版本 → 空态（不显示安装按钮）；
// * 依赖预览失败只影响那一块，不影响安装（安装时服务层自己会再拉依赖）。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "browseDetailPage"

    readonly property bool live: (typeof ModBrowser !== "undefined" && ModBrowser) ? true : false
    readonly property var project: page.live ? ModBrowser.detailProject : ({})

    title: page.live && String(ModBrowser.detailTitle || "").length > 0
           ? String(ModBrowser.detailTitle)
           : page.t("resource_detail_title")
    description: page.live ? page.t(String(ModBrowser.tabLabelKey)) : ""
    iconName: "file"
    loadingText: page.t("mod_browser_loading")
    emptyText: page.t("mod_browser_no_results")
    errorText: page.t("mod_browser_search_failed")
    errorDetail: page.live ? String(ModBrowser.detailError || "") : ""
    retryText: page.t("refresh")

    contentState: !page.live ? "error"
                  : (ModBrowser.detailState === "ready" ? "ready"
                  : (ModBrowser.detailState === "error" ? "error"
                  : (ModBrowser.detailState === "empty" ? "empty" : "loading")))


    onRetried: {
        if (page.live)
            ModBrowser.retryDetail();
    }

    function param(name) {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        var value = raw[name];
        return (value === undefined || value === null) ? "" : String(value);
    }

    //: 已经为哪一组路由参数发过请求（防止 Component.onCompleted / onRouteIdChanged /
    //: onRouteParamsChanged 三条路径各发一次；更重要的是**项目 ID 还没写进来时不能发**）。
    property string activatedKey: ""

    onRouteParamsChanged: page.activateFromRoute()
    onRouteIdChanged: page.activateFromRoute()
    Component.onCompleted: page.activateFromRoute()

    function activateFromRoute() {
        if (!page.live)
            return;
        var raw = page.routeParams;
        //: 页面是 `createObject(null)` 建的：`Component.onCompleted` 时 `routeParams` 还是
        //: 空对象，此时发 `openDetail(..., "", ...)` 会拿空项目 ID 发一个必然失败的请求；
        //: 它晚到的错误还会覆盖随后正确请求的结果 —— 用户看到「进详情显示搜索失败，
        //: 刷新就正常」（2026-10-23）。只认**已经带 projectId 键**的参数，真正赋值时
        //: `onRouteParamsChanged` 会再调一次；`activatedKey` 再把重复调用合并掉。
        if (raw === undefined || raw === null
                || raw.projectId === undefined || raw.projectId === null)
            return;
        var source = page.param("source") || "modrinth";
        var projectId = page.param("projectId");
        var tab = page.param("tab");
        var version = page.param("version");
        var sourceLoader = page.param("sourceLoader");
        var key = [source, projectId, tab, version, sourceLoader].join("|");
        if (key === page.activatedKey)
            return;
        page.activatedKey = key;
        ModBrowser.activate(version, sourceLoader);
        ModBrowser.openDetail(source, projectId, tab, page.param("title"));
    }

    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    //: 当前页是否在前台（见 onDirectoryRequested 的说明）。
    //: 切页动画期间 StackView 会让两页同时 visible，所以必须再看路由：只有路由
    //: 与自己的 `routeId` 相同的那一页才响应目录请求。
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

    Connections {
        target: page.live ? ModBrowser : null
        ignoreUnknownSignals: true

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level));
        }

        function onNoticeRequested(message, level) {
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(message), "icon": "download"});
        }

        //: 只有**当前路由那一页**弹框：两页都活着（详情页压在浏览页上），都听同一个桥信号
        //: —— 不判就会同时弹出两个文件夹选择框（用户 2026-10-23 反馈）。
        //: 不能只判 `visible`：StackView 切页动画期间两页的 visible 都是 true（实测
        //: 0–300ms），信号在这时到达仍会弹两个。路由才是"哪一页在前台"的唯一真相。
        function onDirectoryRequested(kind, defaultDir, defaultUrl) {
            if (!page.isForeground())
                return;
            //: `currentFolder` 才是初始位置；`selectedFolder` 是用户选择的结果，
            //: 在 `open()` 前设置它不会改变起始目录（实测）。
            dirDialog.currentFolder = defaultUrl;
            dirDialog.open();
        }
    }

    ColumnLayout {
        objectName: "browseDetailContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        // ── 项目信息 ──
        FmCard {
            objectName: "browseDetailProjectCard"
            Layout.fillWidth: true
            padding: Theme?.spacingMd ?? 12

            RowLayout {
                objectName: "browseDetailProjectRow"
                Layout.fillWidth: true
                spacing: Theme?.spacingMd ?? 12

                Image {
                    objectName: "browseDetailIcon"
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 64
                    Layout.alignment: Qt.AlignTop
                    visible: String(page.project.icon_url || "").length > 0 && status === Image.Ready
                    source: String(page.project.icon_url || "")
                    sourceSize.width: 64
                    sourceSize.height: 64
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }

                FmIcon {
                    objectName: "browseDetailFallbackIcon"
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 64
                    Layout.alignment: Qt.AlignTop
                    visible: !(String(page.project.icon_url || "").length > 0)
                    name: page.live ? String(ModBrowser.tab === "mods" ? "mods"
                                          : (ModBrowser.tab === "resourcepacks" ? "resourcepack" : "shaderpack")) : "mods"
                    color: Theme?.textSecondary ?? "transparent"
                    size: 40
                }

                ColumnLayout {
                    objectName: "browseDetailInfo"
                    Layout.fillWidth: true
                    spacing: Theme?.spacingXs ?? 4

                    Text {
                        objectName: "browseDetailTitle"
                        Layout.fillWidth: true
                        text: String(page.project.title || "")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeTitle ?? 18
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    RowLayout {
                        objectName: "browseDetailMeta"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmTag {
                            objectName: "browseDetailSource"
                            text: String(page.project.source || "") === "curseforge" ? "CurseForge" : "Modrinth"
                            level: String(page.project.source || "") === "curseforge" ? "warning" : "accent"
                        }

                        Text {
                            objectName: "browseDetailAuthor"
                            visible: String(page.project.author || "").length > 0
                            text: String(page.project.author || "")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        Text {
                            objectName: "browseDetailDownloads"
                            visible: page.live
                            text: page.t("browse_downloads") + ": "
                                  + String(page.project.downloads === undefined ? 0 : page.project.downloads)
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }

                    Text {
                        objectName: "browseDetailDesc"
                        Layout.fillWidth: true
                        visible: String(page.project.description || "").length > 0
                        text: String(page.project.description || "")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                        maximumLineCount: 4
                        elide: Text.ElideRight
                    }
                }
            }
        }

        FmProgressBar {
            objectName: "browseDetailProgress"
            Layout.fillWidth: true
            visible: page.live && ModBrowser.progressVisible
            indeterminate: page.live && ModBrowser.progressValue <= 0
            value: page.live ? ModBrowser.progressValue : 0
            label: page.live ? String(ModBrowser.progressText) : ""
        }

        // ── 版本选择 ──
        Text {
            objectName: "browseDetailVersionsTitle"
            text: page.t("browse_versions_title")
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            font.bold: true
        }

        ListView {
            id: versionList
            objectName: "browseDetailVersionList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 120
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: page.live ? ModBrowser.detailVersions : []
            ScrollBar.vertical: FmScrollBar {}

            delegate: FmListItem {
                id: versionRow
                objectName: "browseDetailVersion-" + String(versionRow.row.id || "")
                readonly property var row: (modelData === undefined) ? ({}) : modelData
                width: versionList.width
                clickable: true
                selected: page.live && String(ModBrowser.detailSelectedId || "") === String(versionRow.row.id || "")
                title: String(versionRow.row.name || versionRow.row.filename || "")
                subtitle: {
                    var parts = [];
                    if (String(versionRow.row.game_versions_text || "").length > 0)
                        parts.push(String(versionRow.row.game_versions_text));
                    if (String(versionRow.row.loaders_text || "").length > 0)
                        parts.push(String(versionRow.row.loaders_text));
                    if (String(versionRow.row.size_text || "").length > 0)
                        parts.push(String(versionRow.row.size_text));
                    if (versionRow.row.deps_count > 0)
                        parts.push(page.t("browse_deps_title") + ": " + String(versionRow.row.deps_count));
                    return parts.join("  ·  ");
                }
                iconName: String(versionRow.row.release_type || "") === "release" ? "check" : "snapshot"
                onClicked: {
                    if (page.live)
                        ModBrowser.selectVersion(String(versionRow.row.id || ""));
                }
            }
        }

        // ── 依赖预览 ──
        Text {
            objectName: "browseDetailDepsTitle"
            text: page.t("browse_deps_title")
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            font.bold: true
        }

        FmInfoBar {
            objectName: "browseDetailDepsLoading"
            Layout.fillWidth: true
            visible: page.live && ModBrowser.detailDepsLoading
            level: "info"
            closable: false
            text: page.t("mod_browser_loading")
        }

        Text {
            objectName: "browseDetailDepsEmpty"
            visible: page.live && !ModBrowser.detailDepsLoading && ModBrowser.detailDeps.length === 0
            text: page.t("browse_deps_empty")
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        Flow {
            objectName: "browseDetailDepsFlow"
            Layout.fillWidth: true
            spacing: Theme?.spacingXs ?? 4
            visible: page.live && ModBrowser.detailDeps.length > 0

            Repeater {
                model: page.live ? ModBrowser.detailDeps : []

                delegate: FmTag {
                    objectName: "browseDetailDep-" + String(modelData.project_id || "")
                    text: (String(modelData.name || "").length > 0 ? String(modelData.name)
                                                                  : String(modelData.project_id || ""))
                          + " · " + page.t(String(modelData.kindKey || "browse_dep_required"))
                    level: String(modelData.kind || "") === "required" ? "warning" : "neutral"
                }
            }
        }

        // ── 安装 ──
        RowLayout {
            objectName: "browseDetailActions"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Item { Layout.fillWidth: true }

            FmButton {
                objectName: "browseDetailInstall"
                primary: true
                iconName: "download"
                text: page.t("browse_install_this")
                enabled: page.live && !ModBrowser.busy
                        && String(ModBrowser.detailSelectedId || "").length > 0
                onClicked: {
                    if (page.live)
                        ModBrowser.installSelected();
                }
            }
        }
    }

    FolderDialog {
        id: dirDialog
        objectName: "browseDetailDirDialog"
        title: page.t("mod_browser_select_dir",
                      {"kind": page.t(page.live ? String(ModBrowser.tabLabelKey) : "mod_browser_tab_mods")})
        onAccepted: {
            if (page.live)
                ModBrowser.provideDirectoryUrl(selectedFolder);
        }
        onRejected: {
            if (page.live)
                ModBrowser.cancelDirectory();
        }
    }
}
