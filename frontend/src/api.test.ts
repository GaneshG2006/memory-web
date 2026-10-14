import assert from 'node:assert/strict'
import test from 'node:test'
import { createBasicAuthHeader, getApiUrl } from './api.ts'

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
