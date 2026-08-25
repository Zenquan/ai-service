# fastapi-app · RAG 产品（FastAPI × RAG → Ant Design X）

把 `rag/`（独立 git 仓库的 RAG 核心）融合进 FastAPI，做成完整 RAG 产品：

- **后端**：FastAPI 提供 REST API（`/api/v1/health | docs | ingest | ask`），rag 作为库零改动接入
- **前端**：Vite + React + TS + **Ant Design X**（Bubble/Sender/Welcome/Prompts/Sources）+ antd 知识库面板
- **链路**：文件上传 → MinerU 解析 → 自研切片 → FastEmbed 向量 → Qdrant → 混合检索 + rerank → DeepSeek 生成 → 引用校验

## ✨ 产品功能

| 模块 | 能力 | 前端入口 |
| --- | --- | --- |
| 📚 知识库管理 | 拖拽/点选上传（PDF/TXT/MD/DOCX/HTML/图片）、文档列表、chunk 数统计、单文档删除（增量不重建） | 左侧 `KnowledgePanel` |
| 💬 知识问答 | 混合检索（关键词 + 向量 RRF 融合）→ bge-reranker 精排 → DeepSeek 生成 | 右侧 `ChatPanel`（Bubble.List + Sender） |
| 🔗 引用溯源 | 回答强制 `[来源N]` 标记 + 程序校验越界引用；引用卡片可展开查看依据素材原文 | `AnswerView`（XMarkdown + Sources） |
| 🛡️ 健壮性 | MinerU→markitdown→纯文本三级解析降级、云解析 sha256 缓存、LLM 超时重试、rerank 失败静默回退 | 后端 rag 层 |
| 🔒 安全 | 上传防路径穿越（只取 basename）、扩展名白名单、密钥只存 `rag/.env`（gitignore 双保险） | 后端 ingest 路由 |

## 🏗️ 系统架构

```
┌────────────────────────── 浏览器（Vite dev :5173）──────────────────────────┐
│  web/  React 19 + TS + Ant Design X                                         │
│  ┌────────────────┐        ┌────────────────────────────────────────────┐    │
│  │ KnowledgePanel │        │ ChatPanel：Welcome/Prompts（空态）           │    │
│  │ Upload/Docs    │        │ Bubble.List（消息流）                        │    │
│  │ 列表/删除/健康  │        │ AnswerView：XMarkdown 正文 + Sources 引用卡片 │    │
│  └───────┬────────┘        └─────────────────────┬──────────────────────┘    │
│          │  fetch（Vite proxy /api → 127.0.0.1:8000）                         │
└──────────┼──────────────────────────────────────┼────────────────────────────┘
           ▼                                       ▼
┌─────────────────── FastAPI（uvicorn :8000，--workers 1）────────────────────┐
│  app/main.py：CORS + 路由挂载（/api/v1/*）                                    │
│  app/api/：health · docs · ingest · ask                                      │
│  app/services/rag.py：融合层（sys.path 注入 rag + threading.Lock 全局锁）      │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
┌─────────────────────────── rag/（独立 git 仓库）─────────────────────────────┐
│  ingest：MinerU 云解析 → clean_text → 段落优先切块(800/100) → FastEmbed(1024) │
│  ask：   混合检索(关键词+向量 RRF) → rerank → Prompt 注入 → DeepSeek → 校验    │
│  qdrant_data/（向量索引 local）· data/_parse_cache/ · data/uploads/           │
└──────────────────────────────────────────────────────────────────────────────┘
```

**两条主链路**

- **入库**：上传文件 → 落盘 `rag/data/uploads/` → 解析（MinerU 云 → markitdown → 纯文本兜底，带内容 sha256 缓存）→ 清洗（去页眉/URL/HTML/高频短行）→ 切块（段落优先聚合 + overlap）→ FastEmbed 向量化 → Qdrant upsert（稳定 id 幂等覆盖）
- **问答**：query 向量化 → Qdrant 相似召回（top_k×2 放宽）+ 关键词 BM25 式双路召回 → RRF 融合取 top_k → bge-reranker 精排取 top 3 → 编号素材注入 Prompt → DeepSeek 生成（强制 `[来源N]`）→ 引用校验 → 返回回答 + 素材 + 校验结果

## 🚀 快速开始（两个终端）

```bash
# 1. 后端（Python 3.12；⚠️ 3.13/3.14 无 fastembed wheel）
cd fastapi-app
rag/.venv/bin/python3.12 -m uvicorn app.main:app --reload --port 8000

# 2. 前端（Vite dev proxy: /api → 127.0.0.1:8000）
cd fastapi-app/web
pnpm install      # 首次
pnpm dev          # http://localhost:5173
```

打开 http://localhost:5173：左侧上传 PDF/MD → 入库自动切片；右侧提问，回答带 [来源N] 引用卡片，可展开查看依据素材。

> 密钥配置：`cp rag/.env.example rag/.env`，必填 `DEEPSEEK_API_KEY`；PDF 解析建议填 `MINERU_TOKEN`，精排填 `SILICONFLOW_API_KEY`（详见 rag/README.md「踩坑实录」）。

## 📂 目录结构

```
fastapi-app/
├── app/                    # FastAPI 应用（壳）
│   ├── main.py             # 应用工厂：CORS + 路由挂载（uvicorn app.main:app）
│   ├── api/                # REST 路由：health / docs / ingest / ask
│   └── services/rag.py     # 融合层：rag 作为库导入 + 全局锁 + 上传落盘目录
├── rag/                    # RAG 核心（独立 git 仓库，CLI 仍可用）
│   ├── main.py             # ingest/ask/run/eval + CLI
│   ├── chunker/embed_store/retrieve/generate/ingest/citations/config
│   ├── tests/              # 30 个单测（清洗/切块/引用/检索/解析缓存/链路分支）
│   └── data/               # uploads/（前端上传）、_parse_cache/（解析缓存）、qdrant_data/（索引）
├── web/                    # 前端（Vite + React + TS + Ant Design X）
│   ├── src/lib/            # api.ts 客户端 + chat-provider.ts（DefaultChatProvider 透传）
│   ├── src/components/     # ChatPanel / KnowledgePanel / AnswerView
│   └── vite.config.ts      # dev proxy /api → 127.0.0.1:8000
└── docs/                   # 本文档体系
    ├── architecture.md     # 架构文档：分层 / 数据流 / 关键设计决策
    └── api.md              # API 参考：请求/响应/错误/curl 示例
```

## 📄 文档导航

| 文档 | 适合谁 | 内容 |
| --- | --- | --- |
| [docs/architecture.md](docs/architecture.md) | 面试/接手 | 分层架构、端到端时序、9 个关键设计决策及取舍 |
| [docs/api.md](docs/api.md) | 联调/二次开发 | 5 个端点完整参考：请求/响应/错误/curl 实测 |
| [web/README.md](web/README.md) | 前端开发 | 前端技术栈、目录语义、chat-provider 原理、构建注意 |
| [rag/README.md](rag/README.md) | RAG 原理 | 核心链路、踩坑实录 9 条、优化记录 P0/P1 |
| [rag/tests/](rag/tests/) | 测试 | 30 个纯函数单测（含解析缓存/链路分支），可离线跑 |
| [tests/test_api.py](tests/test_api.py) | 测试 | 14 个 FastAPI 接口测试（TestClient + 服务层打桩） |

## 🧠 面试亮点（一句话版）

1. **rag 零改动接入**：`sys.path` 注入 + 模块名错开（`app.main` vs `rag/main`），CLI/单测/独立 git 历史全保留
2. **Qdrant local 免 Docker**：与生产远端同 API；单进程锁用 `threading.Lock` + `--workers 1`
3. **混合检索 RRF**：关键词（中文 2-gram + 英文词）与向量双路召回 → 排名倒数融合，术语精确 + 语义扩展兼顾
4. **防幻觉闭环**：生成强制 `[来源N]` → 程序校验越界 → 前端「引用校验通过/含越界引用」徽标 + 素材原文展开
5. **工程边界**：上传防路径穿越、三级解析降级、解析缓存、LLM 超时重试、rerank 静默回退

## ✅ 测试与验证现状

- **rag 单测**：`rag/.venv/bin/python3.12 -m pytest rag/tests -q` → **30 个用例全绿**（清洗/切块/引用/混合检索纯函数 + 解析降级链与缓存 + ask 错误分支/evaluate 命中率/Prompt 组装）
- **接口测试**：`rag/.venv/bin/python3.12 -m pytest tests -q` → **14 个用例全绿**（FastAPI TestClient，打桩 rag 服务层：health/docs 列表与删除/ingest 白名单与防穿越/ask 参数与错误透传，零外部依赖）
- **端到端实测**：health ✓ / 上传入库 ✓ / 文档列表与删除 ✓ / ask（"钱大妈日清模式"、"ResNet 核心创新"）回答 + 引用校验 ✓ / eval 7 用例 100% 命中
- **前端**：`tsc -b` 类型检查通过；`vite build` 可出产物（highlight.js 版本处理见 web/README.md）

## 🛠️ 常用命令

```bash
# rag 核心单测（纯函数，离线可跑）
cd fastapi-app && rag/.venv/bin/python3.12 -m pytest rag/tests -q
# 接口层测试（打桩服务层，不连 Qdrant/LLM）
rag/.venv/bin/python3.12 -m pytest tests -q

# rag CLI（不经 API 直接跑核心）
cd fastapi-app/rag && .venv/bin/python3.12 main.py ask "钱大妈的日清模式是什么？"
.venv/bin/python3.12 main.py eval        # 种子问题命中率

# 前端
cd fastapi-app/web && pnpm lint && pnpm build
```