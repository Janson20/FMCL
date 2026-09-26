// FmPagination.qml —— 分页（阶段 2 任务 2.16）。
//
// 什么时候用它：**服务端分页**或者数据量大到必须分页的列表（在线资源浏览、搜索结果）——
//   一次只取一页，页码是"再取哪一页"的输入。
// 什么时候不要用：数据已经全在本地（用 ListView 自己滚，或者本地过滤，分页只会多点几下）；
//   只有一两页（页码控件比内容还占地方）。
//
// 交互：上一页 / "当前页 / 总页数" / 下一页，边界上按钮自动禁用（**不隐藏** ——
//   位置固定的控件不该跳来跳去）。点击只发 `pageRequested(page)`，**组件不改自己的
//   page**：真正的页号来自服务层的数据（红线 2），"点了没反应"才是对的语义
//   （请求还在飞的时候不该先跳过去）。
// 页码格式 `1 / 10` 是数字与斜杠，无语言差异，不走 i18n（闸门 R3 只判中日韩文字）。

import QtQuick

Row {
    id: pager
    objectName: "fmPagination"

    //: 当前页（1 起）
    property int page: 1
    //: 总页数（至少 1）
    property int pageCount: 1
    //: 显示两端按钮之外的相邻页号（false = 只显示 "n / m"）
    property bool compact: true

    readonly property int total: Math.max(1, pageCount)
    readonly property int current: Math.max(1, Math.min(total, page))
    readonly property bool canPrev: enabled && current > 1
    readonly property bool canNext: enabled && current < total

    signal pageRequested(int page)

    spacing: Theme?.spacingSm ?? 0

    FmButton {
        objectName: "fmPaginationPrev"
        primary: false
        iconOnly: true
        iconName: "arrow-left"
        enabled: pager.canPrev
        onClicked: pager.pageRequested(pager.current - 1)
    }

    Text {
        objectName: "fmPaginationLabel"
        anchors.verticalCenter: pager.verticalCenter
        text: pager.current + " / " + pager.total
        color: pager.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeBase ?? 12
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    FmButton {
        objectName: "fmPaginationNext"
        primary: false
        iconOnly: true
        iconName: "arrow-right"
        enabled: pager.canNext
        onClicked: pager.pageRequested(pager.current + 1)
    }
}
