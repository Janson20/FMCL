// FmProgressRing.qml —— 进度环（阶段 2 任务 2.16）。
//
// 什么时候用它：**紧凑位置**上的进度或忙碌指示 —— 卡片右上角、列表项尾部、按钮旁，
//   横向空间不够放 FmProgressBar 的时候。
// 什么时候不要用：一整页的加载态（用 FmLoadingState，它带文案与占位布局）；
//   有明确文案的下载/安装进度（用 FmProgressBar，百分比 + 说明读起来更清楚）。
//
// 两种模式：确定（`value` 0~1 画一段弧）与不确定（`indeterminate: true` 时四分之一弧
//   整体旋转）。实现用 `QtQuick.Shapes` 的 `PathAngleArc`（矢量弧线），**没有渐变**
//   （项目 UI 红线 5）；Qt 6.7 的 `PathAngleArc` 用的是 `radiusX` / `radiusY`，
//   没有 `radius` 这个属性（写错就是一条 "Cannot assign to non-existent property"）。
// 颜色只来自 Theme.*（可覆盖，但仍应从 Theme.* 取）。

import QtQuick
import QtQuick.Shapes

Item {
    id: ring
    objectName: "fmProgressRing"

    property real value: 0
    property bool indeterminate: false
    property real lineWidth: 4
    property bool showPercent: false
    property color ringColor: Theme?.accent ?? "transparent"
    property color trackColor: Theme?.cardBorder ?? "transparent"

    readonly property real clamped: isNaN(value) ? 0 : Math.max(0, Math.min(1, value))
    readonly property real radius: Math.max(0, Math.min(width, height) / 2 - lineWidth / 2)

    implicitWidth: 44
    implicitHeight: 44

    Shape {
        id: shape
        objectName: "fmProgressRingShape"
        anchors.fill: parent
        antialiasing: true

        // 底环：永远画一整圈，让"还差多少"一眼看得出
        ShapePath {
            strokeColor: ring.trackColor
            strokeWidth: ring.lineWidth
            fillColor: "transparent"
            PathAngleArc {
                centerX: ring.width / 2
                centerY: ring.height / 2
                radiusX: ring.radius
                radiusY: ring.radius
                startAngle: 0
                sweepAngle: 360
            }
        }

        // 进度弧：确定模式按比例，不确定模式画四分之一圈再整体旋转
        ShapePath {
            objectName: "fmProgressRingArc"
            strokeColor: ring.enabled ? ring.ringColor : Theme?.textSecondary ?? "transparent"
            strokeWidth: ring.lineWidth
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ring.width / 2
                centerY: ring.height / 2
                radiusX: ring.radius
                radiusY: ring.radius
                startAngle: -90
                sweepAngle: ring.indeterminate ? 90 : 360 * ring.clamped
            }
        }
    }

    RotationAnimator {
        target: shape
        running: ring.indeterminate && ring.visible
        from: 0
        to: 360
        duration: 1100
        loops: Animation.Infinite
    }

    Text {
        objectName: "fmProgressRingPercent"
        anchors.centerIn: parent
        visible: ring.showPercent && !ring.indeterminate
        text: Math.round(ring.clamped * 100) + "%"
        color: Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
    }
}
