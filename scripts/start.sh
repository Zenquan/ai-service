#!/usr/bin/env bash
set -uo pipefail

# 一键启动 AI 智能客服产品：FastAPI 后端（:8000）+ Vite 前端（:5173）
# 用法：./scripts/start.sh
# 可通过环境变量覆盖端口：BACKEND_PORT=8010 FRONTEND_PORT=5174 ./scripts/start.sh

# 脚本位于 scripts/ 下，项目根为其上一级
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT_DIR/server"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

echo "项目目录：$ROOT_DIR"

# ── 后端 Python（必须 3.12，见 README「为什么锁 3.12」）──
PYTHON_BIN="${PYTHON_BIN:-$SERVER_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "❌ 未找到 server/.venv 中的 Python 3.12。请先创建后端虚拟环境："
  echo "   cd server && uv venv --python 3.12 .venv"
  echo "   uv pip install --python .venv/bin/python -e '.[local]' --group dev"
  exit 1
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$PY_VERSION" != "3.12" ]]; then
  echo "⚠️  当前后端 Python 为 $PY_VERSION（推荐 3.12）。fastembed/onnxruntime 在 3.13/3.14 可能无法安装或运行。"
fi

# ── 密钥配置 ──
if [[ ! -f "$SERVER_DIR/.env" ]]; then
  echo "⚠️  未找到 server/.env，将使用默认配置（DeepSeek API Key 可能缺失）。"
  echo "   可复制示例：cp server/.env.example server/.env"
fi

# ── 会话持久化（云端 MySQL 公网，本地与线上共享同一份会话数据）──
# 连接信息从仓库根目录 .env.local 读取（已被 .gitignore 忽略，不进版本控制）。
# 首次使用请创建 .env.local，内容见 .env.local.example；未配置时会话回退内存存储（重启即丢）。
if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.local"
  set +a
fi
if [[ -z "${MYSQL_HOST:-}" ]]; then
  echo "⚠️  未配置 .env.local（MYSQL_HOST），会话将使用内存存储（重启即丢）。"
  echo "   如需与线上共享会话数据：cp .env.local.example .env.local 并填写 MySQL 连接信息。"
fi

# ── 前端依赖（pnpm + node_modules）──
if ! command -v pnpm >/dev/null 2>&1; then
  echo "❌ 未找到 pnpm，请先安装：npm install -g pnpm"
  exit 1
fi

if [[ ! -d "$ROOT_DIR/web/node_modules" ]]; then
echo "📦 首次启动：安装前端依赖..."
  (cd "$ROOT_DIR/web" && pnpm install) || exit 1
fi

# ── FastEmbed 模型缓存目录（避免首次冷启动下载模型）──
FASTEMBED_CACHE_PATH="$SERVER_DIR/data/fastembed_cache"
mkdir -p "$FASTEMBED_CACHE_PATH"
export FASTEMBED_CACHE_PATH

# ── 退出清理 ──
cleanup() {
  echo ""
  echo "🛑 正在停止服务..."
  [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null
  [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "🚀 启动后端：http://127.0.0.1:$BACKEND_PORT"
# 必须从 server/ 目录启动：config.SERVER_ROOT 默认取 cwd（.env/data/qdrant_data 都在 server/）
(cd "$SERVER_DIR" && exec "$PYTHON_BIN" -m uvicorn server.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT") &
BACKEND_PID=$!

echo "🎨 启动前端：http://localhost:$FRONTEND_PORT"
(cd "$ROOT_DIR/web" && exec pnpm exec vite --host 127.0.0.1 --port "$FRONTEND_PORT") &
FRONTEND_PID=$!

echo "按 Ctrl+C 停止所有服务。"
wait
