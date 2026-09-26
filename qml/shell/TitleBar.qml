// 标题栏 —— 02 第三节骨架图的第一行：`[返回] [面包屑] [全局搜索] [通知] [账号]`。
//
// 与 FluWindow 内置 appBar 的分工（说明一下免得看着重复）：
//   * 内置 appBar（FluWindow 自带）：窗口标题 + 最小化/最大化/关闭按钮，
//     它是无边框窗口的拖动条与命中区，FluentUI 的 FluFrameless 与它强绑定；
//   * 本组件：**应用导航**相关的动作。两者叠在一起就是骨架图里的"标题栏"。
//     没有替换内置 appBar，是因为 FluFrameless 需要它的 `buttonMaximize` /
//     `buttonClose` 等别名（换掉等于自己重写窗口按钮与命中测试）。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: bar
    objectName: "titleBar"

    color: Theme?.bgMedium ?? "transparent"
    implicitHeight: 48

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme?.spacingSm ?? 0
        anchors.rightMargin: Theme?.spacingMd ?? 0
        spacing: Theme?.spacingSm ?? 0

        // 返回：可用性直接来自 Nav（栈里还有上一页才亮）
        ToolButton {
            id: backButton
            objectName: "backButton"

            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            enabled: Nav ? Nav.canGoBack : false
            onClicked: {
                if (Nav)
                    Nav.goBack()
            }

            contentItem: Image {
                source: (Runtime?.iconUrl("arrow-left") ?? "")
                sourceSize.width: Theme?.iconSize ?? 0
                sourceSize.height: Theme?.iconSize ?? 0
                smooth: true
                opacity: backButton.enabled ? 1.0 : 0.35
            }
        }

        Breadcrumb {
            Layout.fillWidth: true
            Layout.fillHeight: true
        }

        // 全局搜索：回车才发请求（阶段 3 接真正的搜索页）
        TextField {
            id: searchBox
            objectName: "globalSearchBox"

            Layout.preferredWidth: 260
            Layout.preferredHeight: 32
            placeholderText: Tr?.map["search"] ?? "search"
            selectByMouse: true
            onAccepted: {
                if (Shell)
                    Shell.globalSearch(text)
            }
        }

        ToolButton {
            id: notifyButton
            objectName: "notificationButton"

            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            onClicked: {
                if (Shell)
                    Shell.toggleNotificationCenter()
            }

            contentItem: Image {
                source: (Runtime?.iconUrl("notify") ?? "")
                sourceSize.width: Theme?.iconSize ?? 0
                sourceSize.height: Theme?.iconSize ?? 0
                smooth: true
            }
        }

        // 账号：暂时直接进设置里的账号页（阶段 3 换成账号菜单/浮层）
        ToolButton {
            id: accountButton
            objectName: "accountButton"

            Layout.preferredWidth: 32
            Layout.preferredHeight: 32
            onClicked: {
                if (Nav)
                    Nav.push("settings/account")
            }

            contentItem: Image {
                source: (Runtime?.iconUrl("account") ?? "")
                sourceSize.width: Theme?.iconSize ?? 0
                sourceSize.height: Theme?.iconSize ?? 0
                smooth: true
            }
        }
    }
}
