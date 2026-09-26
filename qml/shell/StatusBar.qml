// 底部状态条 —— 02 第三节骨架图的最后一行：`当前任务进度 / 状态文本 / 后台任务数`。
//
// 三块内容各自由谁提供：
//   * 进度：`Shell.busy`（= `Tasks.activeCount > 0`）驱动一条不确定进度条；
//   * 状态文本：`Shell.statusText` / `Shell.statusLevel`（10 秒后自动清空，A-09）；
//   * 后台任务数：`Shell.backgroundTaskCount`。
//
// 颜色全部来自 `Theme.*`（闸门 R8），级别 → 颜色的映射写成函数是为了只有一处。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: bar
    objectName: "statusBar"

    color: Theme?.bgMedium ?? "transparent"
    implicitHeight: 28

    function levelColor(level) {
        if (level === "success")
            return Theme?.success ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        return Theme?.textSecondary ?? "transparent"
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme?.spacingMd ?? 0
        anchors.rightMargin: Theme?.spacingMd ?? 0
        spacing: Theme?.spacingSm ?? 0

        // 桥接注册状态：**出问题才显示**（阶段 2 四条支线并行，缺桥是常态，
        // 关键是这个事实可见 —— 入口的 `test_main_qml_entry.py` 直接读这个 objectName）。
        // 一切正常时它显示 "bridges ok (N)"，为避免常驻噪声把它藏起来；
        // 元素本身一直在，所以 `text` 绑定始终有效（接线证据仍然成立）。
        Text {
            objectName: "bridgeStatusText"
            visible: Runtime ? Runtime.missingBridges.length > 0 : false
            text: Runtime ? Runtime.bridgeStatus : ""
            color: Theme?.warning ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
            Layout.maximumWidth: 320
        }

        // 当前任务进度：有后台任务时才有意义（具体百分比属于各自的任务，见 2.4 的 Tasks）
        ProgressBar {
            objectName: "statusProgress"
            Layout.preferredWidth: 120
            Layout.preferredHeight: 6
            indeterminate: true
            visible: Shell ? Shell.busy : false
        }

        Text {
            objectName: "statusText"
            Layout.fillWidth: true
            text: Shell ? Shell.statusText : ""
            color: Shell ? bar.levelColor(Shell.statusLevel) : Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }

        RowLayout {
            objectName: "statusTaskGroup"
            spacing: Theme?.spacingXs ?? 0
            visible: Shell ? Shell.busy : false

            Image {
                source: (Runtime?.iconUrl("pending") ?? "")
                sourceSize.width: Theme?.iconSize ?? 0
                sourceSize.height: Theme?.iconSize ?? 0
                width: Theme?.fontSizeSmall ?? 10
                height: Theme?.fontSizeSmall ?? 10
                smooth: true
            }

            Text {
                text: Tr?.map["plugin_state_running"] ?? "plugin_state_running"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            Text {
                objectName: "statusTaskCount"
                text: Shell ? String(Shell.backgroundTaskCount) : "0"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }
        }
    }
}
