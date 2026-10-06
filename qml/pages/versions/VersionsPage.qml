// VersionsPage.qml —— 版本列表与详情（阶段 3 任务 3.2；IA 见 `docs/refactor/02` 第 4.2 节）。
//
// 这一页承载什么（03 的 3.2 摘要 + 对照表 B 组 + 2026-10-06 用户裁决的"轻量新增"）：
//   * 已安装版本列表 + 计数 + 每行显示文本（B-01）；
//   * 行操作：选中 / 详情 / 模组 / 资源管理 / 重命名 / 删除（B-02）；
//   * 刷新（A-04）、资源浏览入口（B-14）、资源管理入口（B-15）；
//   * **新增**：搜索与排序（B-19）、详情（路径 / 所需 Java / 模组数量，B-20）、
//     打开目录（B-21）、校验文件（B-22）；
//   * 启动与强杀：与首页共用 `GameService`（B-03 / B-04 已验收），旧版本的已安装面板
//     底部本来就有这两个按钮，所以版本页也放一份。
//
// 数据全部来自 `Versions` 桥（`app/bridges/version_bridge.py`）——本文件不碰服务、
// 不做业务判断：显示文本拼法、排序键、校验结果判读、删除后清"最近使用版本"都在
// `VersionService`。这里只做三件事：绑属性、发槽调用、按状态显示。
//
// 两种模式共用一个文件：`versions` = 列表，`versions/detail` = 详情（`routeId` 区分，
// 两条路由在 `app/bridges/nav_bridge.py` 的路由表里都指向本文件）。
//
// 三态（03 的 SOP 第 5 条）：桥自己算出 loading / ready / empty / error（"搜索没命中"
// **不算** empty —— 那会把搜索框一起藏掉，用户就没法清空关键词了），这里直接绑
// `Versions.loadState`。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`/`FmToolButton`（R2）、
// 文案走 `Tr.map[…]`（R3/R4）、只用 `qml/components` 里的 `Fm*`（R9）。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "versionsPage"

    //: 桥缺席（注册失败）时整页走错误态，而不是绑一堆 `undefined` 报错
    readonly property bool bridgeAvailable: (typeof Versions !== "undefined" && Versions) ? true : false
    //: 详情模式（路由 `versions/detail`）
    readonly property bool detailMode: page.routeId === "versions/detail"

    contentState: !page.bridgeAvailable ? "error"
                  : (!Versions || Versions.loadState === "loading" ? "loading"
                  : (Versions.loadState === "error" ? "error"
                  : (Versions.loadState === "empty" ? "empty" : "ready")))

    loadingText: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
    emptyText: Tr?.map["version_none"] ?? "version_none"
    emptyDetail: Tr?.map["version_none_hint"] ?? "version_none_hint"
    //: 空态的唯一动作是"去装一个"（也是整页的强调色来源）
    emptyActionText: Tr?.map["install_new_version"] ?? "install_new_version"
    emptyActionPrimary: true
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: page.bridgeAvailable ? Versions.loadError : ""
    retryText: Tr?.map["version_refresh"] ?? "version_refresh"

    onRetried: page.reload()
    onEmptyActionTriggered: {
        if (page.bridgeAvailable)
            Nav.push("versions/install")
    }

    Component.onCompleted: page.reload()

    //: 详情路由带 `version` 参数（深链 `fmcl://versions/detail?version=1.20.4`）
    onRouteParamsChanged: page.applyRouteParams()
    onRouteIdChanged: page.applyRouteParams()

    function applyRouteParams() {
        if (!page.bridgeAvailable || !page.detailMode)
            return
        var wanted = page.routeParams ? String(page.routeParams.version || "") : ""
        if (wanted.length > 0 && wanted !== Versions.currentId)
            Versions.select(wanted)
    }

    function reload() {
        if (page.bridgeAvailable)
            Versions.load()
    }

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    //: 排序下拉的数据源：标签跟着语言走（`Tr.map` 在绑定里读，语言切换会重算）
    readonly property var sortModel: [
        { "label": Tr?.map["version_sort_name"] ?? "version_sort_name", "value": "name" },
        { "label": Tr?.map["version_sort_vanilla"] ?? "version_sort_vanilla", "value": "vanilla" },
        { "label": Tr?.map["version_sort_loader"] ?? "version_sort_loader", "value": "loader" }
    ]

    // ── 列表模式 ─────────────────────────────────────────────
    ColumnLayout {
        objectName: "versionsListPane"
        anchors.fill: parent
        visible: !page.detailMode
        spacing: Theme?.spacingSm ?? 8

        // ── 工具条：搜索 / 排序 / 计数 / 刷新 / 资源入口 ──
        RowLayout {
            objectName: "versionsToolbar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmSearchField {
                objectName: "versionsSearch"
                Layout.fillWidth: true
                placeholderText: Tr?.map["version_search_placeholder"] ?? "version_search_placeholder"
                onTextChanged: {
                    if (page.bridgeAvailable)
                        Versions.setFilter(text)
                }
            }

            FmComboBox {
                objectName: "versionsSort"
                Layout.preferredWidth: 150
                model: page.sortModel
                textRole: "label"
                valueRole: "value"
                currentIndex: {
                    var key = page.bridgeAvailable ? Versions.sortKey : "name"
                    for (var i = 0; i < page.sortModel.length; i++) {
                        if (page.sortModel[i].value === key)
                            return i
                    }
                    return 0
                }
                onActivated: {
                    if (page.bridgeAvailable)
                        Versions.setSortKey(currentValue)
                }
            }

            Text {
                objectName: "versionsCount"
                //: 搜索时显示"命中/总数"，否则只显示总数（旧界面只有总数）
                text: (page.bridgeAvailable && Versions.filter.length > 0)
                      ? (Versions.count + " / " + Versions.totalCount)
                      : (page.bridgeAvailable ? String(Versions.totalCount) : "0")
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                Layout.alignment: Qt.AlignVCenter
            }

            FmButton {
                objectName: "versionsInstallButton"
                //: 安装入口必须**一直可见**：用户实测报过"没有找到在哪里装版本" ——
                //: 旧界面的安装面板与列表在同一屏，新界面把它拆成了 `versions/install`，
                //: 只在空态给入口等于"装了第一个版本之后就再也找不到它"。
                primary: true
                text: Tr?.map["install_new_version"] ?? "install_new_version"
                iconName: "download"
                onClicked: Nav.push("versions/install")
            }

            FmButton {
                objectName: "versionsBrowseAllButton"
                primary: false
                text: Tr?.map["version_browse_all"] ?? "version_browse_all"
                iconName: "mods"
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.browseAllResources()
                }
            }

            FmToolButton {
                objectName: "versionsResourceButton"
                iconName: "settings-gear"
                enabled: page.bridgeAvailable && Versions.hasCurrent
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.openResourceManagerForCurrent()
                }
            }

            FmButton {
                objectName: "versionsRefreshButton"
                primary: false
                text: Tr?.map["version_refresh"] ?? "version_refresh"
                iconName: "refresh"
                enabled: page.bridgeAvailable
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.refresh()
                }
            }
        }

        // ── 列表 ──
        ListView {
            id: list
            objectName: "versionsListView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: page.bridgeAvailable ? Versions.versions : []
            ScrollBar.vertical: FmScrollBar {}

            delegate: Rectangle {
                id: rowItem
                objectName: "versionRow"
                required property var modelData
                required property int index

                width: list.width
                height: 46
                radius: Theme?.radiusMd ?? 6
                color: rowItem.selected ? (Theme?.itemHover ?? "transparent")
                       : (rowHover.hovered ? (Theme?.itemHover ?? "transparent")
                       : (Theme?.bgMedium ?? "transparent"))

                readonly property bool selected: page.bridgeAvailable && Versions.currentId === modelData.id

                HoverHandler {
                    id: rowHover
                }

                MouseArea {
                    objectName: "versionRowHit"
                    anchors.fill: parent
                    onClicked: {
                        if (page.bridgeAvailable)
                            Versions.select(modelData.id)
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme?.spacingSm ?? 8
                    anchors.rightMargin: Theme?.spacingXs ?? 4
                    spacing: Theme?.spacingXs ?? 4

                    FmIcon {
                        name: modelData.hasLoader ? "mods" : "package"
                        color: rowItem.selected ? (Theme?.accent ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        size: Theme?.iconSize ?? 20
                        Layout.alignment: Qt.AlignVCenter
                    }

                    Text {
                        objectName: "versionRowTitle"
                        Layout.fillWidth: true
                        //: 显示文本由服务拼好（B-01 逐字保留旧规则）
                        text: modelData.display
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        elide: Text.ElideRight
                        Layout.alignment: Qt.AlignVCenter
                    }

                    FmToolButton {
                        objectName: "versionRowDetailButton"
                        iconName: "info"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        onClicked: {
                            if (page.bridgeAvailable)
                                Versions.openDetail(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "versionRowModsButton"
                        //: 旧界面只有装了加载器的版本才显示"安装模组"入口
                        visible: modelData.hasLoader
                        iconName: "mods"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        onClicked: {
                            if (page.bridgeAvailable)
                                Versions.browseMods(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "versionRowResourcesButton"
                        iconName: "settings-gear"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        onClicked: {
                            if (page.bridgeAvailable)
                                Versions.openResourceManager(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "versionRowRenameButton"
                        iconName: "edit"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        enabled: page.bridgeAvailable && !Versions.busy
                        onClicked: {
                            if (page.bridgeAvailable)
                                Versions.renameVersion(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "versionRowDeleteButton"
                        iconName: "trash"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        enabled: page.bridgeAvailable && !Versions.busy
                        onClicked: {
                            if (page.bridgeAvailable)
                                Versions.removeVersion(modelData.id)
                        }
                    }
                }
            }
        }

        //: 搜索没命中：列表区给一句话，搜索框仍然在（否则关键词没法清）
        Text {
            objectName: "versionsNoMatch"
            Layout.fillWidth: true
            visible: page.bridgeAvailable && Versions.loadState === "ready" && Versions.count === 0
            text: Tr?.map["mod_browser_no_results"] ?? "mod_browser_no_results"
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            horizontalAlignment: Text.AlignHCenter
        }

        // ── 底部：启动 / 强杀（旧已安装面板底部那两个按钮）──
        RowLayout {
            objectName: "versionsActionBar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "versionsLaunchButton"
                Layout.fillWidth: true
                primary: true
                text: Tr?.map["version_launch_title"] ?? "version_launch_title"
                iconName: "play"
                enabled: page.bridgeAvailable && Versions.canLaunch
                loading: page.bridgeAvailable
                         && (Versions.gameState === "launching" || Versions.gameState === "waiting")
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.launch("")
                }
            }

            FmButton {
                objectName: "versionsKillButton"
                danger: true
                text: Tr?.map["home_kill_game"] ?? "home_kill_game"
                iconName: "stop"
                enabled: page.bridgeAvailable && Versions.canKill
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.killGame()
                }
            }
        }
    }

    // ── 详情模式 ─────────────────────────────────────────────
    ColumnLayout {
        objectName: "versionsDetailPane"
        anchors.fill: parent
        visible: page.detailMode
        spacing: Theme?.spacingMd ?? 10

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmToolButton {
                objectName: "versionsDetailBack"
                iconName: "arrow-left"
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.goBack()
                }
            }

            Text {
                objectName: "versionsDetailTitle"
                Layout.fillWidth: true
                text: page.bridgeAvailable && Versions.hasCurrent ? Versions.currentId : Tr?.map["version_detail_title"] ?? "version_detail_title"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            FmTag {
                objectName: "versionsDetailVanillaTag"
                visible: page.bridgeAvailable && String(Versions.detail.vanilla || "").length > 0
                text: page.bridgeAvailable ? String(Versions.detail.vanilla || "") : ""
                level: "neutral"
            }

            FmTag {
                objectName: "versionsDetailLoaderTag"
                visible: page.bridgeAvailable && String(Versions.detail.loader || "").length > 0
                text: page.bridgeAvailable
                      ? (String(Versions.detail.loader || "") + " "
                         + String(Versions.detail.loaderVersion || "")).trim()
                      : ""
                level: "accent"
            }

            FmTag {
                objectName: "versionsDetailLoaderNoneTag"
                visible: page.bridgeAvailable && Versions.hasCurrent
                         && String(Versions.detail.loader || "").length === 0
                text: Tr?.map["version_loader_none"] ?? "version_loader_none"
                level: "neutral"
            }
        }

        FmInfoBar {
            objectName: "versionsDetailMissing"
            Layout.fillWidth: true
            visible: page.bridgeAvailable && Versions.hasCurrent && !Versions.detail.exists
            level: "warning"
            text: Tr?.map["version_detail_missing"] ?? "version_detail_missing"
        }

        Repeater {
            objectName: "versionsDetailInfo"
            model: page.infoRows

            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Text {
                    text: modelData.label
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    Layout.preferredWidth: 110
                }

                Text {
                    objectName: "versionsDetailInfoValue"
                    Layout.fillWidth: true
                    text: modelData.value
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    elide: Text.ElideMiddle
                }
            }
        }

        Item {
            Layout.fillHeight: true
        }

        RowLayout {
            objectName: "versionsDetailActions"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "versionsDetailLaunch"
                primary: true
                text: Tr?.map["version_launch_title"] ?? "version_launch_title"
                iconName: "play"
                enabled: page.bridgeAvailable && Versions.canLaunch && Versions.detail.exists
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.launch(Versions.currentId)
                }
            }

            FmButton {
                objectName: "versionsDetailOpenFolder"
                primary: false
                text: Tr?.map["version_open_folder"] ?? "version_open_folder"
                iconName: "folder-open"
                enabled: page.bridgeAvailable && Versions.hasCurrent
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.openFolder(Versions.currentId)
                }
            }

            FmButton {
                objectName: "versionsDetailVerify"
                primary: false
                text: Tr?.map["version_verify"] ?? "version_verify"
                iconName: "check"
                enabled: page.bridgeAvailable && Versions.hasCurrent && !Versions.busy
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.verify(Versions.currentId)
                }
            }

            FmButton {
                objectName: "versionsDetailMods"
                primary: false
                text: Tr?.map["version_row_mods"] ?? "version_row_mods"
                iconName: "mods"
                enabled: page.bridgeAvailable && Versions.hasCurrent && Versions.detail.hasLoader === true
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.browseMods(Versions.currentId)
                }
            }

            FmButton {
                objectName: "versionsDetailResources"
                primary: false
                text: Tr?.map["version_row_settings"] ?? "version_row_settings"
                iconName: "settings-gear"
                enabled: page.bridgeAvailable && Versions.hasCurrent
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.openResourceManager(Versions.currentId)
                }
            }

            FmButton {
                objectName: "versionsDetailRename"
                primary: false
                text: Tr?.map["version_row_rename"] ?? "version_row_rename"
                iconName: "edit"
                enabled: page.bridgeAvailable && Versions.hasCurrent && !Versions.busy
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.renameVersion(Versions.currentId)
                }
            }

            FmButton {
                objectName: "versionsDetailDelete"
                danger: true
                text: Tr?.map["version_row_delete"] ?? "version_row_delete"
                iconName: "trash"
                enabled: page.bridgeAvailable && Versions.hasCurrent && !Versions.busy
                onClicked: {
                    if (page.bridgeAvailable)
                        Versions.removeVersion(Versions.currentId)
                }
            }
        }
    }

    //: 详情里的"字段 / 值"两列：值全部来自桥（Java 大版本为 0 时显示"未声明"）
    readonly property var infoRows: {
        if (!page || !page.bridgeAvailable || !Versions.hasCurrent)
            return []
        var data = Versions.detail
        var java = parseInt(data.javaMajor || 0)
        var none = Tr?.map["version_loader_none"] ?? "version_loader_none"
        return [
            {
                "label": Tr?.map["version_sort_vanilla"] ?? "version_sort_vanilla",
                "value": String(data.vanilla || "-")
            },
            {
                "label": Tr?.map["version_sort_loader"] ?? "version_sort_loader",
                "value": String(data.loader || none)
            },
            {
                "label": Tr?.map["version_field_java"] ?? "version_field_java",
                "value": java > 0 ? ("Java " + java)
                                  : (Tr?.map["version_java_unknown"] ?? "version_java_unknown")
            },
            {
                "label": Tr?.map["version_field_mods"] ?? "version_field_mods",
                "value": String(data.modsCount || 0)
            },
            {
                "label": Tr?.map["version_field_path"] ?? "version_field_path",
                "value": String(data.path || "-")
            }
        ]
    }
}
