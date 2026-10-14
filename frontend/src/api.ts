const DEFAULT_API_URL = 'http://127.0.0.1:8000'

export type ApiCredentials = {
  username: string
  password: string
}

export function getApiUrl(
  path: string,
  baseUrl: string | undefined = import.meta.env?.VITE_API_URL,
): string {
  const base = (baseUrl?.trim() || DEFAULT_API_URL).replace(/\/+$/, '')
  return `${base}/${path.replace(/^\/+/, '')}`
}

export function createBasicAuthHeader(credentials: ApiCredentials): string {
  return `Basic ${btoa(`${credentials.username}:${credentials.password}`)}`
}

export function apiFetch(
  path: string,
  credentials: ApiCredentials,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Authorization', createBasicAuthHeader(credentials))
  return fetch(getApiUrl(path), { ...init, headers })
}
