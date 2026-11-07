// FmMusicAccountDialog.qml —— 网易云扫码登录浮层（阶段 3.18，3.19 扩展）。
//
// 什么时候用它：歌单侧边栏点「登录网易云同步歌单」时；只负责二维码与登录态。
// 什么时候不要用：设置页的账号详情（3.19 的 VIP / 续期 / 退出登录界面）。
//
// 账号状态与二维码都来自 Music.account（fusion_music.core.account）；
// 本件只发 closed()，不写凭据、不直接调音源。未登录关闭时取消轮询，避免
// 后台线程在用户关掉浮层后继续每 1.5 秒请求一次。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "musicAccountDialog"
    z: 32

    property bool opened: false
    property var account: null

    readonly property bool loggedIn: !!(dialog.account && dialog.account.loggedIn)
    readonly property bool busy: !!(dialog.account && dialog.account.busy)
    readonly property string qrState: dialog.account ? String(dialog.account.qrState || "idle") : "idle"
    readonly property string qrStatusKey: {
        if (dialog.qrState === "waiting")
            return "wy_login_qr_waiting";
        if (dialog.qrState === "scanned")
            return "wy_login_qr_scanned";
        if (dialog.qrState === "expired")
            return "wy_login_qr_expired";
        return "";
    }

    signal closed()

    visible: dialog.opened
    focus: dialog.opened
    Keys.onEscapePressed: dialog.close()

    onOpenedChanged: {
        if (dialog.opened && dialog.account && !dialog.account.loggedIn
                && !dialog.account.busy)
            dialog.account.startQrLogin()
    }

    function close() {
        if (!dialog.opened)
            return
        if (dialog.account && !dialog.account.loggedIn)
            dialog.account.cancelQrLogin()
        dialog.opened = false
        dialog.closed()
    }

    Connections {
        target: dialog.account
        function onLoggedInChanged() {
            if (dialog.loggedIn && dialog.opened)
                closeTimer.restart()
        }
    }

    Timer {
        id: closeTimer
        interval: 700
        repeat: false
        onTriggered: {
            if (dialog.loggedIn) {
                dialog.opened = false
                dialog.closed()
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"

        MouseArea {
            anchors.fill: parent
            onClicked: dialog.close()
        }
    }

    Rectangle {
        id: card
        objectName: "musicAccountDialogCard"
        anchors.centerIn: parent
        width: Math.min(420, Math.max(320, dialog.width - 2 * (Theme?.spacingXl ?? 30)))
        height: Math.min(520, Math.max(300, content.implicitHeight + 2 * (Theme?.spacingLg ?? 20)))
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        MouseArea {
            anchors.fill: parent
            onClicked: { }
        }

        ColumnLayout {
            id: content
            anchors.fill: parent
            anchors.margins: Theme?.spacingLg ?? 20
            spacing: Theme?.spacingMd ?? 12

            RowLayout {
                Layout.fillWidth: true

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["settings_wy_login_title"] ?? ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmToolButton {
                    objectName: "musicAccountDialogClose"
                    iconName: "close"
                    iconSize: 14
                    side: 28
                    onClicked: dialog.close()
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 280

                Rectangle {
                    anchors.centerIn: parent
                    width: 248
                    height: 248
                    radius: Theme?.radiusMd ?? 0
                    color: Theme?.bgLight ?? "transparent"
                    border.width: 1
                    border.color: Theme?.cardBorder ?? "transparent"

                    Image {
                        id: qr
                        anchors.centerIn: parent
                        width: 224
                        height: 224
                        source: dialog.account ? String(dialog.account.qrImage || "") : ""
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        visible: !dialog.loggedIn && status === Image.Ready
                    }

                    FmLoadingState {
                        anchors.fill: parent
                        visible: !dialog.loggedIn && !qr.visible
                        title: dialog.qrState === "error"
                               ? (Tr?.map["wy_login_qr_fetch_failed"] ?? "")
                               : (dialog.qrState === "expired"
                                  ? (Tr?.map["wy_login_qr_expired"] ?? "")
                                  : (Tr?.map["wy_login_qr_generating"] ?? ""))
                    }

                    ColumnLayout {
                        anchors.centerIn: parent
                        visible: dialog.loggedIn
                        spacing: Theme?.spacingSm ?? 8

                        FmIcon {
                            Layout.alignment: Qt.AlignHCenter
                            name: "account"
                            size: 40
                            color: Theme?.success ?? "transparent"
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: dialog.account && String(dialog.account.nickname || "").length > 0
                                  ? String(dialog.account.nickname || "")
                                  : (Tr?.map["settings_wy_logged_in"] ?? "")
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeLarge ?? 14
                            font.bold: true
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: dialog.account ? String(dialog.account.vipLabel || "") : ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: (dialog.account && dialog.account.vipQualityKey)
                                  ? (Tr?.map[String(dialog.account.vipQualityKey)] ?? "") : ""
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: Tr?.map["music_wy_playlists"] ?? ""
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }
                }
            }

            Text {
                objectName: "musicAccountQrStatus"
                Layout.fillWidth: true
                visible: !dialog.loggedIn && qr.visible && dialog.qrStatusKey.length > 0
                horizontalAlignment: Text.AlignHCenter
                text: Tr?.map[dialog.qrStatusKey] ?? ""
                color: dialog.qrState === "scanned" ? (Theme?.success ?? "transparent")
                                                    : (Theme?.textSecondary ?? "transparent")
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                visible: !dialog.loggedIn && qr.visible
                horizontalAlignment: Text.AlignHCenter
                text: Tr?.map["wy_login_qr_hint"] ?? ""
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "musicAccountRefresh"
                    primary: false
                    iconName: "refresh"
                    text: Tr?.map["wy_login_refresh_btn"] ?? ""
                    visible: !dialog.loggedIn
                    enabled: dialog.account && !dialog.busy
                    onClicked: {
                        if (dialog.account)
                            dialog.account.startQrLogin()
                    }
                }

                Item { Layout.fillWidth: true }

                FmButton {
                    primary: true
                    text: dialog.loggedIn ? (Tr?.map["close"] ?? "") : (Tr?.map["cancel"] ?? "")
                    onClicked: dialog.close()
                }
            }
        }
    }
}
