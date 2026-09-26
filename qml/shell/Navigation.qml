// 一级导航 —— 02 第三节骨架图的左栏：12 个**领域**入口（P1：只放领域，不放操作按钮）。
//
// 点击 = `Nav.push(routeId)`：一级导航是"压栈"而不是"切标签页"，因此每次切换都
// 保留返回路径（P8），当前项由 `Nav.currentRoute` 的前缀决定高亮。
//
// 数据来自 `Shell.navItems()`（内置 12 项 + 已登记的插件一级页）。它是**槽**，
// 槽不参与绑定跟踪（契约 §5.2 的同一理由），所以这里用一个普通属性缓存，
// 并在 `Nav.pluginRouteAdded` 时显式重取。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: nav
    objectName: "navigation"

    color: Theme?.bgMedium ?? "transparent"
    implicitWidth: 208

    property var items: []

    function refresh() {
        items = Shell ? Shell.navItems() : []
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
        anchors.margins: Theme?.spacingSm ?? 0
        spacing: Theme?.spacingXs ?? 0
        clip: true
        model: nav.items

        delegate: Rectangle {
            id: navItem
            objectName: "navItem_" + modelData.id

            width: navList.width
            height: 40
            radius: Theme?.radiusMd ?? 0
            // 当前领域高亮：读的是 Nav.currentRoute（带 NOTIFY 的属性），
            // 所以路由一变这里就重算；子页面（versions/detail）也归到 versions 上。
            color: {
                var current = Nav ? String(Nav.currentRoute) : ""
                if (current === modelData.id || current.indexOf(modelData.id + "/") === 0)
                    return Theme?.bgLight ?? "transparent"
                return hover.hovered ? Theme?.cardBg ?? "transparent" : Theme?.bgMedium ?? "transparent"
            }

            HoverHandler {
                id: hover
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme?.spacingSm ?? 0
                anchors.rightMargin: Theme?.spacingSm ?? 0
                spacing: Theme?.spacingSm ?? 0

                Image {
                    objectName: "navItemIcon"
                    source: (Runtime?.iconUrl(modelData.icon) ?? "")
                    sourceSize.width: Theme?.iconSize ?? 0
                    sourceSize.height: Theme?.iconSize ?? 0
                    width: Theme?.iconSize ?? 0
                    height: Theme?.iconSize ?? 0
                    smooth: true
                }

                Text {
                    objectName: "navItemText"
                    Layout.fillWidth: true
                    text: Tr?.map[modelData.title_key] ?? modelData.title_key
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }
            }

            TapHandler {
                onTapped: {
                    if (Nav)
                        Nav.push(modelData.id)
                }
            }
        }
    }
}
