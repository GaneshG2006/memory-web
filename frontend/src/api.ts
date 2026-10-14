const DEFAULT_API_URL = 'http://127.0.0.1:8000'

export function getApiUrl(
  path: string,
  baseUrl: string | undefined = import.meta.env?.VITE_API_URL,
): string {
  const base = (baseUrl?.trim() || DEFAULT_API_URL).replace(/\/+$/, '')
  return `${base}/${path.replace(/^\/+/, '')}`
}
