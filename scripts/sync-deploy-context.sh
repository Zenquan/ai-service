#!/usr/bin/env bash
# 把仓库白名单内容同步到 .ragapp-deploy-context/（CloudBase 云托管部署打包目录）
#
# 为什么需要它：
#   CloudBase 云托管 deploy 会把 targetPath 整个目录打包上传，且没有 ignore 能力。
#   仓库根目录包含 rag/.venv(874M)、rag/data(1.2G)、web/node_modules(455M)、rag/qdrant_data(2M)
#   等大目录，直接打包会上传数 GB。因此部署走一个轻量快照目录，只放镜像构建需要的东西。
#   快照目录被 .gitignore 忽略，不进入版本控制，必须用本脚本从仓库同步。
#
# 用法（每次部署前执行）：
#   bash scripts/sync-deploy-context.sh
#
# 同步白名单：
#   app/         FastAPI 应用源码（排除 __pycache__）
#   rag/         RAG 核心源码 + .env（排除 .venv/data/qdrant_data/__pycache__/tests）
#   langgraph/   LangGraph 编排层（排除 __pycache__）
#   web/dist/    前端构建产物（须先 npm run build）
#   deploy/      部署依赖清单
#   Dockerfile   镜像定义
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAP="$ROOT/.ragapp-deploy-context"

mkdir -p "$SNAP"

# 1. app/：应用源码
rsync -a --delete --delete-excluded \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$ROOT/app/" "$SNAP/app/"

# 2. rag/：RAG 核心源码 + 密钥配置（.env 不进 git，但镜像构建需要 COPY rag/.env）
rsync -a --delete --delete-excluded \
    --exclude '.venv' \
    --exclude 'data' \
    --exclude 'qdrant_data' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'tests' \
    "$ROOT/rag/" "$SNAP/rag/"

# 3. langgraph/：LangGraph 编排层（镜像内 COPY langgraph ./langgraph）
rsync -a --delete --delete-excluded \
    --exclude '.venv' \
    --exclude '.pytest_cache' \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$ROOT/langgraph/" "$SNAP/langgraph/"

# 4. web/dist：前端构建产物
if [ -d "$ROOT/web/dist" ]; then
    rsync -a --delete "$ROOT/web/dist/" "$SNAP/web/dist/"
else
    echo "警告: web/dist 不存在，请先构建前端（cd web && npm run build）" >&2
fi

# 5. deploy/ + Dockerfile
rsync -a --delete "$ROOT/deploy/" "$SNAP/deploy/"
cp "$ROOT/Dockerfile" "$SNAP/Dockerfile"

echo "✅ 已同步仓库 → $SNAP"
echo "   部署时使用 targetPath=$SNAP"
