# web/ · 前端（Vite + React 19 + TS + Ant Design X）

RAG 产品前端：左侧知识库管理（antd）+ 右侧 AI 问答（Ant Design X）。开发时经 Vite proxy 把 `/api` 转发到 FastAPI 后端。

## 技术栈

| 包 | 用途 |
| --- | --- |
| [@ant-design/x](https://x.ant.design/) 2.9 | 对话 UI：`Bubble.List` / `Sender` / `Welcome` / `Prompts` / `Sources` / `XProvider` |
| [@ant-design/x-sdk](https://github.com/ant-design/x/blob/main/packages/x/docs/x-sdk) | `DefaultChatProvider` + `useXChat`：消息状态机（local/loading/success/error/abort） |
| [@ant-design/x-markdown](https://github.com/ant-design/x/blob/main/packages/x/docs/x-markdown) | AI 回答的 Markdown 渲染 |
| antd 6 | 知识库面板：Upload / List / Statistic / Popconfirm / Alert |
| Vite 8 + React 19 + TypeScript 6 | 脚手架（verbatimModuleSyntax 严格模式） |

## 目录语义

```
src/
├── main.tsx                # 入口：StrictMode + App
├── App.tsx                 # 布局：Header + Sider(340 KnowledgePanel) + Content(ChatPanel)，XProvider 全局
├── lib/
│   ├── api.ts              # 类型（Health/DocItem/IngestResult/Material/AskResult）+ fetch 薄封装
│   └── chat-provider.ts    # 消息模型 + DefaultChatProvider（后端非流式对接核心）
└── components/
    ├── ChatPanel.tsx       # useXChat 状态 → Bubble.List items；空态 Welcome+Prompts；Sender 输入
    ├── KnowledgePanel.tsx  # 健康统计 + 拖拽上传 + 文档列表 + 删除（Popconfirm）
    └── AnswerView.tsx      # XMarkdown 正文 + 引用徽标 + Sources 引用卡片
```

## 核心：chat-provider.ts 对接非流式接口

后端 `/api/v1/ask` 返回**一次性完整 JSON**（不是 OpenAI 流式）。用 `DefaultChatProvider` 透传：

```ts
export const ragProvider = new DefaultChatProvider<ChatMessage, AskInput, AskOutput>({
  request: XRequest<AskInput, AskOutput>('/api/v1/ask', {
    manual: true, method: 'POST', headers: { 'Content-Type': 'application/json' },
  }),
})

// ① 请求体固定为 { query }
ragProvider.transformParams = (requestParams) => ({ query: requestParams.query ?? '' })

// ② 用户消息：本地立即显示
ragProvider.transformLocalMessage = (requestParams) => ({ role: 'user', text: requestParams.query ?? '' })

// ③ 后端响应 → ChatMessage（answer + 素材 + 校验结果）
ragProvider.transformMessage = (info) => {
  const { chunk } = info
  if (chunk?.error) return { role: 'assistant', text: '', error: chunk.error, materials: chunk.materials ?? [] }
  return {
    role: 'assistant',
    text: chunk.answer ?? '',
    materials: chunk.materials ?? [],
    citations: chunk.citations ?? [],
    citationValid: !!chunk.citation_valid,
    error: undefined,
  }
}
```

`useXChat` 在 `ChatPanel` 里消费：`messages/onRequest/isRequesting/abort`；`requestPlaceholder`（请求中占位）、`requestFallback`（失败兜底）都交给 SDK 状态机。**Bubble.List 用 `role`（不是 roles）**映射用户/助手样式，`contentRender` 自定义渲染（Ant Design X 约定）。

## 开发命令

```bash
pnpm install      # 首次（pnpm 11；根目录有 pnpm-workspace.yaml）
pnpm dev          # http://localhost:5173（proxy /api → 127.0.0.1:8000）
pnpm lint         # oxlint
pnpm build        # tsc -b && vite build → dist/
pnpm preview      # 预览构建产物（:4173，已加 CORS）
```

## 构建注意：highlight.js 版本锁

`@ant-design/x-markdown` 的传递依赖 `react-syntax-highlighter` 异步加载 `highlight.js/lib/languages/sql_more`，而 pnpm 严格隔离可能解析到 **highlight.js@10.4.1（无 sql_more）**，导致 `vite build` 失败：

```bash
Error: Rolldown failed to resolve import "highlight.js/lib/languages/sql_more"
```

**修复**：`pnpm-workspace.yaml` 强制统一版本（pnpm 11 的 overrides 已从 package.json 移到这里）：

```yaml
overrides:
  'highlight.js@<10.7.0': '>=10.7.0'
```

生效后 `pnpm install` 会统一到 10.7.0（含 sql_more），build 通过。**如果改了 overrides 后 pnpm 报 frozen lockfile 不一致，用 `pnpm install --no-frozen-lockfile` 重生成 lockfile。**

## 前端设计要点（面试可讲）

1. **非流式对接模式**：不强行套 OpenAI 流式格式，`DefaultChatProvider` 透传 + `transformMessage` 组装——换流式时同一套 Provider 机制
2. **防幻觉闭环可见性**：引用徽标（校验通过/含越界）+ 素材卡片展开原文，用户能自查回答依据
3. **错误三态**：SDK 占位（思考中）→ 成功渲染 / 失败 Alert（后端 error 字段 / HTTP 500）都有明确 UI
4. **前后端约定**：类型 `AskOutput` 与后端响应逐字段对应，接口契约在 `lib/api.ts` 一处维护
5. **多会话演进**：换 `useXConversations`（x-sdk）即可加会话列表，Bubble.List items 结构不变