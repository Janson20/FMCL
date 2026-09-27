// 一级导航 —— 02 第三节骨架图的左栏：12 个**领域**入口（P1：只放领域，不放操作按钮）。
//
// 点击 = `Nav.push(routeId)`：一级导航是"压栈"而不是"切标签页"，因此每次切换都
// 保留返回路径（P8），当前项由 `Nav.currentRoute` 的前缀决定高亮。
//
// ## 界面返工 B 组重做的三件事
//
// 1. **分组**：12 项平铺时没有任何层次，用户要一行一行读。分组数据来自桥
//    （`Shell.navItems()` 的 `group` / `group_title_key`，成员表在 `nav_bridge.NAV_GROUPS`）——
//    **不**在 QML 里写死，否则加一个领域就会出现"多了一项但没有组"的静默缺口。
// 2. **选中态**：从"换一档底色"改成参考项目 `FusionMusicPlayer` 的做法 —— `accentSoft` 底
//    + 3px 强调色指示条 + 图标与文字都走强调色（文字加粗），一眼看得出在哪一页。
// 3. **悬停与动效**：`itemHover` 叠加色 + 110ms 颜色过渡；图标按状态着色（不再是一片灰）。
//
// 数据来自 `Shell.navItems()`。它是**槽**，槽不参与绑定跟踪（契约 §5.2 的同一理由），
// 所以这里用一个普通属性缓存，并在 `Nav.pluginRouteAdded` 时显式重取。
//
// 约束（闸门 R1/R2/R3/R4/R8）：无渐变、无 emoji、无中文字面量、无颜色字面量。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    id: nav
    objectName: "navigation"

    color: Theme?.navBg ?? "transparent"
    implicitWidth: Theme?.navWidth ?? 208

    //: 一级导航项（来自 `Shell.navItems()`，含 `group` / `group_title_key`）
    property var items: []

    //: 组标题的显示顺序（与桥里的 `NAV_GROUPS` 同序）。
    //: 这里只决定**显示顺序**，成员关系仍以桥为准 —— 两边都写成员就成了两份真相。
    readonly property var groupOrder: ["game", "resources", "tools", "plugin"]

    //: 展平成"组标题 + 导航项"的混合模型。
    //: 为什么不用 ListView 自带的 `section`：分组委托只拿得到分组的**值**（组 id），
    //: 而标题要显示的是 `group_title_key`；而且 `section` 要求同组项在模型里**连续**，
    //: 摊平一次比满足那个隐含前提更好读。
    readonly property var rows: nav.buildRows()

    function buildRows() {
        var out = []
        for (var g = 0; g < nav.groupOrder.length; ++g) {
            var groupId = nav.groupOrder[g]
            var groupRows = []
            var titleKey = ""
            for (var i = 0; i < nav.items.length; ++i) {
                var entry = nav.items[i]
                if (String(entry.group) !== groupId)
                    continue
                if (!titleKey)
                    titleKey = String(entry.group_title_key)
                groupRows.push({
                    "kind": "item",
                    "id": String(entry.id),
                    "title_key": String(entry.title_key),
                    // 兜底空串：`FmIcon` 拿到空名字就不画（拿到 "undefined" 会去请求一张
                    // 叫 undefined 的图，provider 会报错 —— 插件页曾经踩到过）
                    "icon": entry.icon ? String(entry.icon) : ""
                })
            }
            if (groupRows.length === 0)
                continue // 该组在当前平台没有可用项（例如非 Windows 的基岩版）→ 连标题都不画
            // 组标题行**也带上 id/icon 字段**（空值）：delegate 里两套子树都在同一个
            // Item 下、靠 `visible` 切换，藏起来的那套照样会求值 —— 少了字段就会去请求
            // 一张叫 `undefined` 的图标（实测踩到，provider 报错但界面看不出异常）。
            out.push({"kind": "header", "group": groupId, "title_key": titleKey, "id": "", "icon": ""})
            for (var k = 0; k < groupRows.length; ++k)
                out.push(groupRows[k])
        }
        return out
    }

    function refresh() {
        items = Shell ? Shell.navItems() : []
    }

    function isCurrent(routeId) {
        var current = Nav ? String(Nav.currentRoute) : ""
        return current === routeId || current.indexOf(routeId + "/") === 0
    }

    Component.onCompleted: refresh()

    Connections {
        target: Nav

        function onPluginRouteAdded(id) {
            nav.refresh()
        }
    }

    ListView {
        id: navList
        objectName: "navigationList"

        anchors.fill: parent
        anchors.topMargin: Theme?.spacingSm ?? 0
        anchors.bottomMargin: Theme?.spacingSm ?? 0
        spacing: 2
        clip: true
        model: nav.rows
        boundsBehavior: Flickable.StopAtBounds

        // 一个 delegate 承担两种行（组标题 / 导航项）。三条约定：
        //
        // 1. **objectName 必须落在 delegate 根上** —— delegate 的根才是 `contentItem`
        //    的子项，测试与探针正是按它找行的（`tests/qml_shell_probe.py` 的 `nav_delegates()`）。
        // 2. 两套子树都在同一个 Item 下、靠 `visible` 切换。**不用 `Loader`**：`Loader`
        //    加载出来的组件是**独立作用域**，看不到 delegate 的 `modelData` 与本地 id
        //    （实测报一片 `ReferenceError: modelData is not defined`）。
        // 3. 因此**每一行都要字段齐全**（组标题行的 `id`/`icon` 是空串）—— 藏起来的那套
        //    子树照样会求值，缺字段就会去请求一张叫 `undefined` 的图标（实测踩到）。
        delegate: Item {
            id: row
            width: navList.width
            height: row.isHeader ? 28 : 44

            readonly property bool isHeader: String(modelData.kind) === "header"
            readonly property bool current: !row.isHeader && nav.isCurrent(String(modelData.id))

            objectName: row.isHeader ? ("navGroup_" + modelData.group) : ("navItem_" + modelData.id)

            // ── 组标题 ──
            Text {
                objectName: "navGroupLabel"
                visible: row.isHeader
                anchors.left: parent.left
                anchors.leftMargin: Theme?.spacingLg ?? 0
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Theme?.spacingXs ?? 0
                text: Tr?.map[modelData.title_key] ?? modelData.title_key
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            // ── 导航项 ──
            Item {
                id: navItem
                visible: !row.isHeader
                anchors.fill: parent

                readonly property bool hovered: hover.hovered

                Rectangle {
                    id: face
                    anchors.fill: parent
                    anchors.leftMargin: Theme?.spacingSm ?? 0
                    anchors.rightMargin: Theme?.spacingSm ?? 0
                    radius: Theme?.radiusMd ?? 0
                    color: row.current ? (Theme?.accentSoft ?? "transparent")
                                       : (navItem.hovered ? (Theme?.itemHover ?? "transparent") : "transparent")

                    Behavior on color {
                        ColorAnimation {
                            duration: Theme?.durationFast ?? 110
                        }
                    }

                    // 选中指示条：3px 宽的强调色短条（参考项目同款）
                    Rectangle {
                        objectName: "navItemIndicator"
                        width: 3
                        height: 18
                        radius: 1.5
                        color: Theme?.accent ?? "transparent"
                        opacity: row.current ? 1 : 0
                        anchors.left: parent.left
                        anchors.leftMargin: 1
                        anchors.verticalCenter: parent.verticalCenter

                        Behavior on opacity {
                            NumberAnimation {
                                duration: Theme?.durationFast ?? 110
                            }
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme?.spacingMd ?? 0
                        anchors.rightMargin: Theme?.spacingSm ?? 0
                        spacing: Theme?.spacingSm ?? 0

                        FmIcon {
                            objectName: "navItemIcon"
                            name: modelData.icon ? String(modelData.icon) : ""
                            // 常态二级文字色 / 悬停一级 / 选中强调色 —— 三档都来自 Theme
                            color: row.current ? (Theme?.accent ?? "transparent")
                                               : (navItem.hovered ? (Theme?.textPrimary ?? "transparent")
                                                                  : (Theme?.textSecondary ?? "transparent"))
                            size: Theme?.iconSize ?? 0
                        }

                        Text {
                            objectName: "navItemText"
                            Layout.fillWidth: true
                            text: Tr?.map[modelData.title_key] ?? modelData.title_key
                            color: row.current ? (Theme?.accent ?? "transparent") : (Theme?.textPrimary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            font.bold: row.current
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }

                HoverHandler {
                    id: hover
                    cursorShape: Qt.PointingHandCursor
                }

                TapHandler {
                    enabled: !row.isHeader
                    onTapped: {
                        if (Nav)
                            Nav.push(String(modelData.id))
                    }
                }
            }
        }
    }
}
