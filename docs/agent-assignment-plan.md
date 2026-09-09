# 坐席分配方案（转人工自动分配）

> 状态：待评审。落地前需先敲定第 2 节的几个决策点。

## 0. 现状与问题

当前「转人工」只做了**状态标记**，没有「分配」这一环：

| 能力 | 现状 |
| --- | --- |
| 会话归属 | `conversations` 无坐席字段（仅 `id/user_id/tenant_id/status/handoff_reason/created_at/updated_at`） |
| 坐席账号 | 仅 1 个 `operator`（`zenquan`/“Zenquan”），硬编码 demo 账号，注释已标注「后续替换 MySQL 用户表」 |
| 转人工后 | 会话进**公海池**：所有 `handoff` 会话并列展示，任何运营都能回复 |
| 回复记录 | `manual_reply` 的 `agent_name`（默认 `"Zenquan"`）**只写日志和返回体，不落库**，事后查不到「这单谁接的」 |
| 并发 | 无抢占/锁，两个坐席可同时打开同一会话 |

**问题**：无法把工单路由到具体坐席、无法防多人抢单、无法追溯经办人。

## 1. 目标

1. 转人工后，会话能落到**具体坐席**（可自动指派，也可手动领取）。
2. 支持**多坐席**（当前仅 1 个，需先扩展）。
3. 事后可追溯「谁在什么时候接管」。
4. 不破坏现有 `handoff → manual` 主链路。

## 2. 关键决策（需拍板）

### D1 · 坐席账号来源
- **A（推荐）**：先扩 demo 账号支持多 `operator`（环境变量注入），坐席身份直接复用 `AuthUser`。改动小、无新表，适合当前 demo 阶段。
- B：直接引入 `agents` 坐席表（MySQL + 内存回退），与 `AuthUser` 分离，支持坐席独立于登录账号管理。更重，但一步到位。

### D2 · 分配模式
- **A（推荐）**：**手动领取 + 系统自动指派混合**——转人工时若存在在线坐席则自动指派一个，否则进「待分配」池由坐席主动领取。
- B：纯手动领取（坐席在「待分配」队列里点“领取”）。
- C：纯自动指派（轮询 / 最少在办负载）。

### D3 · 坐席在线状态（决定自动指派是否可用）
- **A（推荐）**：P0 先不做在线状态，自动指派退化为「轮询/最少负载全量坐席」；坐席离线风险靠「超时重新分配」兜底。
- B：P0 就做 `agent_status` 在线表 + 心跳，只给在线坐席派单。

### D4 · 技能组路由（可选，P2）
按 `intent` 路由（订单→订单组、投诉→投诉组）。当前坐席只有 1 个，建议**放到 P2**。

## 3. 数据模型

### 3.1 conversations 加列
```
assignee_id    VARCHAR(64)  NULL   -- 坐席 id（AuthUser.id）
assignee_name  VARCHAR(64)  NULL   -- 冗余展示名
assigned_at    DATETIME     NULL   -- 指派/领取时间
```
> 与现有「无 MySQL 回退内存」模式一致：`MemoryChatStore` 同样加这三字段；MySQL 侧 ALTER 幂等（首用检测列存在再 add）。

### 3.2 可选新表（D1-B / D3-B 时才建）
- `agents`：`id, name, status(online/offline), skills JSON, created_at, updated_at`
- `agent_heartbeat`：`agent_id, last_seen`（在线状态心跳）

## 4. 分配策略

### 4.1 手动领取（P0 核心）
- `POST /conversations/{id}/claim`，坐席领取。
- 用 **CAS 抢占**（`UPDATE ... WHERE assignee_id IS NULL`，受影响行数 =1 才算抢到），杜绝两人同抢。
- 已在 `manual`/已被他人领取的会话，领取失败返回明确错误码。

### 4.2 自动指派（P1，D2-A）
- 转人工落库时（`handoff` 状态写入处）触发 `assign()`：
  - 有在线坐席 → 按策略（轮询 / 最少在办）选一个，写 `assignee_id`。
  - 无可用坐席 → 留 `assignee_id=NULL`，进「待分配」池。
- 策略默认「**最少在办负载**」（`COUNT(assignee_id=me AND status=handoff/manual)` 最小者），D3-A 下退化为全量坐席轮询。

### 4.3 超时重新分配（P1 可选）
坐席长时间未响应（如 >N 分钟无回复）自动回池（`assignee_id=NULL`）。依赖「会话最后活动时间」，用现有 `updated_at` 即可，不新增表。

## 5. API 设计

| 端点 | 变更 | 说明 |
| --- | --- | --- |
| `GET /conversations` | 改 | 返回 `assignee_id/assignee_name/assigned_at`；支持 `?filter=mine|unassigned|all`、`?agent_id=` |
| `POST /conversations/{id}/claim` | 新增 | 坐席领取，CAS 抢占，返回抢占结果 |
| `POST /conversations/{id}/release` | 新增 | 坐席释放回池（可选） |
| `POST /conversations/{id}/messages/manual` | 改 | 自动用**当前登录坐席**身份（不再依赖客户端传 `agent_name`），并校验 `assignee_id` |
| `GET /agents` | 新增 | 坐席列表 + 在办数量（供分配/前端展示） |

## 6. 前端（运营工作台）

- 会话队列按 **「待分配 / 我的 / 全部」** 分栏（Tab）。
- 「待分配」会话显示「领取」按钮；「我的」只显示当前坐席的会话。
- 人工回复区显示当前坐席名；回复即绑定当前坐席身份。
- 客户聊天端无改动（仍只见 `handoff/manual` 文案）。

## 7. 实施步骤（分阶段）

- **P0 — 立坐席概念 + 手动领取**（核心，先做）
  1. `AuthUser` 支持多 operator（D1-A：环境变量注入多账号）。
  2. `conversations` 加 `assignee_id/name/assigned_at`（MySQL + Memory 双实现，幂等建表/加列）。
  3. `claim` / `release` 接口（CAS 抢单）。
  4. `manual_reply` 改用登录身份 + `assignee` 校验。
  5. 前端「待分配 / 我的」分栏 + 领取按钮。
- **P1 — 自动指派 + 超时回池**
  6. 转人工时自动 `assign()`（最少负载 / 轮询）。
  7. 超时未响应回池。
- **P2 — 在线状态 + 技能组**（可选，按需）
  8. `agents` + `agent_heartbeat`；`intent → 技能组` 路由。

## 8. 风险与边界

- **并发抢单**：靠 CAS 抢占 + 受影响行数校验解决；内存回退模式用进程锁。
- **坐席离线滞留**：P0 靠「待分配池 + 手动领取」兜底，P1 加超时回池。
- **单 operator 阶段**：只有 1 个坐席时，自动指派退化为「唯一坐席默认领取全部」，语义不变，不报错。
- **存量数据**：旧会话 `assignee_id=NULL`，视为「待分配」，无迁移负担。
- **兼容性**：不破坏 `handoff → manual` 链路，`agent_name` 由「客户端传」改为「服务端取登录身份」——需同步前端与既有调用方。

## 9. 建议的起步组合

**D1-A + D2-A + D3-A + D4 延后**：先扩多坐席账号、加会话归属、做「手动领取 + 简单自动指派（全量轮询/最少负载）」，在线状态与技能组留到 P1/P2。这个组合改动最小、能最快把「公海池」变成「有主工单」，同时保留后续扩展空间。
