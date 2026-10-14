# manga_extract

日文漫画文字提取（OCR）工具。按照日文漫画的阅读习惯——**纵向排版、从右到左、从上到下**——检测并识别页面中的文字，输出带顺序和坐标的结果，为后续翻译流程提供输入。

当前已完成：**文字检测 + 阅读顺序 + 文字识别**、**翻译与译文叠图**，以及面向整话汉化的
**[Manga Studio 汉化工作台](#manga-studio汉化工作台)**（推荐）；另有命令行和旧版悬浮窗截图工具。

## Manga Studio（汉化工作台）

个人汉化用的桌面工作台：导入整话页面，识别、翻译、逐块校对，在原图和叠图之间随时切换，成果存在项目文件夹里，最后整话导出。

```powershell
.\run_studio.bat                       # 双击也行（无控制台窗口）
.\.venv\Scripts\python.exe run_studio.py
.\.venv\Scripts\python.exe -m manga_extract.studio
```

安装见下文「安装」（需要多装一步 PySide6 相关依赖）。

### 界面

<img width="1342" height="890" alt="image" src="https://github.com/user-attachments/assets/50a20fb7-1313-40e7-a47d-936395ecacd0" />


* **页面列表**：每页一个状态点（未识别 / 排队中 / 识别中 / 已识别 / 已翻译 / 已校对），可拖动排序、右键重新识别或删除。
* **画布**：Ctrl+滚轮缩放，拖动平移，双击适应窗口，点文本块框选中它。底部的悬浮栏有「翻译」菜单、**叠图 ⇄ 原图**切换（也可按空格）、文本框开关和缩放。
* **校对栏**：每个文本块一张卡片，按阅读顺序排列，可直接改原文和译文、勾选「已校对」、单块重新翻译。点画布上的框或点卡片会**互相定位**。改动约 1 秒后自动保存，叠图随译文改动自动刷新。右上角的「**一键校对**」把本页所有有文字的块一次勾完，适合对着叠图通读一遍后收尾；全部勾选后再点一次可整页取消。

### 工作流

1. **新建项目**：选一个文件夹，一话一个项目。
2. **导入页面**（导入后默认自动识别，可在「识别」菜单关闭）
   * 导入 → 图片文件 / 文件夹 / 剪贴板图片（`Ctrl+V`），也可以把文件拖进窗口。
   * **截图**：隐藏窗口，冻结屏幕，框选区域（比如浏览器里的漫画页）；每块屏幕一个遮罩，多显示器和不同缩放比例都能对上像素。
   * **网页**：粘贴章节网址，列出页面里的图片（缩略图、尺寸、大小），按宽度自动勾选、可全选或全不选，确认后按网页顺序导入。下载时带 `Referer`（默认是网页地址，防盗链失败时可改后点「重新加载图片」）。支持懒加载属性（`data-src` 等）、`srcset`、`<picture>`、`<noscript>`、`<base href>`；**不支持**用 JavaScript 动态生成图片列表或需要登录的站点（改用截图或先另存）。
3. **翻译**：浮动栏「翻译」菜单 → 本页 / 全部页面 / 只翻译还没有译文的页面，并在同一个菜单里选**翻译通道**（谷歌、有道、MyMemory，或已配置的 AI 接口）。AI 接口在「设置」里配置。
4. **校对**：逐块修改。**机翻永远不会覆盖你的劳动成果**：已勾选「已校对」或你手写过的译文会被跳过；只有你在某一块上点它自己的刷新按钮才会强制重译（已校对的块除外）。整页都没问题时，用校对栏的「一键校对」（`Ctrl+Shift+Return`）一次勾完，不必逐个点。
5. **导出**：「导出」菜单 → 整话图片（PNG / JPG，按页面顺序编号 `001.png`…；没有译文的页面可选择带上原图保持页码完整）、当前页、文本（原文 + 译文）、JSON（含坐标与校对状态）。

### 快捷键

| 键 | 作用 | 键 | 作用 |
| --- | --- | --- | --- |
| `Ctrl+N` / `Ctrl+O` | 新建 / 打开项目 | `Ctrl+T` / `Ctrl+Shift+T` | 翻译本页 / 全部页面 |
| `Ctrl+I` | 导入图片文件 | `Ctrl+E` | 导出整话图片 |
| `Ctrl+V` | 粘贴剪贴板图片（页面列表、画布上） | `Ctrl+,` | 设置 |
| `Ctrl+Shift+S` | 截图 | `F5` | 识别当前页 |
| `Alt+←` / `Alt+→` | 上一页 / 下一页 | `Alt+↑` / `Alt+↓` | 上 / 下一个文本块（光标进入译文框） |
| `Ctrl+Enter` | 标记当前块已校对并跳到下一块（再按取消） | `Ctrl+B` | 显示 / 隐藏文本块框 |
| `空格` | 原图 ⇄ 叠图（焦点在画布时） | `Ctrl+Shift+Return` | 一键校对整页（再按取消） |

窗口级快捷键避开了文本框自己用的编辑键，在校对框里打字不受影响。

### 项目文件夹

```
第12话/
├─ project.json        # 名称、目标语言、页面顺序
├─ pages/001.png …     # 导入时复制进来的原图
└─ data/001.json …     # 每页：文本块的坐标、原文、译文、已校对 / 手改标记
```

页面按编号命名、不随外部文件移动而丢失；数据文件原子写入，中途崩溃不会留下写了一半的 JSON。

### 设置

「设置」窗口分三块：普通翻译（引擎、目标语言、**测试机翻**）、AI 翻译（多套 OpenAI 兼容接口预设，名称 / 地址 / API Key / 模型 / 随机性 / 关闭思考模式 / 风格提示词，**测试 AI**）、性能（执行后端、CPU 线程、OCR 批处理；改动后自动重新加载模型）。设置写在程序目录下的 `translate_config.json`（可能含 API Key）和 `engine_config.json`，与旧版悬浮窗共用；窗口状态、主题、最近项目写在 `studio_config.json`（三者都已 gitignore）。

### 检查安装

```powershell
.\.venv\Scripts\python.exe run_studio.py --selftest result.txt
```

启动后等识别模型加载完成，把结论写进 `result.txt`（`OK 检测 Dml · OCR cpu` 或 `FAIL 原因`）并以 0 / 1 退出，不需要点任何东西，也可配合 `set QT_QPA_PLATFORM=offscreen` 无界面运行。打包脚本用它做发布前的冒烟检查。

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

# Manga Studio 额外依赖（分两步，原因见 requirements-studio.txt 开头的说明）
.\.venv\Scripts\python.exe -m pip install -r requirements-studio.txt
.\.venv\Scripts\python.exe -m pip install --no-deps PySide6-Fluent-Widgets PySideSix-Frameless-Window
```

* 分两步是因为 `PySide6-Fluent-Widgets` 声明依赖完整版 `PySide6`，会连带下载几百 MB 的 QtWebEngine；Studio 只需要 `PySide6-Essentials`，所以那个包用 `--no-deps` 安装。
* 旧版悬浮窗依赖 [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)，已包含在 `requirements.txt` 中；命令行模式和 Studio 都不需要它。
* **Anaconda 的 Python**：conda 自带的 `icuuc.dll` 与 Qt 6.12 需要的符号不兼容，直接导入 PySide6 会报 `DLL load failed … 找不到指定的程序`。Studio 启动时会先预加载系统自带的那份来绕开（见 `manga_extract/studio/bootstrap.py`），不需要手动处理；用 python.org 的 Python 则没有这个问题。
* 许可证：`PySide6-Fluent-Widgets` 是 GPLv3，`PySideSix-Frameless-Window` 是 LGPLv3，对外分发前请留意。

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
| `--device` | 推理后端：`auto`（默认，有 GPU 就走 GPU）/ `cpu` / `cuda` / `dml` / `openvino` |
| `--threads` | CPU 线程数，`0` 为自动（检测与 OCR 共用） |
| `--ocr-batch` | 每次前向识别多少行，`1`（默认）～ `16` |

### 性能 / 加速

纯 CPU 推理本身较慢：检测模型的输入**固定为 1024×1024**（不能靠降分辨率提速），单页检测在普通 CPU 上约 18 秒；OCR 每行约 0.1–1.5 秒。**检测占了整页耗时的 90% 以上**，所以加速的重点全在检测。实测（RTX 4070，单页 709×1022、20 行）：

| 阶段 | 纯 CPU | DirectML |
| --- | --- | --- |
| 检测 + 分组 | 18.4 s（负载高时可达 90 s） | **0.12 s** |
| OCR 20 行（batch=8） | ~2 s | ~1.9 s |
| 整页合计 | 约 30–100 s | **约 9.8 s**（含 7.6 s 的一次性模型加载） |

* **换 GPU（最有效）**。⚠️ **`auto` 不会自己装 GPU 后端**：只要没装带 GPU 支持的 onnxruntime，它就会**静默退回 CPU**，速度几乎没有变化。装一个即可（二选一，会替换掉 `onnxruntime`）：
  * `pip install onnxruntime-directml` —— Windows 通用，NVIDIA / AMD / Intel 都行，**不需要额外装 CUDA**，实测检测 18.4 s → 0.05 s。
  * `pip install onnxruntime-gpu` —— 仅 NVIDIA，需自备 CUDA 12 + cuDNN 9。
  * 装好后 `--device auto`（或 GUI「性能 → 执行后端 → 自动」）就会自动选中；也可用 `--device cuda` / `dml` 强制指定。后端不可用时会退回 CPU，不会启动失败。**GUI 状态栏会显示实际后端**（如 `检测 Dml`），若显示 `检测 CPU（未启用 GPU 加速…）` 就说明没生效。
  * OCR 侧默认是 CPU 版 PyTorch。单页 OCR 只有约 2 秒，通常**不值得**为此再装 2 GB 的 CUDA 版 torch。
* **`--ocr-batch`（纯 CPU 也有收益）**：把整页文本行凑成一批做一次前向。实测示例页 18 行：`1` → 5.8 s，`8` → 3.8 s（**约 1.5×**），再往上基本不再提升，`8` 左右是性价比最高的点。
* **`--threads`**：`0` 为自动（默认，交给各后端按物理核心数决定）。手填时建议填**物理核心数**；填得过小会明显变慢，没有把握就保持 `0`。

GUI 里对应在「翻译设置 → 性能」一栏：**执行后端 / CPU 线程 / OCR 批处理**，点「保存」后会自动重新加载模型生效，设置写入项目根目录的 `engine_config.json`（已 gitignore）。

> 注意：`pip install -r requirements.txt` 会把带 GPU 支持的 onnxruntime 换回 CPU 版；重装依赖后需再装一次上面的 GPU 包。

### 悬浮窗工具（旧版，截图提取）

> 这是最初的小工具，仍可使用。整话汉化请用上面的 **Manga Studio**，它在这个工具的基础上增加了项目、画布、校对栏、网页导入和整话导出。

一个置顶的小悬浮窗，像截图工具一样框选屏幕上的图片，直接出文字。

```powershell
# 方式一：双击 run_gui.bat（无控制台窗口）

# 方式二：命令行启动
.\.venv\Scripts\python.exe run_gui.py
# 或
.\.venv\Scripts\python.exe -m manga_extract.gui
```

启动后会先显示**加载界面**（深色浮窗 + 进度条），重模块在后台线程初始化，约 10–60 秒后自动切换到主窗口，按钮变为可用状态。
状态栏左侧显示当前阶段（检测中 / OCR 进度），右侧常驻后端信息（如 `检测 Dml · OCR cpu`）。

| 按钮 | 作用 |
| --- | --- |
| **截取** | 隐藏悬浮窗 → 冻结屏幕显示半透明遮罩 → 按住鼠标拖动框选 → 松开自动识别 |
| **剪贴板** | 直接读取剪贴板图片（配合 `Win+Shift+S` 截图很方便） |
| **打开图片** | 选择本地图片文件识别 |
| **复制全部** | 把日志里所有文字复制到剪贴板 |
| **保存…** | 把所有文字保存为 `.txt` |
| **导出叠图…** | 选一个目录，把**所有已翻译**的卡片一次性叠图导出为 PNG（`translated_001.png`、`translated_002.png`…），无需逐张点开预览保存 |
| **清空** | 清空日志 |

每识别一张，就在下方追加一张卡片（缩略图 + 文字 + 复制/翻译/叠图/显示原文/详情），像对话框一样往上堆叠。
点「详情」可查看标注了文本块序号、竖/横排方向的大图与逐行文字。

框选小技巧：

* 在遮罩上拖动时实时显示选区尺寸；**Esc 或鼠标右键取消**。
* 支持多显示器（覆盖整个虚拟桌面）与高 DPI 缩放，截取范围与屏幕像素一一对应。
* 「置顶」勾选框可切换是否始终置顶，方便一边看网页一边提取。

#### 手动校对（详情 → 编辑…）

识别错了或者译文不满意时，不用重新截图：在「详情」窗口点左下角的「**编辑…**」，会按文本块列出每一块的**原文**和**译文**，可以直接改（文本块多时可滚动）。

* 改**原文** → 修正 OCR 识别错误。被改过的文本块，其逐行文字会合并成一行（坐标取整个文本块的外框）；**没有改动过的块保持原样**。
* 改**译文** → 人工校对、润色，不受模型输出限制。
* 点「**保存修改**」后，卡片显示、复制、复制全部、保存，以及**译文叠图**都会使用修改后的内容（叠图缓存会自动失效，下次点「叠图」按新译文重排）。
* 点「**取消**」不改动任何数据；原文被清空的块不再参与配对（与自动翻译规则一致）。

改完原文后还想让模型重翻，点卡片上的「重新翻译」即可。

#### 翻译

每张结果卡片上都有「翻译」按钮（**手动触发，窗口状态不变**），也可以点工具栏的「翻译全部」一次翻译所有结果。

翻译方式用工具栏的开关切换：

* **普通**（网页通道，免 API Key）：**谷歌 / 有道 / MyMemory**，在「翻译设置」里选择。
* **AI**（任意 OpenAI 兼容接口）：可填 DeepSeek、OpenAI、通义千问、智谱，或本地 Ollama（如 `http://127.0.0.1:11434/v1`）等。

「翻译设置」窗口分成两块，各带一个**独立的测试按钮**，用一句日文直接验证该通道是否通，互不影响：

| 位置 | 按钮 | 说明 |
| --- | --- | --- |
| 普通翻译 | **测试机翻** | 用当前选中的网页引擎 + 目标语言试译 |
| AI 翻译 | **测试 AI** | 用当前这套接口配置 + 提示词试译 |

**AI 翻译**这一块支持保存**多套 API 配置**：

* 「接口配置」是一个**下拉菜单**，列出所有已保存的预设；右侧「**新增**」复制当前这套并清空 API Key，「**删除**」移除选中的一套（至少保留一套）。
* 选中某套后，下面的**名称 / 接口地址 / API Key / 模型 / 随机性 / 关闭思考模式 / 提示词**就是它的内容，改动会在切换或保存时自动写回该套配置。
* 「**随机性**」就是接口参数 `temperature`：`0` 最死板，`0.2 ~ 0.4` 适合翻译（默认 `0.3`），越高越灵活但越容易自由发挥。旁边的「**?**」按钮鼠标移上去会显示完整取值说明。
* 「**关闭思考模式**」：很多推理模型（如 Qwen3 系列）默认先「思考」一段再输出，翻译这种简单任务会白白多花几倍时间。勾选后请求会带上 `enable_thinking=false` —— 同一个模型翻译两句短句实测 **6.9 秒 → 1.3 秒**。这是 Qwen / 通义等端点的参数，OpenAI 等不接受未知字段的端点可能返回 400，遇到就取消勾选。鼠标移到该勾选框上也有说明。
* 「**模型**」改成**下拉菜单**：可选 `deepseek-chat`、`deepseek-reasoner`、`gpt-4o-mini`、`qwen-plus`、`glm-4-flash`、`moonshot-v1-8k`、`llama3.1` 等常用模型，也可以**直接手动输入**任意模型名。
* 「**提示词**」用于给 AI 追加**风格化要求**，会拼在内置翻译提示词之后一起发送，例如「用轻松吐槽的语气」「保留拟声词」「人名统一译成…」。留空则按默认风格翻译。

「翻译设置」最后的「保存」会把当前选择与全部 API 预设一起写入配置文件。

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

翻译完成后，卡片上（「翻译」旁边）会多出「**叠图**」按钮。点它会把译文按原文位置**替换排版**回原图，生成一张可直接查看的预览图。预览窗口支持**自由缩放**，图片随窗口大小自适应：

* 竖向文本框按**竖排、从右到左**排版；横向文本框自动换行、居中。
* 每个文本块先用该处底色覆盖原文，再写译文（底色偏深时自动改用浅色字）。
* 预览窗口内可「保存图片」导出 PNG/JPG。
* 想一次性导出全部：翻译完成后点工具栏的「**导出叠图…**」，选一个目录即可把所有已翻译的卡片批量导出成 PNG（按卡片顺序命名 `translated_001.png`…）；已经预览过的卡片直接复用缓存，不会重复排版。没有译文的卡片会被跳过，个别排版失败（如缺少中文字体）只影响那一张，导出结束后会汇总提示成功/失败数量。

该功能是**手动触发、默认不启用**，不影响识别与普通译文显示。字体默认用系统「微软雅黑」（`msyh.ttc`），也可以用环境变量 `MANGA_EXTRACT_FONT` 指定其它中文 `.ttf/.ttc` 字体。

设置（含多套 AI 接口预设、提示词）保存在项目根目录的 `translate_config.json`（可能含 API Key，已 gitignore，请勿提交）。旧版本只写了单套 AI 配置的文件可直接读取，会自动迁移成一套名为「默认」的预设。

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
│  ├─ runtime.py        # 性能设置（后端 / 线程 / OCR 批处理）
│  ├─ visualize.py      # 调试图绘制
│  ├─ image_io.py       # 支持中文/日文路径的读写
│  ├─ cli.py            # 命令行入口
│  ├─ gui/              # 旧版悬浮窗截图工具（customtkinter）
│  └─ studio/           # Manga Studio（PySide6 + Fluent 风格）
│     ├─ app.py           #   入口：Splash、主题、--selftest、退出
│     ├─ bootstrap.py     #   启动垫片（Anaconda 的 ICU 冲突）
│     ├─ main_window.py   #   命令栏、三栏工作区、各流程的编排
│     ├─ project.py       #   项目文件夹模型（纯 Python，可单测）
│     ├─ canvas.py        #   缩放 / 平移 / 文本块框 / 原图⇄叠图
│     ├─ floating_bar.py  #   画布上的悬浮栏（翻译菜单、叠图、缩放）
│     ├─ pages_panel.py   #   左栏：页面缩略图与状态
│     ├─ blocks_panel.py  #   右栏：校对卡片
│     ├─ workers.py       #   识别队列（单线程）与翻译线程
│     ├─ overlay.py       #   后台渲染叠图（防抖，只渲染最新版本）
│     ├─ capture_qt.py    #   截图遮罩（每块屏幕一个，按物理像素裁剪）
│     ├─ web_import.py    #   网页图片提取与下载（纯标准库）
│     ├─ web_worker.py    #   后台下载线程池
│     ├─ web_dialog.py    #   网页导入对话框
│     ├─ settings_dialog.py / settings_text.py   # 设置窗口及其帮助文案
│     ├─ export.py / export_dialog.py            # 整话导出
│     ├─ shortcuts.py     #   快捷键总表（菜单提示也引用它）
│     ├─ selftest.py      #   --selftest
│     └─ config.py / ui_common.py / imaging.py
├─ tests/               # 单元测试与集成测试（不加载模型）
├─ run_studio.py / run_studio.bat   # 启动 Manga Studio
├─ run_gui.py / run_gui.bat         # 启动旧版悬浮窗
├─ manga_studio.spec / manga_ocr_translate.spec   # PyInstaller 配置
├─ assets/app.ico       # 程序图标（两个版本的 exe 与窗口都用它）
├─ packaging/           # 打包脚本与随包说明
├─ scripts/download_models.py
├─ models/              # 模型权重（已 gitignore）
└─ requirements.txt / requirements-studio.txt / requirements-build.txt
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

不加载任何模型：识别用假引擎，翻译用假翻译器，网页导入对着本地起的假网站（强制 Referer、重定向、超时、把 HTML 当图片返回等）。界面测试用 Qt 的离屏平台，不需要显示器。部分用例需要 `samples/` 里的示例图和一个中文字体，缺少时会自动跳过。

## 打包（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1            # Manga Studio
powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1 -App legacy # 旧版悬浮窗
```

脚本会安装 PyInstaller（`requirements-build.txt`）、打包、对 Studio 用 `--selftest` 做冒烟检查（失败则不生成发布包），最后压缩成 `dist\MangaStudio-win64.zip`。

## 阅读顺序说明

日文漫画为竖排、从右到左。本项目的做法：

1. 每个文本行是一个四边形；`_examine_block` 通过累加文本行的两个方向向量判断该块是竖排还是横排。
2. 竖排时以页面**右上角**为原点计算每行到原点的垂距，按距离升序即“从右到左”；横排时以左上角为原点，即“从上到下”。
3. 文本块之间先用镜像网格（右侧优先）排序，再在同一网格行内从右到左。
4. 块内间距过大的文本行会被拆分，散落的文本行会按字号/方向合并。

## 限制与说明

* 默认是 CPU 推理：单页检测约十几秒（模型输入固定 1024×1024），OCR 每行约 0.2–1.5 秒（首次需加载模型）。想更快见上文「性能 / 加速」。
* 刻意画在画面中的拟声词、手写效果字等，若不构成常规文本行，检测器可能不会识别——这是该检测模型的固有行为。
* 检测模型来自 [`dmMaze/comic-text-detector`](https://github.com/dmMaze/comic-text-detector)（GPL-3.0）的 ONNX 导出；`manga-ocr` 使用 Apache-2.0 许可的 `kha-white/manga-ocr-base`。发布/分发前请留意相应许可证。
* 后续翻译阶段可直接消费 `*.json` 中的 `text` 与 `polygon`。
