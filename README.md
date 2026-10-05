# manga_extract

日文漫画文字提取（OCR）工具。按照日文漫画的阅读习惯——**纵向排版、从右到左、从上到下**——检测并识别页面中的文字，输出带顺序和坐标的结果，为后续翻译流程提供输入。

当前已完成：**文字检测 + 阅读顺序 + 文字识别**，并提供**悬浮窗截图提取**工具。

## 功能

1. **文字检测**：使用 `comic-text-detector`（YOLOv5 文本块检测 + UNet 文字掩码 + DBNet 文本行检测）的 ONNX 版本，纯 CPU 推理，无需 PyTorch。
2. **阅读顺序**：把检测到的文本行按“文本块”分组，并按漫画阅读顺序排序：
   - 竖排文字按**从右到左**（以页面右上角为原点计算行距）；
   - 横排文字按**从上到下**；
   - 文本块之间按镜像 3×4 网格排序，使最右侧的块最先读取。
3. **文字识别**：使用 [`manga-ocr`](https://github.com/kha-white/manga-ocr)（日文漫画专用 OCR 模型），对每个文本行裁剪后识别。
4. **结果输出**：文本（`*.txt`）、结构化 JSON（`*.json`，含每个文本块/文本行的多边形坐标与文字）、调试图（`*.vis.png`，标注序号与方向）。
5. **悬浮窗截图提取**：置顶小窗，框选屏幕区域（如浏览器里的漫画图）即可自动识别，结果像对话一样逐条堆叠。
6. **翻译**：每张结果手动点「翻译」即可，译文按「原文一句 / 译文一句」成对显示；翻译通道可切换**普通翻译**（谷歌 / 有道 / MyMemory）或 **AI 翻译**（任意 OpenAI 兼容接口）。
7. **译文叠图**（可选，默认关闭）：手动点「叠图」，把译文按原文位置替换排版回原图，生成预览图并可导出。

## 安装

需要 Python 3.10+（已在 3.13 上验证）。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 下载检测模型

```powershell
.\.venv\Scripts\python.exe scripts\download_models.py
```

模型（约 95 MB）会下载到 `models/comic-text-detector.onnx`。

> 无法访问 huggingface.co 时，脚本默认通过 `https://hf-mirror.com` 镜像下载；也可以用 `--endpoint` 指定其它镜像，或设置环境变量 `HF_ENDPOINT`。
> OCR 模型 `kha-white/manga-ocr-base` 由 `manga-ocr` 在首次运行时自动下载，同样受 `HF_ENDPOINT` 控制。

## 使用

### 命令行

```powershell
# 识别示例图片 samples\1.png、samples\2.png，输出同目录下的 .txt
.\.venv\Scripts\python.exe -m manga_extract samples\1.png samples\2.png

# 输出 JSON + 调试图到指定目录
.\.venv\Scripts\python.exe -m manga_extract samples\1.png samples\2.png --json --vis -o out

# 只做检测与阅读顺序，不做 OCR（快速验证检测效果）
.\.venv\Scripts\python.exe -m manga_extract samples\1.png --no-ocr

# 批量处理整个目录（如示例图目录 samples\）
.\.venv\Scripts\python.exe -m manga_extract samples\ --json --vis -o out
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `-o, --output-dir` | 输出目录（默认与输入同目录） |
| `--json` | 输出结构化 JSON（含坐标） |
| `--vis` | 输出标注图 `*.vis.png` |
| `--no-ocr` | 跳过 OCR，仅检测与排序 |
| `--model` | 检测模型路径 |
| `--ocr-model` | 识别模型名称/路径 |
| `--hf-endpoint` | 下载用的 Hugging Face 镜像 |
| `--channel-order` | 传入检测模型的通道顺序，`bgr`（默认）/ `rgb` |
| `--quiet` | 不打印识别结果 |

### 悬浮窗工具（截图提取）

一个置顶的小悬浮窗，像截图工具一样框选屏幕上的图片，直接出文字。

```powershell
# 方式一：双击 run_gui.bat（无控制台窗口）

# 方式二：命令行启动
.\.venv\Scripts\python.exe run_gui.py
# 或
.\.venv\Scripts\python.exe -m manga_extract.gui
```

启动后窗口会置顶悬浮，左上角状态栏显示「正在加载模型…」，首次加载约 30–60 秒，之后按钮可用。

| 按钮 | 作用 |
| --- | --- |
| **截取** | 隐藏悬浮窗 → 冻结屏幕显示半透明遮罩 → 按住鼠标拖动框选 → 松开自动识别 |
| **剪贴板** | 直接读取剪贴板图片（配合 `Win+Shift+S` 截图很方便） |
| **打开图片** | 选择本地图片文件识别 |
| **复制全部** | 把日志里所有文字复制到剪贴板 |
| **保存…** | 把所有文字保存为 `.txt` |
| **清空** | 清空日志 |

每识别一张，就在下方追加一张卡片（缩略图 + 文字 + 复制/详情），像对话框一样往上堆叠。
点「详情」可查看标注了文本块序号、竖/横排方向的大图与逐行文字。

框选小技巧：

* 在遮罩上拖动时实时显示选区尺寸；**Esc 或鼠标右键取消**。
* 支持多显示器（覆盖整个虚拟桌面）与高 DPI 缩放，截取范围与屏幕像素一一对应。
* 「置顶」勾选框可切换是否始终置顶，方便一边看网页一边提取。

#### 翻译

每张结果卡片上都有「翻译」按钮（**手动触发，窗口状态不变**），也可以点工具栏的「翻译全部」一次翻译所有结果。

翻译方式用工具栏的开关切换：

* **普通**（网页通道，免 API Key）：**谷歌 / 有道 / MyMemory**，在「翻译设置」里选择。
* **AI**（任意 OpenAI 兼容接口）：在「翻译设置」里填接口地址、API Key、模型名称（可选随机性）。可填 DeepSeek、OpenAI、通义千问、智谱，或本地 Ollama（如 `http://127.0.0.1:11434/v1`）等。

「翻译设置」里的「测试」按钮可以先用一句日文验证通道是否通。

译文按「**原文一句 / 译文一句**」成对显示：

```
ねえねえ！グムビュッフェのお料理作ってみたい！
呐呐！我想做 gumbuffet 的料理！

りょうか〜い
明白了~
```

翻译后卡片上会出现「显示原文 / 显示译文」按钮来回切换；「复制」「复制全部」「保存」导出的都是**当前显示的内容**（含译文）。
目标语言支持 简体中文 / 繁體中文 / English。

##### 译文叠图（可选，默认关闭）

翻译完成后，卡片上（「翻译」旁边）会多出「**叠图**」按钮。点它会把译文按原文位置**替换排版**回原图，生成一张可直接查看的预览图：

* 竖向文本框按**竖排、从右到左**排版；横向文本框自动换行、居中。
* 每个文本块先用该处底色覆盖原文，再写译文（底色偏深时自动改用浅色字）。
* 预览窗口内可「保存图片」导出 PNG/JPG。

该功能是**手动触发、默认不启用**，不影响识别与普通译文显示。字体默认用系统「微软雅黑」（`msyh.ttc`），也可以用环境变量 `MANGA_EXTRACT_FONT` 指定其它中文 `.ttf/.ttc` 字体。

设置保存在项目根目录的 `translate_config.json`（可能含 API Key，已 gitignore，请勿提交）。

参数：`--model` 指定检测模型，`--hf-endpoint` 指定 OCR 模型下载镜像，`--no-topmost` 启动时不置顶。

### Python API

```python
from manga_extract import ComicTextDetector
from manga_extract.pipeline import MangaExtractor
from manga_extract.recognizer import MangaOcrRecognizer

extractor = MangaExtractor(
    detector=ComicTextDetector("models/comic-text-detector.onnx"),
    recognizer=MangaOcrRecognizer(),          # 传入 None 则不做 OCR
)
result = extractor.extract_file("samples/1.png")

print(result.text)                            # 整页文字（按阅读顺序）
for block in result.blocks:                   # 每个文本块
    print(block.xyxy, block.vertical, block.text)
    for line in block.lines:                  # 每个文本行（含多边形坐标）
        print(line.polygon, line.text)
```

## 输出格式

`<name>.json` 示例：

```json
{
  "source": "1.png",
  "width": 713,
  "height": 861,
  "block_count": 7,
  "text": "２割回復かぁ\nこれから長旅になると思いますが\n...",
  "blocks": [
    {
      "bbox": [608, 54, 636, 164],
      "language": "ja",
      "vertical": true,
      "text": "２割回復かぁ",
      "lines": [
        { "polygon": [[608,54],[636,54],[636,164],[608,164]], "text": "２割回復かぁ" }
      ]
    }
  ]
}
```

* `blocks` 已按阅读顺序排列；`lines` 在块内也按阅读顺序（竖排从右到左）排列。
* `polygon` / `bbox` 为原图像素坐标，可直接用于后续翻译结果的排版叠加。

## 项目结构

```
manga_extract/
├─ manga_extract/
│  ├─ detector.py       # comic-text-detector ONNX 推理与后处理
│  ├─ reading_order.py  # 文本行分组与漫画阅读顺序
│  ├─ recognizer.py     # manga-ocr 封装
│  ├─ translation.py    # 翻译通道（谷歌/有道/MyMemory + OpenAI 兼容 AI）
│  ├─ typeset.py        # 译文叠图：把译文按原文位置排版回图片
│  ├─ pipeline.py       # 检测 → 分组排序 → 识别
│  ├─ visualize.py      # 调试图绘制
│  ├─ image_io.py       # 支持中文/日文路径的读写
│  ├─ cli.py            # 命令行入口
│  └─ gui/              # 悬浮窗截图工具
│     ├─ app.py         #   主窗口、识别 / 翻译 / 叠图流程
│     └─ capture.py     #   全屏截图与框选遮罩
├─ run_gui.py           # 悬浮窗启动脚本
├─ run_gui.bat          # 双击启动（无控制台窗口）
├─ scripts/download_models.py
├─ models/              # 模型权重（已 gitignore）
└─ requirements.txt
```

## 阅读顺序说明

日文漫画为竖排、从右到左。本项目的做法：

1. 每个文本行是一个四边形；`_examine_block` 通过累加文本行的两个方向向量判断该块是竖排还是横排。
2. 竖排时以页面**右上角**为原点计算每行到原点的垂距，按距离升序即“从右到左”；横排时以左上角为原点，即“从上到下”。
3. 文本块之间先用镜像网格（右侧优先）排序，再在同一网格行内从右到左。
4. 块内间距过大的文本行会被拆分，散落的文本行会按字号/方向合并。

## 限制与说明

* 这是 CPU 推理方案：单页检测约 1 秒，OCR 每行约 0.5–1.5 秒（首次需加载模型）。
* 刻意画在画面中的拟声词、手写效果字等，若不构成常规文本行，检测器可能不会识别——这是该检测模型的固有行为。
* 检测模型来自 [`dmMaze/comic-text-detector`](https://github.com/dmMaze/comic-text-detector)（GPL-3.0）的 ONNX 导出；`manga-ocr` 使用 Apache-2.0 许可的 `kha-white/manga-ocr-base`。发布/分发前请留意相应许可证。
* 后续翻译阶段可直接消费 `*.json` 中的 `text` 与 `polygon`。
