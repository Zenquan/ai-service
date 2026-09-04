"""统一配置：切块参数 / 向量维度 / 检索 / 集合名。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# HF 镜像（国内必需）：fastembed 首次会自动下载 bge-small-zh 模型（约 150MB），
# 直连 huggingface.co 常超时；设为 hf-mirror.com 走国内镜像（已实测可达）。
# 已在代码里设置，无需每次命令行 export。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 关键：禁用 HF 新版 xet 存储后端（cas-server.xethub.hf.co 国内 401/超时）。
# 强制走普通 HTTP 下载 → 才会走上面的 HF_ENDPOINT 镜像。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# 切块：CHUNK_SIZE 是段落聚合目标，不是字符硬上限；overlap 以完整段落为单位。
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "0"))

# Embedding（FastEmbed 本地，bge-large-en-v1.5 → 1024 维；文档中英混合选英文强模型）
EMBED_MODEL = "BAAI/bge-large-en-v1.5"
EMBED_DIM = 1024       # 若换模型需同步改（且必须清库重建，向量维度变了旧向量失效）

# Qdrant：默认 local 免 Docker；生产设 QDRANT_URL 即可切远端 server（API 完全一致）
#   例：QDRANT_URL=http://localhost:6333 或 https://xxxx.cloud.qdrant.io，需鉴权时配 QDRANT_API_KEY
#   注意：远端 server 支持多进程并发，local（path=）同一目录只允许一个进程
QDRANT_URL = os.getenv("QDRANT_URL", "")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
QDRANT_PATH = Path(__file__).parent / "qdrant_data"
COLLECTION = "rag_minimal"

# 量化（内存优化，可选）：留空 = 不量化；int8 = 标量量化（内存省 ~3/4，召回精度略降）
#   仅对"新建集合"生效——已存在的集合不会重建，需 delete_collection 后重新 ingest
#   适用场景：向量量大、内存吃紧（如 bge-large 1024 维 × 千万级）
QDRANT_QUANTIZATION = os.getenv("QDRANT_QUANTIZATION", "").strip().lower()  # "" | "int8"

# 检索
TOP_K = 5
RERANK_TOP_K = 3       # rerank 后取前 3 注入
MIXED_DEFAULT = True    # 混合检索默认开：关键词+向量 双路召回 RRF 融合（术语精确命中 + 语义扩展兼顾）
RETRIEVAL_CANDIDATE_MULTIPLIER = int(os.getenv("RAG_CANDIDATE_MULTIPLIER", "4"))
RRF_K = int(os.getenv("RAG_RRF_K", "60"))
BM25_K1 = float(os.getenv("RAG_BM25_K1", "1.2"))
BM25_B = float(os.getenv("RAG_BM25_B", "0.75"))

# LLM（OpenAI 兼容）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Rerank（硅基流动 bge-reranker-v2-m3；默认开启——有 key 就精排）
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
RERANK_DEFAULT = True   # ask/retrieve 默认走 rerank；无 key 时自动跳过不报错
RERANK_SCORE_THRESHOLD = float(os.getenv("RERANK_SCORE_THRESHOLD", "0"))

# MinerU v2 SDK：空 token = Flash 免费模式；填 token = 标准模式（https://mineru.net 免费申请）
MINERU_TOKEN = os.getenv("MINERU_TOKEN", "")

# 文档目录 / 解析缓存（P0：MinerU 云解析结果落盘缓存，文本未变不重复调云）
DATA_DIR = Path(__file__).parent / "data" / "docs"
PARSE_CACHE_DIR = Path(__file__).parent / "data" / "_parse_cache"
