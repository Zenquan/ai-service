"""langgraph-graph：RAG 产品的 LangGraph 图编排层。

包结构：
    state/    状态定义（RAGState）
    nodes/    图节点（检索/重排/生成/引用校验）
    graph/    图编排（build_rag_graph）
    agent/    对外封装（RagAgent）

快速开始：
    from server.graph.agent import create_agent
    agent = create_agent()
    agent.ask("什么是混合检索？")
"""
from __future__ import annotations

from server.graph.agent import RagAgent, create_agent
from server.graph.customer_service import (
    CustomerServiceAgent,
    CustomerServiceState,
    create_customer_service_agent,
)
from server.graph.graph import build_rag_graph
from server.graph.state import RAGState

__all__ = [
    "RAGState",
    "RagAgent",
    "CustomerServiceAgent",
    "CustomerServiceState",
    "build_rag_graph",
    "create_agent",
    "create_customer_service_agent",
]
__version__ = "0.1.0"
