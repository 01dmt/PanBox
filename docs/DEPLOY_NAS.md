# PanBox NAS 部署与迁移

本文记录 PanBox 在 Unraid NAS 上的正式部署方式。当前目标机为 `codex-unraid`（`10.10.10.3`），服务地址 `http://10.10.10.3:8088`。

## 1. 运行结构

```text
/mnt/user/appdata/panbox/
├── app/                 # 代码快照，Docker 构建上下文
├── compose.yaml         # 容器编排
├── .env                 # 运行凭证，600，权限仅 root
└── data/                # 持久化目录，UID/GID 1000
    ├── media.db
    ├── ingestion-config.json
    └── avatar-cache/
```

- 镜像 `panbox:local` 由 `Dockerfile` 多阶段构建：Node 构建前端，Python 运行后端；镜像内不含数据库和 `.env`。
- 容器名 `panbox`，`restart: unless-stopped`，端口 `8088:8088`，日志按 10MB × 3 轮转。
- 容器以 UID/GID `1000` 运行，`data/` 必须对该 UID 可写。
- `compose.yaml` 把 `./data` 挂到 `/app/data`，数据库路径固定为 `/app/data/media.db`。
- 设置接口默认只允许回环地址；NAS 局域网访问由 `.env` 的 `MEDIA_ADMIN_NETWORKS` 放行，例如 `127.0.0.1/32,::1/128,10.10.10.0/24`。该白名单只作用于 `/api/v1/settings/ingestion`。

## 2. 首次部署（不含数据）

在 Mac 仓库根目录执行：

```bash
./scripts/deploy-nas.sh
```

脚本会：

1. 把工作树同步到 `codex-unraid:/mnt/user/appdata/panbox/app`（排除 `data/`、`.env`、虚拟环境和前端依赖）。
2. 首次运行时复制本机 `.env`，并补齐 `MEDIA_ADMIN_NETWORKS`。
3. 执行 `docker build -t panbox:local`，然后 `docker compose up -d`。
4. 轮询 `/api/health`，成功后输出容器状态。

脚本不会同步数据库，也不会覆盖 NAS 上已有的 `.env`。

## 3. 首次数据迁移

迁移期间会短暂停止 Mac 实例。按以下顺序执行，避免 WAL 未落盘或双库同时写入。

### 3.1 冻结本机数据库

```bash
# 停掉 Mac 上的 PanBox（若是 ./scripts/start.sh 启动的前台进程，直接 Ctrl-C）
pkill -f 'app.py serve' || true
sleep 2
! lsof -nP -iTCP:8088 -sTCP:LISTEN

# 做一致性快照，而不是直接拷贝正在写入的 media.db
sqlite3 data/media.db ".backup '/tmp/panbox-final.db'"
sqlite3 /tmp/panbox-final.db 'PRAGMA integrity_check;'
```

`integrity_check` 必须返回 `ok`。如果 `lsof` 仍显示监听，先处理自动重启（终端里的循环、launchd 或手动启动的进程），确认端口释放后再继续。快照期间不要启动 Mac 实例。

### 3.2 上传快照与附属文件

```bash
# 先传到 .incoming，校验通过后再替换，避免传输中断破坏现有库
rsync -av --progress /tmp/panbox-final.db \
  codex-unraid:/mnt/user/appdata/panbox/data/media.db.incoming

ssh codex-unraid "
  sqlite3 /mnt/user/appdata/panbox/data/media.db.incoming 'PRAGMA integrity_check;'
  sqlite3 /mnt/user/appdata/panbox/data/media.db.incoming \
    'select count(*) from source_records; select count(*) from media_items;'
"

# 停容器后替换数据库，并清理旧的 WAL/SHM
ssh codex-unraid "
  cd /mnt/user/appdata/panbox
  docker compose stop panbox
  cd data
  rm -f media.db media.db-shm media.db-wal
  mv media.db.incoming media.db
  chown 1000:1000 media.db
  chmod 600 media.db
"

rsync -av data/ingestion-config.json \
  codex-unraid:/mnt/user/appdata/panbox/data/

rsync -av data/avatar-cache/ \
  codex-unraid:/mnt/user/appdata/panbox/data/avatar-cache/

ssh codex-unraid "
  chown -R 1000:1000 /mnt/user/appdata/panbox/data
  chmod 600 /mnt/user/appdata/panbox/data/ingestion-config.json
"
```

只迁移当前库和运行所需文件；`data/` 下约 20G 的历史备份留在 Mac，不随迁移上传。

### 3.3 启动并校验

```bash
ssh codex-unraid "cd /mnt/user/appdata/panbox && docker compose up -d"
curl -fsS http://10.10.10.3:8088/api/health
```

对比迁移前后计数：

```bash
sqlite3 data/media.db \
  "select 'sources', count(*) from source_records
   union all select 'media', count(*) from media_items
   union all select 'events', count(*) from ingestion_events;"

ssh codex-unraid "sqlite3 /mnt/user/appdata/panbox/data/media.db \
  \"select 'sources', count(*) from source_records
   union all select 'media', count(*) from media_items
   union all select 'events', count(*) from ingestion_events;\""
```

再检查：

- `http://10.10.10.3:8088/movies`、`/resources`、`/imports` 页面正常。
- `http://10.10.10.3:8088/settings` 能显示“接入 Key 已配置”，而不是 403。
- `ssh codex-unraid "docker logs --tail 100 panbox"` 无 traceback、无数据库锁错误。

启动后后台校验 worker 可能接着处理快照里 `pending_validation` 的事件，`source_records` 比快照多几条、`imported` 增加属于正常恢复，不是重复迁移。以 `ingestion_events` 总数和 `PRAGMA integrity_check` 为准。

## 4. 切换 telegram-hub

NAS 上的 `telegram-hub` 通过 `panbox_ingest` 规则推送消息。迁移后把规则地址从 Mac 改为 NAS。

推荐在 telegram-hub 的“自动化规则 → 115资源收集”里把 PanBox 地址改为：

```text
http://10.10.10.3:8088
```

也可以用管理 API（`X-Admin-Token` 从容器环境读取，不要写进仓库或日志）：

```bash
TOKEN="$(ssh codex-unraid "docker inspect telegram-hub-app-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^TELEGRAM_HUB_ADMIN_TOKEN=//p'")"

curl -fsS -X PATCH \
  -H "X-Admin-Token: ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"base_url":"http://10.10.10.3:8088"}' \
  http://10.10.10.3:8010/api/panbox-rules/379a00bc-39e6-4dad-b28f-c440f87e5e2b
```

切换后先确认容器到 NAS 的连通性：

```bash
ssh codex-unraid "docker exec telegram-hub-app-1 \
  python -c \"import urllib.request; print(urllib.request.urlopen('http://10.10.10.3:8088/api/health', timeout=5).read())\""
```

等下一批真实消息入库后，在“入库记录”确认来源频道、媒体和状态正常。不要用重复回放大量历史消息来验证，PanBox 虽按事件幂等，但会产生不必要的 115 探测。

## 5. 日常更新

```bash
./scripts/deploy-nas.sh
```

脚本只替换代码和镜像，不动 `data/` 与 `.env`。更新后检查 `docker compose ps` 和 `/api/health`。

回滚代码时，在 NAS 上把 `app/` 切到上一版快照后重新 `docker build`，或直接使用上一次的镜像 ID 重新打标签；数据库迁移由 `backend/schema.py` 幂等执行，不要手工改表。

## 6. 备份与恢复

### 6.1 自动备份

NAS 已安装 Unraid `appdata.backup` 插件，并已把 `panbox` 加入备份容器列表。默认每周一 06:00 备份 `/mnt/user/appdata/panbox`，备份前按插件设置停止容器，覆盖数据库、`ingestion-config.json`、头像缓存和 compose 配置。目标目录为 `/mnt/user/Backup/Appdata-Backup/`，保留策略由插件配置控制。

### 6.2 手动在线快照

SQLite 支持在线备份，不需要停容器。NAS 自带 `sqlite3`，用 `.backup` 而不是直接 `cp`。快照放在 appdata 之外，避免被 Appdata Backup 再次打包：

```bash
ssh codex-unraid "
  mkdir -p /mnt/user/Backup/PanBox-Snapshots
  sqlite3 /mnt/user/appdata/panbox/data/media.db \
    \".backup '/mnt/user/Backup/PanBox-Snapshots/media-$(date +%F).db'\"
  sqlite3 /mnt/user/Backup/PanBox-Snapshots/media-$(date +%F).db 'PRAGMA integrity_check;'
"
```

保留最近 7 份即可。

### 6.3 恢复

```bash
ssh codex-unraid "cd /mnt/user/appdata/panbox && docker compose stop panbox"
# 用备份覆盖 data/media.db，并清理 media.db-shm / media.db-wal
ssh codex-unraid "
  rm -f /mnt/user/appdata/panbox/data/media.db-shm \
        /mnt/user/appdata/panbox/data/media.db-wal
  cp /mnt/user/Backup/PanBox-Snapshots/media-YYYY-MM-DD.db \
     /mnt/user/appdata/panbox/data/media.db
  chown 1000:1000 /mnt/user/appdata/panbox/data/media.db
"
ssh codex-unraid "cd /mnt/user/appdata/panbox && docker compose up -d"
```

## 7. 回滚到 Mac

Mac 上的原库和旧备份保持不动。NAS 验证失败时：

1. 在 telegram-hub 把规则地址改回 `http://10.10.10.104:8088`。
2. 在 Mac 仓库执行 `./scripts/start.sh`。
3. 确认 `/api/health` 和入库记录恢复。

不要长期让 Mac 与 NAS 同时接收消息，两个 SQLite 会各自分叉。

## 8. 排障

| 现象 | 检查 |
| --- | --- |
| 容器反复重启 | `docker logs --tail 200 panbox`；多为 `.env` 缺失、数据库权限或端口占用 |
| 数据库 `unable to open database file` | `ls -ln /mnt/user/appdata/panbox/data`，确保 UID/GID 1000 可写 |
| 设置页 403 | `.env` 的 `MEDIA_ADMIN_NETWORKS` 是否包含客户端网段；修改后 `docker compose up -d` 重建容器 |
| 端口冲突 | `ss -ltnp \| grep 8088`，确认没有其他服务占用 |
| 页面空白或 404 | 镜像构建阶段前端是否成功；`docker exec panbox ls /app/frontend/dist` |
| 时间统计偏移 | compose 已设置 `TZ=Asia/Shanghai`，检查容器内 `date` |
| telegram-hub 推送失败 | 从 `telegram-hub-app-1` 容器内访问 `/api/health`，并核对规则的 `base_url` 与接入 Key |

## 9. 安全边界

- `.env`、`ingestion-config.json` 不进 Git、不进镜像；NAS 上权限 600。
- 容器非 root 运行，只挂载 `data/`，未开启 privileged。
- 服务目前只面向局域网，没有公网暴露和 HTTPS。若以后加反向代理，必须在代理层增加认证和 TLS，并重新评估 `MEDIA_ADMIN_NETWORKS`。
- 设置接口白名单只放行管理网段，不应扩大到 `0.0.0.0/0`。
