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
export const COL_STEP = NODE_WIDTH + H_GAP // 310
export const ROW_STEP = NODE_HEIGHT + V_GAP // 170

export const COLLISION_THRESHOLD_X = 260
export const COLLISION_THRESHOLD_Y = 130

const STORAGE_KEY_PREFIX = 'memory_web_node_positions_'

/**
 * Checks whether candidate position collides with any occupied positions.
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

/**
 * Computes hierarchical positions for graph nodes.
 *
 * 1. Preserves any existing positions in savedPositions (e.g. from user dragging).
 * 2. For newly added nodes, positions them near their parent node and separates branches.
 * 3. Prevents overlaps by testing bounding-box collisions and shifting to adjacent lanes.
 * 4. For initial layout with no saved positions, performs a top-down hierarchical tree layout.
 */
export function calculateGraphLayout(
  items: ApiNode[],
  edges: ApiEdge[],
  savedPositions: Record<string, Position> = {},
): Record<string, Position> {
  const positions: Record<string, Position> = {}
  const occupied: Position[] = []

  // 1. Keep all valid saved positions for nodes present in items
  const itemIds = new Set(items.map((i) => i.id))
  for (const [id, pos] of Object.entries(savedPositions)) {
    if (itemIds.has(id) && typeof pos?.x === 'number' && typeof pos?.y === 'number') {
      positions[id] = { x: pos.x, y: pos.y }
      occupied.push(positions[id])
    }
  }

  const unpositioned = items.filter((item) => !positions[item.id])
  if (unpositioned.length === 0) {
    return positions
  }

  const nodeMap = new Map<string, ApiNode>(items.map((n) => [n.id, n]))

  function getNodeDepth(node: ApiNode, visited = new Set<string>()): number {
    if (!node.parent_id || !nodeMap.has(node.parent_id) || visited.has(node.id)) {
      return 0
    }
    visited.add(node.id)
    const parent = nodeMap.get(node.parent_id)!
    return 1 + getNodeDepth(parent, visited)
  }

  // 2. If NO nodes have positions (fresh layout from scratch), build full hierarchical layout
  if (occupied.length === 0) {
    const childrenMap = new Map<string, ApiNode[]>()
    const rootNodes: ApiNode[] = []

    for (const node of items) {
      if (!node.parent_id || !nodeMap.has(node.parent_id)) {
        rootNodes.push(node)
      } else {
        const list = childrenMap.get(node.parent_id) || []
        list.push(node)
        childrenMap.set(node.parent_id, list)
      }
    }

    if (rootNodes.length === 0 && items.length > 0) {
      rootNodes.push(items[0])
    }

    const startX = 100
    const startY = 80

    // Assign positions recursively
    function placeSubtree(node: ApiNode, x: number, y: number) {
      let nodeX = x
      while (isPositionColliding({ x: nodeX, y }, occupied)) {
        nodeX += COL_STEP
      }
      positions[node.id] = { x: nodeX, y }
      occupied.push(positions[node.id])

      const children = childrenMap.get(node.id) || []
      if (children.length === 0) return

      // Separate children: continuation straight down, branches and topics to the right
      children.forEach((child, idx) => {
        const isBranchOrTopic =
          child.kind === 'topic' || child.branch_id !== node.branch_id || idx > 0
        const childX = isBranchOrTopic ? nodeX + Math.max(1, idx) * COL_STEP : nodeX
        const childY = y + ROW_STEP
        placeSubtree(child, childX, childY)
      })
    }

    rootNodes.forEach((root, idx) => {
      placeSubtree(root, startX + idx * (COL_STEP * 2), startY)
    })

    // Fallback for any disconnected nodes
    for (const item of items) {
      if (!positions[item.id]) {
        let x = startX
        let y = startY + ROW_STEP
        while (isPositionColliding({ x, y }, occupied)) {
          x += COL_STEP
        }
        positions[item.id] = { x, y }
        occupied.push(positions[item.id])
      }
    }

    return positions
  }

  // 3. Incremental layout: place newly added nodes near their parent without moving existing nodes
  // Sort topologically by depth so ancestors are placed before descendants
  const sortedUnpositioned = [...unpositioned].sort((a, b) => getNodeDepth(a) - getNodeDepth(b))

  for (const item of sortedUnpositioned) {
    const parentPos = item.parent_id ? positions[item.parent_id] : undefined
    const parentNode = item.parent_id ? nodeMap.get(item.parent_id) : undefined

    let targetX = 100
    let targetY = 80

    if (parentPos) {
      targetY = parentPos.y + ROW_STEP

      const isBranchOrTopic =
        item.kind === 'topic' || (parentNode && item.branch_id !== parentNode.branch_id)

      if (isBranchOrTopic) {
        // Find existing sibling X coordinates to offset branches
        const siblings = items.filter(
          (n) => n.parent_id === item.parent_id && n.id !== item.id && positions[n.id],
        )
        const siblingXs = siblings.map((s) => positions[s.id].x)
        const baseOffset =
          siblingXs.length > 0
            ? Math.max(...siblingXs) + COL_STEP
            : parentPos.x + COL_STEP
        targetX = baseOffset
      } else {
        // Normal continuation: target directly below parent
        targetX = parentPos.x
      }
    } else {
      // No parent: find open spot at root level
      targetY = 80
      targetX = occupied.length > 0 ? Math.max(...occupied.map((p) => p.x)) + COL_STEP : 100
    }

    // Check if item is a user message with an assistant reply that will be placed below it
    const assistantChild = items.find(
      (n) => n.parent_id === item.id && n.role === 'assistant',
    )
    const checkPairedChild = assistantChild !== undefined

    // Check for collisions and shift right until free for both item and its paired child
    while (
      isPositionColliding({ x: targetX, y: targetY }, occupied) ||
      (checkPairedChild &&
        isPositionColliding({ x: targetX, y: targetY + ROW_STEP }, occupied))
    ) {
      targetX += COL_STEP
    }

    positions[item.id] = { x: targetX, y: targetY }
    occupied.push(positions[item.id])
  }

  return positions
}
