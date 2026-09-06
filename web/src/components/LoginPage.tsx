/** 登录页：运营/客户共用，按账号角色进入对应界面。 */

import { useState } from 'react'
import { Alert, Button, Form, Input, Typography } from 'antd'
import { LockOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
import { api } from '../lib/api'
import type { AuthUser } from '../lib/auth'
import { setAuth } from '../lib/auth'

export default function LoginPage({ onLogin }: { onLogin: (user: AuthUser) => void }) {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (values: { username: string; password: string }) => {
    setError('')
    setLoading(true)
    try {
      const result = await api.login(values.username, values.password)
      setAuth(result.token, result.user)
      onLogin(result.user)
    } catch (e) {
      setError((e as Error).message || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <img className="brand-mark" src="/logo.png" alt="智应客服" />
          <Typography.Title level={3}>智应客服中心</Typography.Title>
          <Typography.Text type="secondary">登录后按角色进入运营台或客户聊天</Typography.Text>
        </div>
        {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}
        <Form layout="vertical" onFinish={handleSubmit} disabled={loading}>
          <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
            <Input prefix={<UserOutlined />} placeholder="运营：zenquan / 客户：alice" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading} icon={<RobotOutlined />}>
            登录
          </Button>
        </Form>
      </div>
    </div>
  )
}
