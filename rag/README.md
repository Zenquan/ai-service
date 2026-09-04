# rag · 最小完整 RAG 实践

MinerU 云解析（mineru-open-sdk）+ FastEmbed 本地向量 + Qdrant（local 免 Docker）+ DeepSeek（生成）的最小完整 RAG。

**检索链路**：结构感知切块 → 关键词 BM25 + 向量相似双路召回 → RRF 融合 → bge-reranker 精排/阈值过滤 → 引用校验。

## 架构

```
data/docs/**/*.pdf|md|txt（ingest 递归子目录）
      │
      ▼
main.py: ingest →  解析(MinerU 云→markitdown→兜底) → 清洗 → 切块 → FastEmbed 向量 → Qdrant 入库
                     │
                     ▼
main.py: ask   →  query 向量化 + 标题加权 BM25 双路召回 → RRF
      →（可选 rerank/阈值）→ 注入 Prompt → DeepSeek 生成 [来源N] 回答 → 引用校验
```

## 快速开始

```bash
# 1. 安装依赖（Python 3.12，全局统一版本，见根 README「为什么锁 3.12」：fastembed→onnxruntime 无 3.13/3.14 wheel）
cd rag
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置 key（必填 DEEPSEEK；PDF 强烈建议配 MINERU_TOKEN）
cp .env.example .env   # DEEPSEEK_API_KEY + MINERU_TOKEN

# 3. 准备资料（递归支持子目录）
#    把 PDF/MD/TXT 放进 data/docs/（或子目录）

# 4. 入库
python main.py ingest data/docs

# 5. 问答
python main.py ask "你的测试问题"

# 6. 端到端一步到位（ingest + ask）
python main.py run data/docs "你的测试问题"

# 7. 评估（本地标注集 Recall@K / MRR，默认不调用外部 rerank）
python main.py eval

# 8. 比较不同结构切块目标（每轮清库重建，需串行执行）
python exp_chunk_size.py

# 9. 文档增量管理（P1）
python main.py doc-list                                        # 列出库内文档及 chunk 数
python main.py doc-delete "rag-test-pdfs/DeepFace-ICCV2017.pdf" # 删除单个文档（不重建）
```

## 可编程调用（main.py 导出纯函数）

```python
from main import ingest, ask, run

stats = ingest("data/docs")          # → {"total": N, "docs": [...], "skipped": [...]}
ans = ask("钱大妈的模式是什么？")     # → {"answer", "citations", "citation_valid", "materials", ...}
full = run("data/docs", "你的问题")  # → {"ingest": stats, "ask": ans}
```

## 设计要点

- **免 Docker**：Qdrant 默认 local 模式（`path=`），代码与生产远端同一套 API
- **可平滑切远端**：`.env` 配 `QDRANT_URL`（+ `QDRANT_API_KEY`）即连 Qdrant server，支持多进程并发；不配则保持 local
- **可选量化**：`.env` 设 `QDRANT_QUANTIZATION=int8` 建集合时启用标量量化（内存省 ~3/4，精度略降；仅对新建集合生效，需清库重建）
- **降级链**：MinerU 不可用 → markitdown → 纯文本兜底，核心链路不被重依赖阻塞（每步打印解析路径）
- **解析缓存**（P0）：MinerU 云解析结果按文件内容 sha256 落盘缓存，重复 ingest 不重复调云（省 API 费 + 快）
- **引用闭环**：回答强制 [来源N]，程序校验引用序号合法性（防幻觉）
- **检索默认 rerank**（P0）：向量召回 → 硅基流动 bge-reranker 精排（默认开，无 key 自动跳过）
- **结构感知切块**：按标题层级和段落边界聚合，`chunk_size` 是目标而非硬上限；chunk 携带 `chapter/title/section/heading_path`
- **混合检索**（P1）：标题加权 BM25 倒排 + 向量双路召回 → **RRF 融合**（术语精确命中 + 语义扩展兼顾），可 `use_mixed=False` 关
- **离线评测**：`eval_cases.json` 支持 `relevant` 精确 chunk 标注或 `relevant_docs` 文档标注，输出 Recall@K / MRR；`RERANK_SCORE_THRESHOLD` 可调重排过滤阈值
- **文档增量管理**（P1）：`doc-list` / `doc-delete <name>` 单个文档增删，不必全量重建
- **健壮性**（P1）：LLM 调用 60s 超时 + 2 次重试；rerank 失败静默回退融合结果
- **检索可替换**：retrieve 独立成层，关键词/向量/混合/rerank 都是换实现不换管线

## 踩坑实录（面试素材，都是真踩过的）

### 1. Python 3.13/3.14 装不了 fastembed（onnxruntime 无 wheel）
- **现象**：`pip install fastembed` 报依赖冲突（`onnxruntime` / `mmh3` 在新版本无预编译 wheel）
- **修复**：用 Python 3.12 重建 venv（fastembed 官方支持 3.8-3.12）
- **要点**：AI/ML 生态对新 Python 版本支持滞后，"先查依赖 wheel 再选 Python 版本"；这也是全项目统一锁 3.12 的原因（见根 README「为什么锁 3.12」）

### 2. huggingface.co 下载超时 + xet 401
- **现象**：fastembed 首次下载模型 `Operation timed out`；换镜像后报 `CAS Client Error: 401 Unauthorized (cas-server.xethub.hf.co)`
- **根因**：HF 新版默认用 **xet 存储后端**下载大文件，xet 不走 `HF_ENDPOINT` 镜像、国内直连 401
- **修复**（config.py 已内置）：
  ```python
  os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")   # 走国内镜像
  os.environ.setdefault("HF_HUB_DISABLE_XET", "1")                 # 禁用 xet，强制走镜像
  ```

### 3. MinerU 包名大坑：mineru ≠ mineru-open-sdk
- **现象**：`from mineru import MinerU` 报 ImportError；PDF 解析降级成乱码
- **根因**：pypi 的 `mineru` 是**本地推理引擎**（3.x，API 是 `mineru -p file.pdf -o out` 命令行/本地模型）；你想要的云 API（`flash_extract`/`extract`）来自 **`mineru-open-sdk`** 包
- **修复**：`pip install mineru-open-sdk==0.2.5`；`.env` 配 `MINERU_TOKEN`（https://mineru.net 免费申请）走标准模式，Flash 模式免费但排队慢
- **要点**：装 Python 包前先确认包名和 API 版本，同名包可能完全不同

### 4. Qdrant local 模式单进程锁
- **现象**：同时跑两个 ingest 报 `AlreadyLocked / Storage folder already accessed`
- **根因**：Qdrant local（`path=`）同一目录同一时间只允许一个进程访问
- **修复**：命令串行执行；`get_client()` 加了中文友好提示；要并发在 `.env` 设 `QDRANT_URL`（如 `http://localhost:6333`）切 Qdrant server/Docker——代码同一套 API，无需改代码

### 5. 解析器修好了，`ask` 还是乱码/查不到
- **现象**：修好 mineru 后问 PDF 仍是乱码
- **根因**：**Qdrant 里存的是历史乱码 chunk**——修解析器 ≠ 数据自动更新，`ask` 读的永远是库里已有的数据
- **修复**：先 `delete_collection` 清库重建，再重新 ingest
- **要点**：数据管线修 bug 后必须"清库→重入库"，索引陈旧是隐藏坑

### 6. ingest 不递归子目录
- **现象**：`ingest data/docs` 只入库了 md，`rag-test-pdfs/` 里的 PDF 没进去
- **修复**：`iterdir()`（单层）→ `rglob("*")`（递归），文档名带相对路径避免同名覆盖
- **要点**：批量入库要明确"是否递归"、"文件名是否全局唯一"

### 7. FastEmbed 文本预处理会额外拉 clip 模型
- **现象**：缓存里有 `models--openai--clip-vit-large-patch14`（1.4MB 残缺）
- **说明**：fastembed 部分模型首载会拉 clip 做文本前处理，和主 embedding 模型分开缓存；禁用 xet 后从镜像正常拉取

### 8. 换 embedding 模型必须清库重建
- **现象**：把 `EMBED_MODEL` 从 bge-small-zh（512 维）换成 bge-large-en（1024 维）后，检索报维度错误/结果为空
- **根因**：**向量维度变了，旧 collection 里 512 维的向量与新的 1024 维查询不匹配**——Qdrant 集合的 vectors_config 在建库时固化，不随模型变
- **修复**：改模型后必须 `delete_collection` 重建 + 重新 ingest
- **要点**：embedding 模型 = 数据 Schema 的一部分，换模型是"破坏性变更"，不是配置热更新

### 9. 评测指标不敏感：chunk_size 实验 4 组全 100%
- **现象**：初版实验只判断 topK 拼接文本是否包含关键词，无法区分切块粒度
- **根因**：整体命中率没有反映相关结果的位置，也没有衡量多个标注项的覆盖率
- **修复**：`eval_cases.json` 使用 `relevant` 或 `relevant_docs` 标注，`main.evaluate()` 输出 Recall@K、MRR 和逐题首个相关排名
- **要点**：评测集要先能区分好坏，再用 `exp_chunk_size.py` 和 rerank 阈值进行调优

## 验收清单（P0 三项 v2 版）

- [x] 解析缓存：`data/_parse_cache/` 已生成 4 个缓存文件（md 直通 + 3 个 PDF 云解析），重复 ingest 不调云
- [x] embedding 升级：`BAAI/bge-large-en-v1.5`（1024 维），英文论文检索质量提升（JFT/ResNet 用例精准命中文档）
- [x] rerank 默认开：`RERANK_DEFAULT=True`，有 `SILICONFLOW_API_KEY` 自动精排，`--no-rerank` 可关
- [x] 评估升级：7 个本地标注用例输出 Recall@1/3/5、MRR，关键词仅作为辅助校验

## 优化记录（v2 · P0 生产级三项）

| 优先级 | 项 | 做了什么 | 收益 |
|--------|-----|---------|------|
| P0 | **解析缓存** | `ingest.py` 解析结果按内容 sha256 落盘 `data/_parse_cache/`，重复 ingest（含调参实验）命中缓存，不重复调 MinerU 云 | 省 API 费 + 实验迭代快几个数量级 |
| P0 | **embedding 升级** | bge-small-zh（512 维，中文）→ bge-large-en-v1.5（1024 维，英文强）——文档中英混合，英文论文是主要检索对象 | 英文论文检索质量大幅提升 |
| P0 | **rerank 默认开** | `retrieve()` 默认走硅基流动 bge-reranker 精排（`RERANK_DEFAULT=True`）；无 key 自动跳过；`--no-rerank` 可关 | 向量召回后精排，topK 更准 |

**评估升级（配套）**：`eval_cases.json` 以 `relevant_docs` 或 `relevant` 作为相关性标注，`main.evaluate()` 计算 Recall@K / MRR；`relevant_keywords` 作为内容覆盖辅助校验。

## 优化记录（v3 · P1 检索 + 工程三项）

| 优先级 | 项 | 做了什么 | 收益 |
|--------|-----|---------|------|
| P1 | **结构感知切块** | `chunker.py` 识别标题层级，按章节聚合完整段落；chunk 保存标题路径并加入 embedding/Prompt | 边界更符合语义，标题上下文可用于检索与引用 |
| P1 | **混合检索** | `retrieve.py`：标题加权 BM25 倒排 + 向量双路召回 → **RRF 融合**（`_rrf_merge`，按排名倒数加权）→ rerank | 术语精确命中（编号/专名）+ 语义扩展（同义/多语言）兼顾，互补召回 |
| P1 | **离线评测** | 自建标注集输出 Recall@K / MRR，`exp_chunk_size.py` 比较结构切块目标，`RERANK_SCORE_THRESHOLD` 控制重排过滤 | 参数调优从“命中/不命中”升级为可比较的排序指标 |
| P1 | **文档增量管理** | `doc-list`（按文档聚合 chunk 数）/ `doc-delete <name>`（按 payload.doc 过滤删除）；ingest 后可增量 upsert，无需全量重建 | 生产可用：增删单文档秒级，不用重建 collection |
| P1 | **健壮性** | LLM 调用 `timeout=60` + `max_retries=2`（openai SDK 指数退避）；rerank 失败 try/except 静默回退 RRF 结果 | 网络抖动不崩链路，rerank 挂了下游仍能答 |

**测试**：单测扩到 14 个（新增 `_tokenize` 中文 2-gram / `_rrf_merge` 并集去重 / 双路命中排前）。

## 常见问题

- **模型下载慢/失败**：`config.py` 已设 `HF_ENDPOINT=hf-mirror.com` + `HF_HUB_DISABLE_XET=1`，重跑会自动走镜像
- **PDF 解析慢/排队**：Flash 免费模式排队；申请 token 填 `.env` 的 `MINERU_TOKEN` 走标准模式
- **问 PDF 内容答不对**：先看 `ask` 的"检索到的素材"是否干净相关（不是乱码/跑题）；再考虑换更强 embedding 或加 rerank
- **MinerU 装不上**：只装 `markitdown` 也能跑（纯文本/轻量 PDF），论文 PDF 需要 MinerU
