# Manga Studio (漫画汉化工作台)

<div align="center">

![Python Version](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/GUI-PySide6%20Fluent-41CD52?logo=qt&logoColor=white)
![OCR](https://img.shields.io/badge/OCR-manga--ocr%20%7C%20TrOCR-orange)
![Acceleration](https://img.shields.io/badge/Inference-DirectML%20%7C%20CUDA%20%7C%20CPU-blue)
![License](https://img.shields.io/badge/License-GPL--3.0-green)

**一个现代、高效、全流程的日文漫画文字提取（OCR）、AI 并发翻译与排版嵌字工作台**  
*遵循漫画阅读顺序 · 智能 OCR 清洗 · 多页跨页并发 AI 翻译 · 竖排自动避头尾排版 · 整话项目化管理*

[特性亮点](#-特性亮点) • [工作台预览](#-工作台预览) • [快速开始](#-快速开始) • [快捷键速查](#-快捷键速查) • [命令行-cli-使用](#-命令行-cli-使用) • [架构说明](#-目录结构)

</div>

---

## ✨ 特性亮点

- 🚀 **智能检测与漫画阅读顺序解析**
  - **精准定位**：基于 `comic-text-detector` ONNX 模型，纯 CPU 或 DirectML / CUDA 毫秒级检测文字块与文字掩码。
  - **日漫排版重构**：按**从右向左、从上向下**习惯自动排列阅读顺序；竖排与横排混排自动识别。
  - **画布自由交互**：支持 8 手柄缩放与拖拽文本框、右键单框重新检测与自由框选新增。

- 🔤 **多语言文字识别与标点修复**
  - **主语言**：集成日漫专用 [`manga-ocr`](https://github.com/kha-white/manga-ocr) VisionEncoderDecoder 模型。
  - **多语言路由**：基于 `TrOCR` 扩展支持**韩文**与**英文**（如 `microsoft/trocr-base-printed`），自动按语种路由分流。
  - **OCR 噪点清洗**：内置智能标点修正规则，自动将气泡首尾误识别为冒号的省略号（如 `：ごめん`）还原为 `…ごめん`，清除外切线噪点。

- 🤖 **极速双模翻译流水线**
  - **多页跨页并发**：AI 模式默认启用多线程并发，整话多页并行请求，速度大幅翻倍。
  - **结构化输出与零延迟**：全面支持 OpenAI / DeepSeek 协议的 `response_format: {"type": "json_object"}` 约束解码，杜绝思考链多余等待，去除每页间的无谓休眠延迟。
  - **全景上下文连贯**：系统提示词强制结合整页分镜气泡，彻底杜绝长句拆断导致的语意割裂。
  - **保护人工成果**：已校对（Reviewed）或用户手改过的文本块坚决不被机翻覆盖；支持 Google / 有道 / MyMemory 免费通道平滑切换。

- 🎨 **智能排版与译文叠图 (Typeset)**
  - **竖排符号矫正**：自动识别 `…`、`—`、`～` 等横向符号并 90° 旋转居中，标点避头避尾，1~2 位数字横排一格。
  - **字体与样式**：自动扫描系统已安装 CJK 字体，支持全局字号倍率缩放与单块固定字号覆盖。
  - **实时对比**：空格键随时在**原图 ⇄ 叠图**之间瞬时切换。

- 📁 **整话工程化项目管理**
  - **多元导入**：支持本地图片/文件夹、剪贴板（`Ctrl+V`）、多屏幕物理像素截图、网页静态漫画章节一键解析抓取。
  - **安全落盘**：`project.json` + `pages/` 原图 + `data/*.json` 结构化坐标，原子写入杜绝损坏。
  - **一键导出**：一键导出整话排版图片（PNG/JPG）、未汉化页面原图补齐、纯文本对照表与结构化 JSON。

---

## 🖥️ 工作台预览

<div align="center">
<img width="1000" alt="Manga Studio Interface" src="https://github.com/user-attachments/assets/50a20fb7-1313-40e7-a47d-936395ecacd0" />
</div>

* **三栏现代工作区**：左侧页面缩略图状态列表（排队/识别/已翻译/已校对）+ 中间交互式平移缩放画布 + 右侧双向联动校对卡片栏。

---

## 📦 快速开始

### 1. 环境准备与依赖安装

需要 Python 3.10+（已在 Python 3.10 ~ 3.13 验证）：

```powershell
# 1. 创建虚拟环境并激活
python -m venv .venv
.\.venv\Scripts\activate

# 2. 安装基础运行依赖
pip install -r requirements.txt

# 3. 安装 Manga Studio 界面依赖（注意保持 --no-deps 避免拉取臃肿组件）
pip install -r requirements-studio.txt
pip install --no-deps PySide6-Fluent-Widgets PySideSix-Frameless-Window
```

> **可选硬件加速（大幅提升检测速度）**：  
> 安装 `pip install onnxruntime-directml`（AMD / NVIDIA / Intel 显卡通用，免配 CUDA，单页检测 18s ➔ 0.1s）。

### 2. 下载文本检测模型

```powershell
python scripts\download_models.py
```
*检测模型（约 95 MB）将保存至 `models/comic-text-detector.onnx`。*

### 3. 启动汉化工作台

```powershell
# 方式一：双击项目根目录的批处理脚本
.\run_studio.bat

# 方式二：命令行启动
python run_studio.py
```

---

## ⌨️ 快捷键速查

| 快捷键 | 功能说明 | 快捷键 | 功能说明 |
| :--- | :--- | :--- | :--- |
| `Ctrl+N` / `Ctrl+O` | 新建 / 打开项目 | `Ctrl+T` / `Ctrl+Shift+T` | 翻译当前页 / 全部页面 |
| `Ctrl+I` | 导入图片或目录 | `Ctrl+E` | 导出整话图片 |
| `Ctrl+V` | 粘贴剪贴板图片 | `Ctrl+,` | 打开设置中心 |
| `Ctrl+Shift+S` | 多屏屏幕区域截图 | `F5` | 重新识别当前页 |
| `Alt+←` / `Alt+→` | 切换上一页 / 下一页 | `Alt+↑` / `Alt+↓` | 切换文本块（光标定位至译文框） |
| `空格` (Space) | **原图 ⇄ 叠图** 瞬时切换 | `Ctrl+Enter` | 标记当前块已校对并跳转下一块 |
| `Ctrl+B` | 开启 / 隐藏文本检测框 | `Ctrl+Shift+Return` | **一键校对整页**（再次按下取消） |

---

## 🖥️ 命令行 (CLI) 使用

支持无图形界面批处理与自动化流水线：

```powershell
# 1. 识别单张或多张漫画图片，输出 TXT
python -m manga_extract samples\1.png samples\2.png

# 2. 批量处理整个目录，输出带有坐标的 JSON 与标注调试图 (*.vis.png)
python -m manga_extract samples\ --json --vis -o out

# 3. 指定硬件推理后端与批处理并发
python -m manga_extract samples\ --device auto --ocr-batch 8 -o out

# 4. 自动化打包自检冒烟测试
python run_studio.py --selftest result.txt
```

---

## 🏗️ 目录结构

```text
manga-ocr-translate/
├── manga_extract/             # 核心处理与引擎层
│   ├── detector.py            # YOLOv5 + DBNet 文本块与文本行检测器
│   ├── reading_order.py       # 漫画阅读顺序算法与文本行分组
│   ├── recognizer.py          # manga-ocr 与 TrOCR 多语言文字识别封装
│   ├── translation.py         # AI (OpenAI/DeepSeek) 与 Web 多线程翻译引擎
│   ├── typeset.py             # 竖排排版、标点矫正、文字擦除与渲染叠图
│   ├── pipeline.py            # 端到端检测-排序-OCR 处理管道与文本清洗
│   └── studio/                # Manga Studio 现代化 GUI 界面层 (PySide6)
│       ├── main_window.py     # 三栏主窗口、状态流转与信号中枢
│       ├── canvas.py          # 交互式绘图画布、缩放平移、手柄拖曳与叠图
│       ├── blocks_panel.py    # 文本块校对卡片列表、双向联动与一键校对
│       ├── pages_panel.py     # 页面缩略图、多状态标记与排序
│       ├── workers.py         # 跨页多线程并发翻译与模型后台加载 Worker
│       └── project.py         # 工程项目数据读写、原子落盘与状态维护
├── gui/                       # 旧版悬浮窗工具 (CustomTkinter，兼容保留)
├── scripts/                   # 辅助脚本（模型下载等）
├── tests/                     # 自动化回归单元测试集
└── run_studio.py              # Manga Studio 快捷启动器入口
```

---

## 🧪 自动化测试

项目拥有完善的自动化离屏单元测试，无需显示器与真实模型权重即可验证逻辑：

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python -m unittest discover -s tests
```

---

## 📄 开源许可与声明

- 本项目界面基于 [PySide6](https://www.qt.io/) 与 [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PySide-Fluent-Widgets) (GPLv3) 构建。
- 文字检测模型源自 [comic-text-detector](https://github.com/dmMaze/comic-text-detector) (GPL-3.0)。
- 日文文字识别基于 [manga-ocr](https://github.com/kha-white/manga-ocr) (Apache-2.0)。
- 仅供个人汉化交流、漫画无障碍阅读与学术研究使用。
