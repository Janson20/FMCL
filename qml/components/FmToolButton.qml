// FmToolButton.qml —— 纯图标的紧凑按钮（阶段 2 任务 2.16 组件库／界面返工 B 组新增）。
//
// 什么时候用它：**只有图标、没有文字**的方形命中区 —— 顶栏的返回/通知/账号、
//   列表行的尾部动作（复制/删除）、面板右上角的关闭。尺寸小、悬停只换底色。
// 什么时候不要用：有文字的按钮（用 `FmButton`）；导航项（那是 `shell/Navigation.qml` 的导航项）；
//   整行点击的列表（用 `FmListItem`）；需要"主/次/危险"语义的地方（`FmButton` 才有）。
//
// 为什么不是 `FluIconButton`：本项目定的是**界面只用自研 Fm* 组件**（颜色只有一个来源
//   `Theme.*`，且要能被闸门 R8/R9 静态守住）。FluentUI 的按钮颜色来自 `FluTheme` 的 17 个键，
//   与我们的 12 键 + 15 个派生令牌是两套来源，混用就会出现"同一个界面两种灰"。
//
// 三种状态（都用 Theme 的叠加令牌，不写死颜色）：
//   * 常态   —— 透明底 + `iconColor`（默认二级文字色）
//   * 悬停   —— `Theme.itemHover`（白 6%）＋ 图标换成一级文字色
//   * 按下   —— `Theme.itemPress`（白 9%）
//   * 选中   —— `selected: true` 时图标与底色走强调色（`accentSoft`）＋ 3px 指示条可选
// 禁用态统一降不透明度到 0.35 并吞掉点击（比"换个灰色"更省事，也不会与主题打架）。

import QtQuick

Item {
    id: control
    objectName: "fmToolButton"

    //: 图标名（不带目录与扩展名，规则见 qml/assets/icons/README.md）
    property string iconName: ""
    //: 常态图标色；悬停时会自动提到 `Theme.textPrimary`（除非调用方显式覆盖 hoverIconColor）
    property color iconColor: Theme?.textSecondary ?? "transparent"
    //: 显式指定悬停色；不设时按 `Theme.textPrimary` 走
    property color hoverIconColor: Theme?.textPrimary ?? "transparent"
    //: 图标边长（默认 20）
    property int iconSize: Theme?.iconSize ?? 0
    //: 选中态：图标与底色都走强调色（顶栏的"当前页"、面板的"当前工具"）
    property bool selected: false
    //: 方形边长（默认 32 —— 与顶栏的行高、导航项的高度同一档）
    property int side: 32
    //: 角标提示（可选，例如未读数；空串不显示）
    property string badgeText: ""

    readonly property bool interactive: enabled
    readonly property alias hovered: area.containsMouse
    readonly property alias pressed: area.pressed

    signal clicked()

    implicitWidth: side
    implicitHeight: side
    opacity: enabled ? 1.0 : 0.35

    Rectangle {
        id: face
        objectName: "fmToolButtonFace"
        anchors.fill: parent
        radius: Theme?.radiusMd ?? 0
        color: {
            if (control.selected)
                return area.pressed ? (Theme?.itemPress ?? "transparent") : (Theme?.accentSoft ?? "transparent")
            if (area.pressed)
                return Theme?.itemPress ?? "transparent"
            if (area.containsMouse)
                return Theme?.itemHover ?? "transparent"
            return "transparent"
        }

        Behavior on color {
            ColorAnimation {
                duration: Theme?.durationFast ?? 110
            }
        }

        FmIcon {
            objectName: "fmToolButtonIcon"
            anchors.centerIn: parent
            name: control.iconName
            color: control.selected
                   ? (Theme?.accent ?? "transparent")
                   : (area.containsMouse ? control.hoverIconColor : control.iconColor)
            size: control.iconSize
        }

        // 角标：小圆点 + 数字（未读通知这类）
        Rectangle {
            objectName: "fmToolButtonBadge"
            visible: control.badgeText.length > 0
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.rightMargin: 2
            anchors.topMargin: 2
            width: Math.max(14, badge.implicitWidth + 8)
            height: 14
            radius: height / 2
            color: Theme?.accent ?? "transparent"

            Text {
                id: badge
                anchors.centerIn: parent
                text: control.badgeText
                color: Theme?.accentText ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        enabled: control.interactive
        cursorShape: Qt.PointingHandCursor
        onClicked: control.clicked()
    }

    // 键盘激活（与 FmButton 同一约定：焦点在本件上时回车/空格 = 点击）
    Keys.onReturnPressed: if (control.interactive) control.clicked()
    Keys.onEnterPressed: if (control.interactive) control.clicked()
    Keys.onSpacePressed: if (control.interactive) control.clicked()
}
