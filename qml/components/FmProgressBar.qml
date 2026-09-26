// FmProgressBar.qml —— 进度条（阶段 2 任务 2.16）。
//
// 什么时候用它：**能算出百分比**的长任务（下载、安装、校验、解压），而且进度条要
//   跟一段文案在一起（标题 + 百分比 + 条）。
// 什么时候不要用：算不出总量的等待（用 `indeterminate: true`，或者用 FmLoadingState）；
//   页面级的加载占位（用 FmLoadingState）；环形的地方（用 FmProgressRing）。
//
// 两种模式：确定（`value` 0~1）与不确定（`indeterminate: true`，一条来回走的色块）。
// 取值语义：`value` 是**0~1 的比例**，不是百分数 —— 页面从服务层拿到 current/total
//   之后自己除（红线 2：业务只存在于 services/，组件不做单位换算以外的事）。
// 没有任何渐变（项目 UI 红线 5），前后景都是实色。

import QtQuick
import QtQuick.Layouts

Item {
    id: bar
    objectName: "fmProgressBar"

    //: 0~1；超出范围会被夹住（服务层给了 NaN / 越界值也不至于画出负宽度）
    property real value: 0
    property bool indeterminate: false
    //: 条上方的说明文字（由调用方给，可空）
    property string label: ""
    //: 是否显示右侧百分比
    property bool showPercent: true
    //: 条的高度
    property int barHeight: 8

    readonly property real clamped: isNaN(value) ? 0 : Math.max(0, Math.min(1, value))

    implicitWidth: 240
    implicitHeight: column.implicitHeight

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Theme?.spacingXs ?? 0

        RowLayout {
            objectName: "fmProgressBarHeader"
            Layout.fillWidth: true
            visible: bar.label.length > 0 || bar.showPercent

            Text {
                objectName: "fmProgressBarLabel"
                Layout.fillWidth: true
                text: bar.label
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }

            Text {
                objectName: "fmProgressBarPercent"
                visible: bar.showPercent && !bar.indeterminate
                text: Math.round(bar.clamped * 100) + "%"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }
        }

        Rectangle {
            id: track
            objectName: "fmProgressBarTrack"
            Layout.fillWidth: true
            Layout.preferredHeight: bar.barHeight
            radius: height / 2
            color: Theme?.cardBorder ?? "transparent"
            clip: true

            Rectangle {
                objectName: "fmProgressBarFill"
                visible: !bar.indeterminate
                width: track.width * bar.clamped
                height: parent.height
                radius: parent.radius
                color: bar.enabled ? Theme?.accent ?? "transparent" : Theme?.textSecondary ?? "transparent"
            }

            Rectangle {
                id: segment
                objectName: "fmProgressBarSegment"
                visible: bar.indeterminate
                width: Math.max(12, track.width * 0.3)
                height: track.height
                radius: parent.radius
                color: bar.enabled ? Theme?.accent ?? "transparent" : Theme?.textSecondary ?? "transparent"

                NumberAnimation on x {
                    running: bar.indeterminate && bar.visible
                    loops: Animation.Infinite
                    from: -segment.width
                    to: track.width
                    duration: 1200
                }
            }
        }
    }
}
