import assert from 'node:assert/strict'
import test from 'node:test'
import {
  calculateGraphLayout,
  isPositionColliding,
  ROW_STEP,
  type ApiNode,
  type ApiEdge,
  type Position,
} from './layout.ts'

const seedNodes: ApiNode[] = [
  {
    id: 'welcome',
    conversation_id: 'demo',
    parent_id: null,
    branch_id: 'main',
    role: 'user',
    content: 'Explain microservices architecture.',
    kind: 'message',
  },
  {
    id: 'answer',
    conversation_id: 'demo',
    parent_id: 'welcome',
    branch_id: 'main',
    role: 'assistant',
    content: 'Microservices split an application...',
    kind: 'message',
  },
  {
    id: 'docker',
    conversation_id: 'demo',
    parent_id: 'answer',
    branch_id: 'main',
    role: 'topic',
    content: 'Docker: package an application...',
    kind: 'topic',
  },
]

const seedEdges: ApiEdge[] = [
  { id: 'e1', source: 'welcome', target: 'answer', type: 'reply' },
  { id: 'e2', source: 'answer', target: 'docker', type: 'mentions' },
]

function assertNoCollisions(positions: Record<string, Position>) {
  const entries = Object.entries(positions)
  for (let i = 0; i < entries.length; i++) {
    for (let j = i + 1; j < entries.length; j++) {
      const [idA, posA] = entries[i]
      const [idB, posB] = entries[j]
      const collides = isPositionColliding(posA, [posB])
      assert.equal(
        collides,
        false,
        `Node ${idA} at (${posA.x}, ${posA.y}) collides with node ${idB} at (${posB.x}, ${posB.y})`,
      )
    }
  }
}

test('initial layout places nodes hierarchically without overlap', () => {
  const positions = calculateGraphLayout(seedNodes, seedEdges, {})
  assert.ok(positions.welcome, 'welcome should have position')
  assert.ok(positions.answer, 'answer should have position')
  assert.ok(positions.docker, 'docker should have position')

  // Child should be lower than parent
  assert.ok(positions.answer.y > positions.welcome.y, 'answer should be below welcome')
  assert.ok(positions.docker.y > positions.welcome.y, 'docker should be below welcome')

  // Topic should be separated horizontally from main reply
  assert.ok(positions.docker.x > positions.answer.x, 'docker topic should be separated to the side')

  assertNoCollisions(positions)
})

test('layout is stable when API nodes and edges arrive out of order', () => {
  const branchNodes: ApiNode[] = [
    ...seedNodes,
    {
      id: 'branch_b',
      conversation_id: 'demo',
      parent_id: 'answer',
      branch_id: 'branch-b',
      role: 'user',
      content: 'Branch B',
      kind: 'message',
    },
    {
      id: 'branch_a',
      conversation_id: 'demo',
      parent_id: 'answer',
      branch_id: 'branch-a',
      role: 'user',
      content: 'Branch A',
      kind: 'message',
    },
  ]
  const branchEdges: ApiEdge[] = [
    ...seedEdges,
    { id: 'branch-edge-b', source: 'answer', target: 'branch_b', type: 'branch' },
    { id: 'branch-edge-a', source: 'answer', target: 'branch_a', type: 'branch' },
  ]

  const positions = calculateGraphLayout(branchNodes, branchEdges)
  const reversedPositions = calculateGraphLayout(
    [...branchNodes].reverse(),
    [...branchEdges].reverse(),
  )

  assert.deepEqual(reversedPositions, positions)
})

test('preserves user-dragged node positions across refreshes', () => {
  const saved: Record<string, Position> = {
    welcome: { x: 350, y: 150 },
    answer: { x: 350, y: 350 },
  }

  const positions = calculateGraphLayout(seedNodes, seedEdges, saved)

  // Dragged positions must be preserved exactly
  assert.equal(positions.welcome.x, 350)
  assert.equal(positions.welcome.y, 150)
  assert.equal(positions.answer.x, 350)
  assert.equal(positions.answer.y, 350)

  // Unpositioned node is placed near its parent without collision
  assert.ok(positions.docker)
  assertNoCollisions(positions)
})

test('preserves saved positions exactly, including saved overlaps', () => {
  const saved: Record<string, Position> = {
    welcome: { x: 350, y: 150 },
    answer: { x: 350, y: 150 },
  }

  const positions = calculateGraphLayout(seedNodes, seedEdges, saved)

  assert.deepEqual(positions.welcome, saved.welcome)
  assert.deepEqual(positions.answer, saved.answer)
})

test('newly placed nodes avoid existing positions', () => {
  const saved: Record<string, Position> = {
    welcome: { x: 100, y: 80 },
    answer: { x: 100, y: 250 },
    blocker: { x: 100, y: 420 },
  }
  const nodes: ApiNode[] = [
    ...seedNodes.slice(0, 2),
    {
      id: 'blocker',
      conversation_id: 'demo',
      parent_id: null,
      branch_id: 'main',
      role: 'user',
      content: 'Existing node occupying the target position',
      kind: 'message',
    },
    {
      id: 'new_user',
      conversation_id: 'demo',
      parent_id: 'answer',
      branch_id: 'main',
      role: 'user',
      content: 'New continuation',
      kind: 'message',
    },
  ]

  const positions = calculateGraphLayout(nodes, seedEdges, saved)

  assert.deepEqual(positions.welcome, saved.welcome)
  assert.deepEqual(positions.answer, saved.answer)
  assert.notDeepEqual(positions.new_user, saved.blocker)
  assert.equal(isPositionColliding(positions.new_user, Object.values(saved)), false)
})

test('positions new continuation nodes near parent without rearranging existing nodes', () => {
  const initialPositions = calculateGraphLayout(seedNodes, seedEdges, {})
  assertNoCollisions(initialPositions)

  // Add a continuation turn from "answer"
  const updatedNodes: ApiNode[] = [
    ...seedNodes,
    {
      id: 'cont_user',
      conversation_id: 'demo',
      parent_id: 'answer',
      branch_id: 'main',
      role: 'user',
      content: 'Tell me about databases.',
      kind: 'message',
    },
    {
      id: 'cont_answer',
      conversation_id: 'demo',
      parent_id: 'cont_user',
      branch_id: 'main',
      role: 'assistant',
      content: 'Databases store data.',
      kind: 'message',
    },
  ]

  const newPositions = calculateGraphLayout(updatedNodes, seedEdges, initialPositions)

  // Existing nodes MUST retain their exact coordinates
  assert.deepEqual(newPositions.welcome, initialPositions.welcome)
  assert.deepEqual(newPositions.answer, initialPositions.answer)
  assert.deepEqual(newPositions.docker, initialPositions.docker)

  // New nodes are positioned below parent
  assert.ok(newPositions.cont_user.y > newPositions.answer.y)
  assert.ok(newPositions.cont_answer.y > newPositions.cont_user.y)

  // Assistant is aligned vertically under user
  assert.equal(newPositions.cont_user.x, newPositions.cont_answer.x)

  assertNoCollisions(newPositions)
})

test('continue and reply relationships stay in the parent lane', () => {
  const nodes: ApiNode[] = [
    {
      id: 'root',
      conversation_id: 'demo',
      parent_id: null,
      branch_id: 'main',
      role: 'user',
      content: 'Question',
      kind: 'message',
    },
    {
      id: 'reply',
      conversation_id: 'demo',
      parent_id: 'root',
      branch_id: 'main',
      role: 'assistant',
      content: 'Answer',
      kind: 'message',
    },
    {
      id: 'continue',
      conversation_id: 'demo',
      parent_id: 'reply',
      branch_id: 'main',
      role: 'user',
      content: 'Follow-up',
      kind: 'message',
    },
  ]
  const edges: ApiEdge[] = [
    { id: 'reply-edge', source: 'root', target: 'reply', type: 'reply' },
    { id: 'continue-edge', source: 'reply', target: 'continue', type: 'continue' },
  ]

  const positions = calculateGraphLayout(nodes, edges)

  assert.equal(positions.reply.x, positions.root.x)
  assert.equal(positions.continue.x, positions.reply.x)
  assert.equal(positions.reply.y, positions.root.y + ROW_STEP)
  assert.equal(positions.continue.y, positions.reply.y + ROW_STEP)
})

test('positions new branch nodes with distinct horizontal lane and no collisions', () => {
  const initialPositions = calculateGraphLayout(seedNodes, seedEdges, {})

  // Add a branch off "welcome"
  const branchNodes: ApiNode[] = [
    ...seedNodes,
    {
      id: 'branch_user',
      conversation_id: 'demo',
      parent_id: 'welcome',
      branch_id: 'branch-1',
      role: 'user',
      content: 'What about monolithic architecture?',
      kind: 'message',
    },
    {
      id: 'branch_answer',
      conversation_id: 'demo',
      parent_id: 'branch_user',
      branch_id: 'branch-1',
      role: 'assistant',
      content: 'Monoliths are single units.',
      kind: 'message',
    },
  ]

  const newPositions = calculateGraphLayout(branchNodes, seedEdges, initialPositions)

  // Existing nodes are unchanged
  assert.deepEqual(newPositions.welcome, initialPositions.welcome)
  assert.deepEqual(newPositions.answer, initialPositions.answer)
  assert.deepEqual(newPositions.docker, initialPositions.docker)

  // Branch user is offset horizontally from main branch child (answer)
  assert.notEqual(newPositions.branch_user.x, newPositions.answer.x)

  // Branch assistant is aligned under branch user
  assert.equal(newPositions.branch_user.x, newPositions.branch_answer.x)
  assert.ok(newPositions.branch_answer.y > newPositions.branch_user.y)

  assertNoCollisions(newPositions)
})

test('multiple branches from the same parent use stable separate lanes', () => {
  const branches: ApiNode[] = []
  const branchEdges: ApiEdge[] = []
  for (const branchId of ['branch-c', 'branch-a', 'branch-b']) {
    const questionId = `${branchId}-question`
    const answerId = `${branchId}-answer`
    branches.push(
      {
        id: questionId,
        conversation_id: 'demo',
        parent_id: 'answer',
        branch_id: branchId,
        role: 'user',
        content: `${branchId} question`,
        kind: 'message',
      },
      {
        id: answerId,
        conversation_id: 'demo',
        parent_id: questionId,
        branch_id: branchId,
        role: 'assistant',
        content: `${branchId} answer`,
        kind: 'message',
      },
    )
    branchEdges.push({
      id: `${branchId}-edge`,
      source: 'answer',
      target: questionId,
      type: 'branch',
    })
    branchEdges.push({
      id: `${branchId}-reply`,
      source: questionId,
      target: answerId,
      type: 'reply',
    })
  }

  const nodes = [...seedNodes, ...branches]
  const edges = [...seedEdges, ...branchEdges]
  const positions = calculateGraphLayout(nodes, edges)
  const reversedPositions = calculateGraphLayout([...nodes].reverse(), [...edges].reverse())
  const branchXs = ['branch-a', 'branch-b', 'branch-c'].map(
    (branchId) => positions[`${branchId}-question`].x,
  )

  assert.deepEqual(reversedPositions, positions)
  assert.equal(new Set(branchXs).size, branchXs.length)
  for (const branchId of ['branch-a', 'branch-b', 'branch-c']) {
    assert.equal(
      positions[`${branchId}-question`].x,
      positions[`${branchId}-answer`].x,
    )
  }
  assertNoCollisions(positions)
})

test('handles multiple parallel branches from the same node without collision', () => {
  let positions = calculateGraphLayout(seedNodes, seedEdges, {})

  const allNodes = [...seedNodes]

  for (let b = 1; b <= 3; b++) {
    const userId = `branch_${b}_user`
    const ansId = `branch_${b}_answer`
    allNodes.push(
      {
        id: userId,
        conversation_id: 'demo',
        parent_id: 'answer',
        branch_id: `branch-uuid-${b}`,
        role: 'user',
        content: `Branch ${b} question`,
        kind: 'message',
      },
      {
        id: ansId,
        conversation_id: 'demo',
        parent_id: userId,
        branch_id: `branch-uuid-${b}`,
        role: 'assistant',
        content: `Branch ${b} answer`,
        kind: 'message',
      },
    )

    positions = calculateGraphLayout(allNodes, seedEdges, positions)
    assertNoCollisions(positions)
  }

  // Ensure all 3 branches have distinct horizontal lanes
  const x1 = positions.branch_1_user.x
  const x2 = positions.branch_2_user.x
  const x3 = positions.branch_3_user.x
  assert.notEqual(x1, x2)
  assert.notEqual(x2, x3)
  assert.notEqual(x1, x3)
})
