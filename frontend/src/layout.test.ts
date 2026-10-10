import assert from 'node:assert/strict'
import test from 'node:test'
import {
  calculateGraphLayout,
  isPositionColliding,
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
