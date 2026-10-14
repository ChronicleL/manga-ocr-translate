# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式。

---

## [Unreleased]

### Manga Studio（新：PySide6 汉化工作台）

面向整话汉化的桌面工作台，取代“一张一张截图”的小工具。识别、翻译、叠图都复用原有引擎（`detector` / `pipeline` / `translation` / `typeset`），新增的是整个界面层和项目管理。用 `run_studio.bat` 启动，详见 README。

#### Added
- **项目文件夹**：一话一个项目（`project.json` + `pages/` + `data/`），页面、识别结果、译文、校对状态都落盘，关掉再开可以接着做。数据文件原子写入；改动约 1 秒后自动保存，切换页面 / 项目和关窗口时立即保存。
- **三栏工作区**（Win11 Fluent 浅色 / 深色 / 跟随系统，强调色取系统主题色）：页面缩略图列表（状态点、拖动排序、右键重新识别 / 删除）、画布、校对栏。
- **画布**：Ctrl+滚轮缩放、拖动平移、双击适应窗口、点击选中文本块；文本块框带编号徽标；底部悬浮栏有“翻译”菜单、**叠图 ⇄ 原图**同一按钮切换（空格键）、文本框开关、缩放按钮。叠图在后台渲染并防抖，只渲染最新版本。
- **识别队列**：单线程串行识别，页面显示“排队中 / 识别中 / 已识别”，状态栏显示检测和 OCR 进度；模型在后台线程加载，窗口先出现；导入后默认自动识别（可关）。重新识别已有译文 / 手改的页面前会确认。
- **校对栏**：每个文本块一张卡片（序号、方向、已校对勾选、单块重译、可编辑的原文和译文，输入框随内容长高）；画布上的框与卡片**双向定位**；按阅读顺序排列，顶部显示“已校对 N/M”。
- **翻译**：本页 / 全部页面 / 只翻译还没有译文的页面，可取消；翻译通道（谷歌、有道、MyMemory、已配置的 AI 接口）在同一菜单里切换。某一页失败会立刻放弃同批剩下的页并只提示一次。
- **机翻不覆盖人工成果**：已勾选“已校对”或手写过译文（`translation_edited`）的块一律跳过，只有对单个块点它自己的刷新按钮才会强制重译（已校对的除外）；请求发出后你改了原文、手写了译文或勾选了已校对，迟到的结果也不会覆盖。
- **三种新的导入方式**：剪贴板（Ctrl+V，也支持资源管理器里复制的图片文件）、**截图**（每块屏幕一个遮罩，按物理像素裁剪，多显示器和不同缩放比例都能对上）、**网页**（粘贴章节网址，列出图片、缩略图、尺寸，按宽度自动勾选，带 Referer 下载，支持 `data-src` 等懒加载属性、`srcset`、`<picture>`、`<noscript>`、`<base href>`；不支持 JavaScript 动态生成和需要登录的站点）。
- **设置窗口**：普通翻译（引擎、目标语言、测试机翻）、AI 翻译（多套接口预设、模型下拉可手填、随机性、关闭思考模式、风格提示词、测试 AI）、性能（执行后端、CPU 线程、OCR 批处理，保存后自动重新加载模型，重载前先释放旧模型）。在配置副本上编辑，取消不改任何东西。
- **导出**：整话图片（PNG / JPG，按页面顺序编号，可选带上没有译文的页面的原图）、当前页图片、文本、JSON（含坐标与校对状态）；整话导出在后台进行，有进度、可取消，单页失败不影响其他页。
- **快捷键**：集中在 `shortcuts.py` 一张表里，菜单和提示引用同一张表；包括 Alt+↑/↓ 切换文本块并把光标放进译文框、Ctrl+Enter 标记已校对并跳到下一块。窗口级快捷键不占用文本框自己的编辑键。
- **`--selftest`**：启动后等模型加载完成，把 `OK …` / `FAIL …` 写进指定文件并以 0 / 1 退出，供打包后的冒烟检查使用。
- **打包**：新增 `manga_studio.spec`、`requirements-build.txt`、`packaging/README_studio.txt`；`packaging/build_release.ps1` 支持 `-App studio|legacy`，Studio 打包后自动跑 `--selftest`，失败则不生成发布包。
- **测试**：新增 `tests/`，覆盖数据层、画布、校对栏、翻译与编辑流程、截图遮罩、网页导入（对着本地假网站）、设置、导出、快捷键、`--selftest`。不加载任何模型，界面用离屏平台，不需要显示器。

#### Changed
- 项目文件里的 `data/*.json` 在 `PageResult.to_dict()` 基础上增加 `source`、`translation`、`reviewed`、`source_edited`、`translation_edited`；旧格式（只有 `text`）仍可读取。
- `EngineWorker.reload()`、`pipeline.extract_page(on_progress=…)`、`recognizer.recognize_many(on_progress=…)`：供 Studio 使用的引擎接口，旧接口行为不变。
- 启动脚本 `run_studio.py` 在 `exec()` 返回、数据都已保存之后直接 `os._exit`：torch、onnxruntime（DirectML）和 Qt 在退出时各自析构，约三分之一的运行会在窗口关闭后以访问冲突崩溃。

#### Notes（环境）
- **Anaconda 的 Python**：conda 自带的 `icuuc.dll`（导出名带版本后缀）会盖住 Windows 自带的同名文件，Qt 6.12 导入失败（`DLL load failed … 找不到指定的程序`）。启动时先按绝对路径预加载系统那份来绕开（`studio/bootstrap.py`），不改动 Anaconda 和系统。
- **导入顺序**：必须先导入 PySide6，再导入 torch / onnxruntime / cv2（反过来会让 Fluent 控件库的 `win32api` 导入失败），所以识别引擎在窗口出现之后的后台线程里加载。
- **安装分两步**：`PySide6-Fluent-Widgets` 声明依赖完整版 `PySide6`（含 QtWebEngine，几百 MB），用 `--no-deps` 安装，见 `requirements-studio.txt`。
- **许可证**：`PySide6-Fluent-Widgets` 为 GPLv3，`PySideSix-Frameless-Window` 为 LGPLv3；对外分发前请留意。

### 旧版悬浮窗（customtkinter）

#### Added
- **启动加载界面（Splash Screen）**：双击 `run_gui.bat` 后立即弹出深色浮窗并显示不定进度条，重模块（cv2 / onnxruntime / PIL / torch 等）在后台线程加载，期间主线程持续刷新动画，避免"无响应"的感觉。加载完成后 Splash 无缝变形为主窗口（同一 `ctk.CTk` 实例，不销毁重建）。
- **识别进度反馈**：状态栏实时更新每个阶段的进度——检测完成后显示"N 块 / M 行"，OCR 期间每批次更新"已完成 X / 总 Y 行"。底层在 `pipeline.py` 和 `recognizer.py` 中添加了 `on_progress` 回调接口。
- **双区状态栏**：左侧显示当前操作进度，右侧常驻后端信息（如 `检测 Dml · OCR cpu`），两者互不覆盖。
- **叠图窗口响应式缩放**：叠图预览窗口现在可以自由缩放，图片随窗口大小自适应（带 80ms 防抖，使用 `Image.LANCZOS` 重采样），初始大小也改为按图片比例计算而非固定值。
- **CustomTkinter 现代化 UI**：全面迁移到 [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) v6，圆角控件、分层按钮样式（主操作蓝色填充、次要操作透明描边）、深色 Tooltip、现代设置对话框分节卡片。`ctk.CTkScrollableFrame` 取代了手动 Canvas + Scrollbar 方案。
- **requirements.txt**：新增 `customtkinter>=5.2`。

#### Fixed
- **弹出窗口 PhotoImage 内存积累**：详情窗和叠图预览窗的 `PhotoImage` 之前追加进全局 `_thumb_refs` 列表且从不清理；现在改为直接挂在对应的 `tk.Label` 上（`label.photo = photo`），随 Toplevel 销毁自动释放。
- **CTkScrollableFrame 在 Windows 上无法用鼠标滚轮滚动**：通过 `root.bind_all("<MouseWheel>", ...)` 捕获全局滚轮事件，并检查指针是否在 log 内部 Canvas 范围内再决定是否转发，不影响设置对话框内的下拉框和文本框自身的滚动行为。
- **Splash 控件残留主窗口**：Splash 和主窗口复用同一个 `ctk.CTk` 实例，之前未在调用 `main()` 前销毁 Splash 控件，导致标题/进度条占据主窗口上方大量空间；现在在 `run_gui.py` 中明确 `destroy()` 所有子控件后再交给 `main()`。

#### Changed
- `pipeline.MangaExtractor.extract_page()` 新增可选 `on_progress` 参数（`(stage, done, total) -> None`）。
- `recognizer.MangaOcrRecognizer.recognize_many()` 新增可选 `on_progress` 参数（`(done, total) -> None`），按批次触发。
- `gui/app.py` 的 `main()` 新增可选 `root` 参数，允许传入外部已创建的 `tk.Tk` 实例（供 Splash 复用）。
- 主窗口初始大小从 `460×660` 调整为 `480×680`。

---

## [0.4.0] — 之前版本

> 详情见各提交记录：新增 GPU 加速支持（DirectML / CUDA / OpenVINO）、批量导出叠图、手动校对窗口（编辑原文 / 译文）、多套 AI API 预设管理、关闭思考模式选项等。
