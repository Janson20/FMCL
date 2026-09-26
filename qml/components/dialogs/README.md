# 对话框组件（阶段 2 任务 2.13）

QML 侧的对话框宿主与 5 个弹窗组件，服务的是 Python 侧那个 `Dialogs` 上下文属性
（`app/bridges/dialog_bridge.py`，也就是 `DialogHost` 协议的 QML 实现）。

## 文件

| 文件 | 作用 |
|------|------|
| `DialogHost.qml` | 宿主：接 `Dialogs.dialogRequested/dialogClosed/progressChanged/progressClosed`，按 kind 选组件、排队、回填答案；`Component.onCompleted/Destruction` 里做 `markReady()/markClosing()` 握手 |
| `MessageDialog.qml` | `info` / `warning` / `error` 三合一（单个「确定」，关闭即回填 `fallback`） |
| `ConfirmDialog.qml` | `confirm`（确定 -> true，取消/Esc/关闭 -> **false**） |
| `TextInputDialog.qml` | `ask_text`（预填 + 全选、回车确认、Esc 取消、`password` 掩码） |
| `ChoiceDialog.qml` | `choose`（选项即按钮，点了立即作答；`default` 决定主按钮） |
| `ProgressDialog.qml` | `show_progress` / `close_progress` 的进度面板（确定进度走进度条、不确定走转圈、有 `on_cancel` 才有取消按钮） |
| `../FmButton.qml` | 对话框按钮（**2.16 起换成官方件**：`import ".."` 之后直接用 `FmButton`；2.13 的临时件 `DialogButton.qml` 已删除） |
| `../ToastHost.qml` | 右下角 Toast 队列（新 toast 向上叠、y 递减、淡入淡出、超时消失） |

## 怎么挂进主窗口（2.12 的 `App.qml`）

```qml
DialogHost { anchors.fill: parent; z: 100 }   // 越靠后越上层
ToastHost  { anchors.fill: parent; z: 90 }
```

两个组件都会在 `Component.onCompleted` 里调 `Dialogs.markReady()` —— 这一步做了，
`QtUIPort.is_available()` 才会变 True，服务层的弹窗请求才会真的送到 QML。两者都调是
幂等的；`Component.onDestruction` 里调 `markClosing()` 放行还在等待的调用方。

## 装配链上还差一步（任务 2.14）

`main_qml.make_dialog_host()` 目前返回 `NullDialogHost`（2.13 之前没有 QML 宿主时的
占位）。QML 宿主落地之后应当改成：

```python
host = engine._fmcl_bridges.get("Dialogs") or make_dialog_host()
# 或者：先把 DialogBridge 建出来 → 同时喂给 build_qt_context() 与 setContextProperty("Dialogs", …)
```

否则 `Dialogs` 只是"QML 看得见"，`QtUIPort` 那边仍然拿着 `NullDialogHost`，服务层的
弹窗全部走静默降级。`poc/dialog_e2e_2_13.py` 就是按正确接法跑的（端口注入
`DialogBridge`，worker 的 `confirm` 经真 QML 弹窗作答后回到 worker）。

## 几条硬约定

1. **认不出的 kind 也必须回填兜底值**：`DialogHost.enqueue()` 发现没有对应组件时立刻
   调 `Dialogs.cancelDialog(id)`（桥那边用 `payload['fallback']` 收尾）。什么都不做的话
   调用方只能干等到端口超时 —— `tests/test_dialogs_qml.py` 有正例与负例各一条钉住。
2. **队列是模态的**：同时只显示队首那一个，答完自动接着显示下一条。
3. **颜色只能来自 `Theme.*`，文案只能来自 `Tr.map[...]` 或 payload**（闸门 R3/R4/R8，
   由 `scripts/check_qml_rules.py` 静态检查）。
4. **进度不是 Toast**：它有自己的取消按钮、没有超时、同一个面板从 0% 更新到 100%
   （旧界面里它也是独立窗口而不是右下角通知）。
5. **Toast 超过 6 条挤掉最旧的**：理由写在 `../ToastHost.qml` 的注释里（丢新的会丢
   最新信息；整体上移只是把越界推迟几条；挤最旧的信息损失最小且不会跑出窗口）。
6. **退出清理**：`Dialogs.clearToasts()` / `markClosing()` 是退出链要调的槽，退出链
   本身（`main_qml` 的 `aboutToQuit` 顺序）属于任务 2.14。

## 与契约文档的一处差异

`docs/refactor/11-phase2-contract.md` 第二节把 `ToastHost` 写在 `qml/overlays/` 下，
而本任务书把它定在 `qml/components/ToastHost.qml`（任务书优先，且 `overlays/` 在
2.15 归悬浮窗使用）。两者只是目录归属不同，组件内容与用法一致。
