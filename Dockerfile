# RAG 客服系统 · 单容器部署镜像（CloudBase 云托管 / 任意容器平台）
# Python 3.12：与本地开发保持统一（fastembed→onnxruntime 在 3.13/3.14 无 macOS x86_64 wheel）。
# 部署镜像内不做本地向量推理：embedding 走远程 /embeddings（EMBED_BASE_URL），
# 依赖走 server/pyproject.toml 且不装 [local] extra（fastembed 不进镜像）。
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统依赖：curl 供健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 依赖：直接安装 server 包（pyproject.toml 是依赖唯一来源；云端不装 fastembed）
COPY server ./server
RUN pip install --no-cache-dir ./server

# 密钥配置 + 前端产物
COPY server/.env ./server/.env
COPY web/dist ./web/dist

ENV PORT=80
EXPOSE 80

# 健康检查：云托管探活用
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-80}/api/v1/health" || exit 1

# 必须 --workers 1：Qdrant local 单进程锁
CMD ["sh", "-c", "cd /app/server && python -m uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-80} --workers 1"]
