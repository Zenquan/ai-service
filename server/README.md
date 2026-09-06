# server —— RAG 客服系统后端

FastAPI API + RAG Core + LangGraph 客服编排 + 会话/文档持久化，单一 Python 包。

## 目录结构

```
server/
├── pyproject.toml          # 依赖唯一来源（云端不装 fastembed，见 [local] extra）
├── src/server/
│   ├── api/                # FastAPI 路由（health/docs/ingest/ask/conversations）
│   ├── core/               # RAG Core：解析→清洗→切块→向量→混合检索→rerank→生成→引用校验
│   ├── graph/              # LangGraph 编排：rag 图 + customer_service 图
│   ├── services/           # 业务服务：rag 融合层、chat_store、doc_store、customer_service
│   ├── cli.py              # CLI 入口：python -m server.cli ingest/ask/run/eval
│   └── main.py             # FastAPI 应用工厂
├── data/                   # 上传文档、解析缓存、评测集（运行数据）
├── qdrant_data/            # Qdrant local 向量库（本地模式）
├── .env                    # 密钥配置（不入库，模板见 .env.example）
└── tests/                  # 全量单测：core 纯函数 + 图契约 + API 接口
```

## 开发

```bash
# 环境（Python 3.12）
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[local,dev]"

# 测试
.venv/bin/python -m pytest tests

# CLI（RAG 链路）
.venv/bin/python -m server.cli ingest data/docs
.venv/bin/python -m server.cli ask "什么是 AI Agent？"

# API 服务（必须单 worker：Qdrant local 单进程锁）
.venv/bin/python -m uvicorn server.main:app --reload --port 8000 --workers 1
```

## 部署（云端镜像）

云端 embedding 走远程 `/embeddings`（EMBED_BASE_URL），**不装 fastembed**：

```dockerfile
COPY server ./server
RUN pip install --no-cache-dir ./server
```

本地向量化才需要 `[local]` extra。
