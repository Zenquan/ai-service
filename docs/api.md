# API 参考

Base URL：`http://127.0.0.1:8000/api/v1`（前端经 Vite proxy `/api` → 同一地址）

- 交互式文档：http://127.0.0.1:8000/docs （Swagger UI，FastAPI 自动生成）
- 约定：请求/响应均为 JSON（ingest 除外：multipart/form-data）
- CORS：允许 `localhost:5173/4173`（Vite dev/preview）
- 运行约束：uvicorn 必须 `--workers 1`（Qdrant local 单进程锁）
- 链路追踪：所有 HTTP 响应头带 `X-Request-ID`；请求方可传自定义
  `X-Request-ID` 透传同一 ID，服务端日志按该 ID 串联一轮对话（见根 README「日志与链路排查」）

---

## GET /health — 健康检查 + 库统计

无请求体。

**200 响应**（实测）：

```json
{
  "status": "ok",
  "docs": 5,
  "chunks": 155,
  "embed_model": "BAAI/bge-large-en-v1.5",
  "qdrant_path": ".../rag/qdrant_data",
  "collection": "rag_minimal",
  "rerank": true,
  "llm_model": "deepseek-v4-flash"
}
```

| 字段 | 说明 |
| --- | --- |
| status | `ok`（未捕获异常都会抛 500） |
| docs / chunks | 库内文档数 / chunk 总数 |
| rerank | 是否配置了 SILICONFLOW_API_KEY（配置即开） |

```bash
curl http://127.0.0.1:8000/api/v1/health
```

---

## GET /docs — 文档列表

**200 响应**（实测）：

```json
[
  { "doc": "01-AI-Agent入门.md", "chunks": 1 },
  { "doc": "rag-test-pdfs/DeepFace-ICCV2017.pdf", "chunks": 71 }
]
```

| 字段 | 说明 |
| --- | --- |
| doc | 文档名（含入库时的相对路径） |
| chunks | 该文档的 chunk 数 |

`doc` 值可直接用于 DELETE `/docs/{doc}`（记得 URL 编码，见下）。

```bash
curl http://127.0.0.1:8000/api/v1/docs
```

---

## DELETE /docs/{doc_name} — 删除单个文档（增量）

删除指定文档的全部 chunk，**不重建索引**（基于 payload.doc 过滤 + 批量 delete）。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| doc_name | path | 文档名（含路径，需 URL 编码） |

**200 响应**：

```json
{ "deleted": 16, "doc": "rag-test-pdfs/IEEE-Paper-Format.pdf" }
```

**404 响应**（文档不存在）：

```json
{ "detail": "库中不存在文档: xxx.pdf" }
```

```bash
# 文档名含斜杠/中文 → 用 --data-urlencode 或 encodeURIComponent
curl -X DELETE "http://127.0.0.1:8000/api/v1/docs/rag-test-pdfs%2FDeepFace-ICCV2017.pdf"
# 前端等价：encodeURIComponent(docName)
```

---

## POST /ingest — 上传文档入库

`multipart/form-data`，字段名 `files`（可多个）。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| files | file[] | 扩展名白名单：pdf / txt / md / markdown / docx / html / jpg / png |

流程：白名单校验 → 防穿越取 basename → 落盘 `rag/data/uploads/` → 逐文件入库（解析→清洗→切块→向量→Qdrant）。

**200 响应**（实测多文件）：

```json
{
  "total": 155,
  "docs": [
    { "name": "DeepFace-ICCV2017.pdf", "chunks": 71, "inserted": 71 },
    { "name": "IEEE-Paper-Format.pdf", "chunks": 16, "inserted": 16 }
  ],
  "skipped": [],
  "errors": []
}
```

| 字段 | 说明 |
| --- | --- |
| total | 成功入库 chunk 总数 |
| docs[] | 每份文档：name / chunks（切块数）/ inserted（入库数） |
| skipped[] | 解析后为空等业务跳过：{ name, reason } |
| errors[] | 格式不支持/写入失败等：{ name, reason } |

**400 响应**（全部文件都不支持）：

```json
{ "detail": { "errors": [{ "name": "a.exe", "reason": "不支持的格式 .exe（支持: [...pdf, txt, md, ...]）" }] } }
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest \
  -F "files=@01-AI-Agent入门.md" \
  -F "files=@ResNet-CVPR2016.pdf"
```

---

## POST /ask — 知识问答

JSON 请求体：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| query | string | 必填 | 问题（1–2000 字符） |
| use_rerank | bool/null | null | null=按配置默认开；false=关闭；true=强制开 |
| top_k | int/null | null | 检索条数（1–50，默认 config.TOP_K=5） |
| rerank_threshold | float/null | null | rerank 最低相关性分数；过滤低于阈值的素材 |

流程：结构感知 chunk → 向量与标题加权 BM25 双路召回 → RRF → rerank（可关/设阈值）→ Prompt 编号注入 → DeepSeek 生成 → 引用校验。

**200 响应**（实测："什么是 AI Agent？"）：

```json
{
  "query": "什么是 AI Agent？",
  "materials": [
    {
      "text": "AI Agent 以大语言模型为核心，能拆解任务并调用外部工具……",
      "doc": "01-AI-Agent入门.md",
      "seq": 0,
      "score": 0.0476190476190476
    }
  ],
  "answer": "AI Agent 以大语言模型为核心，能自主规划任务、调用外部工具并持续迭代直至达成目标……[来源1]",
  "citations": [1],
  "citation_valid": true,
  "material_count": 3,
  "error": null
}
```

| 字段 | 说明 |
| --- | --- |
| answer | 生成回答（Markdown 文本，含 `[来源N]` 标记） |
| materials[] | 注入的素材：text / doc / seq / score / chapter / title / section / heading_path |
| citations[] | answer 里出现的来源序号 |
| citation_valid | 是否全部合法（`false` = 存在越界引用，即模型可能编造） |
| error | 检索失败/无素材/生成失败时的错误信息；正常为 null |

**错误语义**（业务错误不抛 HTTP，走 `error` 字段）：

```json
{ "query": "...", "answer": "", "error": "检索失败: ..." }
{ "query": "...", "answer": "", "error": "没有检索到相关素材（请先 ingest 入库）" }
```

**500 响应**（未捕获异常，前端会显示「这次回答失败了」）：

```json
{ "detail": "缺少 DEEPSEEK_API_KEY（在 .env 里配置）" }
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "什么是 AI Agent？"}'

# 关闭 rerank（调试检索时更快）
curl -X POST http://127.0.0.1:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"query": "ResNet 的核心创新是什么？", "use_rerank": false, "top_k": 8}'
```

---

## 客服会话 API

客服工作台使用会话消息接口。会话/消息按 `MYSQL_*` 配置落库（否则内存回退并显式返回 `storage`）。
消息处理走三层路由：显性业务意图（投诉/售后/问候/订单）→ 其余先走流式 RAG → 检索不到素材时澄清或转人工。
**订单/物流查询进入只读业务工具**
（演示订单源在 `server/src/server/tools/orders.py`），缺订单号会澄清补齐、非本人订单/工具失败显式转人工；
退款、售后、投诉仍安全转人工。

> 身份字段 `user_id`/`tenant_id` 是无登录阶段的开发/测试占位：会话首次创建时绑定，
> 订单工具只允许查询 `user_id` 自己的订单。接入真实认证前请勿在生产开放客户端自选身份。

登录后调用客服接口建议带 `Authorization: Bearer <token>`：带 token 时 `user_id`
取 token 里的用户，忽略请求体自填值；人工回复接口必须由 `operator` 调用。

### POST /auth/login

```json
{ "username": "alice", "password": "alice123" }
```

返回 `{ "token", "user": { "id", "username", "role", "display_name" } }`。
运营账号 `zenquan` → `role=operator`；客户账号 `alice` → `role=customer`。

### GET /auth/me

返回当前 token 对应的用户信息。

### GET /conversations/{id}

读取会话摘要：

```json
{
  "conversation_id": "demo",
  "created_at": "2026-09-04T09:05:20+00:00",
  "updated_at": "2026-09-04T09:05:20+00:00",
  "message_count": 0,
  "status": "open",
  "handoff_reason": null
}
```

`status` 为 `open`、`waiting`、`handoff` 或 `manual`，分别表示 AI 处理中、等待补充信息、
等待人工接管和人工坐席已回复。

### GET /conversations/{id}/messages

读取当前会话的用户与助手消息列表。

### POST /conversations/{id}/messages

请求体（`user_id`/`tenant_id` 可选，无登录演示默认为 `demo-user`）：

```json
{ "message": "帮我查一下订单 A00001 的物流", "user_id": "demo-user" }
```

行为：

- 知识问题 → `response_mode=answer`，携带 `materials`、`citations`、`citation_valid`。
- 知识问题无素材 → 第一次 `response_mode=clarify`（说明去向）；第二轮仍无素材 → `response_mode=handoff`，不再反复澄清。
- 订单缺单号 → `response_mode=clarify`（`waiting`），下一轮直接补订单号即可继续（checkpoint 恢复）。
- 非本人订单 / 查无此单 / 工具超时 → `response_mode=handoff` + `needs_human=true` + `handoff_reason`。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/conversations/demo/messages \
  -H "Content-Type: application/json" \
  -d '{"message":"A00001","user_id":"demo-user"}'
```

### POST /conversations/{id}/messages/stream

SSE 流式事件（`event: <type>\ndata: <json>\n\n`）：

| event | 说明 |
| --- | --- |
| `progress` | 当前阶段可见状态（“正在理解意图并检索知识库…” / “正在查询订单与物流…”），不拼进最终正文 |
| `meta` | 图/三层路由结果（澄清 / 工具回答 / 转人工）：完整 `answer` 一次到位，带 `intent`、`clarify_reason` |
| `materials` / `token` | 知识问答检索结果与逐 token 生成 |
| `done` | 终态（含 `storage`；图结果分支不重复带 answer） |
| `error` | 检索/生成/图执行失败 |

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/conversations/demo/messages/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我查一下订单 A00001 的物流","user_id":"demo-user"}'
```

### POST /conversations/{id}/messages/manual

人工坐席回复（仅 `handoff`/`manual` 状态会话可用，其它状态返回 409）：

```json
{ "message": "您好，我是人工客服，正在为您核实，请稍候。", "agent_name": "Zenquan" }
```

消息以 `response_mode=manual` 落库，会话状态更新为 `manual`；
前端收到后切换为人工模式，不再经过 AI 链路。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/conversations/demo/messages/manual \
  -H "Content-Type: application/json" \
  -d '{"message":"您好，人工已接管，请稍候。"}'
```

---

## 错误码汇总

| 状态码 | 场景 | 前端表现 |
| --- | --- | --- |
| 200 | 成功（含业务错误 error 字段） | 正常渲染 |
| 400 | ingest 全部文件不支持 | 上传失败提示 |
| 404 | delete 文档不存在 | 删除失败提示 |
| 500 | ask 未捕获异常 / 后端未启动 | 回答失败 Alert / 知识库面板「后端不可用」 |
