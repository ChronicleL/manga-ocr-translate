"""Help texts and presets of the settings dialog (carried over from the first GUI)."""

from __future__ import annotations

# Presets of the AI model drop-down; the box itself stays editable.
AI_MODEL_PRESETS = [
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "qwen-plus",
    "glm-4-flash",
    "moonshot-v1-8k",
    "llama3.1",
]

# Used by both "测试" buttons. It deliberately carries some content (rather than a bare
# greeting) so a style prompt has something to act on.
TEST_SENTENCE = "おい、この魔剣は重すぎるだろ…！とても振れる気がしないんだが。"

THINKING_HELP = (
    "很多推理模型（如 Qwen3 系列）默认会先「思考」再输出，翻译这种\n"
    "简单任务会白白多花几倍时间。\n\n"
    "勾选后请求里会带上 enable_thinking=false。同一个模型、同一句话\n"
    "实测：不勾 6.9 秒（思考 364 字），勾上 1.5 秒。\n\n"
    "注意：这是 Qwen / 通义等端点的参数。OpenAI 等不接受未知字段的\n"
    "端点可能报 400，遇到就取消勾选。"
)

TEMPERATURE_HELP = (
    "「随机性」即接口参数 temperature，决定模型输出的发散程度。\n\n"
    "・0：每次输出几乎完全一样，最死板、最听话\n"
    "・0.2 ~ 0.4：推荐翻译使用，措辞稳定、不跑偏（默认 0.3）\n"
    "・0.7 ~ 1.0：更灵活多变，同一句每次翻法都不同，可能自由发挥\n"
    "・大于 1：容易语无伦次；超出 0 ~ 2 会被服务端拒绝\n\n"
    "想做风格化改写可以调到 0.6 ~ 0.8。\n"
    "留空或填错不会报错，会自动回退到 0.3。"
)

DEVICE_HELP = (
    "推理后端。检测走 ONNX Runtime，OCR 走 PyTorch。\n\n"
    "・自动：只要装了带 GPU 支持的 onnxruntime 且显卡可用，就走 GPU，\n"
    "  否则退回 CPU。默认值。\n"
    "・仅 CPU：强制纯 CPU，兼容性最好。\n"
    "・CUDA / DirectML / OpenVINO：需要对应版本的 onnxruntime\n"
    "  （onnxruntime-gpu / onnxruntime-directml / onnxruntime-openvino），\n"
    "  没装的话下拉里不会出现。\n\n"
    "注意：本模型检测输入固定 1024×1024，纯 CPU 单页检测通常要十几秒，\n"
    "换成 GPU 是最有效的提速手段。"
)

PERF_HELP = (
    "纯 CPU 推理本身较慢（检测固定 1024×1024，最耗时）。\n"
    "・执行后端：装了 GPU 版 onnxruntime 且显卡可用时，选「自动」即可走 GPU。\n"
    "・CPU 线程：0 为自动；检测与 OCR 共用该线程数（填物理核心数通常最稳）。\n"
    "・OCR 批处理：把整页文本行凑成一批做一次前向，纯 CPU 上 4~8 一般最快。\n"
    "改动在点「保存」后会自动重新加载模型。"
)

DEFAULT_TEMPERATURE = 0.3


def parse_temperature(text: str) -> float:
    """A temperature typed by the user; anything unparsable means the default."""
    try:
        return float(str(text).strip())
    except ValueError:
        return DEFAULT_TEMPERATURE


def parse_threads(text: str) -> int:
    """A CPU thread count; anything unparsable means 'auto' (0)."""
    try:
        return max(0, int(str(text).strip()))
    except ValueError:
        return 0
