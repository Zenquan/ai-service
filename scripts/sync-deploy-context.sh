#!/usr/bin/env bash
# 把仓库白名单内容同步到 .ragapp-deploy-context/（CloudBase 云托管部署打包目录）
#
# 为什么需要它：
#   CloudBase 云托管 deploy 会把 targetPath 整个目录打包上传，且没有 ignore 能力。
#   仓库根目录包含 server/.venv(约 1G)、server/data(1.2G)、web/node_modules(455M)、
#   server/qdrant_data 等大目录，直接打包会上传数 GB。因此部署走一个轻量快照目录，
#   只放镜像构建需要的东西。快照目录被 .gitignore 忽略，不进入版本控制，
#   必须用本脚本从仓库同步。
#
# 用法（每次部署前执行）：
#   bash scripts/sync-deploy-context.sh
#
# 同步白名单：
#   server/      后端源码 + pyproject + .env（排除 .venv/data/qdrant_data/tests/__pycache__）
#   web/dist/    前端构建产物（须先 npm run build）
#   Dockerfile   镜像定义
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAP="$ROOT/.ragapp-deploy-context"

mkdir -p "$SNAP"

# 1. server/：后端源码 + 密钥配置（.env 不进 git，但镜像构建需要 COPY server/.env）
#    排除运行数据与缓存：.venv / data / qdrant_data / tests / __pycache__
rsync -a --delete --delete-excluded \
    --exclude '.venv' \
    --exclude 'data' \
    --exclude 'qdrant_data' \
    --exclude 'tests' \
    --exclude '.pytest_cache' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$ROOT/server/" "$SNAP/server/"

# 2. web/dist：前端构建产物
if [ -d "$ROOT/web/dist" ]; then
    rsync -a --delete "$ROOT/web/dist/" "$SNAP/web/dist/"
else
    echo "警告: web/dist 不存在，请先构建前端（cd web && npm run build）" >&2
fi

# 3. Dockerfile
cp "$ROOT/Dockerfile" "$SNAP/Dockerfile"

# 4. 清理历史快照残留（重构前的 app/rag/langgraph/deploy 目录结构已废弃）
rm -rf "$SNAP/app" "$SNAP/rag" "$SNAP/langgraph" "$SNAP/deploy"

echo "✅ 已同步仓库 → $SNAP"
echo "   部署时使用 targetPath=$SNAP"
