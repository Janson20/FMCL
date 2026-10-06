// 底部状态条 —— 02 第三节骨架图的最后一行：`当前任务进度 / 状态文本 / 后台任务数`。
//
// 三块内容各自由谁提供：
//   * 进度：`Shell.busy`（= `Tasks.activeCount > 0`）驱动一条不确定进度条；
//   * 状态文本：`Shell.statusText` / `Shell.statusLevel`（10 秒后自动清空，A-09）；
//   * 后台任务数：`Shell.backgroundTaskCount`。
//
// 返工 B 组的改动（对齐参考项目的语言）：
//   * 顶边一条 1px `Theme.divider`：状态条与页面区**同色**，没有这条就分不清边界；
//   * 进度条从原生 `ProgressBar`（Basic 样式 = 系统灰，且与主题无关）换成自研
//     `FmProgressBar`（强调色、不确定模式），高度 4px；
//   * 文案层级：状态文本用 `textSecondary`，级别色只在 success/warning/error 时覆盖；
//   * 高度走 `Theme.statusBarHeight` 令牌（28）。
//
// 颜色全部来自 `Theme.*`（闸门 R8），级别 → 颜色的映射写成函数是为了只有一处。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Rectangle {
    id: bar
    objectName: "statusBar"

    color: Theme?.barBg ?? "transparent"
    implicitHeight: Theme?.statusBarHeight ?? 28

    // 顶边分割线：1px，用派生令牌 `divider`（比 cardBorder 更弱）
    Rectangle {
        objectName: "statusBarDivider"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: Theme?.divider ?? "transparent"
    }

    function levelColor(level) {
        if (level === "success")
            return Theme?.success ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        //: `loading` = 旧界面的第五档（沙漏图标 + 次要色）。次要色本来就是 info 的落点，
        //: 这里显式写出来是为了和 `ShellBridge.STATUS_LEVELS` 一一对上，别再被当成"漏了一档"。
        if (level === "loading")
            return Theme?.textSecondary ?? "transparent"
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
        FmProgressBar {
            objectName: "statusProgress"
            Layout.preferredWidth: 120
            Layout.alignment: Qt.AlignVCenter
            indeterminate: true
            showPercent: false
            barHeight: 4
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

            FmIcon {
                name: "pending"
                color: Theme?.textSecondary ?? "transparent"
                size: Theme?.fontSizeSmall ?? 10
                Layout.alignment: Qt.AlignVCenter
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
