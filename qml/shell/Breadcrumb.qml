// 面包屑 —— 标题栏中间那一段：`首页 / 版本 / 版本详情`。
//
// 数据源是 `Nav.breadcrumb`（栈的映射，见 nav_bridge 的模块说明）：它带着 NOTIFY，
// 所以路由一变这里就重算 —— 这正是契约 §5.2 要的"绑定只读属性"。
//
// 每一项都可点：点第 i 项 = 退 `back_count` 层（`Nav.goBackTo`），这是 P8
// （"每层页面支持返回与深链"）在界面上的落点。
//
// **只显示最近 MAX_VISIBLE 项**（用户 2026-10-06 实测报的问题：栈一旦深了，
// 标题栏就被挤成一长串 —— "首页 / 版本 / 首页 / 版本 / 基岩版 / …" 九项塞满整行，
// 连当前页在哪都看不出来）。截断的是**显示**，不是栈：被藏起来的那些项仍然在
// `Nav.breadcrumb` 里、`back_count` 也仍然按完整栈算，所以点最近这几项退的层数依旧正确。
// 前面补一个 `…` 说明"上面还有"（它不可点 —— 想一次退很多层应该用左上角的返回箭头）。

import QtQuick
import QtQuick.Layouts

RowLayout {
    id: crumb
    objectName: "breadcrumb"

    spacing: Theme?.spacingXs ?? 0

    //: 最多显示几项（含当前页）
    readonly property int maxVisible: 3

    // 绑定不能写成 `Nav.breadcrumb()` 之类的函数调用：这里读的是属性本身。
    property var allItems: Nav ? Nav.breadcrumb : []
    //: 截断后的显示项（保留最后 maxVisible 项）
    property var items: allItems.length > crumb.maxVisible
                        ? allItems.slice(allItems.length - crumb.maxVisible)
                        : allItems
    //: 有被藏起来的项（前面显示一个省略号）
    readonly property bool truncated: allItems.length > items.length

    Text {
        objectName: "crumbEllipsis"
        visible: crumb.truncated
        text: "…"
        color: Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
    }

    Repeater {
        model: crumb.items

        delegate: RowLayout {
            spacing: Theme?.spacingXs ?? 0

            Text {
                objectName: "crumbSeparator"
                visible: index > 0 || crumb.truncated
                text: "/"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            Text {
                objectName: "crumbItem"
                text: Tr?.map[modelData.title_key] ?? modelData.title_key
                color: modelData.is_current ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                elide: Text.ElideRight
                Layout.maximumWidth: 220
            }

            TapHandler {
                enabled: !modelData.is_current && modelData.back_count > 0
                onTapped: {
                    if (Nav)
                        Nav.goBackTo(modelData.back_count)
                }
            }
        }
    }
}
