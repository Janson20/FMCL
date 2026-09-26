// FmListItem.qml —— 列表项（阶段 2 任务 2.16）。
//
// 什么时候用它：ListView / Repeater 的**委托**（版本列表、模组列表、账号列表、服务器列表），
//   或者"标题 + 副标题 + 右侧次要信息"的单行条目。
// 什么时候不要用：卡片式的内容块（用 FmCard）；导航项（`shell/Navigation.qml` 有自己的
//   高亮语义，导航项要跟 Nav.currentRoute 走）；整块可点的大区域（把 FmListItem 放进
//   FmCard 里，或者直接用 FmButton）。
//
// 状态：正常 / 悬停 / 选中（`selected`，由页面按"当前详情页看的是哪一条"给）/
//   禁用 / 不可点（`clickable: false` 时鼠标不变手型、不发 clicked）。
// 文案（title / subtitle / trailingText）全部由调用方给 —— 组件不拼文案、不判语义。

import QtQuick

Item {
    id: entry
    objectName: "fmListItem"

    property string title: ""
    property string subtitle: ""
    //: 可选图标名（不带扩展名；空串 = 不显示）
    property string iconName: ""
    //: 右侧次要信息（版本号、体积、时间……由调用方格式化好）
    property string trailingText: ""
    property bool selected: false
    property bool clickable: true

    readonly property bool interactive: clickable && enabled

    signal clicked()

    implicitWidth: 320
    implicitHeight: subtitle.length > 0 ? 52 : 40

    Rectangle {
        id: face
        objectName: "fmListItemFace"
        anchors.fill: parent
        radius: Theme?.radiusSm ?? 0
        color: entry.selected
               ? Theme?.bgLight ?? "transparent"
               : (entry.interactive && area.containsMouse ? Theme?.cardBg ?? "transparent" : "transparent")
        border.width: entry.selected ? 1 : 0
        border.color: Theme?.accent ?? "transparent"
    }

    FmIcon {
        id: leading
        objectName: "fmListItemIcon"
        visible: entry.iconName.length > 0
        name: entry.iconName
        color: entry.enabled ? Theme?.textSecondary ?? "transparent" : Theme?.cardBorder ?? "transparent"
        size: Theme?.iconSize ?? 0
        anchors.left: parent.left
        anchors.leftMargin: Theme?.spacingSm ?? 0
        anchors.verticalCenter: parent.verticalCenter
    }

    Column {
        id: texts
        anchors.left: leading.visible ? leading.right : parent.left
        anchors.leftMargin: Theme?.spacingSm ?? 0
        anchors.right: trailing.visible ? trailing.left : parent.right
        anchors.rightMargin: Theme?.spacingSm ?? 0
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2

        Text {
            objectName: "fmListItemTitle"
            width: parent.width
            text: entry.title
            color: entry.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            elide: Text.ElideRight
        }

        Text {
            objectName: "fmListItemSubtitle"
            width: parent.width
            visible: entry.subtitle.length > 0
            text: entry.subtitle
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }
    }

    Text {
        id: trailing
        objectName: "fmListItemTrailing"
        visible: entry.trailingText.length > 0
        anchors.right: parent.right
        anchors.rightMargin: Theme?.spacingSm ?? 0
        anchors.verticalCenter: parent.verticalCenter
        text: entry.trailingText
        color: Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
        elide: Text.ElideRight
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        enabled: entry.interactive
        cursorShape: Qt.PointingHandCursor
        onClicked: entry.clicked()
    }
}
