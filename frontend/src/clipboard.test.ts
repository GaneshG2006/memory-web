import assert from 'node:assert/strict'
import test from 'node:test'
import { copyTextToClipboard } from './clipboard.ts'

test('copies the supplied message text without modification', async () => {
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  let copiedText = ''
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { clipboard: { writeText: async (text: string) => { copiedText = text } } },
  })

  try {
    const message = 'Full conversation message, including its ending.'
    await copyTextToClipboard(message)
    assert.equal(copiedText, message)
  } finally {
    if (originalNavigator) {
      Object.defineProperty(globalThis, 'navigator', originalNavigator)
    } else {
      Reflect.deleteProperty(globalThis, 'navigator')
    }
  }
})

test('reports clipboard write failures to the caller', async () => {
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { clipboard: { writeText: async () => { throw new Error('Permission denied') } } },
  })

  try {
    await assert.rejects(copyTextToClipboard('message'), { message: 'Permission denied' })
  } finally {
    if (originalNavigator) {
      Object.defineProperty(globalThis, 'navigator', originalNavigator)
    } else {
      Reflect.deleteProperty(globalThis, 'navigator')
    }
  }
})
