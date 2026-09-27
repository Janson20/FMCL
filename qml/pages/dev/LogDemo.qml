// LogDemo.qml —— `LogView` 的演示页（阶段 2 任务 2.18 的附带交付，放在 `pages/dev/`）。
//
// ## 它演示什么
//
// 1. **模拟日志**：一键生成 100 / 1000 / 5000 行（级别轮转），用来直观感受
//    追加速率与 `maxLines` 的上限行为（任务 2.19 的冒烟测试也用同一个入口测性能）；
// 2. **真实日志**：点"读取启动器日志"会发 `reloadRequested()` —— **QML 不读文件**
//    （契约第三节红线：QML 不许读写文件）。真正的读取在 Python 侧完成，再经
//    `loadLines(list)` 喂回来。本任务只允许改 `qml/` 与 `tests/`，没有新增桥的位置，
//    所以这条链在 `tests/_smoke_driver.py` 里被真实跑通一次（读 `latest.log` /
//    `latest_structured.log` 的尾部若干行）；阶段 3 接 `LogBridge` 时把
//    `reloadRequested` 接到桥上即可，页面本身一个字都不用改；
// 3. **结构化日志**：`loadLines` 接受 `{text, level}`，所以 `structured_logger` 的
//    JSONL（`latest_structured.log`，每行 `{timestamp, level, event, data}`）可以直接
//    映射成"带级别的日志行" —— 读取与解析都在 Python 侧（见上面第 2 条）。
//
// 本页是开发用的演示页（与 2.17 的 Gallery 同类），不在一级导航里，
// 阶段 3 把真实页面接上之后可以整体删掉。

import QtQuick
import QtQuick.Layouts
import "../../components"

Item {
    id: page
    objectName: "logDemoPage"

    //: 与内置页面同构：`PageStack` 只传页面**声明过**的属性（见 PageStack.qml）
    property string routeId: ""
    property string routeTitleKey: ""
    property string routeDescriptionKey: ""
    property string routeIcon: ""
    property var routeParams: ({})

    //: 点"读取启动器日志"时发出；Python 侧读完文件后调用 `loadLines()` 回填。
    signal reloadRequested()

    //: 最近一次数据来源（Python 注入时回填），显示在状态行上
    property string sourceLabel: ""
    property int generatedCount: 0

    //: Python 侧注入真实日志行的唯一入口。
    //: 参数既可以是字符串数组，也可以是 `[{text, level}, …]`（`structured_logger` 的形状）。
    function loadLines(lines, source) {
        if (lines === undefined || lines === null)
            return 0
        var count = 0
        var batch = []
        for (var i = 0; i < lines.length; i++) {
            var entry = lines[i]
            if (entry !== null && typeof entry === "object") {
                batch.push({ "text": String(entry.text ?? ""), "level": String(entry.level ?? "info") })
            } else {
                batch.push({ "text": String(entry), "level": "info" })
            }
            count++
        }
        logPanel.appendLines(batch)
        page.sourceLabel = (source === undefined || source === null) ? "" : String(source)
        if (typeof Shell !== "undefined" && Shell && page.sourceLabel.length > 0)
            Shell.setStatus(page.sourceLabel, "info")
        return count
    }

    //: 生成 n 行模拟日志（级别按 info/success/warning/error 轮转，便于看配色）。
    function generate(n) {
        var levels = ["info", "info", "success", "info", "warning", "error"]
        var batch = []
        for (var i = 0; i < n; i++) {
            batch.push({
                "text": "[sim] line " + (page.generatedCount + i + 1) + "  level=" + levels[i % levels.length]
                        + "  payload=" + (i % 7 === 0 ? "chunk-" + i : "ok"),
                "level": levels[i % levels.length]
            })
        }
        logPanel.appendLines(batch)
        page.generatedCount += n
        page.sourceLabel = ""
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme?.spacingLg ?? 0
        spacing: Theme?.spacingMd ?? 0

        Text {
            objectName: "logDemoTitle"
            text: (typeof Tr !== "undefined" && Tr)
                  ? (Tr.map["launcher_log"] ?? "launcher_log") : "launcher_log"
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeTitle ?? 18
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 0

            // 返工 C 组：这一排按钮与上限输入框都换成自研件（闸门 R9 禁原生控件）——
            // 原生 Button / SpinBox 在 Basic 样式下是浅色的，锁深色的界面里必然突兀。
            FmButton {
                objectName: "generate100Button"
                primary: false
                text: "100"
                onClicked: page.generate(100)
            }

            FmButton {
                objectName: "generate1000Button"
                primary: false
                text: "1000"
                onClicked: page.generate(1000)
            }

            FmButton {
                objectName: "generate5000Button"
                primary: false
                text: "5000"
                onClicked: page.generate(5000)
            }

            FmButton {
                objectName: "reloadRealLogButton"
                primary: false
                // 键 `refresh` 是既有键；本任务不允许改 ui/locales/*.json，
                // 真正贴切的键留待阶段 3 补。
                text: (typeof Tr !== "undefined" && Tr)
                      ? (Tr.map["refresh"] ?? "refresh") : "refresh"
                onClicked: page.reloadRequested()
            }

            FmButton {
                objectName: "clearDemoButton"
                primary: false
                text: (typeof Tr !== "undefined" && Tr)
                      ? (Tr.map["clear_log"] ?? "clear_log") : "clear_log"
                onClicked: logPanel.clear()
            }

            Item { Layout.fillWidth: true }

            // 上限可调：冒烟测试用它验证"有界"（改成很小的值时行为仍然正确）
            Text {
                text: "maxLines"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 11
            }

            FmSpinBox {
                id: maxLinesBox
                objectName: "maxLinesBox"
                from: 10
                to: 20000
                stepSize: 100
                value: 5000
                editable: true
                onValueModified: logPanel.maxLines = value
            }
        }

        Text {
            objectName: "logDemoStatus"
            Layout.fillWidth: true
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 11
            text: "lines: " + logPanel.lineCount + "  max: " + logPanel.maxLines
                  + (page.sourceLabel.length > 0 ? ("  source: " + page.sourceLabel) : "")
        }

        LogView {
            id: logPanel
            objectName: "logDemoPanel"
            Layout.fillWidth: true
            Layout.fillHeight: true
            title: (typeof Tr !== "undefined" && Tr)
                   ? (Tr.map["launcher_log"] ?? "launcher_log") : "launcher_log"
            emptyText: (typeof Tr !== "undefined" && Tr)
                       ? (Tr.map["mod_browser_no_results"] ?? "") : ""
            maxLines: maxLinesBox.value
        }
    }

    Component.onCompleted: {
        // 一进来就放几行，页面不至于空着（真实页面这里会接桥的初始快照）
        page.generate(8)
    }
}
