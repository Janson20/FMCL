// 启动期致命错误的兜底窗口（阶段 2 任务 2.2）。
//
// 为什么不用 `QMessageBox`：一是与"界面全部走 QML"保持一致，二是**初始化失败时
// 很可能正是 QML 基础设施本身有问题**，走 QtWidgets 反而多一层不确定。
// 任务 2.14 会把这条路径并进完整启动流程（"初始化失败仍显示主窗口并报错"）。
//
// 文案由 Python 通过上下文属性传入（`fatalTitle` / `fatalMessage`），
// 所以中文出现在**数据**里而不是 QML 字面量里 —— 不触发闸门 R3。

import QtQuick
import QtQuick.Controls

Window {
    id: fatal

    width: 620
    height: 320
    visible: true
    title: typeof fatalTitle !== "undefined" && fatalTitle ? fatalTitle : "Startup error"

    Column {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 12

        Text {
            width: parent.width
            text: fatal.title
            font.pixelSize: 18
            font.bold: true
            wrapMode: Text.WordWrap
        }

        ScrollView {
            width: parent.width
            height: parent.height - 96

            TextArea {
                id: body
                readOnly: true
                wrapMode: TextEdit.Wrap
                text: typeof fatalMessage !== "undefined" && fatalMessage ? fatalMessage : ""
                selectByMouse: true
            }
        }

        Button {
            text: "Close"
            onClicked: Qt.quit()
        }
    }
}
