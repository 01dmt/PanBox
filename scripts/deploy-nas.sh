#!/usr/bin/env bash
set -euo pipefail

# 把当前工作树同步到 Unraid，重建镜像并重启 PanBox 容器。
# 不会同步 data/ 与 .env，避免覆盖 NAS 上的数据库和凭证。
# 可用环境变量：
#   PANBOX_NAS_HOST        默认 codex-unraid
#   PANBOX_APP_DIR         默认 /mnt/user/appdata/panbox
#   PANBOX_ADMIN_NETWORKS  首次创建 .env 时写入的管理网段

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAS_HOST="${PANBOX_NAS_HOST:-codex-unraid}"
APP_DIR="${PANBOX_APP_DIR:-/mnt/user/appdata/panbox}"
ADMIN_NETWORKS="${PANBOX_ADMIN_NETWORKS:-127.0.0.1/32,::1/128,10.10.10.0/24}"

echo "==> 准备 NAS 目录 ${NAS_HOST}:${APP_DIR}"
ssh "${NAS_HOST}" "mkdir -p '${APP_DIR}/app' '${APP_DIR}/data'"

echo "==> 同步代码"
rsync -az --delete --no-owner --no-group \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.venv/' \
  --exclude '.venv-p115/' \
  --exclude 'data/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/dist/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "${ROOT_DIR}/" "${NAS_HOST}:${APP_DIR}/app/"

rsync -az --no-owner --no-group "${ROOT_DIR}/compose.yaml" "${NAS_HOST}:${APP_DIR}/compose.yaml"

if ! ssh "${NAS_HOST}" "test -f '${APP_DIR}/.env'"; then
  echo "==> NAS 上尚无 .env，复制本机配置"
  scp "${ROOT_DIR}/.env" "${NAS_HOST}:${APP_DIR}/.env"
fi

echo "==> 补齐 NAS 运行配置"
ssh "${NAS_HOST}" "
  set -e
  cd '${APP_DIR}'
  touch .env
  grep -q '^MEDIA_ADMIN_NETWORKS=' .env || echo 'MEDIA_ADMIN_NETWORKS=${ADMIN_NETWORKS}' >> .env
  chmod 600 .env
  chmod 644 compose.yaml
  chown root:root compose.yaml
  chown -R root:root app
  chown -R 1000:1000 data
"

echo "==> 构建镜像"
ssh "${NAS_HOST}" "cd '${APP_DIR}' && docker build -t panbox:local '${APP_DIR}/app'"

echo "==> 启动容器"
ssh "${NAS_HOST}" "cd '${APP_DIR}' && docker compose up -d"

echo "==> 等待健康检查"
for _ in $(seq 1 30); do
  if ssh "${NAS_HOST}" "curl -fsS --max-time 3 http://127.0.0.1:8088/api/health >/dev/null"; then
    ssh "${NAS_HOST}" "docker compose -f '${APP_DIR}/compose.yaml' ps"
    echo "PanBox 已就绪：http://${NAS_HOST}:8088"
    exit 0
  fi
  sleep 2
done

echo "健康检查超时，查看日志：" >&2
echo "  ssh ${NAS_HOST} \"docker logs --tail 100 panbox\"" >&2
exit 1
