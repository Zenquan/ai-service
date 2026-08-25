/** RAG 产品主界面：左知识库管理 + 右 AI 问答
 *
 * UI 设计：
 * - 统一主色（#1677ff 蓝），红/绿仅用于状态语义（删除/成功）
 * - 左面板固定宽度 340，内部独立滚动；右对话区消息流独立滚动，Sender 固定底部
 * - 顶部 Header：Logo + 标题 + 技术链路，右侧放后端状态灯
 */

import { Layout, theme } from 'antd'
import { App as AntApp, Flex, Tooltip } from 'antd'
import { XProvider } from '@ant-design/x'
import type { ThemeConfig } from 'antd'
import ChatPanel from './components/ChatPanel'
import KnowledgePanel from './components/KnowledgePanel'

const { Header, Sider, Content } = Layout

// 全局主题：统一主色 / 圆角 / 组件尺寸
const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: '#1677ff',
    colorInfo: '#1677ff',
    borderRadius: 8,
    fontSize: 14,
    colorBgLayout: '#f6f8fc',
  },
  components: {
    Layout: {
      headerHeight: 56,
      headerPadding: '0 20px',
    },
    Card: { borderRadiusLG: 12 },
  },
}

// 右侧对话区：纯白背景（替代浅灰）
const CONTENT_BG = '#ffffff'

export default function App() {
  const { token } = theme.useToken()

  return (
    <XProvider theme={themeConfig}>
      <AntApp>
        <Layout style={{ height: '100vh', overflow: 'hidden' }}>
          <Header
            style={{
              background: token.colorBgContainer,
              borderBottom: `1px solid ${token.colorBorderSecondary}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              position: 'relative',
              zIndex: 10,
            }}
          >
            <Flex align="center" gap={12}>
              {/* Logo */}
              <div
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: 10,
                  background: 'linear-gradient(135deg, #1677ff 0%, #69b1ff 100%)',
                  color: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 700,
                  fontSize: 17,
                  boxShadow: '0 2px 8px rgba(22,119,255,0.35)',
                }}
              >
                R
              </div>
              <div>
                <div style={{ fontWeight: 600, lineHeight: 1.25, fontSize: 15 }}>RAG 知识问答</div>
                <div style={{ fontSize: 11.5, color: token.colorTextTertiary, lineHeight: 1.25 }}>
                  MinerU → FastEmbed → Qdrant → DeepSeek
                </div>
              </div>
            </Flex>

            {/* 右侧：后端状态点 */}
            <Tooltip title="后端服务在线">
              <Flex align="center" gap={8}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: '#52c41a',
                    display: 'inline-block',
                    boxShadow: '0 0 0 3px rgba(82,196,26,0.15)',
                  }}
                />
                <span style={{ fontSize: 12, color: token.colorTextTertiary }}>在线</span>
              </Flex>
            </Tooltip>
          </Header>

          <Layout>
            <Sider
              width={340}
              theme="light"
              style={{
                background: token.colorBgContainer,
                borderRight: `1px solid ${token.colorBorderSecondary}`,
                height: '100%',
                overflow: 'hidden',
              }}
            >
              <KnowledgePanel />
            </Sider>
            <Content
              style={{
                background: CONTENT_BG,
                minWidth: 0,
                height: '100%',
                overflow: 'hidden',
                borderLeft: '1px solid rgba(0,0,0,0.04)',
              }}
            >
              <ChatPanel />
            </Content>
          </Layout>
        </Layout>
      </AntApp>
    </XProvider>
  )
}