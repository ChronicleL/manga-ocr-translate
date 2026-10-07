日文漫画文字提取 / 翻译工具（Windows 64 位）
==================================================

启动
----
双击 MangaOcrTranslate.exe 即可，无需安装 Python 或任何依赖。

首次启动需要 10 秒左右加载模型。启动完成后状态栏（窗口底部）会显示实际推理后端，
例如「检测 Dml · OCR cpu」表示检测已走 GPU（DirectML）加速；
若显示「检测 CPU（未启用 GPU 加速…）」，说明当前显卡/驱动不支持 DirectML，
仍可正常使用，只是检测会慢很多。

首次识别前需要联网下载日文 OCR 模型（约 420 MB，默认走 hf-mirror.com 镜像），
之后缓存在 %USERPROFILE%\.cache\huggingface，可离线重复使用。

用法
----
· 截取    —— 隐藏窗口并冻结屏幕，拖动框选漫画区域，松开即自动识别
· 剪贴板  —— 读取剪贴板图片（配合 Win+Shift+S 截图很方便）
· 打开图片 —— 选择本地图片文件
· 翻译    —— 每张结果卡片上的按钮，或工具栏「翻译全部」
· 叠图    —— 把译文按原文位置排回图片；「导出叠图…」可一次性导出全部
· 翻译设置 —— 普通翻译（谷歌/有道/MyMemory）或 AI 翻译（任意 OpenAI 兼容接口）

文件
----
与 exe 同目录，首次运行后自动生成：

  translate_config.json   翻译设置与 AI 接口预设（可能含 API Key，请勿外发）
  engine_config.json      性能设置：执行后端 / CPU 线程 / OCR 批处理
  manga_extract.log       运行日志，遇到问题时可查看

注意
----
· 请保持文件夹结构完整，_internal 目录不可删除或移动。
· 检测模型来自 dmMaze/comic-text-detector（GPL-3.0）的 ONNX 导出，
  manga-ocr 使用 Apache-2.0 许可的 kha-white/manga-ocr-base。
