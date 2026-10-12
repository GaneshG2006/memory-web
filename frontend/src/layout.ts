export type Position = { x: number; y: number }

export type ApiNode = {
  id: string
  conversation_id: string
  parent_id: string | null
  branch_id: string
  role: string
  content: string
  kind: string
}

export type ApiEdge = {
  id: string
  source: string
  target: string
  type: string
}

export const NODE_WIDTH = 235
export const NODE_HEIGHT = 95
export const H_GAP = 75
export const V_GAP = 75
export const COL_STEP = NODE_WIDTH + H_GAP
export const ROW_STEP = NODE_HEIGHT + V_GAP

export const COLLISION_THRESHOLD_X = COL_STEP
export const COLLISION_THRESHOLD_Y = ROW_STEP

const STORAGE_KEY_PREFIX = 'memory_web_node_positions_'

/**
 * Checks whether candidate position collides with any occupied positions,
 * using the node dimensions and configured layout spacing.
 */
export function isPositionColliding(
  candidate: Position,
  occupied: Position[],
  paddingX = COLLISION_THRESHOLD_X,
  paddingY = COLLISION_THRESHOLD_Y,
): boolean {
  return occupied.some(
    (pos) => Math.abs(candidate.x - pos.x) < paddingX && Math.abs(candidate.y - pos.y) < paddingY,
  )
}

/**
 * Reads saved node positions from sessionStorage for the current browser session.
 */
export function getSessionPositions(conversationId: string): Record<string, Position> {
  if (typeof window === 'undefined' || !window.sessionStorage) return {}
  try {
    const raw = window.sessionStorage.getItem(`${STORAGE_KEY_PREFIX}${conversationId}`)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') {
        return parsed as Record<string, Position>
      }
    }
  } catch {
    // sessionStorage read fallback
  }
  return {}
}

/**
 * Saves all node positions to sessionStorage for the current browser session.
 */
export function saveSessionPositions(
  conversationId: string,
  positions: Record<string, Position>,
): void {
  if (typeof window === 'undefined' || !window.sessionStorage) return
  try {
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}${conversationId}`,
      JSON.stringify(positions),
    )
  } catch {
    // sessionStorage write fallback
  }
}

/**
 * Saves or updates a single node's position (e.g. after user drag).
 */
export function updateSingleSessionPosition(
  conversationId: string,
  nodeId: string,
  position: Position,
): void {
  const current = getSessionPositions(conversationId)
  current[nodeId] = { x: Math.round(position.x), y: Math.round(position.y) }
  saveSessionPositions(conversationId, current)
}

type Relationship = 'branch' | 'linear' | 'other'

function relationshipFor(
  child: ApiNode,
  parent: ApiNode,
  edgeTypes: Map<string, string>,
): Relationship {
  const edgeType = edgeTypes.get(`${parent.id}\0${child.id}`)
  if (edgeType === 'branch') return 'branch'
  if (edgeType === 'continue' || edgeType === 'reply') return 'linear'
  return child.branch_id !== parent.branch_id ? 'branch' : 'other'
}

function compareNodes(a: ApiNode, b: ApiNode): number {
  return a.id.localeCompare(b.id)
}

function branchLane(index: number): number {
  const distance = Math.floor(index / 2) + 1
  return index % 2 === 0 ? distance : -distance
}

function findOpenX(
  preferredX: number,
  y: number,
  occupied: Position[],
  reservedRows: number[] = [],
): number {
  const isOpen = (x: number) =>
    !isPositionColliding({ x, y }, occupied) &&
    reservedRows.every((row) => !isPositionColliding({ x, y: row }, occupied))
  if (isOpen(preferredX)) return preferredX
  for (let distance = 1; ; distance += 1) {
    const rightX = preferredX + distance * COL_STEP
    if (isOpen(rightX)) return rightX
    const leftX = preferredX - distance * COL_STEP
    if (isOpen(leftX)) return leftX
  }
}

/**
 * Computes hierarchical positions for graph nodes.
 *
 * Existing saved positions are retained exactly. New nodes are placed from
 * parent/relationship data in stable ID order and shifted into open lanes.
 */
export function calculateGraphLayout(
  items: ApiNode[],
  edges: ApiEdge[],
  savedPositions: Record<string, Position> = {},
): Record<string, Position> {
  const positions: Record<string, Position> = {}
  const occupied: Position[] = []
  const itemIds = new Set(items.map((item) => item.id))

  for (const [id, pos] of Object.entries(savedPositions)) {
    if (
      itemIds.has(id) &&
      typeof pos?.x === 'number' &&
      Number.isFinite(pos.x) &&
      typeof pos?.y === 'number' &&
      Number.isFinite(pos.y)
    ) {
      positions[id] = { x: pos.x, y: pos.y }
      occupied.push(positions[id])
    }
  }

  const nodeMap = new Map(items.map((node) => [node.id, node]))
  const edgeTypes = new Map<string, string>()
  const edgePriority: Record<string, number> = { branch: 3, continue: 2, reply: 1 }
  for (const edge of [...edges].sort((a, b) => {
    const sourceOrder = a.source.localeCompare(b.source)
    if (sourceOrder !== 0) return sourceOrder
    const targetOrder = a.target.localeCompare(b.target)
    return targetOrder !== 0 ? targetOrder : a.type.localeCompare(b.type)
  })) {
    const key = `${edge.source}\0${edge.target}`
    const currentType = edgeTypes.get(key)
    if (!currentType || (edgePriority[edge.type] ?? 0) > (edgePriority[currentType] ?? 0)) {
      edgeTypes.set(key, edge.type)
    }
  }

  const children = new Map<string, ApiNode[]>()
  const roots: ApiNode[] = []
  for (const node of items) {
    const parent = node.parent_id ? nodeMap.get(node.parent_id) : undefined
    if (!parent || parent.id === node.id) {
      roots.push(node)
      continue
    }
    const siblings = children.get(parent.id) ?? []
    siblings.push(node)
    children.set(parent.id, siblings)
  }
  for (const siblings of children.values()) siblings.sort(compareNodes)
  roots.sort(compareNodes)

  const depthCache = new Map<string, number>()
  function getDepth(node: ApiNode, path = new Set<string>()): number {
    const cached = depthCache.get(node.id)
    if (cached !== undefined) return cached
    if (!node.parent_id || path.has(node.id)) return 0
    const parent = nodeMap.get(node.parent_id)
    if (!parent || path.has(parent.id)) return 0
    const nextPath = new Set(path)
    nextPath.add(node.id)
    const depth = getDepth(parent, nextPath) + 1
    depthCache.set(node.id, depth)
    return depth
  }

  const pending = items
    .filter((item) => !positions[item.id])
    .sort((a, b) => getDepth(a) - getDepth(b) || compareNodes(a, b))
  const rootIndex = new Map(roots.map((root, index) => [root.id, index]))

  for (const item of pending) {
    const parent = item.parent_id ? nodeMap.get(item.parent_id) : undefined
    const parentPosition = parent ? positions[parent.id] : undefined
    let preferredX: number
    let targetY: number

    if (parent && parentPosition) {
      targetY = parentPosition.y + ROW_STEP
      const siblings = children.get(parent.id) ?? []
      const relationship = relationshipFor(item, parent, edgeTypes)

      if (item.kind === 'topic') {
        const topics = siblings.filter((sibling) => sibling.kind === 'topic')
        preferredX = parentPosition.x + (topics.findIndex((sibling) => sibling.id === item.id) + 1) * COL_STEP
      } else if (relationship === 'branch') {
        const branchIds = [...new Set(
          siblings
            .filter((sibling) => relationshipFor(sibling, parent, edgeTypes) === 'branch')
            .map((sibling) => sibling.branch_id || sibling.id),
        )].sort((a, b) => a.localeCompare(b))
        const branchIndex = branchIds.indexOf(item.branch_id || item.id)
        preferredX = parentPosition.x + branchLane(Math.max(0, branchIndex)) * COL_STEP
      } else {
        preferredX = parentPosition.x
      }
    } else {
      targetY = 80
      const index = rootIndex.get(item.id) ?? roots.length
      preferredX = 100 + index * COL_STEP * 2
    }

    const reservedRows = (children.get(item.id) ?? [])
      .filter(
        (child) =>
          child.role === 'assistant' &&
          !positions[child.id] &&
          relationshipFor(child, item, edgeTypes) !== 'branch',
      )
      .map(() => targetY + ROW_STEP)
    const x = findOpenX(preferredX, targetY, occupied, reservedRows)
    positions[item.id] = { x, y: targetY }
    occupied.push(positions[item.id])
  }

  return positions
}
