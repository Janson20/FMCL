// FmTabBar.qml —— 页签条（阶段 2 任务 2.16 的组件库，阶段 3 任务 3.6 落地）。
//
// 为什么现在才出现：2.16 锁定的清单里没有它，因为当时没有页面需要"同屏切组"；
// 3.6 的资源管理页要四类型页签，按 2.16 的四步补件流程补进来。
//
// 什么时候用它：**同一屏内切换"看哪一组数据"**且各组数量不多（4±2 个）——
//   资源管理页的「模组 / 资源包 / 地图 / 光影」就是它的典型场景。
// 什么时候不要用：
//   * 只有一个选项（写个标题就行）；
//   * 条目多到要滚动（那是导航，用 `shell/Navigation.qml`）；
//   * 表单里的互斥选项（用 `FmRadio` / `FmComboBox` —— 那是"选一个值"，
//     页签是"切换内容"）；
//   * 可关闭的多文档页签（本件没有关闭语义）。
//
// 契约：组件**不改自己的 currentKey**，点击只发 `tabRequested(key)`，
//   真正生效的是数据源（红线 2；与 `FmPagination` 的 `pageRequested` 同一条纪律）。
//
// 模型：`items` 是 `[{key, labelKey, active}, …]`。`labelKey` 是 i18n 键 ——
//   组件自己用 `Tr.map[键]` 取文案（**不在绑定里调 `Tr.t()`**，闸门 R4）。
//   给 `label` 则直接用（插件页之类的动态文案）。
//   传空数组时组件整体不可见（不画一个空框）。

import QtQuick
import QtQuick.Layouts

Item {
    id: tabBar
    objectName: "fmTabBar"

    //: `[{key, labelKey, label}, …]`
    property var items: []
    //: 当前选中的 key（由数据源给；组件不自己改）
    property string currentKey: ""

    signal tabRequested(string key)

    readonly property var rows: (items === undefined || items === null) ? [] : items
    //: 高度跟着内容走（外层用 `Layout.preferredHeight: tabBar.implicitHeight`）
    implicitHeight: row.implicitHeight

    visible: tabBar.rows.length > 0
    implicitWidth: row.implicitWidth

    RowLayout {
        id: row
        objectName: "fmTabBarRow"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Theme?.spacingXs ?? 0

        Repeater {
            objectName: "fmTabBarRepeater"
            model: tabBar.rows

            delegate: Rectangle {
                id: tab
                objectName: "fmTabBarItem-" + String(tabBar.rowKey(modelData))
                //: ListView 之外用 Repeater：Layout 托管的委托**必须**给最大高度，
                //: 否则 RowLayout 会按内容把它拉到整段高度（组件库踩过的坑）
                Layout.maximumHeight: tab.implicitHeight
                Layout.preferredHeight: tab.implicitHeight
                Layout.preferredWidth: label.implicitWidth + 2 * (Theme?.spacingMd ?? 12)
                implicitHeight: (Theme?.controlHeight ?? 32)
                radius: Theme?.radiusSm ?? 4
                color: tabBar.isActive(modelData) ? (Theme?.bgMedium ?? "transparent")
                                                  : (mouse.containsMouse ? (Theme?.itemHover ?? "transparent")
                                                                         : "transparent")
                //: 选中态的下划线（比底色的变化更明确；不靠颜色单独表达状态）
                Rectangle {
                    objectName: "fmTabBarUnderline"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 2
                    radius: 1
                    color: Theme?.accent ?? "transparent"
                    visible: tabBar.isActive(modelData)
                }

                Text {
                    id: label
                    objectName: "fmTabBarLabel"
                    anchors.centerIn: parent
                    text: tabBar.labelOf(modelData)
                    color: tabBar.isActive(modelData) ? (Theme?.textPrimary ?? "transparent")
                                                      : (Theme?.textSecondary ?? "transparent")
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: tabBar.isActive(modelData)
                }

                MouseArea {
                    id: mouse
                    objectName: "fmTabBarHit"
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: tabBar.tabRequested(tabBar.rowKey(modelData))
                }
            }
        }
    }

    //: 取一行模型的 key（模型可能是 `QVariantMap`，字段缺失时给空串）
    function rowKey(entry) {
        if (entry === undefined || entry === null)
            return "";
        return String(entry.key === undefined ? "" : entry.key);
    }

    //: 取一行的文案：`label` 优先，否则翻 `labelKey`，都没有就给键本身
    function labelOf(entry) {
        if (entry === undefined || entry === null)
            return "";
        var direct = entry.label === undefined ? "" : String(entry.label);
        if (direct.length > 0)
            return direct;
        var key = entry.labelKey === undefined ? "" : String(entry.labelKey);
        if (key.length === 0)
            return tabBar.rowKey(entry);
        return Tr?.map[key] ?? key;
    }

    //: 这一行是不是当前项。**以 `currentKey` 为准**（模型的 `active` 只是给
    //: 不方便传 currentKey 的调用方兜底；两者都给时以 currentKey 为准）
    function isActive(entry) {
        var key = tabBar.rowKey(entry);
        if (tabBar.currentKey.length > 0)
            return key === tabBar.currentKey;
        return entry !== undefined && entry !== null && entry.active === true;
    }
}
