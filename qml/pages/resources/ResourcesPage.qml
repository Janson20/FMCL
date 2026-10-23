// ResourcesPage.qml —— 「我的资源」页（阶段 3 任务 3.6，对照表 L-55）。
//
// 旧实现：`ui/windows/resource_manager.py`（Tk 独立窗口 760x600）。业务早就在
// `services/resource_service.py`，本轮的**会话状态**（当前标签页 / 扫描缓存 / 页码 /
// 模组元数据 / 更新结果）进了 `services/resource_session.py`，QML 侧只通过 `Resources`
// 桥读写 —— 页面里**没有一条业务规则**（红线 2）。
//
// ## 这一页的四件特殊事
//
// 1. **两个入口汇到同一页**：版本页的齿轮（B-15，带 `routeParams.version`）与左侧
//    一级导航 `resources/mods`（**没有**版本上下文）。所以内容区顶部放一个版本选择器，
//    "看哪个版本"由 `Resources.activate(routeParams.version)` 决定（路由优先 →
//    最近使用 → 第一个已安装）。一个版本都没装时整页是空态。
// 2. **四类型页签 + 类型化搜索 + 分页**：模组页签画卡片，其余三类画一行；搜索框的
//    占位符、文件过滤器、空态文案都跟着页签走（键由 `Resources` 给）。
// 3. **拖拽安装**：`FmDropZone` 收 `drop.urls`，页面只把它交给
//    `Resources.takeFiles(urls, true)`（`file://` → 本地路径的折算在桥里做）。
// 4. **两个页内浮层**（确认删除 / 更新选择）：用 `Loader.active` 控制存在性
//    （留着的不可见实例会继续吃键盘焦点 —— 3.5 的浮层纪律）。
//
// ## 为什么根节点就是 `FmPage`（而不是"外层套一个 Item"）
//
// 第一版把根写成 `Item` + 内层 `FmPage`，理由是"工具条要在三态之外也可见"。代价有两个，
// 都在冒烟走查里被抓到：
//
// * `contentState` 跑到**内层**对象上了，而冒烟驱动按页面**根对象**切三态
//   （`tests/_smoke_driver.py` 的 `page.setProperty("contentState", …)`），
//   于是 resources 的三态走查变成空数组、直接判红；
// * 页头图标落点相对页面顶部下移（视觉回归"12 页图标位置一致"那条同样判红）。
//
// 所以改成**单根 `FmPage`**，工具条放进内容区（`ContentArea` 里那个 `ColumnLayout`）。
// 代价是"没有版本时空态覆盖了工具条" —— 可接受：那时版本选择器本来也选不出东西。
// 内容区的 `ColumnLayout` 用 `anchors.fill: parent`（**不要**给根节点写
// `height: implicitHeight`：`ColumnLayout` 会按隐式高度收缩，实测整页只剩 217 高、
// 列表委托全被 clip 掉、点不到）。
//
// ## 三态与"局部加载"是两件事
//
// `contentState` 绑 `Resources.loadState`。模组页签的"正在读取模组信息…"**不是**整页
// loading —— 目录里已经有文件了，只是元数据在路上，所以它走列表上方那条进度条
// （旧实现也是这么分的：`mod_loading_metadata` 是列表上方一行小字，不是整页占位）。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon` 且不带 emoji（R2）、文案一律
// `Tr.map["键"] ?? "键"`（R3/R4；带参数时在**函数体**里调 `Tr.tf` —— 绑定里调
// `Tr.t` 不重算，闸门 R4）、控件只用 `qml/components` 的自研件（R9）、
// 每一个上下文属性读取都判空。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "resourcesPage"

    //: 桥缺席（注册失败）时整页走错误态，而不是绑一堆 `undefined` 报错
    readonly property bool live: (typeof Resources !== "undefined" && Resources) ? true : false
    readonly property var params: (page.routeParams === undefined || page.routeParams === null)
                                  ? ({}) : page.routeParams
    //: 路由给的版本（一级导航进来时是空串）。
    //:
    //: **必须直接读 `routeParams`，不能在信号处理器里读派生绑定**
    //: （2026-10-07 用户第三次反馈，`16` §二十六）：`onRouteParamsChanged` 是
    //: `props.routeParams = frame.params` **同步**触发的，而 QML 对 `params` /
    //: `routeVersion` 这两条**派生绑定**的重算是**异步**的 —— 处理器里读到的仍是
    //: 旧值（实测：`rawParams` 已是 `{"version":"1.21-fabric"}`，`routeVersion` 还是
    //: 空串）。症状就是"从版本列表选哪个版本进来都一样"：桥收到的一直是 `activate("")`，
    //: 于是照"路由没给版本"的兜底链走了最近使用/第一个已安装版本。
    //: 绑定本身没问题（延迟读完是对的），错在**在确切的这一刻读了它**。
    function wantedVersion() {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        return String(raw.version || "");
    }

    //: 路由给的版本（**绑定用**：顶部选择器的高亮、页头文案都读它，那时绑定已重算完）
    readonly property string routeVersion: page.wantedVersion()
    //: 当前生效的版本
    readonly property string version: page.live ? String(Resources.versionId || "") : page.routeVersion
    readonly property var rows: page.live ? Resources.items : []
    readonly property var updates: page.live ? Resources.updates : []
    //: 当前标签页的图标名（**必须用桥给的映射**：`resourcepacks` / `shaderpacks` 不是
    //: 图标名，直接把页签键塞给 `FmIcon` 会找不到 svg 并刷 provider 警告）
    readonly property string tabIcon: page.live ? String(Resources.tabIcon || "mods") : "mods"
    //: 当前看的是不是全局 `.minecraft` 目录（所有版本共用）
    readonly property bool globalScope: page.live ? Resources.globalScope === true : false

    //: 待确认删除的条目（非空 = 确认框那个 Loader 的 `active`）
    property string deleteRequest: ""
    property string deleteName: ""
    //: 更新选择对话框开不开（`true` 时 Loader 才创建组件）
    property bool updateDialogOpen: false

    title: page.live ? page.t("rm_title", {"version": page.version})
                     : page.t("resource_manager", {"version": page.routeVersion})
    description: page.live ? page.t(String(Resources.tabDescKey)) : ""
    iconName: page.tabIcon

    //: 文件夹为空时**不能让整页进入 empty** —— FmPage 会把工具条/页签一起换掉，
    //: 用户就切不到其他类型（2026-10-23 反馈）。页面本体保持 ready，空目录画在
    //: 列表区里的拖拽区上；只有"一个版本都没装"才整页 empty。
    contentState: !page.live ? "error"
                  : (Resources.noVersion === true ? "empty"
                  : (String(Resources.loadState) === "empty" ? "ready"
                  : String(Resources.loadState || "empty")))
    loadingText: page.t("mod_loading_metadata")
    //: 空态的**标题按真实原因分两种**（2026-10-07 第二次反馈：原先写死成"还没有已安装的
    //: 版本"，而状态栏在说"文件夹不存在"，标题和原因对不上）：
    //:   * 一个版本都没装（选择器里只有「全局 .minecraft」）→ 那句"先装一个版本"；
    //:   * 否则是"这个版本的资源目录是空的"（带类型名，如「资源包 文件夹为空」）。
    emptyText: page.live
               ? (Resources.noVersion === true
                  ? page.t(String(Resources.noVersionKey))
                  : page.t(String(Resources.emptyFolderKey), {"label": page.t(String(Resources.tabLabelKey))}))
               : page.t("resource_manager")
    //: 补充说明：看全局目录时说明"所有版本共用"，否则空目录直接把拖拽提示给出来
    emptyDetail: page.live
                 ? (page.globalScope ? page.t(String(Resources.globalHintKey)) : page.t("rm_drop_hint"))
                 : ""
    errorText: page.t("plugin_state_error")
    errorDetail: page.live ? String(Resources.loadError || "") : ""
    retryText: page.t("retry")

    onRetried: {
        if (page.live)
            Resources.retry();
    }

    //: 路由参数是 `createObject()` **之后**才写进来的（`PageStack.pushFrame()` 的
    //: `props.routeParams = frame.params`），而 `Component.onCompleted` 在那之前就跑完了
    //: —— 所以**必须**再接一次这个变化，否则版本页齿轮带过来的 `version` 永远到不了桥。
    onRouteParamsChanged: page.activateFromRoute()
    onRouteIdChanged: page.activateFromRoute()

    Component.onCompleted: page.activateFromRoute()

    //: 把"这一屏看哪个版本"交给桥（`activate(routeVersion)` 自带"路由优先"的判定）。
    //: 两条进入路径都会走到这里：创建完成时一次，`routeParams` 赋值后再一次 ——
    //: 桥侧对"版本没变就不重扫"负责，所以重复调用是安全的。
    function activateFromRoute() {
        if (page.live)
            Resources.activate(page.wantedVersion());
    }

    //: `Tr.map[键] ?? 键`；**带参数时走 `Tr.tf`**（`Tr.t` 是 1 参槽，多给的参数会被
    //: 静默忽略、占位符不替换）。函数体里调用是命令式 JS，允许。
    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    //: 更新对话框「全部更新」的 modid 列表（**不看勾选** —— 旧实现给的就是
    //: `list(self._update_info.keys())`；勾选只影响「更新选中」那颗按钮）
    function updateIds() {
        var list = [];
        for (var i = 0; i < page.updates.length; ++i)
            list.push(String(page.updates[i].modid || ""));
        return list;
    }

    // ══ 内容区 ═════════════════════════════════════════════
    ColumnLayout {
        id: content
        objectName: "resourceContent"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        // ── 工具条：版本选择器 / 四类型页签 / 动作 ──
        RowLayout {
            objectName: "resourceToolbarRow"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "resourceVersionLabel"
                //: 复用 `resource_manager` 这个带 `{version}` 的键，版本号空着当标签用
                //: （版本由右边的选择器显示）；`rm_title` 给页头
                text: page.t("resource_manager", {"version": ""})
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            FmComboBox {
                id: versionBox
                objectName: "resourceVersionBox"
                Layout.preferredWidth: 200
                Layout.maximumWidth: 300
                //: 只有一个选项（"全局 .minecraft"）时不用可点 —— 那等于没得选
                enabled: page.live && model.length > 1
                model: page.live ? Resources.versionChoices : []
                textRole: "text"
                valueRole: "value"
                //: `currentValue` 是 QtQuick.Controls 的 **FINAL** 只读属性（不能绑），
                //: 选中项要经 `currentIndex` 表达
                currentIndex: {
                    var list = page.live ? Resources.versionChoices : [];
                    for (var i = 0; i < list.length; ++i) {
                        if (String(list[i].value) === page.version)
                            return i;
                    }
                    return -1;
                }
                onActivated: function (index) {
                    if (page.live)
                        Resources.setVersion(String(currentValue || ""));
                }
            }

            Item { Layout.fillWidth: true }

            //: 全局目录那句说明（是隔离版本时不占位）
            Text {
                objectName: "resourceScopeHint"
                visible: page.globalScope
                text: page.live ? page.t(String(Resources.globalHintKey)) : ""
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
                Layout.maximumWidth: 280
                Layout.alignment: Qt.AlignVCenter
            }

            FmButton {
                objectName: "resourceRefreshButton"
                primary: false
                iconName: "refresh"
                text: page.t("refresh")
                enabled: page.live && !Resources.busy
                onClicked: {
                    if (page.live)
                        Resources.refresh();
                }
            }
        }

        FmTabBar {
            objectName: "resourceTabBar"
            Layout.fillWidth: true
            items: page.live ? Resources.tabs : []
            currentKey: page.live ? String(Resources.tab || "") : ""
            onTabRequested: function (key) {
                if (page.live)
                    Resources.setTab(String(key));
            }
        }

        RowLayout {
            objectName: "resourceActionRow"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "resourceExportButton"
                visible: page.live && Resources.isMods
                primary: false
                iconName: "copy"
                text: page.t("mod_export_list")
                enabled: page.live && !Resources.busy && Resources.modCount > 0
                onClicked: {
                    if (page.live)
                        Resources.exportList();
                }
            }

            FmButton {
                objectName: "resourceCheckUpdatesButton"
                visible: page.live && Resources.isMods
                primary: false
                iconName: "download"
                text: page.t("mod_check_updates")
                enabled: page.live && !Resources.busy && Resources.modCount > 0
                onClicked: {
                    if (page.live)
                        Resources.checkUpdates();
                }
            }

            Item { Layout.fillWidth: true }

            FmButton {
                objectName: "resourceAddButton"
                primary: false
                iconName: "add"
                text: page.t("resource_add")
                enabled: page.live && page.version.length > 0
                onClicked: {
                    if (page.live)
                        Resources.requestAddFiles();
                }
            }

            FmButton {
                objectName: "resourceOpenFolderButton"
                primary: false
                iconName: "folder-open"
                text: page.t("resource_open_folder")
                enabled: page.live && page.version.length > 0
                onClicked: {
                    if (page.live)
                        Resources.openFolder();
                }
            }
        }

        // ── 搜索 ──
        FmSearchField {
            objectName: "resourceSearchField"
            Layout.fillWidth: true
            placeholderText: page.live ? page.t(String(Resources.searchPlaceholderKey)) : ""
            text: page.live ? String(Resources.search || "") : ""
            enabled: page.live && page.rows.length > 0
            onAccepted: {
                if (page.live)
                    Resources.setSearch(text);
            }
            onCleared: {
                if (page.live)
                    Resources.setSearch("");
            }
        }

        //: 模组页签的元数据进度（局部加载，见文件头）
        FmProgressBar {
            objectName: "resourceModsProgress"
            Layout.fillWidth: true
            visible: page.live && Resources.progressVisible
            label: page.live ? String(Resources.progress.message || "") : ""
            indeterminate: page.live && Number(Resources.progress.total || 0) <= 0
            value: (page.live && Number(Resources.progress.total || 0) > 0)
                   ? (Number(Resources.progress.current || 0) / Number(Resources.progress.total || 1))
                   : 0
            showPercent: page.live && Number(Resources.progress.total || 0) > 0
        }

        // ── 列表 ──
        ListView {
            id: resourceList
            objectName: "resourceListView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.rows.length > 0
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: page.rows
            ScrollBar.vertical: FmScrollBar {}

            delegate: Item {
                id: resourceRow
                objectName: "resourceRow"
                width: resourceList.width
                //: 委托高度 = 卡片/行的隐式高度（ListView 的委托**不是**布局托管的，
                //: 不会被拉伸；显式写高度让 `clip` 与滚动条的 contentHeight 算得准）
                height: (resourceRow.row.isMod === true)
                        ? (modCard.implicitHeight + (Theme?.spacingXs ?? 4))
                        : (plainRow.height + (Theme?.spacingXs ?? 4))

                readonly property var row: (modelData === undefined) ? ({}) : modelData
                readonly property string pathKey: String(resourceRow.row.path || "")
                readonly property bool isMod: resourceRow.row.isMod === true

                // ── 模组卡片 ──
                FmCard {
                    id: modCard
                    objectName: "resourceModCard-" + resourceRow.pathKey
                    visible: resourceRow.isMod
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    padding: Theme?.spacingMd ?? 12

                    RowLayout {
                        objectName: "resourceModCardRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingMd ?? 12

                        //: jar 内图标（`extract_mod_metadata` 取出来的 base64 由桥注册进
                        //: 缩略图通道；没图标时 `icon` 为空 → 兜底 FmIcon）
                        Image {
                            objectName: "resourceModIcon-" + resourceRow.pathKey
                            //: **只在模组行且有图标时才有 source**：否则 Qt 会把图标名当
                            //: 相对 URL 去开文件（实测 `Cannot open: …/resourcepack`）
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            visible: resourceRow.isMod && source.toString().length > 0
                            source: (resourceRow.isMod && String(resourceRow.row.icon || "").length > 0)
                                    ? String(resourceRow.row.icon)
                                    : ""
                            sourceSize.width: 48
                            sourceSize.height: 48
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }

                        FmIcon {
                            objectName: "resourceModFallback-" + resourceRow.pathKey
                            visible: String(resourceRow.row.icon || "").length === 0
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                            Layout.alignment: Qt.AlignTop
                            name: String(resourceRow.row.iconName || "mods")
                            color: Theme?.textSecondary ?? "transparent"
                            size: 32
                        }

                        ColumnLayout {
                            objectName: "resourceModInfo"
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                objectName: "resourceName-" + resourceRow.pathKey
                                Layout.fillWidth: true
                                text: String(resourceRow.row.display_name || "")
                                color: resourceRow.row.disabled === true
                                       ? (Theme?.textSecondary ?? "transparent")
                                       : (Theme?.textPrimary ?? "transparent")
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "resourceAuthor-" + resourceRow.pathKey
                                Layout.fillWidth: true
                                visible: String(resourceRow.row.author || "").length > 0
                                text: String(resourceRow.row.author || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            Text {
                                objectName: "resourceDesc-" + resourceRow.pathKey
                                Layout.fillWidth: true
                                visible: String(resourceRow.row.description || "").length > 0
                                text: String(resourceRow.row.description || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            RowLayout {
                                objectName: "resourceModMeta"
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                FmTag {
                                    objectName: "resourceModId-" + resourceRow.pathKey
                                    visible: String(resourceRow.row.modid || "").length > 0
                                    text: String(resourceRow.row.modid || "")
                                    level: "accent"
                                }

                                Text {
                                    objectName: "resourceModVersion-" + resourceRow.pathKey
                                    visible: String(resourceRow.row.version || "").length > 0
                                    text: String(resourceRow.row.version || "").length > 0
                                          ? ("v" + String(resourceRow.row.version))
                                          : ""
                                    color: Theme?.success ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }

                                Text {
                                    objectName: "resourceFileName-" + resourceRow.pathKey
                                    Layout.fillWidth: true
                                    text: String(resourceRow.row.filename || "")
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    elide: Text.ElideRight
                                }
                            }
                        }

                        ColumnLayout {
                            objectName: "resourceModActions"
                            Layout.alignment: Qt.AlignTop
                            spacing: Theme?.spacingXs ?? 4

                            FmButton {
                                objectName: "resourceToggle-" + resourceRow.pathKey
                                primary: false
                                text: resourceRow.row.disabled === true
                                      ? page.t("rm_enable")
                                      : page.t("rm_disable")
                                onClicked: {
                                    if (page.live)
                                        Resources.toggleMod(resourceRow.pathKey,
                                                            resourceRow.row.disabled === true);
                                }
                            }

                            FmButton {
                                objectName: "resourceDelete-" + resourceRow.pathKey
                                danger: true
                                iconOnly: true
                                iconName: "trash"
                                onClicked: {
                                    if (page.live)
                                        Resources.requestDelete(resourceRow.pathKey,
                                                                String(resourceRow.row.display_name || ""));
                                }
                            }
                        }
                    }
                }

                // ── 其余三类：一行 ──
                Rectangle {
                    id: plainRow
                    objectName: "resourcePlainRow-" + resourceRow.pathKey
                    visible: !resourceRow.isMod
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    height: 44
                    radius: Theme?.radiusSm ?? 6
                    color: Theme?.bgMedium ?? "transparent"

                    RowLayout {
                        objectName: "resourcePlainRowLayout"
                        anchors.fill: parent
                        anchors.leftMargin: Theme?.spacingSm ?? 8
                        anchors.rightMargin: Theme?.spacingSm ?? 8
                        spacing: Theme?.spacingSm ?? 8

                        Image {
                            objectName: "resourceThumb-" + resourceRow.pathKey
                            //: 缩略图是 `image://fmcl-thumb/<id>`，非模组行才有
                            Layout.preferredWidth: 32
                            Layout.preferredHeight: 32
                            visible: resourceRow.isMod === false
                                     && String(resourceRow.row.thumbnail || "").length > 0
                            source: (resourceRow.isMod === false
                                     && String(resourceRow.row.thumbnail || "").length > 0)
                                    ? String(resourceRow.row.thumbnail)
                                    : ""
                            sourceSize.width: 32
                            sourceSize.height: 32
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }

                        FmIcon {
                            objectName: "resourcePlainIcon-" + resourceRow.pathKey
                            visible: String(resourceRow.row.thumbnail || "").length === 0
                            Layout.preferredWidth: 32
                            Layout.preferredHeight: 32
                            name: String(resourceRow.row.iconName || "file")
                            color: Theme?.textSecondary ?? "transparent"
                            size: 20
                        }

                        Text {
                            objectName: "resourcePlainName-" + resourceRow.pathKey
                            Layout.fillWidth: true
                            text: String(resourceRow.row.display_name || "")
                            color: resourceRow.row.disabled === true
                                   ? (Theme?.textSecondary ?? "transparent")
                                   : (Theme?.textPrimary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            elide: Text.ElideRight
                        }

                        Text {
                            objectName: "resourcePlainSize-" + resourceRow.pathKey
                            visible: String(resourceRow.row.size || "").length > 0
                            text: String(resourceRow.row.size || "")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        FmButton {
                            objectName: "resourcePlainDelete-" + resourceRow.pathKey
                            danger: true
                            iconOnly: true
                            iconName: "trash"
                            onClicked: {
                                if (page.live)
                                    Resources.requestDelete(resourceRow.pathKey,
                                                            String(resourceRow.row.display_name || ""));
                            }
                        }
                    }
                }
            }
        }

        //: 拖拽放置区。旧实现注册了两处（空态区 + 列表），本轮挂在整块列表区上：
        //: 列表空时它占满（就是空态），非空时它缩成一条提示带 —— 两处都能收，
        //: 与旧实现"两处都注册"的本意一致。
        FmDropZone {
            objectName: "resourceDropZone"
            Layout.fillWidth: true
            Layout.fillHeight: page.rows.length === 0
            Layout.preferredHeight: page.rows.length === 0 ? -1 : 88
            Layout.minimumHeight: page.rows.length === 0 ? -1 : 88
            visible: page.live && page.version.length > 0
            text: page.t("rm_drop_hint")
            iconName: "download"
            onDropped: function (urls) {
                if (page.live)
                    Resources.takeFiles(urls, true);
            }
        }

        FmPagination {
            objectName: "resourcePagination"
            Layout.alignment: Qt.AlignLeft
            visible: page.live && Number(Resources.pageCount || 1) > 1
            page: page.live ? Number(Resources.page || 1) : 1
            pageCount: page.live ? Number(Resources.pageCount || 1) : 1
            onPageRequested: function (target) {
                if (page.live)
                    Resources.goto(Number(target));
            }
        }
    }

    // ══ 页内浮层的宿主 ═════════════════════════════════════
    //: 普通 `Item` 做宿主、`Loader` 在里面 `anchors.fill`（布局托管的项上写锚点会报
    //: "Detected anchors on an item that is managed by a layout"，"零 QML 警告"判据会红）。
    Item {
        id: overlayHost
        objectName: "resourceOverlayHost"
        anchors.fill: parent

        Loader {
            objectName: "resourceDeleteLoader"
            anchors.fill: parent
            active: page.deleteRequest.length > 0
            sourceComponent: deleteComponent
        }

        Loader {
            objectName: "resourceUpdateLoader"
            anchors.fill: parent
            active: page.updateDialogOpen
            sourceComponent: updateComponent
        }
    }

    // ══ 确认删除 ═══════════════════════════════════════════
    Component {
        id: deleteComponent

        ConfirmDialog {
            objectName: "resourceDeleteDialog"
            request: ({
                "title": page.t("rm_delete_confirm_title"),
                "message": page.t("rm_delete_confirm_msg", {"name": page.deleteName}),
                "default": true
            })
            onAnswered: function (value) {
                var path = page.deleteRequest;
                page.deleteRequest = "";
                page.deleteName = "";
                if (value === true && page.live && path.length > 0)
                    Resources.confirmDelete(path);
            }
        }
    }

    // ══ 更新选择对话框（勾选 + 全部/选中）═══════════════════
    Component {
        id: updateComponent

        ResourceUpdateDialog {
            objectName: "resourceUpdateDialog"
            rows: page.updates
            titleText: page.t("mod_update_dialog_title")
            headerText: page.t("mod_update_dialog_header", {"count": page.updates.length})
            hintText: page.t("mod_update_dialog_hint")
            cancelText: page.t("mod_update_cancel")
            allText: page.t("mod_update_all")
            selectedText: page.t("mod_update_selected")
            onCancelled: page.updateDialogOpen = false
            onAllRequested: {
                page.updateDialogOpen = false;
                if (page.live)
                    Resources.applyUpdates(page.updateIds());
            }
            onSelected: function (modids) {
                page.updateDialogOpen = false;
                if (page.live)
                    Resources.applyUpdates(modids);
            }
        }
    }

    // ══ 文件选择（安装）═════════════════════════════════════
    FileDialog {
        id: addFilesDialog
        objectName: "resourceAddFilesDialog"
        title: page.live
               ? page.t("rm_select_file_title", {"label": page.t(String(Resources.tabLabelKey))})
               : ""
        fileMode: FileDialog.OpenFiles
        nameFilters: page.live ? String(Resources.fileFilter).split(";;") : ["*"]
        //: `selectedFiles` 是 `url` 列表 —— 交给桥折成本地路径（`QUrl.toLocalFile()`），
        //: `drop=false` 走"文件对话框"语义
        onAccepted: {
            if (page.live)
                Resources.takeFiles(selectedFiles, false);
        }
    }

    // ══ 桥 → 页面 ══════════════════════════════════════════
    Connections {
        target: page.live ? Resources : null
        ignoreUnknownSignals: true

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level));
        }

        function onNoticeRequested(message, level) {
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(message), "icon": "mods"});
        }

        function onAddFilesRequested() {
            addFilesDialog.open();
        }

        function onDeleteRequested(path, name) {
            page.deleteRequest = String(path || "");
            page.deleteName = String(name || "");
        }

        function onUpdatesChanged() {
            //: 检查完发现可更新项才弹框（旧实现是"有更新就弹"；没更新只更新状态栏）
            if (page.live && Resources.updatesCount > 0)
                page.updateDialogOpen = true;
        }
    }
}
