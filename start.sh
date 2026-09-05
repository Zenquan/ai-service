#!/usr/bin/env bash
set -uo pipefail

# 一键启动 RAG 产品：FastAPI 后端（:8000）+ Vite 前端（:5173）
# 用法：./start.sh
# 可通过环境变量覆盖端口：BACKEND_PORT=8010 FRONTEND_PORT=5174 ./start.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

echo "项目目录：$ROOT_DIR"

# ── 后端 Python（必须 3.12，见 README「为什么锁 3.12」）──
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/rag/.venv/bin/python3.12}"
if [[ ! -x "$PYTHON_BIN" && -x "$ROOT_DIR/rag/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/rag/.venv/bin/python"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "❌ 未找到 rag/.venv 中的 Python 3.12。请先创建后端虚拟环境："
  echo "   python3.12 -m venv rag/.venv"
  echo "   rag/.venv/bin/pip install -r rag/requirements.txt"
  exit 1
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$PY_VERSION" != "3.12" ]]; then
  echo "⚠️  当前后端 Python 为 $PY_VERSION（推荐 3.12）。fastembed/onnxruntime 在 3.13/3.14 可能无法安装或运行。"
fi

# ── 密钥配置 ──
if [[ ! -f "$ROOT_DIR/rag/.env" ]]; then
  echo "⚠️  未找到 rag/.env，将使用默认配置（DeepSeek API Key 可能缺失）。"
  echo "   可复制示例：cp rag/.env.example rag/.env"
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
FASTEMBED_CACHE_PATH="$ROOT_DIR/rag/data/fastembed_cache"
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
(cd "$ROOT_DIR" && exec "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT") &
BACKEND_PID=$!

echo "🎨 启动前端：http://localhost:$FRONTEND_PORT"
(cd "$ROOT_DIR/web" && exec pnpm exec vite --host 127.0.0.1 --port "$FRONTEND_PORT") &
FRONTEND_PID=$!

echo "按 Ctrl+C 停止所有服务。"
wait
