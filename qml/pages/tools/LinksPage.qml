// LinksPage.qml —— Minecraft 相关网站目录。
//
// 29 条站点卡片（名称 + 最多 3 个标签 + 描述 + URL + 打开/复制），入口是
// 「工具箱 → 相关网站」。站点数据与译文在 services/links_service.py + ui/locales
// （页面只按 key 取文案）。
//
// 纪律：颜色只来自 Theme.*、图标走 FmIcon/FmButton、文案走 Tr.map（本页没有
// 任何硬编码文本）；打开/复制都只发意图给 Links 桥，接口与失败提示由桥统一处理。

import QtQuick
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "linksPage"

    readonly property bool live: (typeof Links !== "undefined" && Links && Links.available) ? true : false
    //: 29 条站点（桥给的是普通数组；断网也不需要，数据全在本地）。
    readonly property var entries: page.live ? Links.sites : []

    contentState: page.live ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    Connections {
        target: (typeof Links !== "undefined" && Links) ? Links : null

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level))
        }
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 12

            Repeater {
                model: page.entries

                FmCard {
                    id: linkCard
                    //: 委托上下文里的 modelData（Repeater 的 JS 数组元素）。
                    //: 不用 `required property var modelData`：那会让卡内绑定在
                    //: 属性赋值前先读一次 undefined（QML 会报 Unable to assign，
                    //: 绑定不再恢复）；这里的写法与 ToolsPage 的卡片一致。
                    property var site: modelData
                    objectName: "linkCard_" + site.id

                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        // ── 名称 + 标签（标签最多 3 个，与旧实现一致）──
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                objectName: "linkName_" + linkCard.site.id
                                Layout.fillWidth: true
                                text: page.t(linkCard.site.name_key)
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeLarge ?? 14
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            Row {
                                spacing: Theme?.spacingXs ?? 4

                                Repeater {
                                    model: linkCard.site.tag_keys

                                    FmTag {
                                        text: page.t(modelData)
                                        level: "accent"
                                    }
                                }
                            }
                        }

                        // ── 描述 ──
                        Text {
                            objectName: "linkDesc_" + linkCard.site.id
                            Layout.fillWidth: true
                            text: page.t(linkCard.site.desc_key)
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 11
                            wrapMode: Text.WordWrap
                        }

                        // ── URL + 打开 / 复制 ──
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                objectName: "linkUrl_" + linkCard.site.id
                                Layout.fillWidth: true
                                text: String(linkCard.site.url || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                                elide: Text.ElideMiddle
                            }

                            FmButton {
                                objectName: "linkCopy_" + linkCard.site.id
                                primary: false
                                text: page.t("copy_link")
                                iconName: "copy"
                                onClicked: {
                                    if (page.live)
                                        Links.copyLink(page.t(linkCard.site.name_key), linkCard.site.url)
                                }
                            }

                            FmButton {
                                objectName: "linkOpen_" + linkCard.site.id
                                primary: true
                                text: page.t("open_link")
                                iconName: "link"
                                onClicked: {
                                    if (page.live)
                                        Links.openUrl(linkCard.site.url)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
