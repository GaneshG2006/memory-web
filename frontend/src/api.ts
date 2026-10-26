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

export async function parseApiError(response: Response): Promise<string> {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    try {
      const data = await response.json()
      if (typeof data?.detail === 'string') {
        return data.detail
      }
      if (Array.isArray(data?.detail)) {
        const errorStrings = data.detail.map((err: unknown) => {
          if (typeof err === 'string') return err
          if (err && typeof err === 'object') {
            const errorObj = err as { loc?: unknown[]; msg?: unknown }
            const loc = Array.isArray(errorObj.loc)
              ? errorObj.loc.filter((segment) => segment !== 'body').join('.')
              : ''
            const msg = typeof errorObj.msg === 'string' ? errorObj.msg : JSON.stringify(err)
            return loc ? `${loc}: ${msg}` : msg
          }
          return String(err)
        })
        if (errorStrings.length > 0) {
          return errorStrings.join('; ')
        }
      }
      if (typeof data?.message === 'string') {
        return data.message
      }
      if (typeof data?.error === 'string') {
        return data.error
      }
    } catch {
      // JSON parsing failed despite header
    }
  } else {
    try {
      const text = await response.text()
      if (text && text.trim().length > 0) {
        const stripped = text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
        if (stripped.length > 0) {
          return stripped.length > 120 ? `${stripped.slice(0, 117)}...` : stripped
        }
      }
    } catch {
      // Reading text body failed
    }
  }
  return `Error ${response.status}: ${response.statusText || 'Request failed'}`
}

export async function parseFollowUpApiError(response: Response): Promise<string> {
  if (response.status === 503) {
    return 'The Gemini AI service is unavailable or not configured. Check the backend settings, then try again.'
  }
  if (response.status === 504) {
    return 'The Gemini AI service timed out. Please try again.'
  }
  if (response.status === 429) {
    return 'The Gemini API quota or rate limit was reached. Please try again later.'
  }
  if (response.status === 502) {
    return 'The AI service returned an invalid response. Please try again.'
  }
  return parseApiError(response)
}

function isFollowUpTimeoutError(error: unknown): boolean {
  if (error && typeof error === 'object' && 'name' in error) {
    if (error.name === 'AbortError' || error.name === 'TimeoutError') {
      return true
    }
  }
  return error instanceof Error && /\b(timeout|timed out|deadline exceeded)\b/i.test(error.message)
}

export async function parseFollowUpResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await parseFollowUpApiError(response))
  }
  try {
    return await response.json() as T
  } catch (error) {
    if (isFollowUpTimeoutError(error)) {
      throw error
    }
    throw new Error('Received invalid JSON from API')
  }
}

export function getFollowUpRequestErrorMessage(error: unknown): string {
  if (isFollowUpTimeoutError(error)) {
    return 'The follow-up request timed out. Please try again.'
  }
  if (error instanceof TypeError) {
    return "Couldn't reach the Memory Web API. Check that the backend is running and your network connection is available, then try again."
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return 'Could not create follow-up. Please try again.'
}
