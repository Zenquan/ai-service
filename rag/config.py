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

# 切块
CHUNK_SIZE = 800       # 字符
CHUNK_OVERLAP = 100

# Embedding（FastEmbed 本地，bge-large-en-v1.5 → 1024 维；文档中英混合选英文强模型）
EMBED_MODEL = "BAAI/bge-large-en-v1.5"
EMBED_DIM = 1024       # 若换模型需同步改（且必须清库重建，向量维度变了旧向量失效）

# Qdrant（local 免 Docker；生产可换 :memory: 或远端 http://localhost:6333）
QDRANT_PATH = Path(__file__).parent / "qdrant_data"
COLLECTION = "rag_minimal"

# 检索
TOP_K = 5
RERANK_TOP_K = 3       # rerank 后取前 3 注入
MIXED_DEFAULT = True    # 混合检索默认开：关键词+向量 双路召回 RRF 融合（术语精确命中 + 语义扩展兼顾）

# LLM（OpenAI 兼容）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Rerank（硅基流动 bge-reranker-v2-m3；默认开启——有 key 就精排）
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
RERANK_DEFAULT = True   # ask/retrieve 默认走 rerank；无 key 时自动跳过不报错

# MinerU v2 SDK：空 token = Flash 免费模式；填 token = 标准模式（https://mineru.net 免费申请）
MINERU_TOKEN = os.getenv("MINERU_TOKEN", "")

# 文档目录 / 解析缓存（P0：MinerU 云解析结果落盘缓存，文本未变不重复调云）
DATA_DIR = Path(__file__).parent / "data" / "docs"
PARSE_CACHE_DIR = Path(__file__).parent / "data" / "_parse_cache"