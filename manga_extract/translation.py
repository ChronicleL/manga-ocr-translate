"""Japanese -> Chinese translation backends.

Two kinds of backend are supported, selected by :class:`TranslationConfig.mode`:

* ``"web"`` -- free online translators (Google, 有道, MyMemory), no API key.
* ``"ai"``  -- any OpenAI-compatible chat-completions endpoint.

All backends return one translated string per input string, preserving order,
so the caller can render "原文一句 / 译文一句".
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# UI label -> language code used by the web engines.
TARGET_LANGUAGES: Dict[str, str] = {
    "简体中文": "zh-CN",
    "繁體中文": "zh-TW",
    "English": "en",
}

WEB_ENGINES: Dict[str, str] = {
    "谷歌": "google",
    "有道": "youdao",
    "MyMemory": "mymemory",
}


class TranslationError(RuntimeError):
    """Raised when a translation backend fails."""


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _http_get_json(url: str, timeout: float = 20.0) -> object:
    return json.loads(_http_get(url, timeout))


def _http_get(url: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _read(request, timeout)


def _http_post_json(url: str, payload: dict, headers: Optional[dict], timeout: float) -> object:
    body = json.dumps(payload).encode("utf-8")
    merged = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    merged.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=merged, method="POST")
    return json.loads(_read(request, timeout))


def _read(request: urllib.request.Request, timeout: float) -> str:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise TranslationError(f"HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise TranslationError(f"网络错误：{exc.reason}") from exc
    except TimeoutError as exc:
        raise TranslationError("请求超时") from exc


def _chunked(items: Sequence[str], max_items: int, max_chars: int) -> List[List[str]]:
    chunks: List[List[str]] = []
    current: List[str] = []
    size = 0
    for item in items:
        if current and (len(current) >= max_items or size + len(item) > max_chars):
            chunks.append(current)
            current, size = [], 0
        current.append(item)
        size += len(item)
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class BaseTranslator:
    """Translate a list of strings, preserving order."""

    name = "base"

    def __init__(self, target_lang: str = "zh-CN", timeout: float = 25.0) -> None:
        self.target_lang = target_lang
        self.timeout = timeout

    def translate(self, texts: Sequence[str]) -> List[str]:
        return [self._translate_one(text) for text in texts]

    def _translate_one(self, text: str) -> str:
        raise NotImplementedError


class GoogleTranslator(BaseTranslator):
    """Free Google endpoint (``dict-chrome-ex`` client).

    Multiple ``q`` parameters are answered with one translation per item, which
    keeps the output aligned with the input.
    """

    name = "google"
    URL = "https://clients5.google.com/translate_a/t"

    def translate(self, texts: Sequence[str]) -> List[str]:
        results: List[str] = []
        for chunk in _chunked(list(texts), max_items=6, max_chars=700):
            params: List[Tuple[str, str]] = [
                ("client", "dict-chrome-ex"),
                ("sl", "ja"),
                ("tl", self.target_lang),
            ]
            params += [("q", text) for text in chunk]
            data = _http_get_json(f"{self.URL}?{urllib.parse.urlencode(params)}", self.timeout)
            values = self._as_list(data)
            if len(values) != len(chunk):
                # Fall back to one request per item if the batch misbehaves.
                values = [self._translate_one(text) for text in chunk]
            results.extend(values)
        return results

    def _translate_one(self, text: str) -> str:
        params = urllib.parse.urlencode(
            {"client": "dict-chrome-ex", "sl": "ja", "tl": self.target_lang, "q": text}
        )
        return self._as_list(_http_get_json(f"{self.URL}?{params}", self.timeout))[0]

    @staticmethod
    def _as_list(data: object) -> List[str]:
        if isinstance(data, str):
            return [data]
        if isinstance(data, list):
            # Sometimes returns [["t1"], ["t2"]]; flatten one level of lists.
            flat: List[str] = []
            for item in data:
                if isinstance(item, list):
                    flat.append("".join(str(part) for part in item))
                else:
                    flat.append(str(item))
            return flat
        raise TranslationError(f"谷歌返回了意外的数据：{str(data)[:200]}")


class YoudaoTranslator(BaseTranslator):
    """Free 有道 demo endpoint; newline-joined input comes back line-aligned."""

    name = "youdao"
    URL = "https://aidemo.youdao.com/trans"
    _TO = {"zh-CN": "zh-CHS", "zh-TW": "zh-CHT", "en": "en"}

    def translate(self, texts: Sequence[str]) -> List[str]:
        results: List[str] = []
        for chunk in _chunked(list(texts), max_items=10, max_chars=900):
            params = urllib.parse.urlencode(
                {"q": "\n".join(chunk), "from": "ja", "to": self._TO.get(self.target_lang, "zh-CHS")}
            )
            data = _http_get_json(f"{self.URL}?{params}", self.timeout)
            if not isinstance(data, dict) or str(data.get("errorCode")) != "0":
                raise TranslationError(f"有道返回错误：{str(data)[:200]}")
            parts = "\n".join(data.get("translation") or []).split("\n")
            if len(parts) != len(chunk):
                parts = [self._translate_one(text) for text in chunk]
            results.extend(parts)
        return results

    def _translate_one(self, text: str) -> str:
        params = urllib.parse.urlencode(
            {"q": text, "from": "ja", "to": self._TO.get(self.target_lang, "zh-CHS")}
        )
        data = _http_get_json(f"{self.URL}?{params}", self.timeout)
        if not isinstance(data, dict) or str(data.get("errorCode")) != "0":
            raise TranslationError(f"有道返回错误：{str(data)[:200]}")
        return "\n".join(data.get("translation") or [])


class MyMemoryTranslator(BaseTranslator):
    """MyMemory free API (one request per line, ~500 char limit)."""

    name = "mymemory"
    URL = "https://api.mymemory.translated.net/get"

    def _translate_one(self, text: str) -> str:
        params = urllib.parse.urlencode({"q": text[:500], "langpair": f"ja|{self.target_lang}"})
        data = _http_get_json(f"{self.URL}?{params}", self.timeout)
        if not isinstance(data, dict):
            raise TranslationError("MyMemory 返回了意外的数据")
        return str(data.get("responseData", {}).get("translatedText", ""))


@dataclass
class AIConfig:
    """One OpenAI-compatible endpoint preset ("一套 API").

    ``name`` is only a label shown in the UI; ``prompt`` carries the style
    requirements plugged into :meth:`AITranslator.system_prompt`.

    ``disable_thinking`` adds ``enable_thinking: false`` to the request body.
    Reasoning models such as the Qwen3 family otherwise spend a long time on a
    chain of thought before answering, which multiplies translation latency.
    """

    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.3
    timeout: float = 90.0
    name: str = "默认"
    prompt: str = ""
    disable_thinking: bool = False


class AITranslator(BaseTranslator):
    """Any OpenAI-compatible ``/chat/completions`` endpoint."""

    name = "ai"

    # Assembled from three parts so a user-supplied style requirement lands
    # *before* the output-format rules instead of trailing them: instructions
    # placed last tend to be outweighed by an earlier default tone.
    PROMPT_HEAD = "你是资深的日文漫画翻译。\n\n【风格要求（优先于默认语气，必须严格遵守）】\n"
    DEFAULT_STYLE = "译文要自然、口语化。"
    PROMPT_TAIL = (
        "\n\n【输出格式】\n"
        "把用户给出的每一条日文按上面的风格翻译成简体中文，保持条目数量与顺序完全不变，"
        "不要合并或拆分条目，不要加解释。只输出一个 JSON 字符串数组，每个元素对应一条译文。"
    )

    def __init__(self, config: AIConfig, target_lang: str = "zh-CN") -> None:
        super().__init__(target_lang=target_lang, timeout=config.timeout)
        self.config = config

    def system_prompt(self) -> str:
        """Built-in role/format rules with the user's style requirement inside."""
        style = (self.config.prompt or "").strip() or self.DEFAULT_STYLE
        return f"{self.PROMPT_HEAD}{style}{self.PROMPT_TAIL}"

    def translate(self, texts: Sequence[str]) -> List[str]:
        items = list(texts)
        if not items:
            return []
        result = self._request(items)
        if len(result) == len(items):
            return result
        # Count mismatch would misalign the pairs; translate one by one instead.
        # The requests are independent, so run them concurrently rather than
        # paying the full round trip once per line.
        workers = min(4, len(items))
        if workers <= 1:
            return [self._single(text) for text in items]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self._single, items))

    def _chat(self, items: List[str]) -> str:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {"role": "system", "content": self.system_prompt()},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
        }
        if self.config.disable_thinking:
            payload["enable_thinking"] = False
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        data = _http_post_json(url, payload, headers, self.timeout)
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError(f"AI 返回了意外的数据：{str(data)[:300]}") from exc

    def _request(self, items: List[str]) -> List[str]:
        return _parse_json_array(self._chat(items))

    def _single(self, text: str) -> str:
        content = self._chat([text])
        parsed = _parse_json_array(content)
        return parsed[0] if parsed else content.strip()


def _parse_json_array(content: str) -> List[str]:
    text = (content or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed]
        except json.JSONDecodeError:
            pass
    lines = [re.sub(r"^\s*\d+\s*[.、)]\s*", "", ln).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def _ai_config(data: object) -> AIConfig:
    """Build an :class:`AIConfig` from untrusted JSON, ignoring unknown keys."""
    if not isinstance(data, dict):
        data = {}
    allowed = set(AIConfig.__dataclass_fields__)
    return AIConfig(**{key: value for key, value in data.items() if key in allowed})


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class TranslationConfig:
    """User-selectable translation settings, persisted as JSON."""

    mode: str = "web"                 # "web" | "ai"
    web_engine: str = "google"        # google | youdao | mymemory
    target_lang: str = "zh-CN"
    ai: AIConfig = field(default_factory=AIConfig)
    ai_profiles: List[AIConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ensure_profiles()

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "web_engine": self.web_engine,
            "target_lang": self.target_lang,
            "ai": asdict(self.ai),
            "ai_profiles": [asdict(profile) for profile in self.ai_profiles],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TranslationConfig":
        data = data or {}
        profiles = [_ai_config(data_item) for data_item in data.get("ai_profiles") or []]
        config = cls(
            mode=data.get("mode", "web"),
            web_engine=data.get("web_engine", "google"),
            target_lang=data.get("target_lang", "zh-CN"),
            ai=_ai_config(data.get("ai")),
            ai_profiles=profiles,
        )
        config.ensure_profiles()
        return config

    def ensure_profiles(self) -> None:
        """Make sure the active AI config is one of the stored profiles."""
        if not self.ai_profiles:
            self.ai_profiles = [self.ai]
            return
        if any(profile is self.ai for profile in self.ai_profiles):
            return
        for profile in self.ai_profiles:
            if profile.name == self.ai.name:
                self.ai = profile
                return
        self.ai_profiles.append(self.ai)

    def unique_profile_name(self, base: str = "新配置") -> str:
        """Return an unused profile name, derived from ``base``."""
        used = {profile.name for profile in self.ai_profiles}
        if base not in used:
            return base
        index = 2
        while f"{base} {index}" in used:
            index += 1
        return f"{base} {index}"

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "TranslationConfig":
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls()

    def build_translator(self) -> BaseTranslator:
        """Create the translator selected by this configuration."""
        if self.mode == "ai":
            if not self.ai.api_key:
                raise TranslationError("AI 翻译需要先在「翻译设置」里填写 API Key")
            if not self.ai.model:
                raise TranslationError("AI 翻译需要先填写模型名称")
            return AITranslator(self.ai, target_lang=self.target_lang)
        engines = {
            "google": GoogleTranslator,
            "youdao": YoudaoTranslator,
            "mymemory": MyMemoryTranslator,
        }
        translator = engines.get(self.web_engine, GoogleTranslator)
        return translator(target_lang=self.target_lang)
