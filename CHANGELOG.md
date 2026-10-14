# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式。

---

## [Unreleased]

### Added
- **启动加载界面（Splash Screen）**：双击 `run_gui.bat` 后立即弹出深色浮窗并显示不定进度条，重模块（cv2 / onnxruntime / PIL / torch 等）在后台线程加载，期间主线程持续刷新动画，避免"无响应"的感觉。加载完成后 Splash 无缝变形为主窗口（同一 `ctk.CTk` 实例，不销毁重建）。
- **识别进度反馈**：状态栏实时更新每个阶段的进度——检测完成后显示"N 块 / M 行"，OCR 期间每批次更新"已完成 X / 总 Y 行"。底层在 `pipeline.py` 和 `recognizer.py` 中添加了 `on_progress` 回调接口。
- **双区状态栏**：左侧显示当前操作进度，右侧常驻后端信息（如 `检测 Dml · OCR cpu`），两者互不覆盖。
- **叠图窗口响应式缩放**：叠图预览窗口现在可以自由缩放，图片随窗口大小自适应（带 80ms 防抖，使用 `Image.LANCZOS` 重采样），初始大小也改为按图片比例计算而非固定值。
- **CustomTkinter 现代化 UI**：全面迁移到 [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) v6，圆角控件、分层按钮样式（主操作蓝色填充、次要操作透明描边）、深色 Tooltip、现代设置对话框分节卡片。`ctk.CTkScrollableFrame` 取代了手动 Canvas + Scrollbar 方案。
- **requirements.txt**：新增 `customtkinter>=5.2`。

### Fixed
- **弹出窗口 PhotoImage 内存积累**：详情窗和叠图预览窗的 `PhotoImage` 之前追加进全局 `_thumb_refs` 列表且从不清理；现在改为直接挂在对应的 `tk.Label` 上（`label.photo = photo`），随 Toplevel 销毁自动释放。
- **CTkScrollableFrame 在 Windows 上无法用鼠标滚轮滚动**：通过 `root.bind_all("<MouseWheel>", ...)` 捕获全局滚轮事件，并检查指针是否在 log 内部 Canvas 范围内再决定是否转发，不影响设置对话框内的下拉框和文本框自身的滚动行为。
- **Splash 控件残留主窗口**：Splash 和主窗口复用同一个 `ctk.CTk` 实例，之前未在调用 `main()` 前销毁 Splash 控件，导致标题/进度条占据主窗口上方大量空间；现在在 `run_gui.py` 中明确 `destroy()` 所有子控件后再交给 `main()`。

### Changed
- `pipeline.MangaExtractor.extract_page()` 新增可选 `on_progress` 参数（`(stage, done, total) -> None`）。
- `recognizer.MangaOcrRecognizer.recognize_many()` 新增可选 `on_progress` 参数（`(done, total) -> None`），按批次触发。
- `gui/app.py` 的 `main()` 新增可选 `root` 参数，允许传入外部已创建的 `tk.Tk` 实例（供 Splash 复用）。
- 主窗口初始大小从 `460×660` 调整为 `480×680`。

---

## [0.4.0] — 之前版本

> 详情见各提交记录：新增 GPU 加速支持（DirectML / CUDA / OpenVINO）、批量导出叠图、手动校对窗口（编辑原文 / 译文）、多套 AI API 预设管理、关闭思考模式选项等。
