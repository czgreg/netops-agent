#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE_FILE="$ROOT_DIR/.env.example"
URL="${NETOPS_WEB_URL:-http://127.0.0.1:8787}"

echo "==> 项目目录: $ROOT_DIR"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 未检测到 python3，请先安装 Python 3.10+"
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> 创建虚拟环境: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "==> 激活虚拟环境"
source "$VENV_DIR/bin/activate"

echo "==> 安装/更新依赖"
python -m pip install --upgrade pip
python -m pip install -e ./netpilot-mcp openai

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_EXAMPLE_FILE" ]]; then
    echo "==> 未找到 .env，已从 .env.example 创建"
    cp "$ENV_EXAMPLE_FILE" "$ENV_FILE"
  else
    echo "==> 未找到 .env 与 .env.example，创建最小 .env"
    cat >"$ENV_FILE" <<'EOF'
AI_PROVIDER=zhipu
AI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
AI_TEXT_MODEL=glm-4.7
AUTO_SELECT_TEXT_MODEL=false
AI_VISION_BASE_URL=https://api.ppio.com/openai
AI_VISION_MODEL=qwen/qwen3-vl-235b-a22b-thinking
AI_VISION_FALLBACK_MODEL=
ZHIPU_API_KEY=
PPIO_API_KEY=
EOF
  fi
fi

if ! grep -q '^ZHIPU_API_KEY=' "$ENV_FILE"; then
  echo 'ZHIPU_API_KEY=' >> "$ENV_FILE"
fi
if ! grep -q '^PPIO_API_KEY=' "$ENV_FILE"; then
  echo 'PPIO_API_KEY=' >> "$ENV_FILE"
fi

ZHIPU_KEY_VALUE="$(grep '^ZHIPU_API_KEY=' "$ENV_FILE" | sed 's/^ZHIPU_API_KEY=//')"
PPIO_KEY_VALUE="$(grep '^PPIO_API_KEY=' "$ENV_FILE" | sed 's/^PPIO_API_KEY=//')"

if [[ -z "${ZHIPU_KEY_VALUE// }" ]]; then
  echo "警告: .env 中 ZHIPU_API_KEY 为空，文本排障能力不可用。"
fi
if [[ -z "${PPIO_KEY_VALUE// }" ]]; then
  echo "提示: .env 中 PPIO_API_KEY 为空，拓扑图片解析功能不可用（文本功能不受影响）。"
fi

echo "==> 启动 Web UI: $URL"
exec "$VENV_DIR/bin/python" "$ROOT_DIR/web_ui.py"
