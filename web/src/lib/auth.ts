/** 登录态：token 与当前用户（localStorage，开发用）。 */

export type AuthRole = 'operator' | 'customer'

export interface AuthUser {
  id: string
  username: string
  role: AuthRole
  display_name: string
}

const TOKEN_KEY = 'ai-service-token'
const USER_KEY = 'ai-service-user'

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, value)
    }
  } catch {
    /* localStorage 不可用时仅当前会话生效 */
  }
}

let memoryToken = readStorage(TOKEN_KEY) ?? ''
let memoryUser: AuthUser | null = (() => {
  const raw = readStorage(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthUser
  } catch {
    return null
  }
})()

export function getAuthToken(): string {
  return memoryToken
}

export function getAuthUser(): AuthUser | null {
  return memoryUser
}

export function setAuth(token: string, user: AuthUser): void {
  memoryToken = token
  memoryUser = user
  writeStorage(TOKEN_KEY, token)
  writeStorage(USER_KEY, JSON.stringify(user))
}

export function clearAuth(): void {
  memoryToken = ''
  memoryUser = null
  writeStorage(TOKEN_KEY, null)
  writeStorage(USER_KEY, null)
}
