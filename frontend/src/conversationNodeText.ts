export function getConversationNodeText(content: string) {
  return {
    preview: `${content.slice(0, 82)}${content.length > 82 ? '…' : ''}`,
    fullMessage: content,
  }
}
