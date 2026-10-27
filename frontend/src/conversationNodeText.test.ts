import assert from 'node:assert/strict'
import test from 'node:test'
import { getConversationNodeText } from './conversationNodeText.ts'

test('conversation node text keeps the preview short and preserves the full message', () => {
  const content = `${'A long conversation message. '.repeat(8)}Final sentence.`
  const { preview, fullMessage } = getConversationNodeText(content)

  assert.equal(preview, `${content.slice(0, 82)}…`)
  assert.equal(preview.length, 83)
  assert.equal(fullMessage, content)
})

test('short conversation messages are not truncated', () => {
  const content = 'A short message.'
  const { preview, fullMessage } = getConversationNodeText(content)

  assert.equal(preview, content)
  assert.equal(fullMessage, content)
})
