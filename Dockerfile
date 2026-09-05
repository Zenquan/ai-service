# RAG 客服系统 · 单容器部署镜像（CloudBase 云托管 Git 仓库自动部署 / 任意容器平台）
#
# CloudBase「通过 Git 仓库部署」会在 master push 后拉取仓库源码，
# 以仓库根目录为 Docker 构建上下文执行本文件，因此：
#   - 前端必须在镜像内构建（web/dist 不入库、不依赖本地预构建产物）
#   - 密钥由 server/.env 提供（若构建上下文含该文件则复制进镜像；
#     CloudBase 环境变量优先级更高，可覆盖 .env 中的同名配置）

# ── 阶段 1：构建前端（Vite + React + TS）──────────────────────────────
FROM node:22-alpine AS frontend

WORKDIR /app/web

# Corepack + 固定 pnpm 版本（与本地 11.x 保持一致）
RUN corepack enable && corepack prepare pnpm@11.7.0 --activate

# 先复制依赖清单，命中缓存时跳过 install
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# 再复制源码并产出 web/dist
COPY web/ ./
RUN pnpm build

# ── 阶段 2：运行镜像（Python 3.12）────────────────────────────────────
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

# 后端源码：直接安装 server 包（pyproject.toml 是依赖唯一来源；云端不装 fastembed）
COPY server ./server
RUN pip install --no-cache-dir ./server

# 密钥配置：server/.env 保留复制（本地/CI 构建目录需包含该文件）
COPY server/.env ./server/.env

# 前端产物：从构建阶段复制
COPY --from=frontend /app/web/dist ./web/dist

ENV PORT=80
EXPOSE 80

# 健康检查：云托管探活用
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-80}/api/v1/health" || exit 1

# 必须 --workers 1：Qdrant local 单进程锁
CMD ["sh", "-c", "cd /app/server && exec python -m uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-80} --workers 1"]
