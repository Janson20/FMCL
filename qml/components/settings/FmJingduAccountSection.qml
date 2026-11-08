// FmJingduAccountSection.qml —— 设置里的净读账号登录（阶段 3.21）。
//
// 什么时候用它：设置 settings/ai 分区，负责净读 AI 账号的登录、退出与用户信息。
// 什么时候不要用：Minecraft 账号管理（settings/account）、网易云音乐账号（settings/music）。
//
// 登录不是设置草稿的一部分：点了登录立即写配置，和旧界面的“净读 AI”区块一致。
// 网络与配置写入在 Jingdu 桥 / services/jingdu_account_service.py；本件只做表单与展示。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: section
    objectName: "fmJingduAccountSection"

    property string localError: ""
    property var infoRows: []
    property bool autoFetched: false
    property bool wasLoggedIn: false

    readonly property bool available: (typeof Jingdu !== "undefined" && Jingdu) ? true : false
    readonly property bool loggedIn: available ? Jingdu.loggedIn : false
    readonly property bool busy: available ? Jingdu.busy : false
    readonly property string errorMessage: localError.length > 0 ? localError
                                           : (available ? String(Jingdu.errorText || "") : "")
    readonly property string statusText: available ? String(Jingdu.statusText || "") : ""
    readonly property string statusLevel: available ? String(Jingdu.statusLevel || "info") : "info"
    readonly property string usernameText: {
        if (!available)
            return ""
        var info = Jingdu.userInfo || ({})
        return String(Jingdu.username || info.username || "-")
    }

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    Component.onCompleted: refresh()
    onVisibleChanged: {
        if (section.visible)
            refresh()
    }

    Connections {
        target: (typeof Jingdu !== "undefined" && Jingdu) ? Jingdu : null
        function onChanged() {
            section.refresh()
        }
        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level))
        }
    }

    Connections {
        target: (typeof Tr !== "undefined" && Tr) ? Tr : null
        function onLanguageChanged() {
            section.refresh()
        }
    }

    function refresh() {
        if (!section.available) {
            section.infoRows = []
            return
        }
        var info = Jingdu.userInfo || ({})
        var level = String(info.level === undefined ? "-" : info.level)
        var levelName = String(info.level_name || "")
        if (levelName.length > 0)
            level = level + " (" + levelName + ")"
        section.infoRows = [
            {"label": section.t("account_info_username"), "value": String(info.username || "-")},
            {"label": section.t("account_info_uuid"), "value": String(info.uuid || "-")},
            {"label": section.t("account_info_level"), "value": level},
            {"label": section.t("account_info_description"), "value": String(info.description || "-")},
            {"label": section.t("account_info_ai_credits"), "value": String(info.ai_credits === undefined ? "-" : info.ai_credits)}
        ]
        if (section.loggedIn && !section.autoFetched && !section.busy) {
            // 与旧界面一致：进入已登录的净读区块时主动刷新一次用户信息，
            // 既能更新积分，也能让过期 Token 在打开设置时就暴露出来。
            section.autoFetched = true
            Jingdu.refreshInfo()
        }
        if (section.loggedIn) {
            passwordField.text = ""
        } else if (section.wasLoggedIn) {
            usernameField.text = ""
            passwordField.text = ""
        }
        section.wasLoggedIn = section.loggedIn
    }

    function login() {
        if (!section.available || section.busy)
            return
        var name = String(usernameField.text || "").trim()
        var secret = String(passwordField.text || "")
        if (name.length === 0 || secret.length === 0) {
            section.localError = section.t("please_enter_username_password")
            return
        }
        section.localError = ""
        // 登录 worker 已经会拉一次 user/info，避免成功后组件再自动拉一次。
        section.autoFetched = true
        Jingdu.login(name, secret)
    }

    function logout() {
        if (section.available && !section.busy) {
            section.localError = ""
            Jingdu.logout()
        }
    }

    function openRegister() {
        if (section.available)
            Qt.openUrlExternally(String(Jingdu.registerUrl || ""))
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 10

            FmCard {
                objectName: "jingduAccountCard"
                Layout.fillWidth: true
                title: Tr?.map["netread_ai"] ?? "netread_ai"
                subtitle: Tr?.map["netread_login_desc"] ?? "netread_login_desc"

                FmInfoBar {
                    objectName: "jingduUnavailableHint"
                    Layout.fillWidth: true
                    visible: !section.available
                    level: "error"
                    text: section.t("settings_service_missing")
                }

                FmInfoBar {
                    objectName: "jingduErrorHint"
                    Layout.fillWidth: true
                    visible: section.available && section.errorMessage.length > 0
                    level: "error"
                    text: section.errorMessage
                }

                FmInfoBar {
                    objectName: "jingduStatusHint"
                    Layout.fillWidth: true
                    visible: section.available && section.statusText.length > 0
                             && section.statusLevel !== "error"
                    level: section.statusLevel
                    text: section.statusText
                }

                // ── 已登录：账户信息 ──────────────────────────
                RowLayout {
                    objectName: "jingduAccountIdentity"
                    Layout.fillWidth: true
                    visible: section.loggedIn
                    spacing: Theme?.spacingMd ?? 12

                    Rectangle {
                        Layout.preferredWidth: 44
                        Layout.preferredHeight: 44
                        radius: width / 2
                        color: Theme?.accentSoft ?? "transparent"
                        border.width: 1
                        border.color: Theme?.accent ?? "transparent"

                        FmIcon {
                            anchors.centerIn: parent
                            name: "account"
                            color: Theme?.accent ?? "transparent"
                            size: Theme?.iconSize ?? 20
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 2

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                objectName: "jingduUsername"
                                Layout.fillWidth: true
                                text: section.usernameText
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeTitle ?? 18
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            FmTag {
                                objectName: "jingduLoggedInTag"
                                text: section.t("netread_logged_in")
                                level: "success"
                                iconName: "check"
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["account_info_title"] ?? "account_info_title"
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }
                }

                ColumnLayout {
                    objectName: "jingduInfoRows"
                    Layout.fillWidth: true
                    visible: section.loggedIn
                    spacing: Theme?.spacingSm ?? 6

                    Repeater {
                        model: section.infoRows

                        delegate: RowLayout {
                            id: infoRow
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                Layout.preferredWidth: 96
                                text: String(infoRow.modelData.label || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            Text {
                                Layout.fillWidth: true
                                text: String(infoRow.modelData.value || "-")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                RowLayout {
                    objectName: "jingduLoggedInActions"
                    Layout.fillWidth: true
                    visible: section.loggedIn
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "jingduRefreshButton"
                        primary: false
                        iconName: "refresh"
                        text: Tr?.map["account_refresh"] ?? "account_refresh"
                        enabled: section.available && !section.busy
                        onClicked: {
                            if (section.available)
                                Jingdu.refreshInfo()
                        }
                    }

                    FmButton {
                        objectName: "jingduLogoutButton"
                        primary: false
                        iconName: "door"
                        text: Tr?.map["netread_logout"] ?? "netread_logout"
                        enabled: section.available && !section.busy
                        onClicked: section.logout()
                    }

                    Item { Layout.fillWidth: true }
                }

                // ── 未登录：用户名 / 密码 ─────────────────────
                FmTextField {
                    id: usernameField
                    objectName: "jingduUsernameField"
                    Layout.fillWidth: true
                    visible: section.available && !section.loggedIn
                    label: Tr?.map["netread_username_placeholder"] ?? "netread_username_placeholder"
                    enabled: !section.busy
                    onEdited: function(value) {
                        if (section.localError.length > 0)
                            section.localError = ""
                    }
                }

                FmTextField {
                    id: passwordField
                    objectName: "jingduPasswordField"
                    Layout.fillWidth: true
                    visible: section.available && !section.loggedIn
                    password: true
                    label: Tr?.map["netread_password_placeholder"] ?? "netread_password_placeholder"
                    enabled: !section.busy
                    onEdited: function(value) {
                        if (section.localError.length > 0)
                            section.localError = ""
                    }
                    onAccepted: section.login()
                }

                RowLayout {
                    objectName: "jingduLoginActions"
                    Layout.fillWidth: true
                    visible: section.available && !section.loggedIn
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "jingduLoginButton"
                        iconName: "account"
                        text: section.busy
                              ? (Tr?.map["logging_in"] ?? "logging_in")
                              : (Tr?.map["login"] ?? "login")
                        loading: section.busy
                        enabled: section.available && !section.busy
                        onClicked: section.login()
                    }

                    FmButton {
                        objectName: "jingduRegisterButton"
                        primary: false
                        text: Tr?.map["netread_register"] ?? "netread_register"
                        enabled: section.available && !section.busy
                        onClicked: section.openRegister()
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            Item { Layout.preferredHeight: Theme?.spacingSm ?? 8 }
        }
    }
}
