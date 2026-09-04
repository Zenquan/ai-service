# fastapi-app · RAG 产品（FastAPI × RAG → Ant Design X）

把 `rag/`（独立 git 仓库的 RAG 核心）融合进 FastAPI，做成完整 RAG 产品：

- **后端**：FastAPI 提供 REST API（`/api/v1/health | docs | ingest | ask | conversations`），rag 作为库零改动接入
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
│  app/api/：health · docs · ingest · ask · customer_service                   │
│  app/services/rag.py：融合层（sys.path 注入 rag + threading.Lock 全局锁）      │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
┌─────────────────────────── rag/（独立 git 仓库）─────────────────────────────┐
│  ingest：MinerU 云解析 → 清洗 → 标题层级/段落感知切块(800目标) → FastEmbed(1024) │
│  ask：   向量 + 标题加权 BM25 → RRF → rerank/阈值 → Prompt → DeepSeek → 校验 │
│  qdrant_data/（向量索引 local）· data/_parse_cache/ · data/uploads/           │
└──────────────────────────────────────────────────────────────────────────────┘
```

**两条主链路**

- **入库**：上传文件 → 落盘 `rag/data/uploads/` → 解析（MinerU 云 → markitdown → 纯文本兜底，带内容 sha256 缓存）→ 清洗 → 按标题层级和段落边界聚合（`chunk_size` 仅为目标，不硬切段落）→ FastEmbed 向量化 → Qdrant upsert；chunk 保存 `chapter/title/section/heading_path`
- **问答**：query 向量化 → 向量与标题加权 BM25 双路候选 → RRF 融合 → rerank 精排/阈值过滤 → 编号素材注入 Prompt → DeepSeek 生成（强制 `[来源N]`）→ 引用校验

## 🚀 快速开始（两个终端）

```bash
# 1. 后端（Python 3.12，见「为什么锁 3.12」）
cd fastapi-app
rag/.venv/bin/python3.12 -m uvicorn app.main:app --reload --port 8000

# 2. 前端（Vite dev proxy: /api → 127.0.0.1:8000）
cd fastapi-app/web
pnpm install      # 首次
pnpm dev          # http://localhost:5173
```

打开 http://localhost:5173：左侧上传 PDF/MD → 入库自动切片；右侧提问，回答带 [来源N] 引用卡片，可展开查看依据素材。

> 密钥配置：`cp rag/.env.example rag/.env`，必填 `DEEPSEEK_API_KEY`；PDF 解析建议填 `MINERU_TOKEN`，精排填 `SILICONFLOW_API_KEY`（详见 rag/README.md「踩坑实录」）。

## 🐍 为什么锁 Python 3.12

**全项目统一使用 Python 3.12**（`pyproject.toml` / `.python-version` / `langgraph.json` / `Dockerfile` 四处已对齐，均为 3.12）。

**原因：`rag` 核心的向量检索链路依赖 `fastembed`，而 `fastembed` 的推理引擎 `onnxruntime` 在 3.13/3.14 下没有预编译 wheel。**

| 依赖 | Python 3.13 / 3.14 支持 | 说明 |
|------|------------------------|------|
| `langgraph` | ✅ 支持 | `requires-python = ">=3.10"`，无上限，3.14 实测可装可跑 |
| `fastembed` 本体 | ✅ 支持 | 纯 Python wheel，不拦新版本 |
| **`onnxruntime`** | ❌ **不支持** | macOS x86_64 下无 cp313/cp314 wheel（`pip index versions` 返回空；cp314 目前仅 Windows/Linux） |

> 说明：`fastembed` 从 0.x 起默认打包 `onnxruntime` 做本地向量推理，且 3.14 下其依赖 `mmh3` 也无预编译 wheel（需源码编译）。**3.13 同样缺 macOS x86_64 的 onnxruntime wheel**，所以实际是 `<3.13`。

**影响与决策：**
- `langgraph` 图编排层**本身**不依赖 onnxruntime，单独跑用 3.14 完全没问题；但本项目 langgraph 层显式依赖 `rag`（同 workspace 包），要走通完整 RAG 链路就必须锁 3.12。
- 为保证**本地开发、LangGraph CLI/Studio、部署容器三者运行版本一致**，`python_version` 统一写 3.12，避免"本地 3.12 能跑、部署却是别的版本"的不一致。
- 若未来把 langgraph 层与 rag 解耦（不接 fastembed/onnxruntime），可单独放宽 langgraph 到 3.13/3.14。

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
│   ├── src/components/     # 客服队列 / ChatPanel / 上下文 / KnowledgePanel
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
3. **混合检索 RRF**：标题加权 BM25（中文 2-gram + 英文词）与语义向量双路召回 → 排名倒数融合，术语精确 + 语义扩展兼顾
4. **结构感知切块**：标题路径和段落边界进入 chunk 元数据、embedding 上下文与 Prompt，超长段落保持完整
5. **防幻觉闭环**：生成强制 `[来源N]` → 程序校验越界 → 前端「引用校验通过/含越界引用」徽标 + 素材原文展开
5. **工程边界**：上传防路径穿越、三级解析降级、解析缓存、LLM 超时重试、rerank 静默回退

## ✅ 测试与验证现状

- **统一入口（推荐）**：`cd fastapi-app && rag/.venv/bin/python3.12 -m pytest` → **44 个用例全绿**（pytest.ini 已配置 testpaths，一次跑全量）
  - rag 核心 30 个：清洗/切块/引用/混合检索纯函数 + 解析降级链与缓存 + ask 错误分支/evaluate 命中率/Prompt 组装
  - 接口层 14 个：FastAPI TestClient，打桩 rag 服务层（health/docs/ingest 白名单与防穿越/ask 参数与错误透传，零外部依赖）
- **端到端实测**：health ✓ / 上传入库 ✓ / 文档列表与删除 ✓ / ask（"钱大妈日清模式"、"ResNet 核心创新"）回答 + 引用校验 ✓ / eval 7 用例 100% 命中
- **前端**：`tsc -b` 类型检查通过；`vite build` 可出产物（highlight.js 版本处理见 web/README.md）

## 🛠️ 常用命令

```bash
# 全量测试（统一入口，pytest.ini 配置 testpaths；离线不打桩外部服务）
cd fastapi-app && rag/.venv/bin/python3.12 -m pytest
# 只看 rag 核心单测
rag/.venv/bin/python3.12 -m pytest rag/tests -q
# 只看接口层测试
rag/.venv/bin/python3.12 -m pytest tests -q

# rag CLI（不经 API 直接跑核心）
cd fastapi-app/rag && .venv/bin/python3.12 main.py ask "钱大妈的日清模式是什么？"
.venv/bin/python3.12 main.py eval        # 离线 Recall@K / MRR
.venv/bin/python3.12 exp_chunk_size.py  # 比较不同结构切块目标

# 前端
cd fastapi-app/web && pnpm lint && pnpm build
```
