// FmTag.qml —— 标签 / 徽标（阶段 2 任务 2.16）。
//
// 什么时候用它：给一条数据挂一个**短**的语义标记 —— "已安装"、"快照"、"Forge"、
//   "仅客户端"、"需更新"、"成就已解锁"。也用于列表项的次要状态。
// 什么时候不要用：可点击的筛选器（那是 FmButton 的次按钮，标签不该有点击语义）；
//   一段说明文字（用 FmInfoBar）；版本号这类中性信息（用 FmListItem 的 trailingText）。
//
// 五档语义（`level`）：neutral / accent / success / warning / error —— 底色一律用
//   Theme.* 里的卡片色（保证在任何主题下都与卡片底有区分），**语义色只用在文字与描边上**，
//   这样一个界面里挂十几个标签也不会花。文案由调用方给（`Tr.map[…]`）。
// `closable: true` 时右侧出现关闭命中区并发 `closed()`（组件不自己删自己 —— 删数据是页面的事）。

import QtQuick

Rectangle {
    id: tag
    objectName: "fmTag"

    property string text: ""
    property string level: "neutral"
    property bool closable: false
    //: 可选图标名（不带扩展名）；空串 = 不显示
    property string iconName: ""

    signal closed()

    implicitWidth: row.implicitWidth + 2 * (Theme?.spacingSm ?? 0)
    implicitHeight: Math.max(20, row.implicitHeight + 2 * (Theme?.spacingXs ?? 0)) + 2
    radius: height / 2
    color: Theme?.bgLight ?? "transparent"
    border.width: 1
    border.color: tag.levelColor()

    function levelColor() {
        if (level === "accent")
            return Theme?.accent ?? "transparent"
        if (level === "success")
            return Theme?.success ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        return Theme?.cardBorder ?? "transparent"
    }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: Theme?.spacingXs ?? 0

        FmIcon {
            objectName: "fmTagIcon"
            visible: tag.iconName.length > 0
            name: tag.iconName
            color: tag.enabled ? tag.levelColor() : Theme?.textSecondary ?? "transparent"
            size: Math.round((Theme?.fontSizeSmall ?? 10) * 1.2)
            anchors.verticalCenter: parent.verticalCenter
        }

        Text {
            objectName: "fmTagText"
            anchors.verticalCenter: parent.verticalCenter
            text: tag.text
            color: tag.enabled ? tag.levelColor() : Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        Item {
            objectName: "fmTagClose"
            visible: tag.closable
            width: Math.round((Theme?.fontSizeSmall ?? 10) * 1.2)
            height: width
            anchors.verticalCenter: parent.verticalCenter

            FmIcon {
                anchors.fill: parent
                name: "close"
                color: closeArea.containsMouse ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
                size: Math.round((Theme?.fontSizeSmall ?? 10) * 1.2)
            }

            MouseArea {
                id: closeArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: tag.closed()
            }
        }
    }
}
