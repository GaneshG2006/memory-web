import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background, Controls, MiniMap, ReactFlow, addEdge,
  type Connection, type Edge, type Node, useEdgesState, useNodesState,
} from '@xyflow/react'
import { GitBranch, MessageSquare, Network, Plus, Send } from 'lucide-react'
import {
  calculateGraphLayout,
  getSessionPositions,
  saveSessionPositions,
  updateSingleSessionPosition,
  type ApiNode,
  type ApiEdge,
  type Position,
} from './layout'

type GraphResponse = { nodes: ApiNode[]; edges: ApiEdge[] }

const API = 'http://127.0.0.1:8000'

function buildFlowNodes(items: ApiNode[], positions: Record<string, Position>): Node[] {
  return items.map((item) => ({
    id: item.id,
    position: positions[item.id] || { x: 100, y: 80 },
    data: {
      label: `${item.kind === 'topic' ? 'TOPIC' : item.role.toUpperCase()}\n${item.content.slice(0, 82)}${item.content.length > 82 ? '…' : ''}`,
    },
    style: {
      width: 235,
      minHeight: 95,
      padding: 14,
      borderRadius: 14,
      border: item.kind === 'topic' ? '1px solid #7c8cff' : '1px solid #353844',
      background: item.kind === 'topic' ? '#20243a' : '#1b1d24',
      color: '#f3f4f6',
      fontSize: 12,
      lineHeight: 1.5,
      whiteSpace: 'pre-wrap' as const,
      cursor: 'grab',
    },
  }))
}

async function parseApiError(response: Response): Promise<string> {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    try {
      const data = await response.json()
      if (typeof data?.detail === 'string') {
        return data.detail
      }
      if (Array.isArray(data?.detail)) {
        const errorStrings = data.detail.map((err: unknown) => {
          if (typeof err === 'string') return err
          if (err && typeof err === 'object') {
            const errorObj = err as { loc?: unknown[]; msg?: unknown }
            const loc = Array.isArray(errorObj.loc)
              ? errorObj.loc.filter((segment) => segment !== 'body').join('.')
              : ''
            const msg = typeof errorObj.msg === 'string' ? errorObj.msg : JSON.stringify(err)
            return loc ? `${loc}: ${msg}` : msg
          }
          return String(err)
        })
        if (errorStrings.length > 0) {
          return errorStrings.join('; ')
        }
      }
      if (typeof data?.message === 'string') {
        return data.message
      }
      if (typeof data?.error === 'string') {
        return data.error
      }
    } catch {
      // JSON parsing failed despite header
    }
  } else {
    try {
      const text = await response.text()
      if (text && text.trim().length > 0) {
        const stripped = text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
        if (stripped.length > 0) {
          return stripped.length > 120 ? `${stripped.slice(0, 117)}...` : stripped
        }
      }
    } catch {
      // Reading text body failed
    }
  }
  return `Error ${response.status}: ${response.statusText || 'Request failed'}`
}

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selected, setSelected] = useState<ApiNode | null>(null)
  const [allMessages, setAllMessages] = useState<ApiNode[]>([])
  const [mode, setMode] = useState<'continue' | 'branch'>('branch')
  const [prompt, setPrompt] = useState('')
  const [status, setStatus] = useState('Connecting to local API…')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/conversations/demo/graph`)
      if (!response.ok) {
        const errorMsg = await parseApiError(response)
        throw new Error(errorMsg)
      }
      let graph: GraphResponse
      try {
        graph = await response.json()
      } catch {
        throw new Error('Received invalid JSON from API')
      }
      setAllMessages(graph.nodes)

      const saved = getSessionPositions('demo')
      const positions = calculateGraphLayout(graph.nodes, graph.edges, saved)
      saveSessionPositions('demo', positions)

      setNodes(buildFlowNodes(graph.nodes, positions))
      setEdges(graph.edges.map((edge) => ({
        id: edge.id, source: edge.source, target: edge.target,
        label: edge.type, animated: edge.type === 'branch',
        style: { stroke: edge.type === 'branch' ? '#a78bfa' : '#626779' },
        labelStyle: { fill: '#aab0c0', fontSize: 10 },
      })))
      setStatus('Connected · demo conversation')
    } catch (error) {
      setStatus(
        error instanceof TypeError && error.message.includes('fetch')
          ? 'API offline · start FastAPI in the backend folder'
          : error instanceof Error
            ? error.message
            : 'API offline · start FastAPI in the backend folder',
      )
    }
  }, [setEdges, setNodes])

  useEffect(() => { void refresh() }, [refresh])

  const selectedNode = useMemo(
    () => selected ? allMessages.find((item) => item.id === selected.id) ?? selected : null,
    [selected, allMessages],
  )

  const onConnect = useCallback((connection: Connection) => {
    setEdges((current) => addEdge(connection, current))
  }, [setEdges])

  const onNodeDragStop = useCallback((_: unknown, node: Node) => {
    updateSingleSessionPosition('demo', node.id, node.position)
  }, [])

  const handleNodesChange: typeof onNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes)
      for (const change of changes) {
        if (change.type === 'position' && change.position && !change.dragging) {
          updateSingleSessionPosition('demo', change.id, change.position)
        }
      }
    },
    [onNodesChange],
  )

  async function submitFollowUp() {
    if (!selectedNode || !prompt.trim()) return
    setBusy(true)
    try {
      const response = await fetch(`${API}/api/follow-ups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          conversation_id: 'demo',
          parent_node_id: selectedNode.id,
          mode,
          prompt: prompt.trim(),
        }),
      })
      if (!response.ok) {
        const errorMsg = await parseApiError(response)
        throw new Error(errorMsg)
      }
      let result: { created_nodes?: ApiNode[]; created_edges?: unknown[] }
      try {
        result = await response.json()
      } catch {
        throw new Error('Received invalid JSON from API')
      }
      setPrompt('')
      await refresh()
      const assistantNode = result.created_nodes?.find((item) => item.role === 'assistant')
      if (assistantNode) {
        setSelected(assistantNode)
      }
      setStatus(`Follow-up added · ${mode} mode · AI response`)
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not create follow-up')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-icon"><Network size={20} /></span><div><strong>Memory Web</strong><small>Conversations, connected.</small></div></div>
        <div className="top-actions"><span className="status-dot" />{status}<button className="ghost" onClick={() => void refresh()}>Refresh graph</button></div>
      </header>
      <section className="workspace">
        <div className="graph-area">
          <div className="graph-heading"><div><h1>Your conversation web</h1><p>Select a node to inspect it or start a follow-up.</p></div><span className="demo-pill">DEMO DATA</span></div>
          <div className="graph-canvas">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={handleNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeDragStop={onNodeDragStop}
              onConnect={onConnect}
              onNodeClick={(_, node) => {
                const item = allMessages.find((message) => message.id === node.id)
                if (item) setSelected(item)
              }}
              onPaneClick={() => setSelected(null)}
              fitView
              fitViewOptions={{ padding: 0.22 }}
            >
              <Background color="#2c303b" gap={24} />
              <Controls />
              <MiniMap nodeColor={(node) => node.id === 'docker' ? '#7c8cff' : '#51586c'} maskColor="rgba(10,11,15,0.65)" />
            </ReactFlow>
          </div>
        </div>
        <aside className="inspector">
          <div className="panel-title"><MessageSquare size={17} /><h2>Node inspector</h2></div>
          {selectedNode ? (
            <>
              <span className="eyebrow">{selectedNode.kind} · {selectedNode.role}</span>
              <div className="message-content">{selectedNode.content}</div>
              <div className="meta-row"><span>Node ID</span><code>{selectedNode.id.slice(0, 12)}</code></div>
              <div className="divider" />
              <h3>Follow-up</h3>
              <p className="hint">Your question uses this node's conversation ancestry as context. Ollama generates the response locally.</p>
              <div className="mode-switch">
                <button className={mode === 'branch' ? 'mode active' : 'mode'} onClick={() => setMode('branch')}><GitBranch size={15} /> Branch</button>
                <button className={mode === 'continue' ? 'mode active' : 'mode'} onClick={() => setMode('continue')}><Plus size={15} /> Continue</button>
              </div>
              <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Ask a follow-up about this node…" rows={4} />
              <button className="primary" onClick={() => void submitFollowUp()} disabled={busy || !prompt.trim()}><Send size={15} />{busy ? 'Adding…' : 'Add follow-up'}</button>
            </>
          ) : (
            <div className="empty-state"><Network size={28} /><strong>Select a node</strong><p>Click any message or topic in the graph to inspect it and start a follow-up.</p></div>
          )}
          <div className="panel-foot">Memory Web MVP · Local prototype</div>
        </aside>
      </section>
    </main>
  )
}
