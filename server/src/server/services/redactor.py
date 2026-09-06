"""PII / 敏感字段脱敏器（纯正则 + dict/list 递归）。

设计目标（迭代「安全与可靠性加固 — 先脱敏与审计」）：
- 覆盖常见 PII：手机号、身份证、邮箱、银行卡、订单号；规则可配置扩展。
- 递归处理 dict / list / tuple / 字符串，原地返回新对象，不修改输入。
- 可全局关闭（``REDACTOR_ENABLED=0``），便于调试与压测。
- 既用于「进入 LLM 的 Prompt」与「结构化日志」的事前清洗（应用层主动调用），
  也用于「API 响应」的事后清洗（FastAPI 中间件调用）。

字段与边界：
- 替换模板用 ``\\g<N>`` 形式反向引用，Python ``re.sub`` 直接支持。
- 订单号保留首字母 + 末两位，便于人工识别但不暴露中间 3 位。
- 不引入第三方 NLP 包（presetio / glean）以保持零额外依赖。
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping, Sequence


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# 脱敏全局开关：默认开启；关闭后 redact* 返回原对象（快速通路）。
REDACTOR_ENABLED = _bool(os.getenv("REDACTOR_ENABLED", "1"), True)
REDACTOR_MASK_CHAR = os.getenv("REDACTOR_MASK_CHAR", "*")[:1] or "*"


# 内置规则：(name, 正则, 替换模板)。替换模板中 ``\g<N>`` 会被 re 替换为第 N 个分组。
_BUILTIN_RULES: tuple[tuple[str, str, str], ...] = (
    # 手机号：1[3-9]开头共 11 位；保留前 3 + 后 4。
    (
        "phone_cn",
        r"(?<![\d])(1[3-9]\d)\d{4}(\d{4})(?![\d])",
        r"\g<1>****\g<2>",
    ),
    # 18 位身份证（含末位 X）；保留前 4 + 后 4。
    (
        "id_card_cn",
        r"(?<![\d])(\d{4})\d{10}([\dXx]{4})(?![\d])",
        r"\g<1>**********\g<2>",
    ),
    # 邮箱：保留用户名首字符 + 域名整体。
    (
        "email",
        r"(?<![A-Za-z0-9._-])([A-Za-z0-9])[A-Za-z0-9._-]*(@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
        r"\g<1>***\g<2>",
    ),
    # 银行卡 16~19 位连续数字；保留前 4 + 后 4。
    (
        "bank_card",
        r"(?<![\d])(\d{4})\d{8,11}(\d{4})(?![\d])",
        r"\g<1>" + ("*" * 8) + r"\g<2>",
    ),
    # 订单号：字母 + 5 位数字；保留首字母 + 末两位（如 A00001 → A***01）。
    (
        "order_no",
        r"(?<![A-Za-z0-9])([A-Za-z])\d{3}(\d{2})(?![\d])",
        r"\g<1>***\g<2>",
    ),
)


def _load_rules() -> tuple[tuple[str, re.Pattern[str], str], ...]:
    """编译内置规则；若环境变量 ``REDACTOR_RULES`` 提供自定义 JSON 列表则覆盖。

    同时刷新 ``REDACTOR_ENABLED`` / ``REDACTOR_MASK_CHAR``，便于测试动态切换。
    """
    global REDACTOR_ENABLED, REDACTOR_MASK_CHAR
    REDACTOR_ENABLED = _bool(os.getenv("REDACTOR_ENABLED", "1"), True)
    REDACTOR_MASK_CHAR = os.getenv("REDACTOR_MASK_CHAR", "*")[:1] or "*"
    custom = os.getenv("REDACTOR_RULES", "").strip()
    if custom:
        import json

        raw_list = json.loads(custom)
        compiled: list[tuple[str, re.Pattern[str], str]] = []
        for item in raw_list:
            name = str(item.get("name", "custom"))
            pattern = str(item.get("pattern", ""))
            replacement = str(item.get("replacement", ""))
            compiled.append((name, re.compile(pattern), replacement))
        return tuple(compiled)
    return tuple(
        (name, re.compile(pattern), replacement) for name, pattern, replacement in _BUILTIN_RULES
    )


_RULES = _load_rules()


def redact_text(text: str) -> str:
    """对一段纯文本按所有内置 / 自定义规则做替换；``REDACTOR_ENABLED=0`` 时返回原文。"""
    if not REDACTOR_ENABLED or not text:
        return text
    out = text
    for _name, pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return out


def _is_redactable_scalar(value: Any) -> bool:
    return isinstance(value, str)


def redact(value: Any) -> Any:
    """递归脱敏；``value`` 可以是 str / dict / list / tuple / 其他标量。

    - 字符串：按 ``redact_text`` 处理；
    - dict：递归处理每个 value，key 保持原样（key 一般是字段名，无需脱敏）；
    - list / tuple：递归处理每个元素，外层类型保持一致；
    - 其他标量：原样返回。

    不修改入参（dict 复制、list/tuple 重建）。``REDACTOR_ENABLED=0`` 时直接返回原对象。
    """
    if not REDACTOR_ENABLED:
        return value
    if _is_redactable_scalar(value):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return type(value)(redact(item) for item in value)
    return value


def redact_messages(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """专为 LangChain / OpenAI messages 提供的脱敏：
    仅递归清洗 ``content`` 字段（其他字段如 ``role`` / ``name`` 不含 PII）。
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        cleaned: dict[str, Any] = {}
        for key, val in message.items():
            if key == "content":
                cleaned[key] = redact(val) if not REDACTOR_ENABLED else _redact_message_content(val)
            else:
                cleaned[key] = val
        out.append(cleaned)
    return out


def _redact_message_content(content: Any) -> Any:
    """``content`` 可能是字符串，也可能是多模态 list（[{"type":"text","text":...}, ...]）。"""
    if isinstance(content, str):
        return redact_text(content)
    if isinstance(content, list):
        cleaned: list[Any] = []
        for part in content:
            if isinstance(part, Mapping):
                new_part = dict(part)
                if isinstance(new_part.get("text"), str):
                    new_part["text"] = redact_text(new_part["text"])
                cleaned.append(new_part)
            else:
                cleaned.append(part)
        return cleaned
    return content


def reload_rules() -> None:
    """测试钩子：环境变量变更后重新加载内置规则。生产代码不需要调用。"""
    global _RULES
    _RULES = _load_rules()


# 指标埋点中常见的「可能含 PII」字段名（明确白名单，避免误伤指标元数据）。
_METRICS_PII_KEYS = frozenset({
    "content",
    "user_message",
    "assistant_message",
    "clarify_reason",
    "handoff_reason",
    "query",
    "answer",
    "args_redacted",
    "error",
})


def redact_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    """为评测埋点提供「白名单字段脱敏」。

    只对 ``_METRICS_PII_KEYS`` 中字段递归脱敏，其它字段（如 ``intent`` / ``response_mode`` /
    ``latency_ms`` 等）原样保留。这样：
    - 落 MySQL 的 ``evaluation_events`` 不会泄露手机号、身份证等；
    - 指标元数据（标签、状态、延迟）保持原值，便于聚合与告警查询。
    """
    if not REDACTOR_ENABLED:
        return dict(payload)
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _METRICS_PII_KEYS:
            out[key] = redact(value)
        else:
            out[key] = value
    return out


__all__ = [
    "REDACTOR_ENABLED",
    "REDACTOR_MASK_CHAR",
    "redact",
    "redact_text",
    "redact_messages",
    "redact_metrics",
    "reload_rules",
]