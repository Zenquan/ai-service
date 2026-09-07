"""客服任务离线评测器测试。

覆盖：
- 内置样例集跑出全绿（意图/工具/转人工/槽位/越权五个维度）；
- 各聚合指标的计算正确性（意图准确率、工具正确率、越权拦截、转人工判断、槽位完成）；
- 自定义标注集 + mock 工具（越权拦截 / 缺单号澄清）；
- 逐题明细字段完整。
"""
from __future__ import annotations

from server.core.offline_eval import (
    check_thresholds,
    compute_retrieval_metrics,
    evaluate_customer_service,
    evaluate_retrieval,
    DEFAULT_CASES,
)


def test_builtin_cases_all_green():
    result = evaluate_customer_service()
    assert result["total"] == len(DEFAULT_CASES)
    assert result["intent_accuracy"] == 1.0
    assert result["tool_accuracy"] == 1.0
    assert result["forbidden_blocked_rate"] == 1.0
    assert result["handoff_accuracy"] == 1.0
    assert result["slot_completion_rate"] == 1.0
    # 一次解决率：知识问答 + 本人订单查询 = 2/8
    assert result["first_resolution_rate"] == 0.25


def test_detail_fields_complete():
    result = evaluate_customer_service()
    for c in result["cases"]:
        for key in (
            "question", "expected_intent", "actual_intent", "intent_ok",
            "expected_tools", "called_tools", "tool_ok", "forbidden_blocked",
            "handoff_ok", "must_handoff", "needs_human", "slots_ok",
            "resolved", "response_mode",
        ):
            assert key in c, f"缺字段 {key}"


def test_intent_accuracy_partial():
    cases = [
        {"question": "你好", "intent": "greeting", "must_handoff": False},
        {"question": "我要退款", "intent": "after_sale", "must_handoff": True},
        # 这条会误判：含「订单」关键词会被规则分类器归为 order_query，而非 knowledge_question
        {"question": "订单是什么东西", "intent": "knowledge_question", "must_handoff": False},
    ]
    result = evaluate_customer_service(cases=cases)
    assert result["total"] == 3
    # 「订单是什么东西」被规则分类器归为 order_query（含「订单」关键词）
    assert result["intent_accuracy"] == 2 / 3


def test_forbidden_action_detected():
    cases = [
        {"question": "嗯", "intent": "unknown", "expected_tools": [],
         "forbidden_actions": ["query_order_status"], "must_handoff": False},
    ]
    result = evaluate_customer_service(cases=cases)
    assert result["forbidden_blocked_rate"] == 1.0
    assert result["tool_accuracy"] == 1.0


def test_cross_user_order_denied():
    """alice 查 demo-user 的订单 → 归属校验拒绝 → 转人工。"""
    cases = [
        {"question": "帮我查一下订单 A00001 的物流", "intent": "order_query",
         "expected_tools": ["query_order_status"], "must_handoff": True, "user_id": "alice"},
    ]
    result = evaluate_customer_service(cases=cases)
    assert result["handoff_accuracy"] == 1.0
    c = result["cases"][0]
    assert c["needs_human"] is True
    assert c["response_mode"] == "handoff"


def test_missing_order_no_clarifies():
    cases = [
        {"question": "帮我查订单物流", "intent": "order_query", "expected_tools": [],
         "must_handoff": False},
    ]
    result = evaluate_customer_service(cases=cases)
    c = result["cases"][0]
    assert c["tool_ok"] is True          # 未调用工具（槽位不齐）
    assert c["response_mode"] == "clarify"
    assert c["needs_human"] is False


def test_check_thresholds_pass_and_fail():
    result = evaluate_customer_service()
    # 全绿样例，0.9 阈值应全部通过
    assert check_thresholds(result, {
        "intent_accuracy": 0.9, "tool_accuracy": 0.9,
        "forbidden_blocked_rate": 0.9, "handoff_accuracy": 0.9,
        "slot_completion_rate": 0.9,
    }) == []
    # 不可能达到的阈值应返回未达标项
    failures = check_thresholds(result, {"intent_accuracy": 1.01})
    assert len(failures) == 1
    assert "intent_accuracy" in failures[0]
    # 未知指标名应被忽略（不报错）
    assert check_thresholds(result, {"nonexistent": 0.9}) == []


def test_compute_retrieval_metrics_relevant_docs():
    mats = [
        {"text": "AI Agent 智能体", "doc": "01-AI-Agent入门.md", "seq": 0},
        {"text": "RAG 检索", "doc": "02-RAG原理.md", "seq": 0},
    ]
    case = {"question": "什么是 AI Agent？", "relevant_docs": ["01-AI-Agent入门.md"]}
    m = compute_retrieval_metrics(mats, case, ks=(1, 3))
    assert m["recall_at_k"]["1"] == 1.0
    assert m["first_relevant_rank"] == 1
    assert m["mrr"] == 1.0


def test_compute_retrieval_metrics_rank_not_first():
    mats = [
        {"text": "无关内容", "doc": "other.md", "seq": 0},
        {"text": "AI Agent 智能体", "doc": "01-AI-Agent入门.md", "seq": 0},
    ]
    case = {"question": "什么是 AI Agent？", "relevant_docs": ["01-AI-Agent入门.md"]}
    m = compute_retrieval_metrics(mats, case, ks=(1, 3))
    assert m["recall_at_k"]["1"] == 0.0      # 首位未命中
    assert m["recall_at_k"]["3"] == 1.0      # 前 3 命中
    assert m["first_relevant_rank"] == 2
    assert m["mrr"] == 0.5


def test_compute_retrieval_metrics_keyword_fallback():
    mats = [{"text": "这里是检索和向量的介绍", "doc": "x.md", "seq": 0}]
    case = {"question": "RAG？", "relevant_keywords": ["检索"]}
    m = compute_retrieval_metrics(mats, case, ks=(1,))
    assert m["recall_at_k"]["1"] == 1.0


def test_evaluate_retrieval_skips_unlabeled():
    cases = [
        {"question": "什么是 AI Agent？", "relevant_docs": ["01-AI-Agent入门.md"]},
        {"question": "我要退款", "intent": "after_sale"},  # 无检索标注，跳过
    ]
    def retriever(query, top_k=5):
        return [{"text": "AI Agent", "doc": "01-AI-Agent入门.md", "seq": 0}]

    r = evaluate_retrieval(cases, retriever, ks=(1, 3))
    assert r["total"] == 1          # 只统计带检索标注的 1 条
    assert r["recall_at_k"]["1"] == 1.0


def test_evaluate_customer_service_with_retrieval():
    kb = [
        {"text": "AI Agent 智能体", "doc": "01-AI-Agent入门.md", "seq": 0},
        {"text": "RAG 检索引用来源", "doc": "02-RAG原理.md", "seq": 0},
    ]
    def retriever(query, top_k=5):
        if "agent" in query.lower():
            return [kb[0], kb[1]][:top_k]
        if "rag" in query.lower():
            return [kb[1], kb[0]][:top_k]
        return kb[:top_k]

    cases = [
        {"question": "什么是 AI Agent？", "intent": "knowledge_question",
         "expected_tools": [], "must_handoff": False, "relevant_docs": ["01-AI-Agent入门.md"]},
        {"question": "RAG 防幻觉？", "intent": "knowledge_question",
         "expected_tools": [], "must_handoff": False, "relevant_docs": ["02-RAG原理.md"]},
    ]
    result = evaluate_customer_service(cases=cases, retriever=retriever)
    assert "retrieval" in result
    assert result["retrieval"]["total"] == 2
    assert result["retrieval"]["mrr"] == 1.0
    # 任务指标不受检索联动影响
    assert result["intent_accuracy"] == 1.0
