/** RAG 产品主界面：左知识库管理 + 右 AI 问答 */

import { Layout, theme } from 'antd'
import { App as AntApp, Flex } from 'antd'
import { XProvider } from '@ant-design/x'
import ChatPanel from './components/ChatPanel'
import KnowledgePanel from './components/KnowledgePanel'

const { Header, Sider, Content } = Layout

export default function App() {
  const { token } = theme.useToken()

  return (
    <XProvider>
      <AntApp>
        <Layout style={{ height: '100vh' }}>
          <Header
            style={{
              background: token.colorBgContainer,
              borderBottom: `1px solid ${token.colorBorderSecondary}`,
              display: 'flex',
              alignItems: 'center',
              paddingInline: 24,
            }}
          >
            <Flex align="center" gap={12}>
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 8,
                  background: token.colorPrimary,
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 700,
                  fontSize: 16,
                }}
              >
                R
              </div>
              <div>
                <div style={{ fontWeight: 600, lineHeight: 1.2 }}>RAG 知识问答</div>
                <div style={{ fontSize: 12, color: token.colorTextSecondary, lineHeight: 1.2 }}>
                  FastAPI × RAG · MinerU → FastEmbed → Qdrant → DeepSeek
                </div>
              </div>
            </Flex>
          </Header>
          <Layout>
            <Sider
              width={340}
              style={{
                background: token.colorBgContainer,
                borderRight: `1px solid ${token.colorBorderSecondary}`,
              }}
              theme="light"
            >
              <KnowledgePanel />
            </Sider>
            <Content style={{ background: token.colorBgLayout, minWidth: 0 }}>
              <ChatPanel />
            </Content>
          </Layout>
        </Layout>
      </AntApp>
    </XProvider>
  )
}