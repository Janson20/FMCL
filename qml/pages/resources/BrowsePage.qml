// BrowsePage.qml —— 在线资源浏览与搜索（阶段 3 任务 3.7，对照表 L-56）。
//
// ## 为什么只有一页
//
// `docs/refactor/02` §4.3 的 IA 原本写着「L1 在线浏览 → L2 搜索结果 → L2 资源详情」。
// 第一版按字面拆了三页，结果是：L1 只有一张搜索表单、按下去到 L2 —— **又一个一模一样的
// 工具条**，只是下面多了结果；面包屑还出现「资源浏览 → 搜索 → 资源搜索」这种连读像同一页的
// 三层（用户 2026-10-23 反馈「这样不就重复了吗」）。
//
// 旧实现（`ui/windows/mod_browser.py`，1,248 行 Tk 窗口）从头到尾就是**一屏**：
// 搜索框 + 筛选 + 结果 + 分页都在同一个页面里。现在按用户的裁决并回来：
//
//   * 本页 = 旧窗口那一屏（类型 / 关键词 / 游戏版本 / 加载器 / 结果 / 分页 / 卡片安装）；
//   * `resources/browse/detail` = 新增的资源详情页（版本选择 + 依赖预览 + 安装）。
//
// 所以路由只有两层：**资源浏览 → 资源详情**。
//
// ## 工具条永远在
//
// 结果为空时**不能**把整页交给 `FmPage` 的空态块（那会把工具条一起藏掉，用户就没法改
// 关键词了）。所以本页 `contentState` 恒为 `ready`，"没有结果 / 搜索失败 / 正在搜索"
// 都画在结果区内部 —— 与旧窗口一致（搜索框一直在，结果区换内容）。
//
// ## 路由参数
//
// `version` / `loader` 来自版本页的两颗按钮（`Versions.browseMods` /
// `Versions.browseAllResources`）：前者带版本，后者不带（"全部版本"）。
// 参数在 `Component.onCompleted` **之后**才写进来，只能读**原始** `routeParams`
// —— 派生绑定的重算是异步的（`16` §二十六）。
//
// ## 纪律
//
// 颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`（R2）、文案一律 `Tr.map[…]`（R3/R4）、
// 控件只用 `qml/components` 的自研件（R9）、每一个上下文属性读取都判空。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "browsePage"

    //: 桥缺席（注册失败）时整页走错误态，而不是绑一堆 `undefined` 报错
    readonly property bool live: (typeof ModBrowser !== "undefined" && ModBrowser) ? true : false

    title: page.t("mod_browser_title_all")
    description: page.live ? page.t(String(ModBrowser.tabLabelKey)) : ""
    iconName: "search"
    //: 工具条必须一直在（见文件头），所以三态画在结果区内部
    contentState: "ready"

    //: 路由给的版本（空串 = "全部版本"）
    function wantedVersion() {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        return String(raw.version || "");
    }

    function wantedLoader() {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        return String(raw.loader || "");
    }

    onRouteParamsChanged: page.activateFromRoute()
    onRouteIdChanged: page.activateFromRoute()
    Component.onCompleted: page.activateFromRoute()

    //: 把"从哪个版本进来"交给桥；顺带后台加载游戏版本候选（失败就是只有"全部版本"）。
    //:
    //: **进入就搜一次**（用户 2026-10-23 反馈「进入浏览直接就显示热门」）：空关键词
    //: 的搜索就是热门列表，配合桥自动填好的版本/加载器筛选，进来即有内容。
    //:
    //: 为什么用 `Qt.callLater` 而不是就地 `search()`：页面创建与路由参数写入是**两步**
    //: （`Component.onCompleted` → 再写 `props.routeParams`），就地搜索会先用空版本搜一次、
    //: 参数到了再搜一次 —— 白跑一次网络请求，界面上还会闪一下不相关的结果。
    //: `callLater` 把同一轮里的这两次激活合并成一次，那时参数已经就位。
    function activateFromRoute() {
        if (!page.live)
            return;
        ModBrowser.activate(page.wantedVersion(), page.wantedLoader());
        ModBrowser.loadVersionChoices();
        Qt.callLater(page.autoSearch);
    }

    //: 只在"还没搜过"（`idle`）时自动搜 —— 从详情页返回或再次进入时结果还在，不重复联网
    function autoSearch() {
        if (!page.live)
            return;
        if (ModBrowser.loadState === "idle")
            ModBrowser.search();
    }

    //: `Tr.map[键] ?? 键`；带参数走 `Tr.tf`（`Tr.t` 是 1 参槽，多给的参数会被忽略）
    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    //: 这一屏的上下文文案：有版本就显示版本，否则"全部版本"
    function contextText() {
        if (!page.live)
            return "";
        var vid = String(ModBrowser.versionId || "");
        if (vid.length === 0)
            return page.t("mod_browser_all_versions");
        var gv = String(ModBrowser.defaultGameVersion || "");
        var loader = String(ModBrowser.sourceLoader || "");
        if (gv.length > 0 && loader.length > 0)
            return gv + " · " + loader;
        return vid;
    }

    //: 当前页区间文案（`显示 {start}-{end} / 共 {total} 个`）
    function rangeText() {
        if (!page.live || ModBrowser.loadState !== "ready")
            return "";
        return page.t("mod_browser_result_range", {
            "start": ModBrowser.rangeStart,
            "end": ModBrowser.rangeEnd,
            "total": ModBrowser.browsable
        });
    }

    //: 搜索（普通或 AI）：先把筛选同步给桥，再让它搜 —— **本页内完成**，不跳页
    function research(ai) {
        if (!page.live)
            return;
        ModBrowser.setQuery(String(searchField.text || ""));
        ModBrowser.setGameVersion(String(versionBox.currentValue || ""));
        ModBrowser.setLoader(String(loaderBox.currentValue || ""));
        if (ai === true)
            ModBrowser.aiSearch();
        else
            ModBrowser.search();
    }

    function openDetail(row) {
        if (!page.live)
            return;
        Nav.push("resources/browse/detail", {
            "source": String(row.source || "modrinth"),
            "projectId": String(row.project_id || ""),
            "tab": String(ModBrowser.tab || "mods"),
            "title": String(row.title || ""),
            "version": String(ModBrowser.versionId || ""),
            "sourceLoader": String(ModBrowser.sourceLoader || "")
        });
    }

    //: 结果区里那句提示（没搜过 / 没结果时）
    function placeholderText() {
        if (!page.live)
            return "";
        if (ModBrowser.loadState === "idle")
            return page.t("browse_search_hint");
        if (ModBrowser.loadState === "empty")
            return page.t(String(ModBrowser.emptyKey));
        return "";
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
        objectName: "browseContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        // ── 上下文 + 类型 ──
        RowLayout {
            objectName: "browseContextRow"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmIcon {
                objectName: "browseContextIcon"
                name: "info"
                color: Theme?.textSecondary ?? "transparent"
                size: Theme?.fontSizeBase ?? 12
                Layout.alignment: Qt.AlignVCenter
            }

            Text {
                objectName: "browseContextText"
                Layout.fillWidth: true
                text: page.contextText()
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            //: 来源不切换（合并搜索），在上下文行里说清楚，免得用户找"换源"按钮
            Text {
                objectName: "browseSourceNote"
                text: page.t("browse_source_note")
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }
        }

        FmTabBar {
            objectName: "browseTabBar"
            Layout.fillWidth: true
            items: page.live ? ModBrowser.tabs : []
            currentKey: page.live ? String(ModBrowser.tab || "") : ""
            onTabRequested: function (key) {
                if (page.live)
                    ModBrowser.setTab(String(key));
            }
        }

        // ── 工具条：关键词 + 筛选 + 动作（结果为空时也一直在）──
        RowLayout {
            objectName: "browseToolbar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmSearchField {
                id: searchField
                objectName: "browseSearchField"
                Layout.fillWidth: true
                placeholderText: page.t("browse_search_hint")
                onAccepted: page.research(false)
            }

            FmComboBox {
                id: versionBox
                objectName: "browseVersionBox"
                Layout.preferredWidth: 150
                model: page.live ? ModBrowser.gameVersionChoices : []
                textRole: "text"
                valueRole: "value"
                currentIndex: {
                    var list = page.live ? ModBrowser.gameVersionChoices : [];
                    var want = page.live ? String(ModBrowser.gameVersion || "") : "";
                    for (var i = 0; i < list.length; ++i) {
                        if (String(list[i].value) === want)
                            return i;
                    }
                    return 0;
                }
            }

            FmComboBox {
                id: loaderBox
                objectName: "browseLoaderBox"
                visible: !page.live || String(ModBrowser.tab || "") === "mods"
                Layout.preferredWidth: 140
                model: page.live ? ModBrowser.loaderChoices : []
                textRole: "text"
                valueRole: "value"
                currentIndex: {
                    var list = page.live ? ModBrowser.loaderChoices : [];
                    var want = page.live ? String(ModBrowser.loader || "") : "";
                    for (var i = 0; i < list.length; ++i) {
                        if (String(list[i].value) === want)
                            return i;
                    }
                    return 0;
                }
            }

            FmButton {
                objectName: "browseSearchButton"
                primary: true
                iconName: "search"
                text: page.t("mod_browser_search")
                onClicked: page.research(false)
            }

            FmButton {
                objectName: "browseAiButton"
                primary: false
                iconName: "bulb"
                enabled: page.live && !ModBrowser.aiBusy
                text: (page.live && ModBrowser.aiBusy) ? page.t("ai_search_optimizing") : page.t("browse_ai_search")
                onClicked: page.research(true)
            }
        }

        // ── 计数 / AI 关键词 / 进度 ──
        RowLayout {
            objectName: "browseStatusRow"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "browseCountText"
                text: page.rangeText()
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            Text {
                objectName: "browseKeywordsText"
                visible: page.live && ModBrowser.aiKeywords.length > 0
                text: page.live ? ModBrowser.aiKeywords.join(", ") : ""
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
                Layout.maximumWidth: 320
                Layout.alignment: Qt.AlignVCenter
            }
        }

        FmProgressBar {
            objectName: "browseProgress"
            Layout.fillWidth: true
            visible: (page.live && ModBrowser.progressVisible) || (page.live && ModBrowser.loadState === "loading")
            indeterminate: page.live && (ModBrowser.progressValue <= 0 || ModBrowser.loadState === "loading")
            value: page.live ? ModBrowser.progressValue : 0
            label: page.live
                   ? (ModBrowser.loadState === "loading" ? page.t("mod_browser_searching")
                                                         : String(ModBrowser.progressText))
                   : ""
        }

        // ── 错误提示（画在结果区上方，工具条不消失）──
        FmInfoBar {
            objectName: "browseErrorBar"
            Layout.fillWidth: true
            visible: page.live && ModBrowser.loadState === "error"
            level: "error"
            closable: false
            text: page.t("mod_browser_search_failed_status", {"error": page.live ? String(ModBrowser.loadError || "") : ""})
        }

        // ── AI 可用性提示（没登录时不藏按钮，而是说明为什么点了只有一句提示） ──
        FmInfoBar {
            objectName: "browseAiHint"
            Layout.fillWidth: true
            visible: page.live && !ModBrowser.aiAvailable
            level: "info"
            closable: false
            text: page.t("ai_search_login_required")
        }

        // ── 结果 ──
        ListView {
            id: resultList
            objectName: "browseResultList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Theme?.spacingXs ?? 4
            visible: page.live && ModBrowser.items.length > 0
            model: page.live ? ModBrowser.items : []
            ScrollBar.vertical: FmScrollBar {}

            delegate: Item {
                id: resultRow
                objectName: "browseResultCard-" + String(resultRow.row.project_id || "")
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
                        objectName: "browseResultRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingMd ?? 12

                        //: 项目图标（远程 URL）；没图标或没加载出来时露出兜底 FmIcon
                        Image {
                            objectName: "browseResultIcon-" + String(resultRow.row.project_id || "")
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
                            objectName: "browseResultFallbackIcon-" + String(resultRow.row.project_id || "")
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            visible: !(String(resultRow.row.icon_url || "").length > 0)
                            name: String(resultRow.row.type_icon || "mods")
                            color: Theme?.textSecondary ?? "transparent"
                            size: 32
                        }

                        ColumnLayout {
                            objectName: "browseResultInfo"
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            RowLayout {
                                objectName: "browseResultTitleRow"
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                Text {
                                    objectName: "browseResultTitle-" + String(resultRow.row.project_id || "")
                                    Layout.fillWidth: true
                                    text: String(resultRow.row.title || "")
                                    color: Theme?.textPrimary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeBase ?? 12
                                    font.bold: true
                                    elide: Text.ElideRight
                                }

                                FmTag {
                                    objectName: "browseResultSource-" + String(resultRow.row.project_id || "")
                                    text: String(resultRow.row.source_name || "")
                                    level: String(resultRow.row.source || "") === "curseforge"
                                           ? "warning" : "accent"
                                }
                            }

                            Text {
                                objectName: "browseResultDesc-" + String(resultRow.row.project_id || "")
                                Layout.fillWidth: true
                                visible: String(resultRow.row.description || "").length > 0
                                text: String(resultRow.row.description || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            RowLayout {
                                objectName: "browseResultMeta"
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                Text {
                                    objectName: "browseResultLoaders-" + String(resultRow.row.project_id || "")
                                    visible: String(resultRow.row.loaders_text || "").length > 0
                                    text: String(resultRow.row.loaders_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }

                                Text {
                                    objectName: "browseResultVersions-" + String(resultRow.row.project_id || "")
                                    visible: String(resultRow.row.versions_text || "").length > 0
                                    text: String(resultRow.row.versions_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    objectName: "browseResultDownloads-" + String(resultRow.row.project_id || "")
                                    text: String(resultRow.row.downloads_text || "")
                                    color: Theme?.textTertiary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }
                            }
                        }

                        ColumnLayout {
                            objectName: "browseResultActions"
                            Layout.alignment: Qt.AlignTop
                            spacing: Theme?.spacingXs ?? 4

                            FmButton {
                                objectName: "browseResultDetail-" + String(resultRow.row.project_id || "")
                                primary: false
                                iconName: "guide"
                                text: page.t("version_row_detail")
                                onClicked: page.openDetail(resultRow.row)
                            }

                            FmButton {
                                objectName: "browseResultInstall-" + String(resultRow.row.project_id || "")
                                primary: true
                                iconName: "download"
                                text: page.t("mod_browser_install")
                                enabled: page.live && !ModBrowser.busy
                                onClicked: {
                                    if (page.live)
                                        ModBrowser.installFromCard(String(resultRow.row.source || "modrinth"),
                                                                   String(resultRow.row.project_id || ""),
                                                                   String(resultRow.row.title || ""));
                                }
                            }
                        }
                    }
                }
            }
        }

        // ── 结果区提示（没搜过 / 没结果）：工具条不消失，提示画在下面 ──
        Text {
            objectName: "browsePlaceholder"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.live && ModBrowser.items.length === 0 && ModBrowser.loadState !== "loading"
            text: page.placeholderText()
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.WordWrap
        }

        // ── 分页 ──
        FmPagination {
            objectName: "browsePagination"
            Layout.alignment: Qt.AlignHCenter
            visible: page.live && ModBrowser.pageCount > 1
            page: page.live ? ModBrowser.page : 1
            pageCount: page.live ? ModBrowser.pageCount : 1
            onPageRequested: function (target) {
                if (page.live)
                    ModBrowser.goto(target);
            }
        }
    }

    //: 安装目录选择（旧实现三个标签页**都**先问目录；默认路径由桥解析）
    FolderDialog {
        id: dirDialog
        objectName: "browseDirDialog"
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
