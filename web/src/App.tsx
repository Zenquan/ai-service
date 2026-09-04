/** 智应客服中心：会话队列 + AI 对话 + 实时上下文。 */

import { useState } from 'react'
import { App as AntApp, Modal, Tooltip } from 'antd'
import { XProvider } from '@ant-design/x'
import ChatPanel from './components/ChatPanel'
import ContextPanel from './components/ContextPanel'
import ConversationSidebar from './components/ConversationSidebar'
import KnowledgePanel from './components/KnowledgePanel'
import type { Material } from './lib/api'
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
  const [knowledgeOpen, setKnowledgeOpen] = useState(false)
  const [conversationVersion, setConversationVersion] = useState(0)
  const [materials, setMaterials] = useState<Material[]>([])
  const [conversationState, setConversationState] = useState<{
    conversationId: string
    responseMode: ChatMessage['responseMode']
    needsHuman: boolean
    needsClarification: boolean
    handoffReason: string | null
  }>({ conversationId: '', responseMode: undefined, needsHuman: false, needsClarification: false, handoffReason: null })

  return (
    <XProvider theme={themeConfig}>
      <AntApp>
        <div className="support-app">
          <header className="support-header">
            <div className="brand-lockup">
              <div className="brand-mark">N</div>
              <div><strong>智应客服中心</strong><span>AI SERVICE DESK</span></div>
            </div>
            <div className="header-trail"><span>工作台</span><span>/</span><strong>全部会话</strong></div>
            <div className="header-actions">
              <Tooltip title="后端、检索与生成服务均在线"><span className="system-health"><i /> 系统在线</span></Tooltip>
              <span className="header-divider" />
              <div className="operator"><span className="operator-avatar">Z</span><span><strong>Zenquan</strong><small>客服运营</small></span></div>
            </div>
          </header>

          <div className="support-layout">
            <aside className="support-sidebar">
              <ConversationSidebar
                currentStatus={conversationState.needsHuman ? 'handoff' : conversationState.needsClarification ? 'waiting' : 'active'}
                onNewConversation={() => {
                  setConversationVersion((value) => value + 1)
                  setMaterials([])
                  setConversationState({ conversationId: '', responseMode: undefined, needsHuman: false, needsClarification: false, handoffReason: null })
                }}
                onOpenKnowledge={() => setKnowledgeOpen(true)}
              />
            </aside>
            <main className="support-main">
              <ChatPanel key={conversationVersion} onMaterialsChange={setMaterials} onConversationStateChange={setConversationState} />
            </main>
            <aside className="support-context">
              <ContextPanel materials={materials} onOpenKnowledge={() => setKnowledgeOpen(true)} {...conversationState} />
            </aside>
          </div>
        </div>
        <Modal title="知识库管理" open={knowledgeOpen} footer={null} width={520} onCancel={() => setKnowledgeOpen(false)}>
          <div className="knowledge-modal-body"><KnowledgePanel /></div>
        </Modal>
      </AntApp>
    </XProvider>
  )
}
