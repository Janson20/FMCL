// ProgressDialog.qml —— 进度对话框（`show_progress` / `close_progress`）。
//
// 契约（app/bridges/dialog_host.py 顶部表）：
//   payload = {report(ProgressReport), title, heading, detail, cancel_label, on_cancel}
// 桥把 report 折成 {id, revision, title, heading, message, detail, level, determinate,
//                   fraction, current, total, cancellable, cancelLabel}
//
// 与旧实现 `ui/ports_tk.py:_build_progress_window` 的对位：
//   * 尺寸 480x220 左右、居中、标题行加粗、状态文字按 level 着色、细进度条
//     （旧实现 420x8、`progress_color=accent` / `fg_color=bg_medium`）；
//   * **取消按钮只在 `on_cancel` 非空时出现**（旧实现 `if on_cancel is not None`），
//     点了只转调回调、**不自己关窗**（窗口何时关由任务调 `close_progress` 决定）；
//   * `current < 0` 或 `total <= 0`（不确定进度）时用转圈而不是进度条
//     （旧实现切到 `mode="indeterminate"`）。
//
// 为什么它不是一条 Toast：旧界面里进度是**独立的置顶窗口**（`show_progress` →
// Toplevel + grab_set），不是右下角通知队列的一部分 —— 它有取消按钮、没有超时、
// 同一个窗口从 0% 更新到 100%。所以本组件挂在 DialogHost 上，不走 ToastHost。

import QtQuick
import QtQuick.Controls

Item {
    id: dialog
    objectName: "progressDialog"

    //: 桥发来的进度快照（progressChanged 的 payload）
    property var report: ({})

    readonly property bool cancellable: !!(report && report.cancellable)
    readonly property bool determinate: !!(report && report.determinate)
    readonly property real fraction: (report && report.fraction !== undefined) ? Number(report.fraction) : 0.0

    function requestCancel() {
        if (typeof Dialogs !== "undefined" && Dialogs)
            Dialogs.cancelProgress()
    }

    function levelColor() {
        var level = (report && report.level) ? String(report.level) : "info"
        if (level === "success")
            return Theme?.success ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        return Theme?.textSecondary ?? "transparent"
    }

    Rectangle {
        id: card
        objectName: "progressCard"
        anchors.centerIn: parent
        width: Math.min(480, Math.max(300, parent.width - 2 * Theme?.spacingXl ?? 0))
        height: layout.implicitHeight + 2 * Theme?.spacingLg ?? 0
        radius: Theme?.radiusLg ?? 0
        color: Theme?.bgDark ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        Column {
            id: layout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme?.spacingLg ?? 0
            spacing: Theme?.spacingSm ?? 0

            Text {
                id: headingText
                objectName: "progressHeading"
                width: parent.width
                text: (dialog.report && dialog.report.heading) ? String(dialog.report.heading) : ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
                elide: Text.ElideRight
            }

            Text {
                id: statusText
                objectName: "progressMessage"
                width: parent.width
                text: (dialog.report && dialog.report.message) ? String(dialog.report.message) : ""
                color: dialog.levelColor()
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            // 确定进度 -> 进度条；不确定 -> 转圈（旧实现切 indeterminate 模式）
            ProgressBar {
                id: bar
                objectName: "progressBar"
                width: parent.width
                height: 8
                visible: dialog.determinate
                from: 0.0
                to: 1.0
                value: dialog.fraction

                background: Rectangle {
                    radius: height / 2
                    color: Theme?.bgMedium ?? "transparent"
                }

                contentItem: Item {
                    Rectangle {
                        width: bar.visualPosition * parent.width
                        height: parent.height
                        radius: height / 2
                        color: Theme?.accent ?? "transparent"
                    }
                }
            }

            BusyIndicator {
                id: busy
                objectName: "progressBusy"
                anchors.horizontalCenter: parent.horizontalCenter
                visible: !dialog.determinate
                running: visible
                implicitWidth: (Theme?.iconSize ?? 0) * 2
                implicitHeight: (Theme?.iconSize ?? 0) * 2
            }

            Text {
                id: detailText
                objectName: "progressDetail"
                width: parent.width
                text: (dialog.report && dialog.report.detail) ? String(dialog.report.detail) : ""
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }

            Item {
                width: parent.width
                height: dialog.cancellable ? cancelButton.height : 0

                Row {
                    anchors.right: parent.right
                    spacing: Theme?.spacingSm ?? 0

                    DialogButton {
                        id: cancelButton
                        objectName: "progressCancelButton"
                        visible: dialog.cancellable
                        enabled: dialog.cancellable
                        text: (dialog.report && dialog.report.cancelLabel)
                              ? String(dialog.report.cancelLabel)
                              : (Tr?.map["cancel"] ?? "cancel")
                        onClicked: dialog.requestCancel()
                    }
                }
            }
        }
    }
}
