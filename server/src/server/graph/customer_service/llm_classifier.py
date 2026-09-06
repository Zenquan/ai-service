"""LLM 意图分类器：替换确定性规则，低置信度自适应追问。

设计目标（迭代「LLM 意图分类器」）：
- 用 DeepSeek（OpenAI 兼容）做结构化意图分类，输出与规则分类器完全一致的结构：
  ``{"intent", "intent_confidence", "slots"}``，因此可无痛替换 ``classify_query``。
- 低置信度（置信度 < ``CONFIDENCE_THRESHOLD``）时，让 LLM 针对用户原话生成
  一句「自适应追问」（写入 ``clarify_reason``），而不是固定文案。
- 健壮性：无 API key / LLM 调用失败 / 返回非法 JSON 时，回退到规则分类器
  ``classify_query``，保证服务不因分类器故障而中断。

契约：``LLMIntentClassifier`` 满足 ``Classifier = Callable[[str], dict]``。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

from openai import OpenAI

from server.graph.customer_service.classifier import classify_query
from server.graph.customer_service.state import Intent

logger = logging.getLogger(__name__)

# 低于该置信度视为「不确定」，触发自适应追问，而不是硬路由。
CONFIDENCE_THRESHOLD = 0.7

# 允许 LLM 输出的意图集合（白名单，防止模型幻觉出未知意图标签）。
_VALID_INTENTS = {
    "greeting",
    "knowledge_question",
    "order_query",
    "after_sale",
    "complaint",
    "unknown",
}

_SYSTEM = (
    "你是智能客服的意图分类器。请把用户消息分类到以下意图之一：\n"
    "- greeting：寒暄、打招呼（你好、在吗等）\n"
    "- order_query：查询订单、物流、快递、配送、发货、收货\n"
    "- after_sale：退款、退货、换货、售后、维修、质量问题\n"
    "- complaint：投诉、举报、不满意、要求人工客服、赔偿\n"
    "- knowledge_question：咨询产品/服务/政策等知识类问题\n"
    "- unknown：无法判断意图\n\n"
    "同时抽取可用槽位（slots）：order_query 场景抽取订单号（如 A00001，形如字母+5位数字）。\n"
    "再给出置信度 confidence（0~1 的小数）。\n"
    "只输出 JSON，不要输出任何其他文字。格式：\n"
    '{"intent": "<意图>", "confidence": <0~1>, "slots": {"order_no": "<订单号或空>"}}'
)


def _extract_json(text: str) -> dict | None:
    """从模型输出里稳健地解析 JSON（容忍前后多余文字 / markdown 代码块）。"""
    text = (text or "").strip()
    # 去掉 markdown 代码块围栏
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 优先找首个 {...} 块
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _sanitize(parsed: dict) -> dict:
    """把 LLM 输出归一化为 Classifier 契约；非法字段回退到规则分类器结果。"""
    intent = parsed.get("intent")
    if intent not in _VALID_INTENTS:
        return None
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    slots = parsed.get("slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    return {"intent": intent, "intent_confidence": confidence, "slots": slots}


class LLMIntentClassifier:
    """LLM 意图分类器，输出结构与规则分类器一致，可作 Classifier 注入。"""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        clarify_prompt: Callable[[str], str] | None = None,
    ):
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._threshold = confidence_threshold
        self._clarify_prompt = clarify_prompt or _default_clarify_prompt

    def __call__(self, query: str) -> dict:
        result = self._classify(query)
        if result is None:
            # LLM 不可用或失败 → 回退规则分类器，保证链路不中断。
            return classify_query(query)

        confidence = result["intent_confidence"]
        if confidence < self._threshold:
            # 低置信度：自适应追问，而非硬路由到未知意图。
            result = dict(result)
            result["clarify_reason"] = self._ask_clarify(query, result["intent"])
        return result

    def _classify(self, query: str) -> dict | None:
        if not self._api_key:
            return None
        client = OpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=15.0,
            max_retries=1,
        )
        try:
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,  # 分类要确定性，低温
            )
            content = resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 —— 分类器失败应回退而非抛出
            logger.warning("LLM 意图分类失败，回退规则分类器: %s", exc)
            return None

        parsed = _extract_json(content)
        if parsed is None:
            logger.warning("LLM 意图分类返回非法 JSON，回退规则分类器: %r", content[:200])
            return None
        return _sanitize(parsed)

    def _ask_clarify(self, query: str, intent: str | None) -> str:
        """低置信度时，让 LLM 生成一句针对用户原话的追问。失败则返回通用追问。"""
        prompt = self._clarify_prompt(query)
        if not self._api_key:
            return _default_clarify_text(intent)
        client = OpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=15.0,
            max_retries=1,
        )
        try:
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是智能客服。用户消息意图不明确，请用一句话友好地追问，"
                            "引导用户说清楚是要查订单、办售后，还是咨询产品知识。"
                            "只输出追问内容，不要解释。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            return text or _default_clarify_text(intent)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 澄清追问生成失败，用通用追问: %s", exc)
            return _default_clarify_text(intent)


def _default_clarify_prompt(query: str) -> str:
    return f"用户说：「{query}」"


def _default_clarify_text(intent: str | None) -> str:
    if intent == "order_query":
        return "请提供您的订单号（如 A00001），我再帮您查询物流状态。"
    if intent == "after_sale":
        return "请补充您的订单号和具体售后诉求（退款/退货/换货），我再帮您处理。"
    return "请补充您需要咨询的产品、订单或售后问题，我再帮您处理。"


def build_llm_classifier(
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> LLMIntentClassifier:
    """工厂：默认从 server.core.config 读 LLM 配置，无 key 时返回仍可用的分类器
    （__call__ 内部会回退规则分类器）。"""
    from server.core import config

    return LLMIntentClassifier(
        model=model or config.LLM_MODEL,
        base_url=base_url or config.LLM_BASE_URL,
        api_key=api_key if api_key is not None else config.LLM_API_KEY,
        confidence_threshold=confidence_threshold,
    )
