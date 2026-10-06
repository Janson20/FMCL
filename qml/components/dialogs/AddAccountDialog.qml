// AddAccountDialog.qml —— 添加账号对话框（阶段 3 任务 3.5，对照表 M-26）。
//
// ## 对应旧实现的哪一块
//
// `ui/windows/account_manager.py` 的 `AddAccountDialog`（36-174 行）：一个 420x320 的
// 独立小窗，按 `account_type` 换成三种形态 ——
//
// | 形态 | 旧界面 | 本文件 |
// |------|--------|--------|
// | 微软 | 一句说明 + 「打开浏览器登录」按钮 | 同一句话 + 同一颗按钮（`account_ms_info` / `account_ms_login_btn`） |
// | 离线 | 角色名输入框 + 「添加」 | 同一个输入框（占位符 `account_offline_name_placeholder`）+ 「添加」 |
// | 外置 | 服务器地址 / 用户名 / 密码三格 + 「添加」 | 同三格，**密码格用掩码**（旧实现就是 `show="•"`） |
//
// ## 三处与旧实现不同的地方（都记在对照表 M-26 备注）
//
// 1. **不再自己弹警告框**：旧实现用 `messagebox.showwarning(_("warning"), _("account_name_required"))`；
//    这里把校验结果**就地**显示在输入框下面（少一次弹窗，也不会挡住刚填的字段）。
//    校验规则一个字没变（都是"空 → 报错"，且服务层还会再校验一遍）。
// 2. **重名只提醒不拦截**（用户 2026-10-06 裁决）：同名的离线账号已存在时给一句提示，
//    仍然允许创建 —— 旧实现连提醒都没有。
// 3. **回车提交**：旧实现没有绑定回车（要动手点按钮），这里三格都支持回车。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "accountAddDialog"

    //: `microsoft` / `offline` / `yggdrasil`；空串 = 不显示（父级据此实例化/销毁）
    property string kind: ""
    //: 服务或本地校验给出的错误文案（已翻译）；空串 = 无错
    property string errorText: ""
    //: 离线账号重名的提醒（只提醒不拦截）
    property string hintText: ""

    signal submitted(var payload)
    signal cancelled()

    readonly property bool isOffline: dialog.kind === "offline"
    readonly property bool isYggdrasil: dialog.kind === "yggdrasil"

    function titleKey() {
        if (dialog.kind === "microsoft")
            return "account_add_microsoft"
        if (dialog.kind === "offline")
            return "account_add_offline"
        if (dialog.kind === "yggdrasil")
            return "account_add_yggdrasil"
        return "account_add"
    }

    function t(key) {
        return Tr ? (Tr.map[key] ?? key) : key
    }

    //: 清空三格（每次打开时调，避免上次的输入串到这次）
    function reset() {
        nameField.text = ""
        serverField.text = ""
        userField.text = ""
        passwordField.text = ""
    }

    function submit() {
        if (!dialog)
            return
        if (dialog.isOffline) {
            var name = String(nameField.text || "").trim()
            if (name.length === 0) {
                dialog.errorText = dialog.t("account_name_required")
                return
            }
            dialog.errorText = ""
            dialog.submitted({"kind": "offline", "name": name})
            return
        }
        if (dialog.isYggdrasil) {
            var server = String(serverField.text || "").trim()
            var user = String(userField.text || "").trim()
            var password = String(passwordField.text || "")
            if (server.length === 0 || user.length === 0 || password.length === 0) {
                dialog.errorText = dialog.t("account_ygg_fields_required")
                return
            }
            dialog.errorText = ""
            dialog.submitted({"kind": "yggdrasil", "server_url": server,
                              "username": user, "password": password})
            return
        }
        dialog.errorText = ""
        dialog.submitted({"kind": "microsoft"})
    }

    focus: true
    Keys.onEscapePressed: dialog.cancelled()

    //: 半透明遮罩（页面内联对话框没有独立窗口，靠它把后面的内容压暗）
    Rectangle {
        objectName: "accountAddScrim"
        anchors.fill: parent
        color: Theme?.bgDark ?? "transparent"
        opacity: 0.55
    }

    Rectangle {
        id: card
        objectName: "accountAddCard"
        anchors.centerIn: parent
        width: Math.min(460, Math.max(300, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: layout.implicitHeight + 2 * (Theme?.spacingLg ?? 16)
        radius: Theme?.radiusLg ?? 8
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        MouseArea {
            //: 吃掉点击，别让"点卡片"穿透到下面的账号列表
            anchors.fill: parent
        }

        ColumnLayout {
            id: layout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme?.spacingLg ?? 16
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "accountAddTitle"
                Layout.fillWidth: true
                text: dialog.t(dialog.titleKey())
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                elide: Text.ElideRight
            }

            // ── 微软：说明 + 一颗按钮（旧 `_build_microsoft_ui`）──
            Text {
                objectName: "accountAddMicrosoftInfo"
                Layout.fillWidth: true
                visible: dialog.kind === "microsoft"
                text: dialog.t("account_ms_info")
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            FmButton {
                objectName: "accountAddMicrosoftLogin"
                visible: dialog.kind === "microsoft"
                Layout.fillWidth: true
                text: dialog.t("account_ms_login_btn")
                onClicked: dialog.submit()
            }

            // ── 离线：角色名（旧 `_build_offline_ui`）──
            Text {
                objectName: "accountAddOfflineLabel"
                Layout.fillWidth: true
                visible: dialog.isOffline
                text: dialog.t("account_offline_name")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
            }

            FmTextField {
                id: nameField
                objectName: "accountAddNameField"
                Layout.fillWidth: true
                visible: dialog.isOffline
                placeholder: dialog.t("account_offline_name_placeholder")
                onAccepted: dialog.submit()
            }

            // ── 外置：服务器 / 用户名 / 密码（旧 `_build_yggdrasil_ui`）──
            Text {
                objectName: "accountAddServerLabel"
                Layout.fillWidth: true
                visible: dialog.isYggdrasil
                text: dialog.t("account_ygg_server")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
            }

            FmTextField {
                id: serverField
                objectName: "accountAddServerField"
                Layout.fillWidth: true
                visible: dialog.isYggdrasil
                placeholder: dialog.t("account_ygg_server_placeholder")
                onAccepted: dialog.submit()
            }

            Text {
                objectName: "accountAddUserLabel"
                Layout.fillWidth: true
                visible: dialog.isYggdrasil
                text: dialog.t("account_ygg_username")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
            }

            FmTextField {
                id: userField
                objectName: "accountAddUserField"
                Layout.fillWidth: true
                visible: dialog.isYggdrasil
                onAccepted: dialog.submit()
            }

            Text {
                objectName: "accountAddPasswordLabel"
                Layout.fillWidth: true
                visible: dialog.isYggdrasil
                text: dialog.t("account_ygg_password")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
            }

            FmTextField {
                id: passwordField
                objectName: "accountAddPasswordField"
                Layout.fillWidth: true
                visible: dialog.isYggdrasil
                //: 旧实现就是掩码（`show="\u2022"`，`account_manager.py:137`）
                password: true
                onAccepted: dialog.submit()
            }

            //: 校验/提示（就地显示，不再弹 `messagebox`）
            FmInfoBar {
                objectName: "accountAddError"
                Layout.fillWidth: true
                visible: dialog.errorText.length > 0
                level: "error"
                text: dialog.errorText
            }

            FmInfoBar {
                objectName: "accountAddHint"
                Layout.fillWidth: true
                visible: dialog.hintText.length > 0
                level: "warning"
                text: dialog.hintText
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingXs ?? 4
                spacing: Theme?.spacingSm ?? 8

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "accountAddSubmit"
                    text: dialog.t("account_add")
                    onClicked: dialog.submit()
                }

                FmButton {
                    objectName: "accountAddCancel"
                    primary: false
                    text: dialog.t("cancel")
                    onClicked: dialog.cancelled()
                }
            }
        }
    }
}
