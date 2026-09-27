# qml/components —— 通用组件库白名单（阶段 2 任务 2.16 / 2.17）

> 这份文件就是闸门 **R5** 的白名单数据源（`scripts/check_qml_rules.py` 的
> `load_component_whitelist()`）：`qml/pages/**` 里用了自研组件而名字不在本文件里，
> R5 就报违规。文件不存在或解析不出名字时 R5 会"跳过并说明"—— 所以本文件落地
> 之后 R5 才真的开始判定。

## 一、写这份文档时必须守的一条（闸门解析器很宽容，宽容是双向的）

R5 的解析器把文档里 **任何被单反引号包住的大驼峰词**都当成"已登记组件名"
（正则：反引号 + 字母开头的标识符 + 反引号，然后只保留首字母大写的那批）。

所以：

- **只用反引号包组件名**（例如反引号 + FmButton + 反引号）；表格第一列的大驼峰词
  也会被收进白名单，所以两处都要真实存在对应的 `.qml` 文件。
- **不要把缩写、桥名、QML 内置类型或普通词写成"单反引号 + 大驼峰"**——
  Theme、Tr、Nav、Shell、TextField、ListView 这类词一旦被包进单反引号，就会被
  悄悄登记成"组件"，白名单从此虚胖，R5 也就拦不住真的越界件了。
  需要行内代码样式时用**双反引号**（示例列就是这么写的）。
- 组件名一律 Fm 前缀 + 大驼峰，一个组件一个文件（文件名 = 组件名 = 白名单里的名字）。

## 二、页面能用的 21 个组件（阶段 3 只允许用这些）

| 组件 | 用途（什么时候用 / 什么时候不要用） | 关键属性 | 最小示例 |
|------|--------------------------------------|----------|----------|
| `FmButton` | 任何"点一下发生一件事"的动作；一个界面最多一个主按钮。不要用于导航项、纯图标命中区、整行点击 | `text` `primary` `danger` `loading` `iconName` `iconOnly` `enabled` `clicked()` | ``FmButton { text: Tr?.map["confirm"] ?? "confirm"; onClicked: save() }`` |
| `FmTextField` | 表单里的单行输入（版本名、玩家名、路径）。不要用于搜索、多行、只读展示 | `text` `label` `placeholder` `errorText` `password` `readOnly` `accepted()` `edited(v)` | ``FmTextField { label: "ID"; errorText: page.idError; text: page.versionId }`` |
| `FmTextArea` | 需要换行的输入（公告、备注、启动参数、提示词）。不要用于单行、只读长文、日志 | `text` `label` `placeholder` `errorText` `lines` `readOnly` `edited(v)` | ``FmTextArea { lines: 6; text: page.jvmArgs }`` |
| `FmSearchField` | 关键词搜索/过滤的那一个输入框。不要用于普通表单（会让人以为立即过滤） | `text` `placeholderText` `accepted()` `cleared()` | ``FmSearchField { placeholderText: Tr?.map["search"] ?? "search"; onAccepted: page.search(text) }`` |
| `FmSwitch` | 立即生效的二值设置。不要用于需要"保存"才生效的表单、三态、互斥组 | `checked` `text` `toggled()` `enabled` | ``FmSwitch { text: Tr?.map["settings_minimize"] ?? "minimize"; checked: page.minimize }`` |
| `FmRadio` | 2~5 个短文案的互斥选项（同一父项内自动互斥）。不要用于可保存的设置、长列表 | `checked` `text` `autoExclusive` `toggled()` | ``FmRadio { text: "4"; checked: page.threads === 4; onToggled: page.threads = 4 }`` |
| `FmComboBox` | 选项多或文案长的互斥选择（镜像源、Java、语言、加载器）。不要用于 2~3 个短选项、开关、可输入值 | `currentIndex` `currentText` `model` `textRole` `valueRole` `currentValue` `activated(i)` | ``FmComboBox { model: page.mirrors; textRole: "name"; valueRole: "url" }`` |
| `FmSlider` | 区间内取值且"看得到当前值"有意义（音量、缩放、线程数）。不要用于精确输入、二值 | `value` `from` `to` `stepSize` `showValue` `valueText` `moved()` | ``FmSlider { from: 1; to: 32; stepSize: 1; value: page.threads }`` |
| `FmListItem` | 列表委托：标题 + 副标题 + 右侧次要信息。不要用于卡片、导航项、整块可点区域 | `title` `subtitle` `iconName` `trailingText` `selected` `clickable` `clicked()` | ``FmListItem { title: modelData.name; subtitle: modelData.loader; onClicked: page.open(modelData.id) }`` |
| `FmCard` | 把一组内容框成视觉单元、Page 分区。不要卡片套卡片、不要给卡片加点击语义 | `title` `subtitle` `padding` `content`（默认属性） `footer` | ``FmCard { title: "Java"; FmListItem { title: page.javaPath } }`` |
| `FmPagination` | 服务端分页 / 大列表翻页。不要用于本地已全量取到的数据、只有一两页 | `page` `pageCount` `compact` `pageRequested(page)` | ``FmPagination { page: page.currentPage; pageCount: page.totalPages; onPageRequested: page.goto(p) }`` |
| `FmTag` | 短语义标记（已安装 / 快照 / Forge / 需更新）。不要用于可点击筛选、说明文字 | `text` `level` `iconName` `closable` `closed()` | ``FmTag { text: modelData.loaderName; level: "accent" }`` |
| `FmProgressBar` | 能算出百分比的长任务（下载、安装、校验）。不要用于算不出总量的等待、区域级占位 | `value`（0~1） `indeterminate` `label` `showPercent` `barHeight` | ``FmProgressBar { label: report.message; value: report.fraction }`` |
| `FmProgressRing` | 紧凑位置的进度或忙碌指示（卡片角、行尾）。不要用于整页加载态、有文案的下载 | `value` `indeterminate` `lineWidth` `showPercent` `ringColor` | ``FmProgressRing { indeterminate: true; lineWidth: 3 }`` |
| `FmInfoBar` | 页面级说明/结果提示，可带一个动作与关闭。不要用于 Toast、字段校验、整页三态 | `text` `level` `actionText` `closable` `actionTriggered()` `closed()` | ``FmInfoBar { level: "warning"; text: page.javaWarning; actionText: Tr?.map["retry"] ?? "retry" }`` |
| `FmEmptyState` | 取到数据但一条都没有（没装版本、搜索无结果）。不要用于加载中、出错、单个字段为空 | `title` `description` `iconName` `actionText` `actionTriggered()` | ``FmEmptyState { iconName: "folder-open"; title: Tr?.map["mod_browser_no_results"] ?? "empty" }`` |
| `FmLoadingState` | 整块内容区正在等数据。不要用于可量化进度、局部小块、空、错 | `title` `description` `iconName` `spinning` | ``FmLoadingState { title: Tr?.map["plugin_state_loading"] ?? "loading" }`` |
| `FmErrorState` | 取数据失败（网络、磁盘、服务异常）。不要用于字段校验、一次性提示、加载中 | `title` `description` `detailText` `retryText` `iconName` `retried()` | ``FmErrorState { description: page.errorText; detailText: page.errorDetail; retryText: "retry"; onRetried: page.reload() }`` |
| `FmTable` | 多列、需要对照阅读的结构化数据。不要用于一两列的行、卡片内容、行内编辑 | `columns`（`[{key,title,width,align}]`） `rows` `emptyText` `clickable` `rowActivated(i,row)` | ``FmTable { columns: page.cols; rows: page.rows; emptyText: "no rows" }`` |
| `FmIcon` | 界面上的一切图标（唯一的上色入口）。不要用于 FluentUI 控件内部图标、多色插画 | `name` `color` `size` `fallbackUsed` | ``FmIcon { name: "check"; color: Theme?.success ?? "transparent"; size: Theme?.iconSize ?? 0 }`` |
| `FmToolButton` | **纯图标**的方形命中区（顶栏的返回/通知/账号、行尾的复制/删除）。不要用于有文字的按钮（`FmButton`）、导航项、整行点击（`FmListItem`） | `iconName` `iconColor` `hoverIconColor` `iconSize` `selected` `side` `badgeText` `enabled` `clicked()` | ``FmToolButton { iconName: "notify"; selected: page.unread > 0; badgeText: page.unread > 0 ? String(page.unread) : "" }`` |

## 三、宿主与对话框组件（2.13 / 2.15 落地，页面一般不直接用，但一样登记）

它们在 `qml/components/` 下、也确实是自研件；登记是为了让阶段 3 的页面
（例如"设置 → 日志/关于"里放一个对话框宿主预览）不会撞上 R5 的误报。

| 组件 | 用途 | 关键属性 |
|------|------|----------|
| `DialogHost` | 对话框宿主：接 Dialogs 桥的请求、排队、按 kind 选组件、回填答案（`App.qml` 里挂一层） | `queue` `current` `markReady()` `markClosing()` |
| `ToastHost` | 右下角 Toast 队列（`App.qml` 里挂一层；业务侧用 Dialogs.notify() 触发） | `itemHeight` `itemWidth` `itemGap` `itemMargin` `fadeInDuration` |
| `MessageDialog` | info / warning / error 三合一提示框 | `request` `level` `accept()` `cancel()` |
| `ConfirmDialog` | 确认框（确定 -> true，取消 -> false） | `request` `defaultIsConfirm` `accept()` `cancel()` `activateDefault()` |
| `TextInputDialog` | 单行文本输入框（`ask_text`） | `request` `fieldText` `accept()` `cancel()` |
| `ChoiceDialog` | 选项框（选项即按钮，点了立即作答） | `request` `options` `primaryIndex` `select(value)` |
| `ProgressDialog` | 进度面板（确定进度 / 不确定转圈 / 可取消） | `request` `report` `cancellable` `requestCancel()` |
| `LogView` | 通用日志面板 / 控制台（任务 2.18，`qml/pages/dev/LogDemo.qml` 是它的自查页）：ListModel + ListView 虚拟化、按块裁剪（内存硬上界 `maxLines + trimChunk`）、用户上滚后不再抢滚动位置；日志内容由调用方喂进来，组件不读文件、不碰服务 | `maxLines` `trimChunk` `lineCount` `autoScroll` `searchText` `title` `emptyText` `showToolbar` `append(line,level)` `appendLines(list)` `clear()` `copyAll()` `cleared()` `copied(n)` |

## 四、新增一个组件要走完的四步

1. **加文件**：`qml/components/FmXxx.qml`。文件头必须写两段注释：**什么时候用它**、
   **什么时候不要用**（阶段 3 的人靠这个决定用哪个件）；根节点必须有稳定的 `objectName`
   （Gallery 与冒烟测试按它找件，例如 `fmXxx`）。
2. **守纪律**：颜色只来自 `Theme.*`（闸门 R8 拦十六进制字面量）、文案由调用方给或
   来自 `Tr.map[...]`（闸门 R3/R4）、图标走 `FmIcon`（闸门 R2 禁 emoji）、
   不许渐变（闸门 R1）、每一个上下文属性读取都要判空（`Theme?.x ?? 兜底`）。
3. **进白名单**：在本文件的第二节表里加一行（名字 / 用途 / 关键属性 / 最小示例）。
4. **跑闸门**：`python scripts/check_qml_rules.py` 退出码 0；再跑
   `python -m pytest tests/test_components_qml.py -q`（它会实例化**每一个** `Fm*.qml`
   并断言 Gallery 里也有对应实例）。

> 阶段 3 的纪律（`03` 的 3.0 SOP 第 3 条）：页面上出现"一次性控件"时**不是**往页面里
> 就地写，而是回到本文件按上面四步补一个通用件 —— 组件清单是锁定的，越用越少而不是越多。

## 五、三条纪律（页面作者最常踩的）

1. **颜色**：`Theme.*` 的 12 个语义色键 ＋ **15 个派生令牌**（`windowBg / windowBgInactive /
   navBg / barBg / cardHover / divider / overlayBg / scrim / textTertiary / accentSoft /
   accentPressed / accentText / itemHover / itemPress / itemCheck`）＋ 字号/间距/圆角/图标尺寸
   十四个设计令牌 ＋ 动效（`durationFast/Normal/Slow`）与布局（`navWidth/titleBarHeight/
   statusBarHeight`）令牌，除此之外没有合法颜色来源（缺陷 D-102 与风险 R-15 就是这么来的）。
   派生令牌由 **Python 侧**从 12 键算出来（`app/bridges/theme_bridge.py: derive_tokens()`）——
   QML 里**不许**自己调 `Qt.rgba` / `Qt.lighter`（闸门 R8 连 `Qt.rgba` 一起拦）。
   界面**锁深色**（5 个预设主题全是深色）：`FluTheme.darkMode` 显式设成它自己的枚举里的
   暗色档（`FluThemeType::DarkMode::Dark`，值 **2**；写成 1 是浅色，那条坑记在 D-139）。
2. **文案**：一律 `Tr.map["键"] ?? "键"`，**不要**在绑定里写 `Tr.t(...)`
   （QML 只跟踪属性读取，语言切换时不会重算 —— 契约第六节决策 1，闸门 R4 静态拦）。
3. **图标**：`FmIcon { name: "check" }`，名字来自 `qml/assets/icons/*.svg`
   （小写 + 连字符，语义命名）。图标上色由 Python 侧的 `image://fmcl-icon` 提供者完成
   （`app/bridges/icon_provider.py`），QML 侧不要自己拼颜色、也不要用 shader 效果上色。
