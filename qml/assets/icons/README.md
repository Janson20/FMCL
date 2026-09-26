# qml/assets/icons —— 图标资源集（阶段 2 任务 2.10）

界面**禁止 emoji**（项目全局规则 + 闸门 R2）。这里的 SVG 就是替代品：**一个图标一个文件**，
`fill="currentColor"`，24x24，纯矢量，没有渐变、没有位图、没有脚本。

配套的两份"可核对"清单：

| 文件 | 作用 |
|------|------|
| `emoji-map.json` | 旧界面每个 emoji 的归属：映射到哪个 SVG，或为什么不用换（`exempt`） |
| `docs/refactor/14-emoji-inventory.md` | 逐条清单（由 `poc/_inventory_emoji.py --write` 生成，不要手改） |

---

## 一、QML 里怎么用

```qml
Image {
    // 名字不带路径、不带扩展名；路径由 Python 侧拼（打包态与开发态不同）
    source: Runtime.iconUrl("check")
    sourceSize.width: 16          // 必须给：不给的话光栅化尺寸按 SVG 的 24 算，缩小会走缩放
    sourceSize.height: 16
    width: 16
    height: 16
    smooth: true
    // 当前实现下图标是黑色（currentColor），暗色主题必须上色，见第三节
}
```

`Runtime.iconUrl(name)` 的两个约定（实测踩过，写在这里免得阶段 3 再踩）：

1. **必须传 `file:///` 形式的 URL**。QML `Image` 的 `source` 收到 `"D:/…/check.svg"`
   这种裸路径时，会当成**相对当前 QML 文件的相对路径**去解析，结果是
   `status = Image.Error`、`implicitWidth = 0`、**界面上一片空白且不报错**。
   `Runtime.iconUrl()` 内部用 `QUrl.fromLocalFile()` 生成 `file:///D:/…/check.svg`，
   所以调用方不要自己拼路径。
2. 图标名不合法或文件不存在时返回**空串**（并打一条 warning）。`Image` 拿到空串只是不画，
   不会崩 —— 但界面会缺图，所以这是"能做但会被日志抓住"的降级，不是正常状态。

## 二、命名规则

- 全小写 + 连字符分段：`folder-open.svg`、`settings-gear.svg`、`mood-great.svg`。
- 正则（`runtime_bridge.py` 与 `tests/test_icon_set.py` 用的是同一条）：`^[a-z][a-z0-9]*(-[a-z0-9]+)*$`。
- 名字表达**语义**，不表达形状，也不表达 emoji：用 `modpack` 而不是 `sloth`（旧界面的
  「懒人福利」成就拿一个动物 emoji 充数，新界面用语义图标）。
- 同一个语义只允许一个文件：`U+1F527`（扳手）与 `U+1F6E0`（锤子加扳手）都映射到
  `tool.svg`，不新建 `hammer-wrench.svg`。

## 三、`currentColor` 与主题上色（**实测结论**）

每个 SVG 的每个可见元素都显式写 `fill="currentColor"`。原因有两条，都是实测出来的：

1. **QtSvg 把 `currentColor` 解析成不透明黑**（渲染结果 RGBA = `0,0,0,255`，
   不是透明、也不是报错）。所以**不换色也不会消失**，但暗色主题下几乎看不见 ——
   上色是必须的，不是可选的。
2. 显式写在每个元素上（而不是靠根 `<svg>` 继承），是为了让"换色"这一步可以简单地对文件
   做字符串替换（见下表的推荐方案），不给 QtSvg 的继承规则留解释空间。

### 五种上色路线，实测结果

同一份 24x24 方块 SVG、同一个 `Direct3D11` 后端，只换 QPA 平台：

| 方案 | 真实 Windows 会话（`QT_QPA_PLATFORM=windows`） | `offscreen`（**本仓库全部测试的跑法**） | 结论 |
|------|-----------------------------------------------|----------------------------------------|------|
| SVG 里写死 `fill="#RRGGBB"` | 精确色 | 精确色 | 不可行：不跟主题，12 个色键没法表达 |
| `fill="currentColor"` 不换色 | 黑 | 黑 | 只能当兜底（可见但不可读） |
| `ColorOverlay`（`Qt5Compat.GraphicalEffects`） | **精确**（期望蓝 → 实测 `0,0,255`） | **静默失效**（源色透出，不报错） | 不建议：离线测不了 + 纯 shader 依赖 |
| `MultiEffect { colorization: 1 }`（`QtQuick.Effects`） | 生效但**色值偏暗**（期望蓝 → 实测 `0,0,76`） | 静默失效 | 不可行：`colorization` 是混合语义，不是替换色 |
| `layer.enabled + layer.effect: MultiEffect` | 同 `MultiEffect`（`0,0,76`） | 静默失效 | 不可行 |
| `OpacityMask`（图标当遮罩 + 实色矩形） | **精确**（`0,0,255`） | 静默失效 | 可行但同样测不了 |
| **`image://` 图片提供者**：Python 侧读 SVG 文本、把 `currentColor` 换成目标色、交给 `QSvgRenderer` 渲染 | **精确** | **精确** | **推荐** |

关键对照：`offscreen` 下连"纯 `Rectangle` + `ColorOverlay`"都不变色（期望蓝、实测仍是源色红），
所以失效的是**整个 shader 效果通路**，不是"SVG 不支持"。

### 因此的约定

- **上色必须在 Python 侧做**：`QQuickImageProvider` + `QUrl("image://fmcl-icon/<name>?color=%23RRGGBB")`。
  它零 shader 依赖，两种环境下都精确，而且**测试能验**（`tests/test_icon_set.py` 就是这么验的）。
- 这个 provider **属于任务 2.16（组件库）**：本任务只落了 `Runtime.iconUrl()`（最小改动），
  阶段 3 之前由组件库补 provider 与 `Icon.qml`，届时图标统一走 `Icon { name: "check"; color: Theme.textPrimary }`。
- **在 provider 落地之前**不要把 `ColorOverlay` 当成主题上色的依靠：它在 offscreen 下不生效，
  意味着"图标颜色对不对"这件事在 CI 里测不出来，也没有任何报错会提醒你。

## 四、哪些用 FluentUI 自带的、哪些必须自己画

**结论：FluentUI 的图标只用于 FluentUI 控件自己；我们界面上的一切图标都用本目录的 SVG。**

依据（都来自 `third_party/_install/qml/FluentUI/` 与源码 `third_party/FluentUI/src/`）：

1. **FluentUI 没有图标 SVG 库。** `FluentUI/Image/` 里只有 9 个窗口按钮 PNG
   （`btn_close/max/min_*.png`）+ `noise.png`（材质噪声）。它的图标是**字体字形**：
   `FluentUI/Font/FluentIcons.ttf`（408 KB）+ `Controls/FluIcon.qml`（`Text` + `FontLoader`，
   `iconSource: int` 就是 `FluentIcons.*` 的私有区码点）+ C++ 侧 `src/FluentIconDef.h`
   导出的枚举（约 1400 个名字）。**码点不是文件** —— 它没法进 `emoji-map.json` 这种
   "文件是否存在/合法/无脚本/无渐变"的可核对清单。
2. **FluentUI 控件内部的图标由它自己带，不用我们管，也不该我们换。**
   实测 `Controls/*.qml` 里有 30 个字形、17 个控件在用 `FluIcon`：
   `ChromeClose/ChromeMaximize/ChromeMinimize/ChromeRestore`（窗口按钮）、
   `ChevronDown/ChevronUp/CaretDownSolid8/...`（下拉与折叠箭头）、
   `InfoSolid/StatusErrorFull`（`FluInfoBar` 的状态图标）、`RevealPasswordMedium`（密码显隐）等。
   这些是控件实现的一部分，跟着控件走；替换它们等于改第三方控件。
3. **`FluIcon` 的颜色只有三档，跟不了我们的调色板。** `FluIcon.qml` 的 `iconColor` 只看
   `FluTheme.dark` 与 `enabled`：暗色=白、亮色=黑、禁用=灰。而本项目 `Theme` 有 12 个语义色键
   （`accent/success/warning/error/textPrimary/textSecondary/...`）。
   要表达"成功/警告/错误"用 `FluIcon` 就得外面再套一层上色 —— 那还不如直接用 SVG。
4. **字体图标的缺字风险正是 D-07 要修的东西。** 私有区码点缺字形时显示成"豆腐块"或空白，
   和 emoji 在部分字体下缺字是同一类问题（`docs/refactor/07-known-defects.md` D-07 的第二个理由）。
   SVG 没有这个风险。
5. **领域图标 FluentUI 根本没有。** `mods / resourcepack / shaderpack / saves / bedrock /
   agent / crash / backup / modpack / snapshot / mood-*` 这些是 FMCL 自己的语义，
   FluentIconDef 的字形名里不存在对应物。

因此本目录的 93 个 SVG 分三类：

- **替换旧 emoji**（85 条映射 → 81 个文件；`U+1F3AE` 与 `U+1F579` 共用 `game.svg`、
  `U+1F527` 与 `U+1F6E0` 共用 `tool.svg`、`U+1F4E4` 与 `U+2B06` 共用 `upload.svg`、
  `U+1F464` 与 `U+1F9D1` 共用 `account.svg`）：`emoji-map.json` 里逐个登记。
- **新界面需要的领域图标**（旧界面没有对应 emoji）：`close / check / language / theme /
  online / plugin / crash / bedrock / lyrics / memory / achievement / save` 等。
- **窗口与控件层**（阶段 3 用）：`chevron-down`、`arrow-left/right`、`close` 等。

## 五、纪律

1. **新增图标 = 同时改两个地方**：加 `qml/assets/icons/<name>.svg`，
   并在 `emoji-map.json` 里登记（映射一个 emoji，或放进 `exempt` 并写清为什么不用换）。
   注意闸门只挡一个方向：**"代码里有 emoji、映射表里没有归属"会红**，而
   "加了一个谁都不引用的新 SVG"不会红 —— 后者靠评审，所以别只看闸门绿了就以为表对上了。
2. **新增 emoji 会被挡住**：代码里出现新 emoji 而清单里没有归属时，
   `poc/_inventory_emoji.py --check` 退出码 1。不要用"往 `exempt` 里塞"来绕过：
   界面元素必须映射到图标。
3. 单个 SVG 必须满足（`tests/test_icon_set.py` 逐条验）：24x24 viewBox、含 `currentColor`、
   不含 `<script>`、不含 `Gradient`/`gradient`、不含中文、体积 < 2KB、**能被真的渲染出非空像素**。
4. 图标**不要**用渐变（项目 UI 红线 5）、不要内嵌位图、不要在 SVG 里写字（文字要走 i18n，
   而 SVG 里的文字没法本地化）。

## 六、怎么自己核对

```powershell
.venv\Scripts\python.exe poc\_inventory_emoji.py            # 清单汇总 + 一致性检查
.venv\Scripts\python.exe poc\_inventory_emoji.py --check     # 只做一致性检查（不一致退出码 1）
.venv\Scripts\python.exe poc\_inventory_emoji.py --write     # 重新生成 docs/refactor/14-emoji-inventory.md
.venv\Scripts\python.exe -m pytest tests\test_icon_set.py -q # 图标集本身的守卫（含真实渲染）
.venv\Scripts\python.exe scripts\check_qml_rules.py          # 闸门（R1/R2 也会扫这里的 SVG）
```
