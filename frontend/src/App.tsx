import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  Background, Controls, Handle, MiniMap, Position as FlowPosition, ReactFlow, addEdge,
  type Connection, type Edge, type Node, type ReactFlowInstance,
  type NodeProps,
  useEdgesState, useNodesState,
} from '@xyflow/react'
import {
  Bot, Brain, GitBranch, LayoutGrid, MessageSquare, Network, Plus,
  RotateCw, Search, Send, UserRound,
} from 'lucide-react'
import {
  calculateGraphLayout,
  getSessionPositions,
  NODE_HEIGHT,
  NODE_WIDTH,
  saveSessionPositions,
  updateSingleSessionPosition,
  type ApiNode,
  type ApiEdge,
  type Position,
} from './layout'
import {
  getSearchStatus,
  normalizeSearchQuery,
  resolveSearchSelection,
  type SearchResult,
} from './search'
import {
  apiFetch,
  getFollowUpRequestErrorMessage,
  parseApiError,
  parseFollowUpResponse,
  type ApiCredentials,
} from './api'
import './search.css'

type GraphResponse = { nodes: ApiNode[]; edges: ApiEdge[] }
type SearchResponse = { results: SearchResult[] }
const AUTHENTICATION_ERROR = 'Authentication failed. Check the username and password, then try again.'
const FOLLOW_UP_REQUEST_TIMEOUT_MS = 130_000

type ConversationNodeData = {
  kind: string
  role: string
  content: string
}

function ConversationNode({ data }: NodeProps<Node<ConversationNodeData>>) {
  const isTopic = data.kind === 'topic'
  const variant = isTopic ? 'is-topic' : data.role === 'user' ? 'is-user' : data.role === 'assistant' ? 'is-assistant' : 'is-other'
  const title = isTopic ? 'TOPIC / MEMORY' : data.role.toUpperCase()
  const NodeIcon = isTopic ? Brain : data.role === 'user' ? UserRound : Bot

  return (
    <>
      <Handle type="target" position={FlowPosition.Left} className="conversation-node-handle" />
      <article className={`conversation-node-card ${variant}`}>
        <header className="conversation-node-header">
          <span className="conversation-node-icon"><NodeIcon size={12} /></span>
          <span className="conversation-node-title">{title}</span>
        </header>
        <p>{data.content}</p>
      </article>
      <Handle type="source" position={FlowPosition.Right} className="conversation-node-handle" />
    </>
  )
}

const nodeTypes = { conversation: ConversationNode }

function buildFlowNodes(items: ApiNode[], positions: Record<string, Position>): Node[] {
  return items.map((item) => ({
    id: item.id,
    position: positions[item.id] || { x: 100, y: 80 },
    type: 'conversation',
    data: {
      kind: item.kind,
      role: item.role,
      content: `${item.content.slice(0, 82)}${item.content.length > 82 ? '…' : ''}`,
    },
    style: {
      width: 235,
      minHeight: 95,
    },
  }))
}

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selected, setSelected] = useState<ApiNode | null>(null)
  const [allMessages, setAllMessages] = useState<ApiNode[]>([])
  const [mode, setMode] = useState<'continue' | 'branch'>('branch')
  const [prompt, setPrompt] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searchError, setSearchError] = useState('')
  const [searchBusy, setSearchBusy] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [credentials, setCredentials] = useState<ApiCredentials | null>(null)
  const [authError, setAuthError] = useState('')
  const [authBusy, setAuthBusy] = useState(false)
  const [status, setStatus] = useState('Enter API credentials to connect.')
  const [busy, setBusy] = useState(false)
  const [followUpError, setFollowUpError] = useState('')
  const flowInstance = useRef<ReactFlowInstance | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)

  function handleUnauthorized() {
    setCredentials(null)
    setAllMessages([])
    setNodes([])
    setEdges([])
    setSelected(null)
    setSearchResults([])
    setAuthError(AUTHENTICATION_ERROR)
    setStatus('Authentication required.')
  }

  const refresh = useCallback(async () => {
    if (!credentials) {
      setStatus('Enter API credentials to connect.')
      return
    }

    setAuthBusy(true)
    try {
      const response = await apiFetch('/api/conversations/demo/graph', credentials)
      if (response.status === 401) {
        handleUnauthorized()
        return
      }
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
        style: { stroke: edge.type === 'branch' ? '#A0A0A0' : '#707070', strokeWidth: 1.8 },
        labelStyle: { fill: '#C3C3C3', fontSize: 10, fontWeight: 500 },
        labelBgStyle: { fill: '#171717', fillOpacity: 0.94 },
        labelBgPadding: [5, 3],
        labelBgBorderRadius: 5,
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
    } finally {
      setAuthBusy(false)
    }
  }, [credentials, setEdges, setNodes])

  useEffect(() => { void refresh() }, [refresh])

  function submitCredentials(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAuthError('')
    if (!username || !password) {
      setAuthError('Enter both a username and password.')
      return
    }
    setCredentials({ username, password })
  }

  const selectedNode = useMemo(
    () => selected ? allMessages.find((item) => item.id === selected.id) ?? selected : null,
    [selected, allMessages],
  )

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const query = normalizeSearchQuery(searchQuery)
    setSearchError('')
    setSearchResults([])
    setHasSearched(true)
    if (!query) return

    setSearchBusy(true)
    try {
      if (!credentials) {
        setSearchError('Enter API credentials to search.')
        return
      }
      const response = await apiFetch(
        `/api/conversations/demo/search?q=${encodeURIComponent(query)}`,
        credentials,
      )
      if (response.status === 401) {
        handleUnauthorized()
        setSearchError(AUTHENTICATION_ERROR)
        return
      }
      if (!response.ok) {
        throw new Error(await parseApiError(response))
      }
      let result: SearchResponse
      try {
        result = await response.json()
      } catch {
        throw new Error('Received invalid JSON from API')
      }
      setSearchResults(result.results)
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : 'Could not search conversation')
    } finally {
      setSearchBusy(false)
    }
  }

  function selectSearchResult(result: SearchResult) {
    const selection = resolveSearchSelection(result.id, allMessages, nodes)
    if (!selection) {
      setSearchError('This node is no longer in the graph. Refresh the graph and search again.')
      return
    }

    setFollowUpError('')
    setSelected(selection.node)
    setNodes((current) => current.map((node) => ({
      ...node,
      selected: node.id === result.id,
    })))
    flowInstance.current?.setCenter(
      selection.position.x + NODE_WIDTH / 2,
      selection.position.y + NODE_HEIGHT / 2,
      { zoom: 1.15, duration: 500 },
    )
  }

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
    if (!credentials) {
      setAuthError('Enter API credentials to create a follow-up.')
      return
    }
    const controller = new AbortController()
    const timeoutId = window.setTimeout(
      () => controller.abort(),
      FOLLOW_UP_REQUEST_TIMEOUT_MS,
    )
    setFollowUpError('')
    setBusy(true)
    try {
      const response = await apiFetch('/api/follow-ups', credentials, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          conversation_id: 'demo',
          parent_node_id: selectedNode.id,
          mode,
          prompt: prompt.trim(),
        }),
      })
      if (response.status === 401) {
        handleUnauthorized()
        return
      }
      const result = await parseFollowUpResponse<{
        created_nodes?: ApiNode[]
        created_edges?: unknown[]
      }>(response)
      setPrompt('')
      await refresh()
      const assistantNode = result.created_nodes?.find((item) => item.role === 'assistant')
      if (assistantNode) {
        setSelected(assistantNode)
      }
      setStatus(`Follow-up added · ${mode} mode · AI response`)
    } catch (error) {
      const errorMessage = getFollowUpRequestErrorMessage(error)
      setFollowUpError(errorMessage)
      setStatus(errorMessage)
    } finally {
      window.clearTimeout(timeoutId)
      setBusy(false)
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-icon"><Network size={19} /></span>
          <div><strong>Memory Web</strong><small>Conversation workspace</small></div>
        </div>
        <nav className="sidebar-nav" aria-label="Workspace navigation">
          <button className="sidebar-nav-item active" onClick={() => flowInstance.current?.fitView({ padding: 0.22 })}>
            <LayoutGrid size={16} /><span>Conversation graph</span>
          </button>
          <button className="sidebar-nav-item" onClick={() => searchInputRef.current?.focus()}>
            <Search size={16} /><span>Search messages</span>
          </button>
        </nav>
        <div className="sidebar-divider" />
        <section className="sidebar-connection" aria-label="API connection">
          <div className="sidebar-section-heading"><span>WORKSPACE</span><span className="local-badge">LOCAL</span></div>
          <form className="auth-form" onSubmit={submitCredentials}>
            <span className="connection-title">API connection</span>
            <label>
              <span>API username</span>
              <input
                autoComplete="off"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
            <label>
              <span>API password</span>
              <input
                type="password"
                autoComplete="off"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            <button className="search-button" type="submit" disabled={authBusy}>
              {authBusy ? 'Connecting…' : 'Connect'}
            </button>
          </form>
          {authError && <p className="search-message search-error" role="alert">{authError}</p>}
        </section>
        <div className="sidebar-foot"><span className="sidebar-foot-mark" /><span>Private conversation space</span></div>
      </aside>
      <div className="workspace-shell">
        <header className="topbar">
          <div className="topbar-title">
            <span className="topbar-kicker">CONVERSATIONS <span>/</span> DEMO</span>
            <h1>Conversation graph</h1>
          </div>
          <div className="top-actions">
            <span className={`connection-status${status.startsWith('Connected') ? ' is-connected' : ''}`}>
              <span className="status-dot" />{status}
            </span>
            <button className="refresh-button" onClick={() => void refresh()} disabled={!credentials || authBusy}>
              <RotateCw size={14} className={authBusy ? 'is-spinning' : ''} />
              <span>Refresh</span>
            </button>
          </div>
        </header>
        <section className="workspace">
          <div className="graph-area">
          <section className="conversation-search" aria-label="Search conversation">
            <form className="search-form" onSubmit={(event) => void submitSearch(event)}>
              <label className="search-input-wrap">
                <Search size={16} aria-hidden="true" />
                <input
                  ref={searchInputRef}
                  type="search"
                  value={searchQuery}
                  onChange={(event) => {
                    setSearchQuery(event.target.value)
                    setSearchResults([])
                    setSearchError('')
                    setHasSearched(false)
                  }}
                  placeholder="Search messages and topics…"
                  aria-label="Search messages and topics"
                />
              </label>
              <button className="search-button" type="submit" disabled={!credentials || searchBusy}>
                {searchBusy ? 'Searching…' : 'Search'}
              </button>
            </form>
            {searchError ? (
              <p className="search-message search-error" role="alert">{searchError}</p>
            ) : (
              <p className="search-message" aria-live="polite">
                {searchBusy ? 'Searching conversation…' : getSearchStatus(searchQuery, searchResults.length, hasSearched)}
              </p>
            )}
            {searchResults.length > 0 && (
              <ul className="search-results" aria-label="Search results">
                {searchResults.map((result) => (
                  <li key={result.id}>
                    <button
                      className="search-result"
                      type="button"
                      onClick={() => selectSearchResult(result)}
                    >
                      <span className="search-result-meta">{result.kind} · {result.role}</span>
                      <span className="search-result-excerpt">{result.excerpt}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <div className="graph-canvas">
            <div className="canvas-heading">
              <div><span className="canvas-heading-icon"><Network size={14} /></span><span>Conversation map</span></div>
              <span className="demo-pill">DEMO DATA</span>
            </div>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={handleNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeDragStop={onNodeDragStop}
              onConnect={onConnect}
              onInit={(instance) => { flowInstance.current = instance }}
              onNodeClick={(_, node) => {
                const item = allMessages.find((message) => message.id === node.id)
                if (item) {
                  setSelected(item)
                  setFollowUpError('')
                }
              }}
              onPaneClick={() => {
                setSelected(null)
                setFollowUpError('')
              }}
              fitView
              fitViewOptions={{ padding: 0.22 }}
            >
              <Background color="#292929" gap={24} size={1.1} />
              <Controls position="bottom-left" />
              {nodes.length > 0 && (
                <MiniMap
                  position="bottom-right"
                  nodeColor="#A0A0A0"
                  maskColor="rgba(10, 10, 10, 0.72)"
                />
              )}
            </ReactFlow>
            {authBusy || nodes.length === 0 ? (
              <div className="canvas-empty-state" aria-live="polite">
                <span className="canvas-empty-icon">{authBusy ? <RotateCw size={19} className="is-spinning" /> : <Network size={19} />}</span>
                <strong>{authBusy ? 'Connecting to your graph' : status.startsWith('Connected') ? 'No nodes in this conversation' : credentials ? 'Graph unavailable' : 'Connect to load your graph'}</strong>
                <span>{authBusy ? 'Loading conversation nodes…' : status.startsWith('Connected') ? 'No conversation nodes were returned by the API.' : credentials ? 'Check the connection status above and refresh to try again.' : 'Connect to load your conversation map.'}</span>
              </div>
            ) : null}
          </div>
        </div>
        <aside className="inspector">
          <div className="inspector-heading">
            <div className="panel-title"><MessageSquare size={16} /><h2>Node inspector</h2></div>
            <span className="inspector-indicator" />
          </div>
          {selectedNode ? (
            <>
              <div className={`inspector-node-type${selectedNode.kind === 'topic' ? ' is-topic' : selectedNode.role === 'user' ? ' is-user' : ' is-assistant'}`}>
                {selectedNode.kind === 'topic' ? <Brain size={13} /> : selectedNode.role === 'user' ? <UserRound size={13} /> : <Bot size={13} />}
                <span>{selectedNode.kind === 'topic' ? 'Topic / memory' : selectedNode.role}</span>
              </div>
              <div className="message-content">{selectedNode.content}</div>
              <div className="metadata-card">
                <span className="metadata-heading">NODE DETAILS</span>
                <div className="meta-row"><span>Node ID</span><code>{selectedNode.id.slice(0, 12)}</code></div>
                <div className="meta-row"><span>Type</span><span className="metadata-value">{selectedNode.kind}</span></div>
                <div className="meta-row"><span>Role</span><span className="metadata-value">{selectedNode.role}</span></div>
              </div>
              <div className="divider" />
              <div className="follow-up-heading"><h3>Follow-up</h3><span>AI RESPONSE</span></div>
              <p className="hint">Your question uses this node's conversation ancestry as context. Ollama generates the response locally.</p>
              <div className="mode-switch">
                <button className={mode === 'branch' ? 'mode active' : 'mode'} onClick={() => setMode('branch')}><GitBranch size={15} /> Branch</button>
                <button className={mode === 'continue' ? 'mode active' : 'mode'} onClick={() => setMode('continue')}><Plus size={15} /> Continue</button>
              </div>
              <textarea value={prompt} onChange={(event) => {
                setPrompt(event.target.value)
                setFollowUpError('')
              }} placeholder="Ask a follow-up about this node…" rows={4} />
              <button className="primary" onClick={() => void submitFollowUp()} disabled={!credentials || busy || !prompt.trim()}><Send size={14} />{busy ? 'Adding…' : 'Add follow-up'}</button>
              {followUpError && <p className="follow-up-error" role="alert">{followUpError}</p>}
            </>
          ) : (
            <div className="empty-state"><span className="empty-state-icon"><Network size={21} /></span><strong>Select a node to inspect</strong><p>Choose a message or topic in the graph to view its details and create a follow-up.</p></div>
          )}
          <div className="panel-foot"><span className="panel-foot-dot" />Memory Web <span>·</span> Local workspace</div>
        </aside>
        </section>
      </div>
    </main>
  )
}
