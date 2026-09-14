#!/usr/bin/env bash
# workbuddy2api 一键部署：自动生成密钥、构建镜像、启动管理后台。
# 用法：bash deploy/one-click/deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

rand() { python3 -c "import secrets; print(secrets.token_urlsafe($1))" 2>/dev/null || openssl rand -base64 48 | tr -dc 'A-Za-z0-9_-' | head -c "$1"; }

GENERATED=0
if [ ! -f .env ]; then
  ADMIN_KEY="$(rand 36)"
  CLIENT_KEY="sk-wb-$(rand 32)"
  printf 'ADMIN_KEY=%s\nCODEBUDDY2OPENAI_KEY=%s\nPORT=8787\n' "$ADMIN_KEY" "$CLIENT_KEY" > .env
  chmod 600 .env
  GENERATED=1
fi

docker compose --env-file .env up -d --build "$@"

PORT="$(grep '^PORT=' .env 2>/dev/null | cut -d= -f2 || true)"
PORT="${PORT:-8787}"

echo
echo "管理后台已启动：http://127.0.0.1:${PORT}/admin/"
if [ "$GENERATED" = "1" ]; then
  echo
  echo "首次部署已生成密钥（同时保存在 deploy/one-click/.env，仅创建时显示一次）："
  echo "  管理密钥  ADMIN_KEY            : $(grep '^ADMIN_KEY=' .env | cut -d= -f2)"
  echo "  客户端 Key CODEBUDDY2OPENAI_KEY : $(grep '^CODEBUDDY2OPENAI_KEY=' .env | cut -d= -f2)"
  echo
  echo "提示：管理会话 Cookie 带 Secure 标志，正式对外部署请通过 HTTPS 反向代理访问（示例见 deploy/admin/nginx.conf.example）。"
fi
