# Copilot 项目说明：Manga Studio（漫画汉化工作台）

Windows 桌面应用：导入漫画页 → 检测文字块 → 日文 OCR → 翻译 → 把译文排回图上，并在右侧逐块校对。
界面是 PySide6 + PySide6-Fluent-Widgets（`qfluentwidgets`）。回答和代码注释使用中文，标识符使用英文。

## 目录与分层

- 引擎层（不依赖界面，尽量别改）：`manga_extract/detector.py`、`pipeline.py`、`recognizer.py`、`translation.py`、`typeset.py`、`runtime.py`、`image_io.py`。
- 界面层：`manga_extract/studio/`
  - `project.py`：项目文件夹读写与数据模型（`BlockData` / `PageData` / `Project`），纯 Python，可单测。
  - `main_window.py`：三栏窗口，页面列表（`pages_panel.py`）、画布（`canvas.py`）、校对栏（`blocks_panel.py`）。
  - `workers.py`：识别与翻译的后台线程，结果通过 Qt 信号回到主线程。
  - `web_import.py` / `web_worker.py` / `web_dialog.py`：网页图片提取（只用标准库，静态解析 + Referer）。
  - `capture_qt.py`、`export.py`、`settings_dialog.py`、`shortcuts.py`（快捷键唯一来源）。
- 旧版悬浮窗：`gui/`（customtkinter），暂时保留，不要在新功能里引用。
- 项目文件夹格式：`project.json` + `pages/` + `data/*.json`。

## 环境与命令

- 使用仓库内 `.venv`。安装分两步：
  1. `pip install -r requirements.txt` 然后 `pip install -r requirements-studio.txt`
  2. `pip install --no-deps PySide6-Fluent-Widgets PySideSix-Frameless-Window`（不要去掉 `--no-deps`，否则会拉下几百 MB 的完整 PySide6）
- 运行：`run_studio.bat` 或 `python run_studio.py`。
- 测试（不加载模型，使用离屏平台）：`set QT_QPA_PLATFORM=offscreen` 后 `python -m unittest discover -s tests`。
- 冒烟检查：`MangaStudio.exe --selftest result.txt`，结果文件以 `OK` 开头为通过。
- 打包：`powershell -File packaging/build_release.ps1`（含自检与压缩）。
- 访问 GitHub 在本机需要代理 `127.0.0.1:7890`。

## 必须遵守的约定

1. **导入顺序**：先导入 PySide6，再导入 torch / onnxruntime / cv2，反过来会导致 DLL 加载失败。识别引擎在窗口出现后的后台线程里加载。
2. **Anaconda 环境**：conda 的 `icuuc.dll` 会盖住系统的，`studio/bootstrap.py` 会预加载系统那份，不要删除这个调用。
3. **退出**：`run()` 在数据保存完后用 `os._exit`，原因是 torch / onnxruntime / Qt 退出时析构冲突会崩溃。
4. **机翻不能覆盖人工成果**：`reviewed` 或 `translation_edited` 的块必须跳过机翻；异步结果回来时要重新校验（原文没变、仍然符合条件）再写入。
5. **落盘**：JSON 原子写入；编辑后防抖约 800 ms 保存，切页、切项目、关窗时立即保存。
6. **线程**：不要在非主线程操作控件；跨线程只用信号，并连接到 QObject 的绑定方法。
7. **文本块的叠图重绘**防抖约 300 ms，并丢弃过期结果。
8. **不要修改或删除用户的 `studio_config.json`**（已在 `.gitignore`），测试中不要写入真实配置路径。
9. 不要在没有明确要求时提交或推送；删除文件、`git reset --hard`、`git clean`、强制推送前先确认。

## 测试约定

- 测试用假引擎、假翻译器、假网站和假对话框，通过 `MainWindow(...)` 的构造参数注入（见 `tests/_studio_fakes.py`）。
- 新增行为时同时写回归测试，并临时破坏实现确认测试会失败。
- Qt 中文输入测试不要用 `QTest.keyClicks`，用 IME 提交的辅助函数 `type_text`。
- 测试结束要销毁窗口和控件（`dispose_window` / `dispose_widget`），否则后续用例会越来越慢。

## 已知限制 / 待办

- 没有人工验证过：真实 IME 打字、翻译菜单弹出样式、AI 翻译通道（需要 API Key）、多显示器截图、真实模态对话框。
- 是否移除旧 `gui/` 与 `customtkinter`，并让 `run_gui.*` 指向 Studio，尚未决定。
- 许可证：`PySide6-Fluent-Widgets` 为 GPLv3，检测模型为 GPL-3.0，对外分发前需留意。
