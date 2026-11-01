// FmMusicOverlayBackdrop.qml —— 播放页 / 歌单详情覆盖层的垫底（阶段 3.17）。
//
// FMP 的 OverlayBackdrop 同款：先铺一层不透明背景，再用全屏 MouseArea 吃掉
// 点击/滚轮。少了这一层，展开播放页的空白处会把点击穿到后面的页面。

import QtQuick

Item {
    id: root
    objectName: "musicOverlayBackdrop"

    //: 事件遮罩的 objectName（探针据此验证「点不穿」）
    property string blockerObjectName: ""

    Rectangle {
        anchors.fill: parent
        color: Theme?.windowBg ?? "transparent"
    }

    MouseArea {
        objectName: root.blockerObjectName
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onWheel: function (wheel) {
            wheel.accepted = true
        }
    }
}
