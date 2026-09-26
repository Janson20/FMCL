// 面包屑 —— 标题栏中间那一段：`首页 / 版本 / 版本详情`。
//
// 数据源是 `Nav.breadcrumb`（栈的映射，见 nav_bridge 的模块说明）：它带着 NOTIFY，
// 所以路由一变这里就重算 —— 这正是契约 §5.2 要的"绑定只读属性"。
//
// 每一项都可点：点第 i 项 = 退 `back_count` 层（`Nav.goBackTo`），这是 P8
// （"每层页面支持返回与深链"）在界面上的落点。

import QtQuick
import QtQuick.Layouts

RowLayout {
    id: crumb
    objectName: "breadcrumb"

    spacing: Theme?.spacingXs ?? 0

    // 绑定不能写成 `Nav.breadcrumb()` 之类的函数调用：这里读的是属性本身。
    property var items: Nav ? Nav.breadcrumb : []

    Repeater {
        model: crumb.items

        delegate: RowLayout {
            spacing: Theme?.spacingXs ?? 0

            Text {
                objectName: "crumbSeparator"
                visible: index > 0
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
