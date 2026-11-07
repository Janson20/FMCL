// FmMusicMenu.qml —— 音乐页内的浮动操作菜单（阶段 3.18）。
//
// 什么时候用它：歌单/曲目的右键菜单这种“点空白处就消失”的小操作集。
// 什么时候不要用：需要用户填写的对话框（用 TextInputDialog）、页内常驻工具栏。
//
// 关闭语义对齐旧实现（ui/app_music.py:2367-2414）：点菜单外任意位置、
// Escape、5 秒超时。QML 里没有独立 Toplevel，因此不需要“延迟登记防自关闭”；
// 菜单卡片自己的 MouseArea 会吃掉点击，不会冒泡到遮罩。
// 菜单只发 selected(key)；文案由调用方给，组件不读业务键。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: menu
    objectName: "musicMenu"
    z: 70

    property bool opened: false
    //: [{key, text, enabled, danger}]
    property var items: []
    property real anchorX: 0
    property real anchorY: 0

    readonly property int itemHeight: 32
    readonly property real cardWidth: 176
    readonly property real cardHeight: Math.min(menu.height - 16,
                                               Math.max(1, menu.items.length) * itemHeight + 8)

    signal selected(string key)
    signal closed()

    visible: opened
    focus: opened
    Keys.onEscapePressed: menu.close()

    function openAtScene(sceneX, sceneY) {
        var local = menu.mapFromItem(null, sceneX, sceneY)
        menu.anchorX = local.x
        menu.anchorY = local.y
        menu.opened = true
        menu.forceActiveFocus()
        hideTimer.restart()
    }

    function close() {
        if (!menu.opened)
            return
        menu.opened = false
        hideTimer.stop()
        menu.closed()
    }

    onOpenedChanged: {
        if (menu.opened) {
            menu.forceActiveFocus()
            hideTimer.restart()
        } else {
            hideTimer.stop()
        }
    }

    Timer {
        id: hideTimer
        interval: 5000
        repeat: false
        onTriggered: menu.close()
    }

    // 整页遮罩：点空白处关闭，同时阻止点击穿透到曲目行。
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onClicked: menu.close()
    }

    Rectangle {
        id: card
        objectName: "musicMenuCard"
        x: Math.max(8, Math.min(menu.anchorX, menu.width - width - 8))
        y: Math.max(8, Math.min(menu.anchorY, menu.height - height - 8))
        width: menu.cardWidth
        height: menu.cardHeight
        radius: Theme?.radiusMd ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        MouseArea {
            anchors.fill: parent
            onClicked: { }
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 4
            spacing: 0

            Repeater {
                model: menu.items
                delegate: Rectangle {
                    id: row
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: menu.itemHeight - 2
                    radius: Theme?.radiusSm ?? 0
                    color: (modelData.enabled !== false && rowMouse.containsMouse)
                           ? (Theme?.itemHover ?? "transparent") : "transparent"

                    Text {
                        anchors.fill: parent
                        anchors.leftMargin: 9
                        anchors.rightMargin: 9
                        verticalAlignment: Text.AlignVCenter
                        text: String(modelData.text || "")
                        color: modelData.enabled === false
                               ? (Theme?.textTertiary ?? "transparent")
                               : (modelData.danger === true
                                  ? (Theme?.error ?? "transparent")
                                  : (Theme?.textPrimary ?? "transparent"))
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        elide: Text.ElideRight
                    }

                    MouseArea {
                        id: rowMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: modelData.enabled !== false
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            menu.close()
                            menu.selected(String(modelData.key || ""))
                        }
                    }
                }
            }
        }
    }
}
