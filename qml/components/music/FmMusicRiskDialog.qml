// FmMusicRiskDialog.qml —— B站风控验证提示（K-30）。
//
// 真正的滑块在系统浏览器里（services/music_risk_captcha 起的本地页），本组件只负责
// 「告诉用户去浏览器完成 + 取消后停掉本地回调服务」。窗口全屏铺在壳层，播放器在
// 任意页面都可能触发风控，不能塞进音乐页。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "biliRiskDialog"

    signal cancelRequested()

    focus: visible
    Keys.onEscapePressed: dialog.cancelRequested()

    Rectangle {
        objectName: "biliRiskScrim"
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"
        opacity: 0.55
    }

    FmCard {
        objectName: "biliRiskCard"
        anchors.centerIn: parent
        width: Math.min(440, dialog.width - 2 * (Theme?.spacingLg ?? 16))
        title: Tr?.map["music_risk_title"] ?? "music_risk_title"
        subtitle: Tr?.map["music_risk_hint"] ?? "music_risk_hint"

        FmButton {
            objectName: "biliRiskCancel"
            Layout.fillWidth: true
            text: Tr?.map["music_risk_cancel"] ?? "music_risk_cancel"
            primary: false
            onClicked: dialog.cancelRequested()
        }
    }
}
