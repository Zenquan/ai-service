"""客服任务离线评测器测试。

覆盖：
- 内置样例集跑出全绿（意图/工具/转人工/槽位/越权五个维度）；
- 各聚合指标的计算正确性（意图准确率、工具正确率、越权拦截、转人工判断、槽位完成）；
- 自定义标注集 + mock 工具（越权拦截 / 缺单号澄清）；
- 逐题明细字段完整。
"""
from __future__ import annotations

from server.core.offline_eval import evaluate_customer_service, DEFAULT_CASES


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
