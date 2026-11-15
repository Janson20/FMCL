// FmMusicTrackList.qml —— 所有歌曲列表共用的 ListView + 状态层。
//
// 什么时候用它：发现/本地/歌单/队列/在线结果的曲目列表。
// 什么时候不要用：歌单卡片网格、专辑封面墙、非曲目列表。
//
// 只负责“列表怎么显示、状态怎么叠”，不写业务：点击/加入/移除/上移/下移/右键
// 都只发 index，页面再用自己的模型解析出曲目。页面仍负责外层卡片与页头按钮。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: control
    objectName: "musicTrackList"

    property var model: null
    property var player: null

    property string variant: "local"
    property int sequenceOffset: 0
    property bool showSequence: false
    property bool showAdd: false
    property bool showRemove: false
    property bool showMove: false
    property bool showCopy: true
    property bool showSource: true
    property bool showQuality: true
    property bool showOriginal: true
    property bool showPlayCount: true

    property bool showStates: true
    property bool loading: false
    property string loadingTitle: ""
    property bool error: false
    property string errorTitle: ""
    property string errorDescription: ""
    property string retryText: ""
    property bool showEmpty: true
    property string emptyTitle: ""
    property string emptyDescription: ""
    property string emptyIcon: "note"
    property string emptyActionText: ""
    property bool emptyActionPrimary: false

    property int bottomMargin: 0
    property string listObjectName: "musicTrackListView"

    signal activated(int index)
    signal addRequested(int index)
    signal removeRequested(int index)
    signal moveUpRequested(int index)
    signal moveDownRequested(int index)
    signal contextRequested(int index, real sceneX, real sceneY)
    signal retryRequested()
    signal emptyActionTriggered()

    readonly property int itemCount: {
        if (!control.model)
            return 0
        if (control.model.count !== undefined)
            return Number(control.model.count)
        if (control.model.length !== undefined)
            return Number(control.model.length)
        return 0
    }

    readonly property bool emptyVisible: control.showStates && control.showEmpty
                                           && control.itemCount === 0
                                           && !control.loading && !control.error

    ListView {
        id: list
        objectName: control.listObjectName
        anchors.fill: parent
        clip: true
        spacing: 1
        model: control.model
        visible: !(control.showStates && (control.loading || control.error))
        boundsBehavior: Flickable.StopAtBounds
        bottomMargin: control.bottomMargin
        ScrollBar.vertical: FmScrollBar { }

        delegate: FmMusicTrackRow {
            width: list.width
            current: !!(control.player && control.player.currentTrack
                     && control.player.currentTrack.uid === uid)
            showSequence: control.showSequence
            sequence: control.sequenceOffset + index + 1
            showAdd: control.showAdd
            showRemove: control.showRemove
            showMove: control.showMove
            canMoveUp: index > 0
            canMoveDown: index < control.itemCount - 1
            showCopy: control.showCopy
            showSource: control.showSource
            showQuality: control.showQuality
            showOriginal: control.showOriginal
            showPlayCount: control.showPlayCount
            onActivated: function (rowIndex) { control.activated(rowIndex) }
            onAddRequested: function (rowIndex) { control.addRequested(rowIndex) }
            onRemoveRequested: function (rowIndex) { control.removeRequested(rowIndex) }
            onMoveUpRequested: function (rowIndex) { control.moveUpRequested(rowIndex) }
            onMoveDownRequested: function (rowIndex) { control.moveDownRequested(rowIndex) }
            onContextRequested: function (rowIndex, sceneX, sceneY) {
                control.contextRequested(rowIndex, sceneX, sceneY)
            }
        }
    }

    FmEmptyState {
        anchors.fill: parent
        visible: control.emptyVisible
        iconName: control.emptyIcon
        title: control.emptyTitle
        description: control.emptyDescription
        actionText: control.emptyActionText
        actionPrimary: control.emptyActionPrimary
        onActionTriggered: control.emptyActionTriggered()
    }

    FmLoadingState {
        anchors.fill: parent
        visible: control.showStates && control.loading
        title: control.loadingTitle
    }

    FmErrorState {
        anchors.fill: parent
        visible: control.showStates && control.error && !control.loading
        title: control.errorTitle
        description: control.errorDescription
        retryText: control.retryText
        onRetried: control.retryRequested()
    }
}
