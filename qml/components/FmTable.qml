// FmTable.qml —— 表格（阶段 2 任务 2.16）。
//
// 什么时候用它：**多列、需要对照阅读**的结构化数据 —— 模组列表（名字/版本/大小）、
//   已安装版本（版本/加载器/时间）、服务器配置、成就进度表。
// 什么时候不要用：只有一两列的行（用 FmListItem，行高更大更好点）；卡片式内容（用 FmCard）；
//   需要行内编辑（本件是只读展示，编辑请用 FmTextField 另开表单）。
//
// 数据形状（全部由调用方给，组件不猜字段）：
//   columns: [{ key: "name", title: <文案>, width: 180, align: "left" }, ...]
//   rows:    [{ name: "sodium", version: "0.5.8" }, ...]
// `key` 是取值字段名，`title` 是表头文案（写 `Tr.map[…]`，别硬编码中文 —— 闸门 R3）。
// `align` 取 "left" / "center" / "right"（缺省 left）。行数超过可视高度时**本件不滚动**：
//   在外层套 `Flickable` / `ScrollView`（滚动条属于页面布局，不属于表格）。
// 行点击（可选）：给 `clickable: true` 并接 `rowActivated(index, row)`。

import QtQuick

Rectangle {
    id: table
    objectName: "fmTable"

    property var columns: []
    property var rows: []
    //: rows 为空时显示的一行提示（可空；页面也可以自己用 FmEmptyState 替掉整张表）
    property string emptyText: ""
    property bool clickable: false
    property int rowHeight: 32
    property int headerHeight: 34

    signal rowActivated(int index, var row)

    readonly property int contentHeight: headerHeight + (rows ? rows.length : 0) * rowHeight + 1

    implicitWidth: 480
    implicitHeight: Math.max(contentHeight, headerHeight + rowHeight)
    radius: Theme?.radiusMd ?? 0
    color: Theme?.cardBg ?? "transparent"
    border.width: 1
    border.color: Theme?.cardBorder ?? "transparent"
    clip: true

    function alignment(name) {
        if (name === "center")
            return Text.AlignHCenter
        if (name === "right")
            return Text.AlignRight
        return Text.AlignLeft
    }

    Column {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top

        // 表头
        Rectangle {
            objectName: "fmTableHeader"
            width: parent.width
            height: table.headerHeight
            color: Theme?.bgLight ?? "transparent"

            Row {
                id: headerRow
                anchors.fill: parent
                anchors.leftMargin: Theme?.spacingSm ?? 0
                anchors.rightMargin: Theme?.spacingSm ?? 0

                Repeater {
                    model: table.columns

                    delegate: Text {
                        objectName: "fmTableHeaderCell"
                        width: modelData.width !== undefined ? modelData.width : 120
                        // 引用 `headerRow` 而不是 `parent`：`Repeater` 建委托时**父对象还没赋值**，
                        // `parent.height` 会先求值一次并抛 `TypeError: Cannot read property
                        // 'height' of null`（缺陷 D-145，`Gallery.qml` 的滚动演示同一个坑）。
                        height: headerRow.height
                        text: modelData.title !== undefined ? String(modelData.title) : String(modelData.key)
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        font.bold: true
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: table.alignment(modelData.align)
                        elide: Text.ElideRight
                    }
                }
            }
        }

        Rectangle {
            objectName: "fmTableDivider"
            width: parent.width
            height: 1
            color: Theme?.cardBorder ?? "transparent"
        }

        // 空表提示（只有一行，不要摆整块空状态）
        Text {
            objectName: "fmTableEmpty"
            visible: !table.rows || table.rows.length === 0
            width: parent.width
            height: table.rowHeight
            text: table.emptyText
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        Repeater {
            id: rowRepeater
            model: table.rows

            delegate: Rectangle {
                id: rowItem
                objectName: "fmTableRow"
                property var rowData: modelData
                property int rowIndex: index

                width: table.width
                height: table.rowHeight
                color: table.clickable && rowArea.containsMouse
                       ? Theme?.bgLight ?? "transparent"
                       : (index % 2 === 0 ? "transparent" : Theme?.bgMedium ?? "transparent")

                Row {
                    id: rowCells
                    anchors.fill: parent
                    anchors.leftMargin: Theme?.spacingSm ?? 0
                    anchors.rightMargin: Theme?.spacingSm ?? 0

                    Repeater {
                        model: table.columns

                        delegate: Text {
                            objectName: "fmTableCell"
                            property var columnData: modelData
                            width: columnData.width !== undefined ? columnData.width : 120
                            // 同上（D-145）：委托根不能读 `parent.*`
                            height: rowCells.height
                            text: rowItem.rowData ? String(rowItem.rowData[columnData.key] ?? "") : ""
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: table.alignment(columnData.align)
                            elide: Text.ElideRight
                        }
                    }
                }

                MouseArea {
                    id: rowArea
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: table.clickable
                    cursorShape: Qt.PointingHandCursor
                    onClicked: table.rowActivated(rowItem.rowIndex, rowItem.rowData)
                }
            }
        }
    }
}
