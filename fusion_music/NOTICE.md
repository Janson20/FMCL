# NOTICE —— fusion_music 后端来源

本目录的后端代码（`core/`、`sources/`、`security/`、`bridges/` 与根级路径/配置模块）
从工作区中的 `tmp/FusionMusicPlayer/app/` 移植，来源提交 `2c81831`
（refactor(ui): 收敛列表接线、歌手行、覆盖层与设置项的重复实现）。

上游项目与 FMCL 同为 GPL-3.0-only；移植时保留了源文件的相对导入与行为语义，
改动集中在：

* 包名从 `app` 改为 `fusion_music`；
* 数据根由宿主设为 `<FMCL base>/data/music/`；
* 未使用的独立窗口/托盘/启动画面/自动更新模块不参与本次装配；
* 新增 `host.py`（生命周期）与 `migration.py`（旧 `data/music.json` 只读迁移）。

原始 QML 界面没有整包复制：FMCL 侧的 `qml/components/music/` 与
`qml/pages/music/` 是按其交互模型重写的四语言组件。上游完整说明见
`tmp/FusionMusicPlayer/README.md` 与 `LICENSE`。
