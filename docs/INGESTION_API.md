# PanBox 原始消息接收接口接入文档

本文描述当前 FastAPI 实现，供其他项目转发原始影视资源消息使用。外部项目不需要提前解析标题、网盘链接或 TMDB ID。

> 重要：当前版本完成原始消息保存、115/ED2K 文本提取及资源导入。接收后台不会自动执行 TMDB 匹配；`imported` 不代表已完成影视识别或链接存活性验证。请同时阅读文末“当前实现边界”。

## 1. 地址与端口

前端与 API 共用同一服务端口，默认 `8088`，不需要单独启动 Webhook 端口。

| 项目 | 地址 |
| --- | --- |
| 本机基础地址 | `http://127.0.0.1:8088` |
| 接收消息 | `POST /api/v1/ingestion/messages` |
| 查询接收结果 | `GET /api/v1/ingestion/messages/{id}` |
| 健康检查 | `GET /api/health` |
| 本机交互式 API 文档 | `/api/docs` |

使用项目默认的 FastAPI 服务：

```bash
./scripts/start.sh
```

更新后端代码后需重启服务；不要使用 `--legacy`，旧服务不支持设置页保存 Key 的读取逻辑。

### 跨设备调用

`127.0.0.1` 只代表调用者自己。如果发送方运行在另一台服务器或 Docker 容器中，应使用能访问到 PanBox 的主机地址或 HTTPS 域名，而不是照抄本机地址。

推荐部署为：

```text
外部项目 → HTTPS 反向代理 → 127.0.0.1:8088 → PanBox
```

公网入口只放行两个消息接口，不要代理整个应用：

- `POST /api/v1/ingestion/messages`
- `GET /api/v1/ingestion/messages/{id}`

**不要公开 `/api/v1/settings/ingestion` 或其他管理 API。** 当前设置接口只检查客户端地址，反向代理可能使外部请求看起来来自本机，这不是完整的管理员鉴权。反向代理应明确拒绝这些路径，并配置 HTTPS、请求体大小限制和限流。当前应用也没有全站登录保护。

## 2. API Key 许可

在本机页面进入：**设置 → 外部消息接入**。

1. 输入新 Key，或点击“随机生成”。
2. 点击“保存 Key”。仅生成、未保存的 Key 不会生效。
3. 将 Key 安全保存到发送方的环境变量或密钥管理系统。

所有接收及结果查询请求都必须携带：

```http
Authorization: Bearer YOUR_INGESTION_API_KEY
```

注意 `Bearer` 后有一个空格。不要将 Key 放入 URL、消息正文或 Git 仓库。

后端读取优先级：

1. `data/ingestion-config.json` 中有效的 `api_key`。
2. 环境变量 `INGESTION_API_KEY`。

设置页保存的 Key 会在后续请求中生效，不需要为此重启服务；修改 `.env` 则需要重启。替换 Key 后旧 Key 失效（不会同时接受文件 Key 和环境变量 Key）。当前没有多项目独立授权、过期时间、分角色权限或单独的启停功能。

**不要与资源查询 Key 混用：** `MEDIA_API_KEY` / `X-API-Key` 用于另一套只读资源查询接口，不用于消息推送。

## 3. 推送完整消息

```http
POST /api/v1/ingestion/messages
Authorization: Bearer YOUR_INGESTION_API_KEY
Content-Type: application/json
```

### 最小请求

```json
{
  "event_id": "telegram:-1001234567890:456",
  "message": {
    "text": "沙丘2 (2024) https://115.com/s/example?password=abcd"
  }
}
```

这是格式示例，分享链接不是真实资源。

### 推荐请求

```json
{
  "event_id": "telegram:-1001234567890:456",
  "source": {
    "service": "telegram",
    "channel_id": "-1001234567890",
    "channel_name": "示例影视频道",
    "message_id": "456",
    "published_at": "2026-09-30T10:14:02Z"
  },
  "message": {
    "text": "沙丘2 (2024) https://115.com/s/example?password=abcd\n4K UHD，内封中字",
    "raw": {
      "message_id": 456,
      "sender_type": "channel"
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `event_id` | string | 是 | 去除首尾空白后 1～200 字符；发送方生成，重试时保持不变 |
| `source` | object | 否 | 来源溯源信息 |
| `source.service` | string | 否 | 稳定的发送服务标识，默认 `unknown`；参与消息去重 |
| `source.channel_id` | string | 否 | 频道或群组 ID |
| `source.channel_name` | string | 否 | 展示名称 |
| `source.message_id` | string | 否 | 原消息 ID，建议转成字符串 |
| `source.published_at` | string | 否 | 建议 ISO 8601 时间；当前只保存，不严格验证格式 |
| `message.text` | string | 与其他文本形式任选一种 | 完整消息正文 |
| `message.caption` | string | 否 | 图片/视频附带的文本，正文为空时使用 |
| `message.raw` | JSON | 否 | 额外原始消息数据，仅保存，不参与链接提取 |

也兼容 `message` 直接为字符串，或顶层 `text` / `caption`。建议新项目统一使用 `message.text`。

文本选取顺序为：非空的 `message` 字符串 → `message.text` → `message.caption` → 顶层 `text` → 顶层 `caption`。只处理首个可用文本，不会拼接正文与 caption；如两者都有重要内容，请发送方合为一个 `message.text`。

当前代码使用 `len(text) <= 512 * 1024`，实际是字符数限制，不是 UTF-8 字节数；错误提示写作 512 KB。原始 JSON 的总体大小没有等效应用层限制，部署时应由反向代理限制请求体。发送方建议单条正文不超过 32 KiB，避免附带无关的大体积 raw JSON。

### 原始链接的保留

请完整保留消息换行、文件名、访问码和链接：

- 图片本身、视频文件、OCR、附件下载不在接收范围内。
- 如果 Telegram 使用隐藏超链接（text-link entity），必须把实际 URL 展开到 `message.text`；仅存放在 `message.raw` 中的 URL 不会被提取。
- 不要发送机器人 Token、Telegram 登录会话、Cookie 等无关凭据；整个请求 JSON 会被持久化。

## 4. 接收响应与消息去重

新事件返回 `202 Accepted`：

```json
{
  "id": "ing_0123456789abcdef0123456789abcdef",
  "event_id": "telegram:-1001234567890:456",
  "status": "queued",
  "duplicate": false
}
```

- `id` 是 PanBox 的接收记录 ID，保存它以便查询结果。
- `202` 表示原文已保存并已启动后台处理，不表示处理已完成。
- 消息去重键为 **`source.service + event_id`**，不是单独的 `source.message_id`。
- 同一个 Telegram 频道的消息 ID 可能与其他频道重复，建议 `event_id` 包含频道 ID。

重复推送也返回 `202`，`id` 不变，`status` 是既有记录的当前状态：

```json
{
  "id": "ing_0123456789abcdef0123456789abcdef",
  "event_id": "telegram:-1001234567890:456",
  "status": "imported",
  "duplicate": true
}
```

同一去重键的新正文不会覆盖原记录，也不会重新处理。需要处理消息编辑版本时，发送方可使用版本化 ID，例如 `telegram:channel:456:edit:2`。这会创建新的接收记录，已有资源仍按来源规则去重。

注意：`duplicate: true` 表示**接收事件已存在**；事件的 `status: duplicate` 表示**此次导入没有新增来源**，两者不是一回事。

## 5. 查询处理结果

```http
GET /api/v1/ingestion/messages/{id}
Authorization: Bearer YOUR_INGESTION_API_KEY
```

成功返回 `200 OK`。结构示例（ID、时间及数据均为示例）：

```json
{
  "id": "ing_0123456789abcdef0123456789abcdef",
  "event_id": "telegram:-1001234567890:456",
  "service": "telegram",
  "channel_id": "-1001234567890",
  "channel_name": "示例影视频道",
  "message_id": "456",
  "published_at": "2026-09-30T10:14:02Z",
  "raw_text": "沙丘2 (2024) https://115.com/s/example?password=abcd",
  "raw_json": {
    "event_id": "telegram:-1001234567890:456",
    "source": { "service": "telegram" },
    "message": { "text": "沙丘2 (2024) https://115.com/s/example?password=abcd" }
  },
  "status": "imported",
  "error": null,
  "received_at": "2026-09-30T10:14:03+00:00",
  "processed_at": "2026-09-30T10:14:04+00:00",
  "media_ids": [123],
  "links": [
    {
      "id": 1,
      "ingestion_id": "ing_0123456789abcdef0123456789abcdef",
      "source_key": "115:https://115.com/s/example?password=abcd",
      "provider": "115",
      "source_type": "115",
      "url": "https://115.com/s/example?password=abcd",
      "status": "inserted",
      "media_id": 123,
      "error": null
    }
  ]
}
```

`raw_json` 是发送的整个请求对象，不仅是 `message.raw`。`media_ids` 是 PanBox 本地作品 ID，不是 TMDB ID；链接和原文可能包含访问码，应当作敏感数据处理。

### 事件状态

| `status` | 含义 | 建议 |
| --- | --- | --- |
| `queued` | 接收记录已建立，后台尚未写入终态；处理过程中仍可能保持此值 | 低频轮询 |
| `imported` | 至少新增了一条资源来源 | 接收处理结束，未必完成 TMDB 匹配 |
| `duplicate` | 没有新增来源，通常为已有资源 | 接收处理结束 |
| `ignored` | 未提取到支持的链接 | 保留原文，核查格式/网盘支持情况 |
| `failed` | 后台处理出现异常 | 记录接收 ID，由管理端排查 |

当前不存在 `extracting`、`matching`、`matched` 等接收事件状态。

## 6. 错误响应

当前 FastAPI 接口使用两种错误结构，调用方应同时兼容 `detail` 和 `error`。

| HTTP | 场景 | 响应示例 |
| --- | --- | --- |
| 400 | 缺少 event_id、消息文本为空或超长 | `{"error":"event_id 必须是 1 至 200 个字符。"}` |
| 401 | 缺失或错误的 Bearer Key | `{"detail":"需要有效的接入凭据。"}` |
| 404 | 查询的接收记录不存在 | `{"detail":"接收记录不存在。"}` |
| 422 | JSON 格式错误或请求体不是要求的对象 | `{"detail":[...]}` |
| 503 | 没有有效的接入 Key 配置 | `{"detail":"接收接口尚未配置 INGESTION_API_KEY。"}` |
| 500 | 未处理的服务端异常 | 不保证为 JSON |

反向代理还可能返回 `413`（请求过大）或 `429`（限流）。

## 7. cURL 快速接入

在发送方准备两个环境变量（密钥值由你自己的部署提供）：

```bash
export PANBOX_BASE_URL='http://127.0.0.1:8088'
# 公网部署时改为 https://panbox.example.com
read -r -s -p 'PanBox 接入 Key: ' PANBOX_INGESTION_KEY
export PANBOX_INGESTION_KEY
printf '\n'
```

推送测试消息（不包含真实分享，预期最终 ignored）：

```bash
curl --fail-with-body --max-time 15 \
  -X POST "$PANBOX_BASE_URL/api/v1/ingestion/messages" \
  -H "Authorization: Bearer $PANBOX_INGESTION_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary '{"event_id":"integration-smoke-001","source":{"service":"my-project"},"message":{"text":"接入连通性测试，无影视链接"}}'
```

用响应中的真实 `id` 查询：

```bash
curl --fail-with-body --max-time 15 \
  -H "Authorization: Bearer $PANBOX_INGESTION_KEY" \
  "$PANBOX_BASE_URL/api/v1/ingestion/messages/ing_REPLACE_WITH_RESPONSE_ID"
```

正式消息只需替换 `event_id` 和 `message.text`。再次发送完全相同的测试请求，应返回相同 `id` 且 `duplicate: true`。

## 8. Python 调用示例（标准库，无额外依赖）

```python
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = os.environ["PANBOX_BASE_URL"].rstrip("/")
KEY = os.environ["PANBOX_INGESTION_KEY"]


def call(path, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        BASE + path,
        data=data,
        method="GET" if payload is None else "POST",
        headers={
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        # 不记录原始消息、完整请求头或 Key。
        raise RuntimeError("PanBox HTTP 错误：%s" % exc.code) from None


def forward_message(channel_id, message_id, text, channel_name=""):
    return call("/api/v1/ingestion/messages", {
        "event_id": "telegram:%s:%s" % (channel_id, message_id),
        "source": {
            "service": "telegram",
            "channel_id": str(channel_id),
            "channel_name": channel_name,
            "message_id": str(message_id),
        },
        "message": {"text": text},
    })


receipt = forward_message("example-channel", "smoke-001", "连通性测试，无影视链接")
print("接收记录：", receipt["id"], receipt["status"])

# 最多等待约 60 秒；生产环境建议将轮询放在自己的任务队列中。
for _ in range(20):
    result = call("/api/v1/ingestion/messages/" + quote(receipt["id"], safe=""))
    if result["status"] != "queued":
        print("处理完成：", result["status"], "本地作品 ID：", result["media_ids"])
        break
    time.sleep(3)
else:
    print("处理尚未完成，请保存接收 ID 并稍后核查，不要无限重复提交。")
```

## 9. 发送方的可靠性建议

- 维护自己的待发送队列，只有收到合法的 202 JSON 和 `id` 后才记为已送达。
- 超时、连接断开或临时 5xx 时，使用**同一 event_id**指数退避重试，例如 2/5/15/30 秒，设定最大次数。
- 400/422 修正请求后再发；401/503 修复密钥或配置，不要快速循环重试。
- 避免并发推送同一事件：当前消息去重采用“先查后插”，并发时可能遇到唯一约束冲突而返回 500；串行重试可查询到原记录。
- 收到 202 后低频查询结果（例如每 3～5 秒），不要用反复 POST 代替结果查询。
- 同一个 event_id 即便已经 failed，也不会重新执行。失败重放目前没有专用接口；排查原因后可用带版本的新 ID，但这不提供跨整个处理流程的 exactly-once 保证。

## 10. 当前实现边界（接入前必读）

1. **没有自动 TMDB 刮削**：接收后台调用现有导入逻辑，将来源写入缓存。TMDB 关联仍由现有管理操作/批量匹配触发。
2. **消息全文解析仍有限**：当前复用逐行导入器。115 每行只提取第一个分享链接，并主要使用同一行的前缀作为标题；片名在上一行、链接在下一行的消息可以收件，但不能保证正确关联片名。独立行访问码尚不会自动拼入分享 URL。ED2K 支持一行多个链接。无需发送方替 PanBox 识别作品，但必须接受当前版本的解析限制。
3. **不是持久任务队列**：原文已落库，但处理使用每消息一个 daemon 线程；没有并发上限、自动恢复或持久重试。进程重启可能留下 queued 记录。建议发送方保留原文和接收 ID，低并发接入；大规模推送前需要补齐 worker 调度和恢复机制。
4. **链接结果状态的已知限制**：当前 ingestion_links 的 inserted/duplicate 判断基于导入后来源是否存在，已有来源也可能被标为 inserted。不要据此统计新增链接；以事件状态为粗粒度结果，精确统计需后续修复。
5. **暂不支持其他网盘或磁力解析**：目前为 115.com、115cdn.com、ED2K。ED2K 在本项目中 provider 为 115。收到其他消息通常为 ignored。
6. **原文不等于永久有效资源**：收件、导入成功都不验证分享存活性，不进行转存、离线下载或 TG 抓取。
7. **当前共用一个接入 Key**：持有该 Key 的客户端可查询其已知 ID 对应的任何接收记录，没有按调用方隔离；只分发给可信项目。

本文不包含真实 API Key 或本地影视缓存。依据：`backend/fastapi_app.py`、`backend/ingestion.py`、`backend/importers.py`。
