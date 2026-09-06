/** 智应客服中心：会话队列 + AI 对话 + 实时上下文。 */

import { useCallback, useEffect, useState } from 'react'
import { App as AntApp, Button, Modal, Tooltip } from 'antd'
import { XProvider } from '@ant-design/x'
import ChatPanel from './components/ChatPanel'
import ContextPanel from './components/ContextPanel'
import ConversationSidebar from './components/ConversationSidebar'
import CustomerChat from './components/CustomerChat'
import KnowledgePanel from './components/KnowledgePanel'
import LoginPage from './components/LoginPage'
import { api } from './lib/api'
import type { ConversationSummary, Material } from './lib/api'
import { clearAuth, getAuthUser } from './lib/auth'
import type { AuthUser } from './lib/auth'
import type { ChatMessage } from './lib/chat-provider'

const themeConfig = {
  token: {
    colorPrimary: '#2563eb',
    colorInfo: '#2563eb',
    borderRadius: 10,
    fontSize: 14,
    colorBgLayout: '#f4f7fb',
  },
}

export default function App() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(() => getAuthUser())
  const [knowledgeOpen, setKnowledgeOpen] = useState(false)
  const [conversationVersion, setConversationVersion] = useState(0)
  const [materials, setMaterials] = useState<Material[]>([])
  // 真实会话列表（数据库）+ 当前选中会话（null = 新会话）
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [conversationState, setConversationState] = useState<{
    conversationId: string
    responseMode: ChatMessage['responseMode']
    needsHuman: boolean
    needsClarification: boolean
    handoffReason: string | null
    intent?: ChatMessage['intent']
  }>({ conversationId: '', responseMode: undefined, needsHuman: false, needsClarification: false, handoffReason: null })

  // 刷新会话列表（挂载 + 每条消息发送后）
  const refreshConversations = useCallback(() => {
    api.listConversations()
      .then(setConversations)
      .catch(() => { /* 列表拉取失败静默，不阻塞聊天 */ })
  }, [])

  useEffect(() => {
    refreshConversations()
  }, [refreshConversations])

  // 运营端轮询：客户新会话/人工队列变化时侧栏自动更新。
  useEffect(() => {
    if (authUser?.role !== 'operator') return
    const timer = window.setInterval(refreshConversations, 5000)
    return () => window.clearInterval(timer)
  }, [authUser?.role, refreshConversations])

  // 稳定引用：ChatPanel 的 useEffect 依赖它，inline 函数会导致每次 render 都触发 effect
  // 从而 setConversationState → App re-render → 新 inline 引用 → effect 再触发 → 无限循环请求
  const handleConversationStateChange = useCallback(
    (state: {
      conversationId: string
      responseMode: ChatMessage['responseMode']
      needsHuman: boolean
      needsClarification: boolean
      handoffReason: string | null
      intent?: ChatMessage['intent']
    }) => {
      setConversationState(state)
      refreshConversations()
    },
    [refreshConversations],
  )

  const handleLogout = () => {
    clearAuth()
    setAuthUser(null)
  }

  if (!authUser) {
    return (
      <XProvider theme={themeConfig}>
        <AntApp><LoginPage onLogin={setAuthUser} /></AntApp>
      </XProvider>
    )
  }

  if (authUser.role === 'customer') {
    return (
      <XProvider theme={themeConfig}>
        <AntApp><CustomerChat user={authUser} onLogout={handleLogout} /></AntApp>
      </XProvider>
    )
  }

  return (
    <XProvider theme={themeConfig}>
      <AntApp>
        <div className="support-app">
          <header className="support-header">
            <div className="brand-lockup">
              <img className="brand-mark" src="/logo.png" alt="智应客服中心" />
              <div><strong>智应客服中心</strong><span>AI SERVICE DESK</span></div>
            </div>
            <div className="header-trail"><span>工作台</span><span>/</span><strong>全部会话</strong></div>
            <div className="header-actions">
              <Tooltip title="后端、检索与生成服务均在线"><span className="system-health"><i /> 系统在线</span></Tooltip>
              <span className="header-divider" />
              <div className="operator"><span className="operator-avatar">Z</span><span><strong>{authUser.display_name}</strong><small>客服运营</small></span></div>
              <Button className="logout-button" type="text" onClick={handleLogout}>退出</Button>
            </div>
          </header>

          <div className="support-layout">
            <aside className="support-sidebar">
              <ConversationSidebar
                conversations={conversations}
                activeId={activeConversationId ?? ''}
                onSelect={(conversationId) => {
                  setActiveConversationId(conversationId)
                  setMaterials([])
                  setConversationState({ conversationId, responseMode: undefined, needsHuman: false, needsClarification: false, handoffReason: null, intent: undefined })
                }}
                currentStatus={
                  conversationState.responseMode === 'manual'
                    ? 'manual'
                    : conversationState.needsHuman || conversationState.responseMode === 'handoff'
                      ? 'handoff'
                      : conversationState.needsClarification
                        ? 'waiting'
                        : 'active'
                }
                onNewConversation={() => {
                  setActiveConversationId(null)
                  setConversationVersion((value) => value + 1)
                  setMaterials([])
                  setConversationState({ conversationId: '', responseMode: undefined, needsHuman: false, needsClarification: false, handoffReason: null, intent: undefined })
                }}
                onOpenKnowledge={() => setKnowledgeOpen(true)}
              />
            </aside>
            <main className="support-main">
              <ChatPanel
                key={`${activeConversationId ?? 'new'}-${conversationVersion}`}
                conversationId={activeConversationId}
                onMaterialsChange={setMaterials}
                onConversationStateChange={handleConversationStateChange}
              />
            </main>
            <aside className="support-context">
              <ContextPanel materials={materials} {...conversationState} />
            </aside>
          </div>
        </div>
        <Modal
          title="知识库管理"
          open={knowledgeOpen}
          footer={null}
          width={800}
          styles={{ body: { padding: 0 } }}
          onCancel={() => setKnowledgeOpen(false)}
        >
          <div className="knowledge-modal-body"><KnowledgePanel /></div>
        </Modal>
      </AntApp>
    </XProvider>
  )
}
