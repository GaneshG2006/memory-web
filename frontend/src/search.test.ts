import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getSearchStatus,
  normalizeSearchQuery,
  resolveSearchSelection,
} from './search.ts'
import type { ApiNode } from './layout.ts'

test('empty searches ask for a search term', () => {
  assert.equal(normalizeSearchQuery('   '), '')
  assert.equal(getSearchStatus('   ', 0, true), 'Enter a search term.')
})

test('search reports when there are no matching nodes', () => {
  assert.equal(
    getSearchStatus('not present', 0, true),
    'No matching messages or topics.',
  )
  assert.equal(getSearchStatus('match', 1, true), '1 result found.')
})

test('search result focus resolves the existing graph node position', () => {
  const graphNodes: ApiNode[] = [
    {
      id: 'answer',
      conversation_id: 'demo',
      parent_id: 'welcome',
      branch_id: 'main',
      role: 'assistant',
      content: 'Existing answer',
      kind: 'message',
    },
  ]
  const nodes = [
    { id: 'welcome', position: { x: 10, y: 20 } },
    { id: 'answer', position: { x: 30, y: 40 } },
  ]

  assert.deepEqual(resolveSearchSelection('answer', graphNodes, nodes), {
    node: graphNodes[0],
    position: { x: 30, y: 40 },
  })
  assert.equal(resolveSearchSelection('missing', graphNodes, nodes), null)
})
