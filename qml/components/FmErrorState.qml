// FmErrorState.qml —— 错误态（阶段 2 任务 2.16；`03` 的 SOP 第 5 条要求每页覆盖三态）。
//
// 什么时候用它：页面**取数据失败**（网络断了、磁盘满了、服务抛异常、版本文件损坏）。
//   必须与"空数据"分开：空是"没有"，错误是"没拿到"，用户要做的事完全不同（重试 vs 去创建）。
// 什么时候不要用：表单字段校验失败（用 FmTextField 的 `errorText`）；
//   一次性的操作结果提示（用 FmInfoBar 或 Toast）；还在加载（用 FmLoadingState）。
//
// `detailText` 放**技术信息**（异常原文、路径、错误码），用小字 + 次要色显示：
//   这是"出了事能排查"的唯一线索，别把它藏进日志里（用户反馈问题时截的就是这一块）。
//   `retryText` 非空时出现重试按钮并 `retried()` —— 重试动作由调用方接（红线 2：
//   组件不去调桥）。

import QtQuick

Item {
    id: block
    objectName: "fmErrorState"

    property string title: ""
    property string description: ""
    //: 技术细节（异常原文 / 路径 / 错误码），可空
    property string detailText: ""
    property string iconName: "error"
    property string retryText: ""

    signal retried()

    implicitWidth: 300
    implicitHeight: column.implicitHeight

    Column {
        id: column
        anchors.centerIn: parent
        width: Math.min(parent.width, 420)
        spacing: Theme?.spacingSm ?? 0

        FmIcon {
            objectName: "fmErrorStateIcon"
            anchors.horizontalCenter: parent.horizontalCenter
            name: block.iconName
            color: Theme?.error ?? "transparent"
            size: Math.round((Theme?.iconSize ?? 0) * 1.6)
        }

        Text {
            objectName: "fmErrorStateTitle"
            width: parent.width
            visible: block.title.length > 0
            text: block.title
            color: Theme?.error ?? "transparent"
            font.pixelSize: Theme?.fontSizeLarge ?? 14
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Text {
            objectName: "fmErrorStateDescription"
            width: parent.width
            visible: block.description.length > 0
            text: block.description
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Text {
            objectName: "fmErrorStateDetail"
            width: parent.width
            visible: block.detailText.length > 0
            text: block.detailText
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WrapAnywhere
        }

        FmButton {
            objectName: "fmErrorStateRetry"
            anchors.horizontalCenter: parent.horizontalCenter
            visible: block.retryText.length > 0
            primary: false
            iconName: "refresh"
            text: block.retryText
            onClicked: block.retried()
        }
    }
}
