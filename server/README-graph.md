# langgraph-graph

智能客服的 **LangGraph 图编排层**，复用 `rag` 核心（FastEmbed 向量 + Qdrant 混合检索 + DeepSeek 生成），当前先落地“知识问答 + 低置信度澄清 + 业务请求转人工”的安全闭环。

`rag/` 继续负责检索质量；本目录负责客服状态、意图路由、重试和人工接管。模型、检索器和分类器都可通过依赖注入替换。

## 图结构

客服图：

```text
START → load_session → classify_intent
  ├── knowledge_question → retrieve → compose → validate → finalize
  ├── greeting/unknown → clarify → END
  ├── after_sale/complaint → handoff → END
  └── order_query → extract_slots
        ├── 缺订单号 → clarify（多轮补齐，checkpoint 恢复）
        └── 有订单号 → execute_order_tool
              ├── 本人订单 → 工具结果组装 answer → finalize
              └── 非本人/查无此单/超时/异常 → handoff → END
```

原始 RAG 图仍保留，用于兼容已有实验和 LangGraph Studio 调试。

```
 START → retrieve → rerank → generate → validate
                                          │
                    needs_revision? ──────┤ (回到 generate 重写，最多 2 次)
                                          ↓
                                       finalize → END
```

- **retrieve**：按 question 检索 top_k 上下文片段（FastEmbed + Qdrant）
- **rerank**：二次排序，剔除无关片段
- **generate**：基于上下文生成带 `[n]` 引用的答案（DeepSeek，OpenAI 兼容接口）
- **validate_citations**：核验引用是否落在上下文中，幻觉引用触发重写
- **finalize**：汇总 answer + citations 输出

## 目录结构

```
langgraph/
├── pyproject.toml          # uv workspace 子成员（根 members=["langgraph"]）
├── langgraph.json          # LangGraph CLI 配置
├── .env.example
├── src/langgraph_graph/
│   ├── state/              # RAGState 定义
│   ├── nodes/              # retrieve / rerank / generate / validate
│   ├── graph/              # build_rag_graph（图编排 + 条件边）
│   ├── agent/              # RagAgent 封装
│   ├── customer_service/   # 客服状态、意图路由、RAG 适配和人工接管
│   └── __init__.py
├── examples/quickstart.py  # 注入 mock 的可运行示例
└── tests/                  # 单元测试
```

## 快速开始

```bash
# 依赖安装（根 workspace 已声明 langgraph 成员）
cd /Users/zenquan/ZCodeProject/fastapi-app
uv sync  # 或：uv pip install -e ./langgraph

# 可独立运行示例（mock 检索/生成，无需真实 LLM）
cd langgraph
python examples/quickstart.py "什么是混合检索？"

# 单元测试
python -m pytest langgraph/tests

# 客服 Agent 使用
from langgraph_graph.customer_service import create_customer_service_agent

agent = create_customer_service_agent()
result = agent.ask("门店的经营模式是什么？", conversation_id="demo-1")
print(result["final_answer"])

# 原始 RAG Agent 使用
from langgraph_graph.agent import create_agent
agent = create_agent()
result = agent.ask("什么是混合检索？")
print(result["final_answer"])
```

## 接入真实 rag 核心

`retrieve` / `rerank` 节点默认尝试导入 `rag` 包并调用其检索/重排函数；`generate` 默认读取 `DEEPSEEK_API_KEY` 走 DeepSeek。也可显式注入：

```python
from rag import search, rerank
agent = create_agent(retriever=search, reranker=rerank)
```

## LangGraph CLI / LangSmith

```bash
# 本地启动 LangGraph Studio / server（需安装 langgraph-cli）
langgraph dev --python 3.12
```

`langgraph.json` 同时暴露 `rag` 和 `customer_service` 两个图。配置 `.env` 中的 `LANGSMITH_TRACING` 即可接入链路追踪。
配置 `.env` 中的 `LANGSMITH_TRACING` 即可接入链路追踪。
