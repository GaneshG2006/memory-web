import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createBasicAuthHeader,
  getApiUrl,
  getFollowUpRequestErrorMessage,
  parseApiError,
  parseFollowUpResponse,
} from './api.ts'

test('API URL defaults to the local development server', () => {
  assert.equal(
    getApiUrl('/api/conversations/demo/graph', undefined),
    'http://127.0.0.1:8000/api/conversations/demo/graph',
  )
})

test('API URL uses the configured base URL', () => {
  assert.equal(
    getApiUrl('/api/follow-ups', 'https://memory-api.example.com'),
    'https://memory-api.example.com/api/follow-ups',
  )
})

test('API URL removes extra slashes when joining base and path', () => {
  assert.equal(
    getApiUrl('///api/conversations/demo/search?q=term', 'https://memory-api.example.com///'),
    'https://memory-api.example.com/api/conversations/demo/search?q=term',
  )
})

test('blank API URL uses the local development server', () => {
  assert.equal(
    getApiUrl('/api/follow-ups', '  '),
    'http://127.0.0.1:8000/api/follow-ups',
  )
})

test('Basic authorization header encodes the entered credentials', () => {
  assert.equal(
    createBasicAuthHeader({ username: 'demo-user', password: 'demo-password' }),
    `Basic ${btoa('demo-user:demo-password')}`,
  )
})

test('follow-up 503 errors explain AI service availability and configuration', async () => {
  const response = Response.json(
    { detail: 'Gemini API is not configured' },
    { status: 503 },
  )

  await assert.rejects(parseFollowUpResponse(response), (error: unknown) => {
    assert.ok(error instanceof Error)
    assert.equal(
      error.message,
      'The Gemini AI service is unavailable or not configured. Check the backend settings, then try again.',
    )
    return true
  })
})

test('follow-up 504 errors explain Gemini API timeouts', async () => {
  const response = Response.json(
    { detail: 'Gemini API request timed out. Please try again.' },
    { status: 504 },
  )

  await assert.rejects(parseFollowUpResponse(response), (error: unknown) => {
    assert.ok(error instanceof Error)
    assert.equal(error.message, 'The Gemini AI service timed out. Please try again.')
    return true
  })
})

test('follow-up 429 errors explain Gemini API quota limits', async () => {
  const response = Response.json(
    { detail: 'Gemini API quota or rate limit reached' },
    { status: 429 },
  )

  await assert.rejects(parseFollowUpResponse(response), (error: unknown) => {
    assert.ok(error instanceof Error)
    assert.equal(
      error.message,
      'The Gemini API quota or rate limit was reached. Please try again later.',
    )
    return true
  })
})

test('follow-up 502 errors explain invalid AI responses', async () => {
  const response = Response.json(
    { detail: 'Gemini API returned an invalid response' },
    { status: 502 },
  )

  await assert.rejects(parseFollowUpResponse(response), (error: unknown) => {
    assert.ok(error instanceof Error)
    assert.equal(error.message, 'The AI service returned an invalid response. Please try again.')
    return true
  })
})

test('follow-up API errors preserve details for other status codes', async () => {
  const response = Response.json(
    { detail: 'Parent node not found' },
    { status: 404 },
  )

  await assert.rejects(parseFollowUpResponse(response), {
    message: 'Parent node not found',
  })
})

test('general API errors do not use follow-up-specific 503 messaging', async () => {
  const response = Response.json(
    { detail: 'The API is temporarily unavailable' },
    { status: 503 },
  )

  assert.equal(await parseApiError(response), 'The API is temporarily unavailable')
})

test('follow-up request errors explain network failures', () => {
  assert.match(
    getFollowUpRequestErrorMessage(new TypeError('Failed to fetch')),
    /Couldn.t reach the Memory Web API.*backend is running/,
  )
})

test('follow-up request errors explain client-side timeouts', () => {
  const timeoutError = new Error('The operation was aborted')
  timeoutError.name = 'AbortError'

  assert.equal(
    getFollowUpRequestErrorMessage(timeoutError),
    'The follow-up request timed out. Please try again.',
  )
})

test('follow-up response parsing preserves abort and timeout exceptions', async () => {
  for (const timeoutError of [
    Object.assign(new Error('The operation was aborted'), { name: 'AbortError' }),
    Object.assign(new Error('Request timed out'), { name: 'TimeoutError' }),
  ]) {
    const response = new Response('{}')
    response.json = async () => {
      throw timeoutError
    }

    await assert.rejects(parseFollowUpResponse(response), (error) => error === timeoutError)
    assert.equal(
      getFollowUpRequestErrorMessage(timeoutError),
      'The follow-up request timed out. Please try again.',
    )
  }
})

test('follow-up response parsing reports malformed successful JSON', async () => {
  const response = new Response('{ invalid json')

  await assert.rejects(parseFollowUpResponse(response), {
    message: 'Received invalid JSON from API',
  })
})
