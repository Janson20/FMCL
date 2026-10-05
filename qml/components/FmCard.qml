// FmCard.qml —— 卡片（阶段 2 任务 2.16）。
//
// 什么时候用它：把一组相关内容框成一个视觉单元（"账号信息"、"启动设置"、"版本详情摘要"、
//   三态区域），也让页面有分区感（14 个设计令牌里 `radiusLg` 与 `spacingLg` 就是给它的）。
// 什么时候不要用：整页之外再套一层卡片（卡片嵌卡片会分不清层级）；
//   一行就能说清的信息（用 FmListItem / FmInfoBar）；纯列表容器（列表自己滚，不要外面套卡片）。
//
// 内容放法：直接写在卡片里就行（默认属性转发到内部的 ColumnLayout，所以会**竖排堆叠**）。
//   `title` / `subtitle` 都有才显示标题区；`footer` 里的东西放到底部（按钮行那种）。
// 颜色只来自 Theme.*；标题文案由调用方给（`Tr.map[…]`）。卡片本身**不可点** ——
//   需要整卡点击就在里面放一个 FmButton，别给卡片加点击语义（会和内容里的控件打架）。

import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "fmCard"

    property string title: ""
    property string subtitle: ""
    //: 内边距（默认 spacingLg）
    property int padding: Theme?.spacingLg ?? 0
    //: 内容（默认属性，直接写在 FmCard { } 里）
    default property alias content: body.data
    //: 底部区域（放按钮行）
    property alias footer: foot.data

    readonly property alias bodyItem: body

    implicitWidth: 320
    implicitHeight: layout.implicitHeight + 2 * padding
    radius: Theme?.radiusLg ?? 0
    color: Theme?.cardBg ?? "transparent"
    border.width: 1
    border.color: Theme?.cardBorder ?? "transparent"

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: card.padding
        spacing: Theme?.spacingMd ?? 0

        ColumnLayout {
            objectName: "fmCardHeader"
            Layout.fillWidth: true
            visible: card.title.length > 0 || card.subtitle.length > 0
            spacing: Theme?.spacingXs ?? 0

            Text {
                objectName: "fmCardTitle"
                Layout.fillWidth: true
                visible: card.title.length > 0
                text: card.title
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
                elide: Text.ElideRight
            }

            Text {
                objectName: "fmCardSubtitle"
                Layout.fillWidth: true
                visible: card.subtitle.length > 0
                text: card.subtitle
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }
        }

        ColumnLayout {
            id: body
            objectName: "fmCardBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme?.spacingSm ?? 0
        }

        RowLayout {
            id: foot
            objectName: "fmCardFooter"
            Layout.fillWidth: true
            // **必须显式写 `Layout.fillHeight: false`**（阶段 3 任务 3.1 人工验收的缺陷 D-161）：
            // 在 ColumnLayout 里只给 `Layout.alignment: Qt.AlignRight`（只有水平分量）时，
            // Qt Quick Layouts 会认为**纵向没有对齐约束**，于是把它也当成"可拉伸项"，
            // 与 `fmCardBody`（fillHeight: true）**平分**多出来的高度。后果是：
            // 卡片被拉伸时（`FmPage` 的内容卡就是），页脚拿走一半高度，正文只剩一半 ——
            // 首页四张卡因此只有前两张看得见（滚动视图的可视区被压到 322px）。
            // 页脚本来就该是"内容多高就多高"，显式写死 false 才是本意。
            Layout.fillHeight: false
            Layout.alignment: Qt.AlignRight
            spacing: Theme?.spacingSm ?? 0
        }
    }
}
