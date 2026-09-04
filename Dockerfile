# RAG 产品 · FastAPI 单容器部署镜像（CloudBase 云托管 / 任意容器平台）
# Python 3.12：全局统一版本（见 README「为什么锁 3.12」）——rag 依赖 fastembed→onnxruntime，
# 在 macOS x86_64 + 3.13/3.14 下无预编译 wheel，故部署镜像同样锁 3.12，与本地环境一致。
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # HF 国内镜像（config.py 已默认，此处再兜底），构建期下载 embedding 模型用
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_DISABLE_XET=1 \
    # fastembed 模型缓存路径（构建期预下载，持久在镜像内，避免冷启动下载）
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed

# 系统依赖：gcc/g++ 兜底个别 wheel 源码编译；curl 供健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 依赖（固定版本清单，见 deploy/requirements.txt）
COPY deploy/requirements.txt ./deploy/requirements.txt
RUN pip install --no-cache-dir -r deploy/requirements.txt

# 预下载 embedding 模型（bge-large-en-v1.5，~1.3GB，走 hf-mirror 国内镜像）
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-large-en-v1.5')"

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
