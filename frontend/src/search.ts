import type { ApiNode, Position } from './layout'

export type SearchResult = {
  id: string
  conversation_id: string
  parent_id: string | null
  branch_id: string
  role: string
  kind: string
  excerpt: string
}

export function normalizeSearchQuery(query: string): string {
  return query.trim()
}

export function getSearchStatus(
  query: string,
  resultCount: number,
  hasSearched: boolean,
): string | null {
  if (!hasSearched) return null
  if (!normalizeSearchQuery(query)) return 'Enter a search term.'
  if (resultCount === 0) return 'No matching messages or topics.'
  return `${resultCount} result${resultCount === 1 ? '' : 's'} found.`
}

export function getSearchFocus(
  nodeId: string,
  nodes: readonly { id: string; position: Position }[],
): { position: Position } | null {
  const node = nodes.find((item) => item.id === nodeId)
  return node ? { position: node.position } : null
}

export function resolveSearchSelection(
  nodeId: string,
  graphNodes: readonly ApiNode[],
  flowNodes: readonly { id: string; position: Position }[],
): { node: ApiNode; position: Position } | null {
  const node = graphNodes.find((item) => item.id === nodeId)
  const focus = getSearchFocus(nodeId, flowNodes)
  return node && focus ? { node, position: focus.position } : null
}
