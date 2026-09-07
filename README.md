<p align="center">
  <img src="logo.png" width="150" alt="assemble-platform logo" />
</p>

<h1 align="center">ai-service · AI 智能客服产品</h1>

<p align="center">
  智能客服系统：文档知识库 + 混合检索问答 + LangGraph 客服编排（意图识别 / 澄清 / 转人工）+ 会话与文档云端持久化。
  <br />
  FastAPI · LangGraph · RAG  · React · Typescript · Ant Design X
</p>

---

- **后端**：单一 `server` Python 包（src 布局）——FastAPI REST API + RAG Core + LangGraph 客服图 + MySQL 持久化
- **前端**：Vite + React + TS + **Ant Design X**（Bubble/Sender/Sources）+ antd 知识库面板
- **链路**：文件上传 → MinerU 解析 → 结构感知切块 → 向量化 → Qdrant → 混合检索 + rerank → DeepSeek 生成 → 引用校验

## ✨ 产品功能

| 模块 | 能力 | 前端入口 |
| --- | --- | --- |
| 📚 知识库管理 | 拖拽/点选上传（PDF/TXT/MD/DOCX/HTML/图片）、文档列表、chunk 数统计、单文档删除（增量不重建） | 左侧 `KnowledgePanel` |
| 💬 知识问答 | 混合检索（关键词 + 向量 RRF 融合）→ bge-reranker 精排 → DeepSeek 生成 | 右侧 `ChatPanel`（Bubble.List + Sender） |
| 🔗 引用溯源 | 回答强制 `[来源N]` 标记 + 程序校验越界引用；引用卡片可展开查看依据素材原文 | `AnswerView`（XMarkdown + Sources） |
| 🧭 客服编排 | 三层路由：快速业务意图 → RAG 知识优先 → 检索无素材时澄清/转人工；多轮历史上下文；**多意图风险仲裁**（投诉>售后>订单>寒暄，混合诉求不再被「订单」抢先吞掉）| 后端 graph 层 + services 层 |
| ✋ 人工接管 | AI 转人工后坐席可直接回复（`response_mode=manual`），会话进入 `manual` 状态，界面切换为人工模式 | ChatPanel + customer_service |
| 🔐 登录认证 | demo JWT：`operator`（运营）与 `customer`（普通用户）双角色；客户消息身份取自 token，人工回复需运营角色 | auth 模块 + LoginPage/CustomerChat |
| 🔎 只读业务工具 | 订单/物流查询走真实 LangGraph 工具节点：Pydantic 参数校验、归属校验、超时/失败显式转人工；缺单号多轮澄清（checkpoint 恢复） | 后端 tools 层 + graph 层 |
| 💾 云端持久化 | 会话/消息落 MySQL（回退内存显性标注 `storage`）；文档切块存 MySQL，部署后自动重建向量索引 | 后端 services 层 |
| 🛡️ 健壮性 | MinerU→markitdown→纯文本三级解析降级、云解析 sha256 缓存、LLM 超时重试、rerank 失败静默回退、空库友好回答 / 客服层无素材二次澄清后转人工 | 后端 core 层 |
| 🔒 安全 | 上传防路径穿越（只取 basename）、扩展名白名单、密钥只存本地 `server/.env` / 云端环境变量（均不入库）；**PII 三层脱敏**（Prompt/指标/响应）；**限流 429 / 熔断 / 审计日志**（全 MySQL 实现，零 Redis 依赖） | 后端 ingest 路由 + services/middleware 层 |
| 🎯 安全评测（红队） | `cs-redteam` 门禁对三类攻击语料断言「被阻断」：提示注入（`security_flag=prompt_injection` 归投诉转人工）、越权查单（归属校验 forbidden）、对抗样本（拆字/谐音/符号先归一化再匹配） | 后端 offline_eval + CLI |

## 🏗️ 系统架构

```
┌────────────────────────── 浏览器（Vite dev :5173）──────────────────────────┐
│  web/  React + TS + Ant Design X                                            │
│  ┌────────────────┐        ┌────────────────────────────────────────────┐    │
│  │ KnowledgePanel │        │ ConversationSidebar（真实会话列表）          │    │
│  │ Upload/Docs    │        │ ChatPanel：Bubble.List + AnswerView         │    │
│  └───────┬────────┘        └─────────────────────┬──────────────────────┘    │
│          │  fetch（Vite proxy /api → 127.0.0.1:8000）                         │
└──────────┼──────────────────────────────────────┼────────────────────────────┘
           ▼                                       ▼
┌─────────────────── server 包（uvicorn :8000，--workers 1）──────────────────┐
│  server/main.py：应用工厂（CORS + 路由 + lifespan 云存储重建）                 │
│  server/api/：health · docs · ingest · ask · conversations                  │
│  server/auth/：demo 账号 + JWT 签发/校验 + operator/customer 角色依赖       │
│  server/services/：rag 融合层 · customer_service · chat_store · doc_store · 限流/熔断/审计/脱敏 │
│  server/tools/：只读业务工具（订单/物流查询 + ToolResult 统一返回）           │
│  server/graph/：LangGraph 图（rag 图 + customer_service 图，依赖注入）        │
│  server/core/：RAG 核心（解析→切块→向量→检索→rerank→生成→引用校验）           │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
        Qdrant local（server/qdrant_data）    MySQL（会话/文档切块，公网直连）
        远程 embeddings（EMBED_BASE_URL）      DeepSeek（LLM）+ 硅基流动（rerank）
```

**两条主链路**

- **入库**：上传文件 → 落盘 `server/data/uploads/` → 解析（MinerU 云 → markitdown → 纯文本兜底，带内容 sha256 缓存）→ 清洗 → 按标题层级和段落边界聚合（`chunk_size` 仅为目标，不硬切段落）→ 向量化 → Qdrant upsert → 切块同步存 MySQL（部署后自动重建）
- **问答**：query 向量化 → 向量与标题加权 BM25 双路候选 → RRF 融合 → rerank 精排/阈值过滤 → 编号素材注入 Prompt → DeepSeek 生成（强制 `[来源N]`）→ 引用校验

## 🚀 快速开始

登录（demo 账号，密码可用 `AUTH_*` 环境变量覆盖）：

| 角色 | 账号 | 默认密码 |
| --- | --- | --- |
| 运营 Zenquan | `zenquan` | `zenquan123` |
| 普通用户 Alice | `alice` | `alice123` |

运营账号进入客服工作台；普通用户账号进入客户聊天窗，可端到端验证“提问 → AI → 转人工 → 运营回复”。

```bash
# 一键启动（后端 :8000 + 前端 :5173）
./scripts/start.sh

# 或手动两个终端
# 1. 后端（Python 3.12，见「为什么锁 3.12」）
cd server
.venv/bin/python -m uvicorn server.main:app --reload --port 8000 --workers 1

# 2. 前端（Vite dev proxy: /api → 127.0.0.1:8000）
cd web
pnpm install      # 首次
pnpm dev          # http://localhost:5173
```

打开 http://localhost:5173：左侧上传 PDF/MD → 入库自动切片；右侧提问，回答带 [来源N] 引用卡片，可展开查看依据素材。

> **环境准备**：
> - 后端 venv：`cd server && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[local]" --group dev`
> - 密钥配置：`cp server/.env.example server/.env`，必填 `DEEPSEEK_API_KEY`；PDF 解析建议填 `MINERU_TOKEN`，精排填 `SILICONFLOW_API_KEY`
> - 会话持久化（可选）：`cp .env.local.example .env.local` 并填写 MySQL 连接信息——本地与线上共享同一份会话数据；不配则回退内存存储

## 🐍 为什么锁 Python 3.12

**全项目统一使用 Python 3.12**（`pyproject.toml` / `server/pyproject.toml` / `.python-version` / `Dockerfile` 已对齐，均为 3.12）。

**原因：本地向量推理依赖 `fastembed`，而 `fastembed` 的推理引擎 `onnxruntime` 在 3.13/3.14 下没有 macOS x86_64 的预编译 wheel。**

| 依赖 | Python 3.13 / 3.14 支持 | 说明 |
|------|------------------------|------|
| `langgraph` | ✅ 支持 | `requires-python = ">=3.10"`，无上限，3.14 实测可装可跑 |
| `fastembed` 本体 | ✅ 支持 | 纯 Python wheel，不拦新版本 |
| **`onnxruntime`** | ❌ **不支持** | macOS x86_64 下无 cp313/cp314 wheel（cp314 目前仅 Windows/Linux） |

**影响与决策：**
- **云端镜像不需要本地向量推理**：部署时 embedding 走远程 `/embeddings`（`EMBED_BASE_URL`），`fastembed` 是 `[local]` 可选依赖、不进镜像——因此云端其实不受 3.12 限制；锁 3.12 是为了本地开发（本地向量化）与容器行为一致。
- 若未来本地也切远程 embedding（不再用 fastembed），可整体放宽到 3.13/3.14。

## 📂 目录结构

```
ai-service/
├── server/                      # 后端单一 Python 包（依赖唯一来源）
│   ├── pyproject.toml           # 依赖声明：核心 + [local]（fastembed）+ dev 组
│   ├── src/server/
│   │   ├── main.py              # FastAPI 应用工厂（uvicorn server.main:app）
│   │   ├── api/                 # REST 路由：health/docs/ingest/ask/conversations
│   │   ├── services/            # rag 融合层 / customer_service / chat_store / doc_store
│   │   ├── tools/               # 只读业务工具：订单/物流查询 + ToolResult 统一返回
│   │   ├── graph/               # LangGraph 图：rag 图 + customer_service 图（依赖注入）
│   │   ├── core/                # RAG 核心：chunker/embed_store/retrieve/generate/ingest/citations/config
│   │   └── cli.py               # CLI：python -m server.cli ingest/ask/run/eval/doc-list/doc-delete
│   ├── tests/                   # 全量单测（core 纯函数 + 图契约 + API 接口）
│   ├── data/                    # uploads/（前端上传）、docs/、_parse_cache/、评测标注集（cs_eval_cases/cs_redteam_cases）
│   ├── qdrant_data/             # Qdrant local 向量索引
│   └── .env / .env.example      # 密钥配置（不入库）
├── web/                         # 前端（Vite + React + TS + Ant Design X）
│   ├── src/lib/                 # api.ts 客户端 + chat-provider.ts（DefaultChatProvider 透传）
│   ├── src/components/          # 会话侧栏 / ChatPanel / 上下文 / KnowledgePanel
│   └── vite.config.ts           # dev proxy /api → 127.0.0.1:8000
├── scripts/start.sh                # 一键启动前后端
├── Dockerfile                      # 单容器部署镜像（镜像内构建前端，密钥由环境变量注入）
└── docs/                        # 架构与 API 文档
    ├── architecture.md          # 分层 / 数据流 / 关键设计决策
    ├── customer-service-plan.md # 客服系统演进规划（Phase 路线）
    └── api.md                   # API 参考：请求/响应/错误/curl 示例
```

## 📄 文档导航

| 文档 | 适合谁 | 内容 |
| --- | --- | --- |
| [server/README.md](server/README.md) | 后端开发 | server 包结构、开发/测试/部署说明 |
| [server/README-rag.md](server/README-rag.md) | RAG 原理 | 核心链路、踩坑实录、优化记录 P0/P1 |
| [server/README-graph.md](server/README-graph.md) | 图编排 | LangGraph 图约定与关键踩坑 |
| [docs/architecture.md](docs/architecture.md) | 面试/接手 | 分层架构、端到端时序、关键设计决策及取舍 |
| [docs/api.md](docs/api.md) | 联调/二次开发 | API 端点完整参考：请求/响应/错误/curl 实测 |
| [docs/customer-service-plan.md](docs/customer-service-plan.md) | 规划 | 智能客服系统 Phase 路线、版本里程碑（v0.1–v0.4）、验收标准与剩余迭代 |
| [web/README.md](web/README.md) | 前端开发 | 前端技术栈、目录语义、chat-provider 原理、构建注意 |

## 🧠 面试亮点（一句话版）

1. **单一后端包 + src 布局**：rag/langgraph/api 三层合并为 `server` 包，正规 `from server.core.retrieve import retrieve` 导入（无 sys.path hack、无模块名碰撞），依赖单一来源 `server/pyproject.toml`
2. **编排与检索解耦**：LangGraph 图全部依赖注入（retriever/generator/classifier 参数化），契约测试不碰向量库与模型
3. **优雅降级 + 显性暴露**：LangGraph 不可用回退 RAG 直答；MySQL 不可用回退内存并在响应体带 `storage` 字段；空库时友好回答而非报错
4. **只读工具闭环**：订单/物流查询走 LangGraph 工具节点，Pydantic 入参校验 + 会话归属校验，非本人/失败显式转人工且不改写结果；缺单号由原生 checkpoint 多轮澄清补齐（MySQL 持久化，重启可恢复）
5. **可替换意图分类器 + 安全防线**：规则/LLM 分类器同契约（`{intent, intent_confidence, slots}`），LLM 低置信度自适应追问、失败自动回退规则；规则层支持多意图风险仲裁（投诉>售后>订单>寒暄）、提示注入检测（`security_flag`）与对抗样本归一化（拆字/混淆符号不绕过）；`LLM_CLASSIFIER=1` 一键切换
6. **云端持久化双保险**：会话消息落 MySQL；文档切块同步存 MySQL，服务启动 lifespan 自动重建向量索引——重新部署不丢数据
7. **Qdrant local 免 Docker**：与生产远端同 API；单进程锁用 `threading.Lock` + `--workers 1`
8. **混合检索 RRF**：标题加权 BM25（中文 2-gram + 英文词）与语义向量双路召回 → 排名倒数融合
9. **结构感知切块**：标题路径和段落边界进入 chunk 元数据、embedding 上下文与 Prompt，超长段落保持完整
10. **防幻觉闭环**：生成强制 `[来源N]` → 程序校验越界 → 前端「引用校验通过/含越界引用」徽标 + 素材原文展开

## ✅ 测试与验证现状

- **统一入口（推荐）**：`cd server && .venv/bin/python -m pytest` → 全量用例全绿
  - core 纯函数：清洗/切块/引用/混合检索 + 解析降级链与缓存 + ask 错误分支/evaluate 命中率/Prompt 组装
  - graph 图契约：知识问答 / 澄清 / 转人工 / 引用校验重写循环（注入 mock，零外部依赖）
  - 存储层：MySQL 选择 / 内存回退 / 会话列表
  - 接口层：FastAPI TestClient，打桩服务层（health/docs/ingest 白名单与防穿越/ask 参数与错误透传/会话 API）
  - 评测与告警：指标埋点 / Prometheus 暴露聚合 / 告警阈值判定（转人工率/出错率/无依据承诺率）
  - 安全脱敏：手机号/身份证/邮箱/银行卡/订单号 PII 脱敏（Prompt/指标/响应三层）
  - 多意图与安全：`detect_intents` 风险优先级仲裁、提示注入检测（`_INJECTION_PATTERNS`）、对抗样本归一化、红队评测 `evaluate_redteam`（`test_multi_intent.py`/`test_redteam.py`）
  - 可靠性组件：限流（固定窗口原子计数）/ 熔断（closed/open/half-open 状态机）/ 审计日志（trace_id 关联）（`test_rate_limit.py`/`test_circuit_breaker.py`/`test_audit_log.py`）
- **端到端实测**：health ✓ / 上传入库 ✓ / 文档列表与删除 ✓ / ask（"什么是 AI Agent？"）回答 + 引用校验 ✓ / 会话消息 storage=mysql 落库 ✓ / 部署重启后向量索引自动重建 ✓
- **前端**：`tsc --noEmit` 类型检查通过；`vite build` 可出产物

## 📊 评测与监控（客服任务评测体系）

- **指标埋点**：每轮对话结束记录 `evaluation_events`（意图/响应模式/引用校验/转人工/追问/工具状态/时延），MySQL 持久化 + 内存回退。
- **指标暴露**：`GET /metrics` 输出 Prometheus 文本（前缀 `cs_eval_`），官方 `prometheus_client` 库，覆盖一次解决率、转人工率、工具成功率、无依据承诺率、响应时延 P50/P95。
- **告警**：转人工率 / 出错率 / 无依据承诺率 三项滑动窗口阈值判定，命中落 `evaluation_alerts` 表 + 可选 webhook 推送（`ALERT_WEBHOOK_URL`）；运营端「告警」入口查看近期告警。
- **监控配置**：`deploy/grafana/dashboard.json`（面板）+ `deploy/prometheus/alerts.yml` + `prometheus.yml`（抓取 + 告警规则），导入 Grafana 即可。
- **阈值可配**：`ALERT_HANDOFF_RATE` / `ALERT_ERROR_RATE` / `ALERT_UNFOUNDED_RATE` / `ALERT_MIN_SAMPLES`（详见 `.env.local.example`）。
- **离线评测集**：`python -m server.cli cs-eval` 用 mock 工具跑客服图，回归验证意图识别/工具调用/转人工判断/越权拦截/槽位收集五项任务级正确性；默认标注集 `server/data/cs_eval_cases.json`（57 条真实语料含边界/长尾与 10 条检索标注，`--cases` 指定自定义 JSON）；`--fail-under 0.9` 阈值门禁 + `--json` 报告，已接入 GitHub Actions CI（`.github/workflows/ci.yml`）。`--with-retrieval` 对带 `relevant_docs`/`relevant_keywords` 标注的知识问答样例联动跑 Recall@K/MRR（需真实向量库），实测 Recall@1/3/5=1.0、MRR=1.0。
- **红队安全评测**：`python -m server.cli cs-redteam` 对三类攻击语料 `server/data/cs_redteam_cases.json`（提示注入 6 / 越权 2 / 对抗样本 5）逐题断言「被阻断/不泄露」，聚合 `redteam_block_rate`（安全阻断率，实测 1.0）作 CI 门禁；攻击手法随迭代持续扩充。

## 🔍 日志与链路排查（trace_id）

- 每个 HTTP 请求响应头都会带 `X-Request-ID`（调用方可自定义请求头透传，未传则由服务端生成），
  SSE 流式请求全程使用同一个 ID。
- 服务端业务日志（INFO 起）统一输出到 stdout，**每行自动带当前请求的 trace_id**，
  格式如 `2026-09-06 15:42:00 INFO [a1b2c3...] server.services.customer_service: 客服消息收到 ...`，
  中文不乱码、不再“哑火”。
- 从日志中按 trace_id 即可串起一轮对话的「消息进入 → 混合检索 → LLM 生成 → 回答落库 / 转人工」；
  启动重建、CLI 等无请求场景的 trace_id 显示为 `-`。
- 级别默认 INFO，可用环境变量 `LOG_LEVEL=DEBUG`（或 `WARNING`）调整。

## 🛠️ 常用命令

```bash
# 全量测试（server/pyproject.toml 已配 pythonpath=["src"]）
cd server && .venv/bin/python -m pytest

# RAG CLI（不经 API 直接跑核心）
cd server
.venv/bin/python -m server.cli ingest data/docs      # 入库
.venv/bin/python -m server.cli ask "什么是 AI Agent？"
.venv/bin/python -m server.cli eval                   # 离线 Recall@K / MRR
.venv/bin/python -m server.cli cs-eval                # 客服任务离线评测（意图/工具/转人工/越权/槽位）
.venv/bin/python -m server.cli cs-eval --fail-under 0.9   # 阈值门禁（CI 用，未达标退出码 1）
.venv/bin/python -m server.cli cs-eval --json              # 输出 JSON 报告（含逐题明细）
.venv/bin/python -m server.cli cs-eval --with-retrieval    # 联动检索评测 Recall@K/MRR（需向量库）
.venv/bin/python -m server.cli cs-redteam --fail-under 0.9 # 红队安全评测门禁（注入/越权/对抗样本，CI 用）
.venv/bin/python -m server.cli doc-list               # 列出库内文档

# 前端
cd web && pnpm lint && pnpm build

# 部署（CloudBase 云托管 · Git 仓库自动触发）
# 控制台：绑定 GitHub 仓库 → 服务选择 master 分支并开启自动部署 → Dockerfile 选仓库根目录 Dockerfile
# 密钥与数据库连接（DEEPSEEK_API_KEY / MYSQL_* / EMBED_* 等）在云托管环境变量中配置，不写入镜像
```

## 🗺️ 迭代路线（Roadmap）

> 当前进度：Phase 0 → Phase 2 安全/正确性（P0）已全部收口，GitHub Actions CI 全绿（红队安全评测门禁 `cs-redteam` 已纳入）。完整规划见 [docs/customer-service-plan.md](docs/customer-service-plan.md) §14「版本里程碑总览」。

| 版本 | 范围 | 状态 |
| --- | --- | --- |
| **v0.1** | Phase 0–1 客服 MVP：三层路由、订单/售后/投诉安全分流、SSE、人工接管、MySQL 持久化、checkpoint 恢复 | ✅ 已交付 |
| **v0.2** | Phase 2 生产化与安全（P0 收口）：JWT、评测体系、PII 脱敏、多意图识别、限流/熔断/审计（MySQL）、红队评测 | ✅ 已交付 |
| **v0.3** | P1 业务真实化：真实订单/物流只读接口、JWT/OAuth 生产化、MySQL 表扩展 | ⬜ 下一步 |
| **v0.4** | Phase 3 业务闭环：受控写操作、售后流程 + 工单、坐席工作台增强 | ⬜ 待启动 |
| **持续** | 红队语料与防线演进（谐音/编码/多轮注入、LLM 注入检测） | 🔁 已入 CI |
