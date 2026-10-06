// PasswordDialog.qml —— 密码输入对话框（阶段 3 任务 3.5，对照表 M-29 的密码框）。
//
// ## 为什么不用 `components/dialogs/TextInputDialog.qml`
//
// 那个件是**桥的对话框队列**的视图：它靠 `request` 里的 `id` 把答案回给
// `Dialogs.submitDialog(id, value)`。账号页的导入/导出密码是"页面自己发起的**三步**问答"
// （密码 → 确认密码 → 文件框），没有桥那边的 id，所以需要一个能"就地拿答案"的件。
// `ConfirmDialog` 在 3.4 就是为这种场景加了 `answered(bool)` 信号；这里同理，
// 只是把输入框那一半也做成页面内联件（本文件）。
//
// ## 与旧实现的关系
//
// 旧界面用 `CTkInputDialog`（`account_manager.py:526-557`）：
//   * 导入：一次密码；
//   * 导出：先设密码、再输一遍核对，**不一致就 `showwarning` 并整段放弃**。
//
// 本件的语义与之一致，只有两处形态差异（记在对照表 M-29 备注）：
//   1. **掩码**：旧 `CTkInputDialog` 是明文，这里 `password: true`（M-Q6 的既有裁决）；
//   2. **不返回空串**：用户点「取消」或直接关掉时发 `cancelled()`，由调用方决定
//      "什么都不做"（旧实现是 `get_input()` 返回空 → `return`），语义等价。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "accountPasswordDialog"

    property string title: ""
    property string prompt: ""
    property string confirmPrompt: ""
    property bool password: true
    //: 服务端给的错误（已翻译）—— 由调用方在**第二次输入**不匹配时填进来
    //: （旧实现那条 `account_export_password_mismatch`）。本件只显示，不自己判断。
    property string errorText: ""

    signal submitted(string value)
    signal cancelled()

    //: 测试/诊断用：当前格里的明文
    readonly property alias value: field.text

    function t(key) {
        return Tr ? (Tr.map[key] ?? key) : key
    }

    //: 供调用方 / 探针写入（`value` 是只读别名，外部只能从这里进）
    function input(text) {
        field.text = (text === undefined) ? "" : String(text)
    }

    function reset() {
        field.text = ""
        //: **不动 `errorText`**：导出那两遍密码不一致时，桥会把旧文案塞进 errorText
        //: 并立刻退回首步重开本框 —— 在这里清掉就等于"错误提示从不显示"（实测过）。
        //: 错误由调用方在换步/取消时自己清。
    }

    //: 换一步（"设密码" → "再输一次"）时清空上一格的输入。
    //: **挂在 `prompt` 上而不是 `title`**：导出那两步的标题相同、只有提示语不同。
    onPromptChanged: dialog.reset()

    //: 提交当前这一步的值（**不在这里做两次核对** —— 导出那两遍是"服务端的两次问答"，
    //: 由 `Accounts.setExportPassword` / `confirmExportPassword` 决定要不要继续）。
    function submit() {
        if (!dialog)
            return
        dialog.submitted(String(field.text || ""))
    }

    focus: true
    Keys.onEscapePressed: dialog.cancelled()

    Rectangle {
        objectName: "accountPasswordScrim"
        anchors.fill: parent
        color: Theme?.bgDark ?? "transparent"
        opacity: 0.55
    }

    Rectangle {
        id: card
        objectName: "accountPasswordCard"
        anchors.centerIn: parent
        width: Math.min(460, Math.max(300, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: layout.implicitHeight + 2 * (Theme?.spacingLg ?? 16)
        radius: Theme?.radiusLg ?? 8
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        MouseArea { anchors.fill: parent }

        ColumnLayout {
            id: layout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme?.spacingLg ?? 16
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "accountPasswordTitle"
                Layout.fillWidth: true
                text: dialog.title
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                elide: Text.ElideRight
            }

            Text {
                objectName: "accountPasswordPrompt"
                Layout.fillWidth: true
                text: dialog.prompt
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            FmTextField {
                id: field
                objectName: "accountPasswordField"
                Layout.fillWidth: true
                password: dialog.password
                onAccepted: dialog.submit()
            }

            //: 调用方给的错误（导出那一步的"两次输入的密码不一致"）。
            //: **本件不做两次核对** —— 导出是"服务端的两次问答"，不一致时由桥把
            //: 错误文本塞回来、并把流程退回第一步（与旧实现那条分支等价）。
            FmInfoBar {
                objectName: "accountPasswordError"
                Layout.fillWidth: true
                visible: dialog.errorText.length > 0
                level: "error"
                text: dialog.errorText
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingXs ?? 4
                spacing: Theme?.spacingSm ?? 8

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "accountPasswordOk"
                    text: dialog.t("confirm")
                    onClicked: dialog.submit()
                }

                FmButton {
                    objectName: "accountPasswordCancel"
                    primary: false
                    text: dialog.t("cancel")
                    onClicked: dialog.cancelled()
                }
            }
        }
    }
}
