# 架构文档：RAG 知识问答产品

> 本文档从**分层架构 → 端到端时序 → 关键设计决策**三个层次解释这个产品是怎么搭起来的，每个决策都给出「为什么这么做、不这么做会怎样」。

---

## 1. 分层架构

产品分四层，职责严格分离，每一层都可以独立替换：

```
┌── 表现层（web/）───────────────────────────────────────────────┐
│   React 19 + TS + Ant Design X（x 组件库）/ antd（表单、列表）   │
│   · 对话：Bubble.List + Sender + Welcome + Prompts              │
│   · 回答渲染：XMarkdown（正文）+ Sources（引用卡片）              │
│   · 知识库：Upload / List / Statistic / Popconfirm              │
│   数据流：lib/api.ts（fetch 薄封装）→ 非流式 JSON               │
├── 接口层（app/api/）───────────────────────────────────────────┤
│   FastAPI Router × 4：health / docs / ingest / ask              │
│   · 只做 HTTP 语义：参数校验（Pydantic）、错误转 HTTPException、 │
│     文件白名单与防穿越                                          │
├── 服务融合层（app/services/rag.py）────────────────────────────│
│   · sys.path 注入 rag 目录（零改动接入）                         │
│   · threading.Lock 全局锁：Qdrant local 单进程 + Embedding 懒加载 │
│   · 冻结的领域 API：ingest_files / ask / docs_list / health      │
├── 领域核心层（rag/，独立仓库）───────────────────────────────── │
│   ingest：MinerU→清洗→切块→向量→入库                             │
│   ask：   混合检索→rerank→生成→引用校验                          │
│   纯函数可单测（chunker/citations 零外部依赖）                   │
└──────────────────────────────────────────────────────────────────┘
```

**为什么这么分层**：rag 层是纯 Python 的「领域核心」，不依赖 FastAPI —— 这意味着它可以在 CLI、单测、服务三种环境复用同一个 `ask()` 函数，接口层只是薄壳。这也是面试时「架构分层、职责单一」的直接证据。

---

## 2. 端到端时序

### 2.1 入库链路（POST /api/v1/ingest）

```
浏览器 KnowledgePanel
  │  FormData(files[])  →  /api/v1/ingest
  ▼
app/api/ingest.py
  │  ① 校验：扩展名白名单（pdf/txt/md/docx/html/图片）+ basename 防穿越
  │  ② 落盘：rag/data/uploads/<filename>
  ▼
app/services/rag.py.ingest_files(file_paths)
  │  ③ 持 rag_lock，逐文件调 rag_main.ingest(path)  （单文件模式）
  ▼
rag/main.py.ingest(path)
  │  ④ rag/ingest.py.parse_document：
  │      .txt/.md 直通 → sha256 缓存命中 → MinerU 云（有 token extract / 无 token flash）→ markitdown → 二进制兜底
  │  ⑤ rag/chunker.py：clean_text（去页眉/URL/HTML/高频短行）→ chunk_paragraphs（段落优先 + overlap）
  │  ⑥ rag/embed_store.py：FastEmbed(bge-large-en-v1.5, 1024维) → Qdrant upsert
  │     点 id = abs(hash(f"{doc}:{seq}")) % 2^63（稳定，重跑幂等覆盖）
  ▼
响应 { total, docs:[{name,chunks,inserted}], skipped:[], errors:[] }
```

### 2.2 问答链路（POST /api/v1/ask）

```
浏览器 ChatPanel（useXChat）
  │  { query } →  /api/v1/ask
  ▼
app/services/rag.py.ask(query, use_rerank, top_k)
  ▼
rag/main.py.ask(query)
  │  ① retrieve()  —— 三层检索
  │     a. 向量：query 向量化 → Qdrant.query_points(limit=top_k*2)     # 语义召回，放宽
  │     b. 关键词：_tokenize(中文2-gram+英文词) → 全量 chunk 计数打分    # 术语精确
  │     c. RRF 融合：score = Σ 1/(k+rank)，k=60，按 (doc,seq) 去重      # 双路互补
  │     d. rerank（默认开）：硅基流动 bge-reranker-v2-m3 → top 3（失败静默回退）
  │  ② generate()：
  │     素材编号注入 [来源1]..[来源N]（含出处《doc》）
  │     system 提示：只准用资料、必须 [来源N]、未覆盖写明"资料中未提及"
  │     DeepSeek temperature=0.3, timeout=60s, max_retries=2
  │  ③ verify_citations()：正则提取 [来源N] → 越界(>N 或 <=0) = 模型编造 → invalid
  ▼
响应 { query, answer, materials[], citations[], citation_valid, material_count, error }
  ▼
浏览器 AnswerView：XMarkdown 渲染正文 + Tag 徽标（引用校验结果）+ Sources 卡片（依据原文）
```

---

## 3. 关键设计决策（面试逐条可讲）

### D1. rag 零改动接入产品（不 fork、不重写）

**做法**：`app/services/rag.py` 在导入时 `sys.path.insert(0, rag 目录)`，然后 `import main as rag_main`、`from embed_store import delete_doc, list_docs`。

**为什么**：rag 是独立 git 仓库（后来并入 fastapi-app 统一管理、30 个单测、CLI 全保留）。改成包（加 `__init__.py`、相对导入）会破坏它的独立可运行性。sys.path 注入让它「既能独立 CLI 跑，又能当库用」。

**坑与规避**：模块名冲突——rag 入口叫 `main`，FastAPI 应用叫 `app.main`，两个 `main` 不会撞（Python 按完整模块名区分）。CLI 的 `python main.py` 走 `__main__`，与 `import main` 不冲突。

### D2. Qdrant local 免 Docker + 单进程锁

**做法**：`QdrantClient(path=…)` local 模式，向量库就是一个目录（`rag/qdrant_data/`）。代码与生产远端（`:memory:` 或 http://...:6333）同一套 API，切换只改构造参数。

**单进程锁**：Qdrant local 同一目录同一时刻只允许一个进程访问（`AlreadyLocked`）。两个层面约束：
1. `rag_lock = threading.Lock()` 串行化进程内所有库操作（含 FastEmbed 首次懒加载的并发保护）
2. 启动命令要求 `--workers 1`（README 与 main.py docstring 都写明）

### D3. 混合检索：关键词 + 向量双路召回，RRF 融合

**为什么不是纯向量**：纯向量对「精确术语/编号」（如 JFT-300M、DeepFace）容易漏召回；纯关键词对「语义等价」（土豆↔马铃薯）失效。双路召回 + RRF 让两个召回器互补。

**RRF（Reciprocal Rank Fusion）**：对每条结果按排名倒数加权 `score += 1/(60 + rank)`，**不需要归一化分数**——不同召回器的分数量纲不同，直接相加会偏袒某一方；按排名融合天然鲁棒、零调参。

**精排交给 rerank**：融合结果再送 bge-reranker（cross-encoder）做最终排序，取 top 3 注入 Prompt。rerank 失败**静默回退**到融合结果——检索链路绝不因加分项崩溃。

### D4. 防幻觉闭环（检索→生成→校验→可见）

| 环节 | 机制 |
| --- | --- |
| 生成前 | 素材编号注入 `[来源1]`，system 提示「只能用资料、必须标注、不知道就说不提及」 |
| 生成后 | `verify_citations()` 正则提取引用序号，越界（>素材数或 ≤0）= 模型编造，标记 invalid |
| 产品里 | 前端 Tag 徽标显示「引用 N · 校验通过/含越界引用」+ Sources 卡片可展开素材原文 |

**为什么校验拦截而不是直接纠错**：模型「一本正经编造」时无法从生成侧彻底杜绝，但**程序校验是确定性**的——任何越界引用都能被抓住并明示。诚实展示比假装可信更有价值。

### D5. 解析三级降级 + sha256 缓存

**链路**：`.txt/.md 直通 → MinerU 云（有 token extract 标准 / 无 token flash 免费）→ markitdown 本地轻量 → 二进制强解码兜底`。每一步失败都 `try/except` 降级，不中断整批入库。

**缓存**：以**文件内容 sha256** 为 key（不是路径/时间——改名/复制仍命中），结果落 `data/_parse_cache/<hash>.txt`。重复 ingest（含换 chunk_size 跑实验）不再调云，省 API 费。**配置了 MINERU_TOKEN 才能用标准 extract；无 token 会走 Flash 免费模式**（README/.env.example 已注明）。

### D6. 上传安全：白名单 + 防路径穿越

- `Path(up.filename).name` 只取文件名（`../evil.pdf` → `evil.pdf`）
- 扩展名白名单与 rag 的 `SUPPORTED_EXTS` 严格一致
- 落盘目录固定 `rag/data/uploads/`，已被 rag/.gitignore 排除

### D7. 前端 DefaultChatProvider 透传（非流式对接）

后端 `/ask` 一次返回完整 JSON，不是 OpenAI 流式格式。方案：
- `DefaultChatProvider<ChatMessage, AskInput, AskOutput>` 透传原始响应
- `transformParams` 固定请求体 `{ query }`
- `transformMessage` 把 `AskOutput` 组装成 `{ text, materials, citations, citationValid }`
- `Bubble.List` 的 `role.contentRender` 用 `XMarkdown` + `Sources` 自定义渲染

**为什么不强上流式/SSE**：核心链路当前以「质量 + 可溯源」优先，非流式实现在不确定网络下更稳；后续要流式只需给 ask 加 SSE 分支，Provider 层 `transformMessage` 本来就是流式/整包两用的（x-sdk 设计）。

### D8. 模块名与命名空间纪律

- 后端应用 `app.main` vs rag 核心 `main`：不冲突
- rag 内部扁平导入（`import config`）依赖「rag 目录在 sys.path 首位」——`sys.path.insert(0, …)` 保证
- 前端类型：`ChatMessage`/`AskInput`/`AskOutput` 全显式，`AskOutput` 类型 = 后端响应体逐字段对应

### D9. 迭代纪律：git 小步提交

- rag 核心 7 个 conventional commits（chore 骨架 → feat 每层 → test）
- fastapi-app 同样按层提交（骨架 → 后端 → 前端 → 文档）
- `.gitignore` 双保险：rag 独立忽略自己，fastapi-app 忽略 `rag/`（不产生 gitlink 嵌套）

---

## 4. 数据模型

**Qdrant point**（一条 chunk）：

```jsonc
{
  "id": 7195160736229512000,          // abs(hash(f"{doc}:{seq}")) % 2^63，稳定幂等
  "vector": [0.0123, ...1024 维],     // bge-large-en-v1.5
  "payload": {
    "doc": "rag-test-pdfs/DeepFace-ICCV2017.pdf",  // 文档名（含相对路径）
    "seq": 12,                          // chunk 在文档内顺序（可定位原文）
    "text": "……",                       // chunk 文本
    "para_range": [3, 5]                // 覆盖段落区间（调试/溯源）
  }
}
```

**文档级操作**（增量管理，不重建索引）：
- `list_docs`：scroll 全量按 payload.doc 聚合 chunk 数
- `delete_doc`：`scroll_filter=Filter(must=[doc == 目标])` 取 id 批量 delete
- ⚠️ qdrant-client 1.9 参数是 `scroll_filter`（非 query_filter）+ limit 必填正整数（实测坑）

## 5. 依赖与环境约束

| 项 | 值 | 为什么 |
| --- | --- | --- |
| Python | **3.10–3.12**（项目用 3.12） | 3.13/3.14 无 fastembed 依赖 wheel（onnxruntime/tokenizers）——AI 生态对新版本滞后 |
| HF 下载 | `HF_ENDPOINT=hf-mirror.com` + `HF_HUB_DISABLE_XET=1` | 国内直连 huggingface.co 超时；xet 后端不走镜像 401 |
| Qdrant | local（path=） | 免 Docker、API 同生产；代价是单进程 |
| 密钥 | `rag/.env`：DEEPSEEK_API_KEY 必填；MINERU_TOKEN / SILICONFLOW_API_KEY 可选 | 前端绝不能碰密钥（XRequest 安全指南：浏览器禁带 Authorization） |

---

## 6. 演进路线（TODO）

- [ ] ask 流式（SSE）：`XRequest` 流式解析 + `transformMessage` 增量，`Bubble typing` 效果
- [ ] 会话历史：`useXConversations` 多会话 + 服务端持久化
- [ ] 文档重解析/版本更新；上传进度/取消
- [ ] 检索评估面板：eval 用例可视化（命中率/耗时）
- [ ] Docker 化：Qdrant 远端 + uvicorn + nginx 静态前端
- [ ] 权限：多租户知识库隔离（payload 加 namespace 过滤）