# RAG 产品 · FastAPI 单容器部署镜像（CloudBase 云托管 / 任意容器平台）
# Python 3.12：与本地开发环境保持统一（本地 rag/.venv 仍依赖 fastembed→onnxruntime）。
# 部署镜像内不做本地向量推理：embedding 走远程 /embeddings（EMBED_BASE_URL），镜像不含模型。
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统依赖：curl 供健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 依赖（固定版本清单，见 deploy/requirements.txt）
COPY deploy/requirements.txt ./deploy/requirements.txt
RUN pip install --no-cache-dir -r deploy/requirements.txt

# 应用代码 + 密钥配置 + 前端产物
COPY app ./app
COPY rag ./rag
COPY web/dist ./web/dist
COPY rag/.env ./rag/.env

ENV PORT=80
EXPOSE 80

# 健康检查：云托管探活用
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-80}/api/v1/health" || exit 1

# 必须 --workers 1：Qdrant local 单进程锁
CMD ["sh", "-c", "cd /app && python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80} --workers 1"]
