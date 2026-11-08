// DownloadPage.qml —— 多线程下载（阶段 3 任务 3.20 / C-08）。
//
// 服务层按 Range 分片；本页提供 URL / UA / 保存目录 / 线程数展示，以及
// 暂停（保留分片）与取消（清理分片）两套动作。刷新启动器后可从同一目录继续。

import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "downloadPage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false
    property string urlText: ""
    property string uaText: "FMCL/2.0"
    property string saveDir: ""
    property bool appliedResume: false

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function tf(key, params) {
        return Tr ? Tr.tf(key, params) : key
    }

    function applyResume() {
        if (!(page ? page.live : false) || page.appliedResume)
            return;
        page.appliedResume = true;
        if (Tools.downloadHasResume) {
            var info = Tools.downloadResume;
            page.urlText = String(info.url || "");
            page.uaText = String(info.userAgent || "FMCL/2.0");
            var dest = String(info.dest || "");
            var slash = Math.max(dest.lastIndexOf("/"), dest.lastIndexOf("\\"));
            page.saveDir = slash > 0 ? dest.substring(0, slash) : page.saveDir;
        }
        if (page.saveDir.length === 0)
            page.saveDir = Tools.defaultDownloadDir;
    }

    function stateText() {
        if (!(page ? page.live : false))
            return "";
        var state = String(Tools.downloadState || "idle");
        if (state === "connecting")
            return page && page.t("tool_download_connecting");
        if (state === "pausing")
            return page && page.t("tool_download_pausing");
        if (state === "cancelling")
            return page && page.t("tool_download_cancelling");
        if (state === "paused")
            return page && page.t("tool_download_paused");
        if (state === "success")
            return page && page.t("tool_download_success_short");
        if (state === "cancelled")
            return page && page.t("tool_download_cancelled");
        if (state === "error")
            return page && page.t("tool_download_error_short");
        if (state === "downloading") {
            var progress = Tools.downloadProgress || ({});
            if (String(progress.currentText || "").length > 0) {
                return page && page.tf("tool_download_progress", {
                    "current": String(progress.currentText),
                    "total": String(progress.totalText)
                });
            }
            return page && page.t("tool_download_downloading");
        }
        return "";
    }

    contentState: (page ? page.live : false) ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

    Component.onCompleted: {
        if ((page ? page.live : false)) {
            Tools.activateDownload();
            Qt.callLater(page.applyResume);
        }
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 12

            FmCard {
                Layout.fillWidth: true

                FmTextField {
                    objectName: "downloadUrlField"
                    Layout.fillWidth: true
                    label: page && page.t("tool_download_url")
                    placeholder: "https://..."
                    text: urlText
                    onEdited: function(value) { page.urlText = value }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmTextField {
                        objectName: "downloadUaField"
                        Layout.fillWidth: true
                        label: page && page.t("tool_download_ua")
                        text: uaText
                        onEdited: function(value) { page.uaText = value }
                    }

                    FmTextField {
                        Layout.preferredWidth: 150
                        label: page && page.t("tool_download_threads")
                        text: (page ? page.live : false) && Tools.downloadHasResume
                              ? String(Tools.downloadThreadsUsed)
                              : String((page ? page.live : false) ? Tools.configuredThreads : 0)
                        readOnly: true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmTextField {
                        objectName: "downloadFolderField"
                        Layout.fillWidth: true
                        label: page && page.t("tool_download_save_path")
                        text: saveDir
                        readOnly: true
                    }

                    FmButton {
                        primary: false
                        text: page && page.t("tool_select_folder")
                        onClicked: folderDialog.open()
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: page && page.tf("tool_download_threads_hint", {
                        "threads": (page ? page.live : false)
                                  ? (Tools.downloadHasResume
                                     ? String(Tools.downloadThreadsUsed)
                                     : String(Tools.configuredThreads))
                                  : 0
                    })
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "downloadStartButton"
                        text: (page ? page.live : false) && Tools.downloadHasResume
                              ? page && page.t("tool_download_resume")
                              : page && page.t("tool_download_start")
                        loading: (page ? page.live : false) && Tools.downloadBusy
                        enabled: (page ? page.live : false) && !Tools.downloadBusy
                                 && page.urlText.trim().length > 0
                                 && page.saveDir.length > 0
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.startDownload(page.urlText, page.uaText, page.saveDir);
                        }
                    }

                    FmButton {
                        objectName: "downloadPauseButton"
                        primary: false
                        visible: (page ? page.live : false) && Tools.downloadCanPause
                        text: page && page.t("tool_download_pause")
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.pauseDownload();
                        }
                    }

                    FmButton {
                        objectName: "downloadCancelButton"
                        primary: false
                        danger: true
                        visible: (page ? page.live : false) && Tools.downloadCanCancel
                        text: page && page.t("tool_download_cancel")
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.cancelDownload();
                        }
                    }

                    FmButton {
                        objectName: "downloadDiscardButton"
                        primary: false
                        danger: true
                        visible: (page ? page.live : false) && Tools.downloadCanDiscard
                        text: page && page.t("tool_download_discard")
                        onClicked: {
                            if ((page ? page.live : false)) {
                                Tools.discardDownload();
                                page.urlText = "";
                                page.uaText = "FMCL/2.0";
                                page.saveDir = Tools.defaultDownloadDir;
                                page.appliedResume = true;
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }
                }

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && Tools.downloadHasResume
                    level: "info"
                    text: (page ? page.live : false)
                          ? page && page.tf("tool_download_resume_found", {
                                "path": String(Tools.downloadResume.dest || ""),
                                "current": String(Tools.downloadResume.downloadedText || ""),
                                "total": String(Tools.downloadResume.totalText || "")
                            })
                          : ""
                }

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.downloadError || "").length > 0
                    text: (page ? page.live : false) ? String(Tools.downloadError || "") : ""
                    level: "error"
                }
            }

            FmCard {
                Layout.fillWidth: true
                visible: (page ? page.live : false) && Tools.downloadState !== "idle"
                title: page && page.t("tool_download_progress_title")

                FmProgressBar {
                    Layout.fillWidth: true
                    label: page && page.stateText()
                    value: (page ? page.live : false) ? Number(Tools.downloadProgress.fraction || 0) : 0
                    indeterminate: (page ? page.live : false)
                                   && (Tools.downloadState === "connecting"
                                       || Boolean(Tools.downloadProgress.indeterminate))
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        Layout.fillWidth: true
                        text: (page ? page.live : false)
                              ? String(Tools.downloadProgress.speedText || "")
                              : ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    Text {
                        Layout.fillWidth: true
                        text: (page ? page.live : false)
                              ? page && page.tf("tool_download_progress", {
                                    "current": String(Tools.downloadProgress.currentText || ""),
                                    "total": String(Tools.downloadProgress.totalText || "")
                                })
                              : ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        horizontalAlignment: Text.AlignRight
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.downloadTarget || "").length > 0
                    text: (page ? page.live : false)
                          ? page && page.tf("tool_download_target", {"path": String(Tools.downloadTarget)})
                          : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideMiddle
                }
            }
        }
    }

    FolderDialog {
        id: folderDialog
        title: page && page.t("tool_download_save_title")
        onAccepted: page.saveDir = Tools.localPath(selectedFolder.toString())
    }
}
