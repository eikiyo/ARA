# ARA Code Architecture

> **Location:** `Support Docs/Product Design/arch.md`
> **Purpose:** Code organization guide with extreme reuse constraints
> **Rules:** Functions ≤ 15 lines | Files ≤ 100 lines | 100% reuse

---

## 1. Constraints

| Rule | Limit | Enforced By |
|------|-------|------------|
| Function body | 15 lines max | Code review + linting |
| File length | 100 lines max (imports + exports included) | If exceeded → split by responsibility |
| Duplication | Zero tolerance | Every pattern defined once, referenced everywhere |
| Magic strings | None | All strings in `constants.ts` |
| Inline styles | None | All tokens in `tokens.ts` |
| Scattered state | None | Single Zustand store |
| Direct fetch | None | All HTTP through `api.ts` |

**Exception:** Pure data files (types, constants, configs) may exceed 100 lines if they contain no logic.

---

## 2. Project Structure

```
ara/
├── ui/                              # Next.js app
│   ├── src/
│   │   ├── app/                     # Pages (thin — layout + hooks + render)
│   │   │   ├── layout.tsx           # Root layout, providers, font
│   │   │   ├── page.tsx             # PIN entry screen
│   │   │   ├── new/
│   │   │   │   └── page.tsx         # Session config form
│   │   │   ├── session/
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx     # Live dashboard
│   │   │   ├── history/
│   │   │   │   └── page.tsx         # Session list
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx     # Session detail (read-only + fork)
│   │   │   └── api/                 # API routes
│   │   │       ├── auth/
│   │   │       │   └── route.ts     # PIN validation
│   │   │       ├── sessions/
│   │   │       │   └── route.ts     # POST create, GET list
│   │   │       ├── sessions/[id]/
│   │   │       │   └── route.ts     # GET detail, PUT update
│   │   │       ├── gates/[id]/
│   │   │       │   └── route.ts     # POST approve/reject/edit/revert
│   │   │       ├── rules/
│   │   │       │   └── route.ts     # POST add, PUT edit, DELETE remove
│   │   │       └── webhook/
│   │   │           └── route.ts     # Receives n8n callbacks
│   │   ├── lib/                     # Shared logic (THE core of reuse)
│   │   │   ├── types.ts             # ALL TypeScript types (mirrors DB schema)
│   │   │   ├── constants.ts         # ALL strings, configs, registries
│   │   │   ├── tokens.ts            # ALL colors, spacing, typography
│   │   │   ├── api.ts               # ONE function for all HTTP calls
│   │   │   ├── ws.ts                # WS message handler registry
│   │   │   ├── store.ts             # Single Zustand store
│   │   │   ├── validate.ts          # Zod schemas for all inputs
│   │   │   └── db.ts                # Postgres connection pool (API routes only)
│   │   ├── hooks/                   # Custom React hooks (thin wrappers)
│   │   │   ├── use-session.ts       # Fetch + cache session data
│   │   │   ├── use-ws.ts            # WebSocket connect + message routing
│   │   │   ├── use-gates.ts         # Approval gate actions
│   │   │   ├── use-rules.ts         # Rule gate CRUD
│   │   │   └── use-auth.ts          # PIN auth state
│   │   └── components/              # React components
│   │       ├── ui/                  # Atomic (Button, Card, Badge, Input, Modal, Table)
│   │       │   ├── button.tsx
│   │       │   ├── card.tsx
│   │       │   ├── badge.tsx
│   │       │   ├── input.tsx
│   │       │   ├── modal.tsx
│   │       │   └── table.tsx
│   │       ├── gates/               # Approval gate system
│   │       │   ├── gate-card.tsx     # ONE component for ALL 8 gate types
│   │       │   ├── gate-actions.tsx  # Approve/Reject/Edit/Revert buttons
│   │       │   └── renderers/       # Per-gate data renderers (small, focused)
│   │       │       ├── scout.tsx
│   │       │       ├── triage.tsx
│   │       │       ├── analyst.tsx
│   │       │       ├── verifier.tsx
│   │       │       ├── hypothesis.tsx
│   │       │       ├── brancher.tsx
│   │       │       ├── critic.tsx
│   │       │       └── writer.tsx
│   │       ├── dashboard/           # Dashboard composites
│   │       │   ├── stepper.tsx       # Phase progress bar
│   │       │   ├── feed.tsx          # Live event stream
│   │       │   ├── budget.tsx        # Budget meter
│   │       │   ├── errors.tsx        # Error panel + actions
│   │       │   └── rules.tsx         # Rule gate sidebar
│   │       ├── session/             # Session composites
│   │       │   ├── config-form.tsx   # Session config form
│   │       │   ├── session-list.tsx  # History table
│   │       │   └── session-detail.tsx # Read-only detail + fork
│   │       └── layout/
│   │           └── shell.tsx         # App shell (header, nav)
│   ├── Dockerfile
│   └── package.json
│
├── ws-server/                       # WebSocket relay server
│   ├── index.ts                     # Single file (~80 lines)
│   ├── Dockerfile
│   └── package.json
│
├── n8n/                             # n8n workflow exports + shared code
│   ├── workflows/
│   │   ├── manager.json             # Manager workflow (exported from n8n)
│   │   ├── scout.json
│   │   ├── analyst-triage.json
│   │   ├── analyst-deep-read.json
│   │   ├── verifier.json
│   │   ├── hypothesis.json
│   │   ├── brancher.json
│   │   ├── critic.json
│   │   ├── writer.json
│   │   └── shared-utils.json        # Shared utility sub-workflow
│   └── code/                        # JS snippets for n8n Code nodes
│       ├── task-router.js           # TASK_ROUTER registry
│       ├── gate-creator.js          # createApprovalGate()
│       ├── task-creator.js          # createTask()
│       ├── budget-checker.js        # checkBudget()
│       ├── rule-injector.js         # injectRules()
│       ├── dedup.js                 # deduplicatePapers()
│       └── embed.js                 # embedText() via Gemini API
│
├── schema/
│   └── ara_schema.sql               # Database schema (14 tables)
│
├── docker-compose.yml               # Full stack: ui, ws, n8n, pg, qdrant, redis, grobid
├── .env.example                     # All environment variables documented
├── PHASE.md
└── Support Docs/
    └── Product Design/
        ├── DESIGN-ARA-research-agent.md
        └── arch.md                  # This file
```

---

## 3. Shared Layer: The 7 Core Files

Every piece of code in the project depends on these 7 files. They are the single source of truth.

### 3.1 `lib/types.ts` — All Types

Mirrors `ara_schema.sql` exactly. Every component, hook, API route, and WS handler imports from here.

```typescript
// Enums (match SQL ENUMs)
export type TaskStatus = 'queued' | 'claimed' | 'running' | 'done' | 'failed' | 'blocked'
export type Phase = 'scout' | 'analyst_triage' | 'analyst' | 'verifier'
  | 'hypothesis' | 'brancher' | 'critic' | 'writer'
export type SessionPhase = Phase | 'completed' | 'waiting_approval'
export type GateAction = 'approve' | 'reject' | 'edit' | 'revert'
export type GateStatus = 'pending' | 'approved' | 'rejected' | 'edited' | 'reverted'
export type PaperType = 'research_article' | 'literature_review'
  | 'systematic_review' | 'meta_analysis' | 'position_paper' | 'case_study'
export type CitationStyle = 'apa7' | 'ieee' | 'chicago' | 'vancouver' | 'harvard'
export type RuleType = 'include' | 'exclude' | 'constraint' | 'methodology'
export type RuleCreator = 'user' | 'manager'

// Data models (1:1 with SQL tables)
export interface Session { session_id: number; topic: string; ... }
export interface Paper { paper_id: number; session_id: number; ... }
export interface Claim { claim_id: number; session_id: number; ... }
export interface Hypothesis { hypothesis_id: number; rank: number; ... }
export interface HypothesisScore { score_id: number; dimension: string; score: number; ... }
export interface BranchMap { branch_id: number; branch_type: string; ... }
export interface ApprovalGate { gate_id: number; phase: Phase; gate_data: Record<string, any>; ... }
export interface RuleGate { rule_id: number; rule_text: string; rule_type: RuleType; ... }
export interface Task { task_id: number; task_type: string; status: TaskStatus; ... }

// API contracts
export interface ApiRes<T = any> { ok: boolean; data?: T; error?: string }
export interface GateActionReq { gate_id: number; action: GateAction; comments?: string; data?: any }

// WebSocket
export type WsEventType = 'phase_started' | 'agent_progress' | 'task_completed'
  | 'phase_completed' | 'approval_required' | 'error'
  | 'budget_warning' | 'budget_exceeded' | 'session_completed' | 'rule_gate_updated'
export interface WsMessage { type: WsEventType; session_id: number; [key: string]: any }
```

### 3.2 `lib/constants.ts` — All Config

Registries, labels, defaults. Components never hardcode strings — they read from here.

```typescript
export const PHASES: Record<Phase, { label: string; order: number }> = {
  scout:          { label: 'Scout',           order: 0 },
  analyst_triage: { label: 'Triage',          order: 1 },
  analyst:        { label: 'Deep Read',       order: 2 },
  verifier:       { label: 'Verify',          order: 3 },
  hypothesis:     { label: 'Hypothesize',     order: 4 },
  brancher:       { label: 'Branch',          order: 5 },
  critic:         { label: 'Critique',        order: 6 },
  writer:         { label: 'Write',           order: 7 },
}

export const PAPER_TYPES: Record<PaperType, { label: string }> = { ... }
export const CITATION_STYLES: Record<CitationStyle, { label: string }> = { ... }
export const RULE_TYPES: Record<RuleType, { label: string }> = { ... }

export const SOURCES = [
  { id: 'semantic_scholar', label: 'Semantic Scholar' },
  { id: 'arxiv',            label: 'arXiv' },
  // ... all 10
] as const

export const GATE_ACTIONS: Record<GateAction, { label: string; variant: ButtonVariant }> = {
  approve: { label: 'Approve', variant: 'success' },
  reject:  { label: 'Reject',  variant: 'danger' },
  edit:    { label: 'Edit',    variant: 'primary' },
  revert:  { label: 'Revert',  variant: 'warning' },
}

export const PRESETS = {
  quick:  { label: 'Quick Scan',    budget: 1,  depth: 20,  sources: ['semantic_scholar'] },
  standard: { label: 'Standard',    budget: 5,  depth: 100, sources: 'all' },
  deep:   { label: 'Deep Research', budget: 20, depth: 200, sources: 'all' },
}
```

### 3.3 `lib/tokens.ts` — All Design Tokens

Single source for every visual value. Tailwind config extends from this.

```typescript
export const COLOR = {
  primary:   { bg: 'bg-blue-600',   text: 'text-blue-600',   border: 'border-blue-600' },
  success:   { bg: 'bg-green-600',  text: 'text-green-600',  border: 'border-green-600' },
  warning:   { bg: 'bg-amber-500',  text: 'text-amber-500',  border: 'border-amber-500' },
  danger:    { bg: 'bg-red-600',    text: 'text-red-600',    border: 'border-red-600' },
  neutral:   { bg: 'bg-gray-500',   text: 'text-gray-500',   border: 'border-gray-500' },
  surface:   { bg: 'bg-gray-50',    text: 'text-gray-900',   border: 'border-gray-200' },
}

export const PHASE_COLOR: Record<Phase, string> = {
  scout: 'bg-blue-500', analyst_triage: 'bg-emerald-500', analyst: 'bg-emerald-600',
  verifier: 'bg-amber-500', hypothesis: 'bg-violet-500', brancher: 'bg-pink-500',
  critic: 'bg-cyan-500', writer: 'bg-teal-500',
}

export type ButtonVariant = 'primary' | 'success' | 'warning' | 'danger' | 'ghost'
export const BTN: Record<ButtonVariant, string> = {
  primary: 'bg-blue-600 hover:bg-blue-700 text-white',
  success: 'bg-green-600 hover:bg-green-700 text-white',
  warning: 'bg-amber-500 hover:bg-amber-600 text-white',
  danger:  'bg-red-600 hover:bg-red-700 text-white',
  ghost:   'bg-transparent hover:bg-gray-100 text-gray-700',
}
```

### 3.4 `lib/api.ts` — One Function for All HTTP

Every HTTP call in the entire app goes through `callApi`. No fetch/axios anywhere else.

```typescript
import type { ApiRes } from './types'

const BASE = process.env.NEXT_PUBLIC_API_URL ?? ''

export async function callApi<T>(
  endpoint: string,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET',
  body?: unknown
): Promise<ApiRes<T>> {
  const res = await fetch(`${BASE}${endpoint}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json().catch(() => null)
  if (!res.ok) return { ok: false, error: json?.error ?? res.statusText }
  return { ok: true, data: json }
}
```

### 3.5 `lib/ws.ts` — WebSocket Message Registry

Maps event types to store actions. Adding a new event = add one line.

```typescript
import type { WsMessage } from './types'
import { useStore } from './store'

type Handler = (msg: WsMessage) => void
type StoreApi = ReturnType<typeof useStore.getState>

export function createHandlers(s: StoreApi): Record<string, Handler> {
  return {
    phase_started:     (m) => s.setPhase(m.phase),
    agent_progress:    (m) => s.addFeed(m),
    task_completed:    (m) => s.addFeed(m),
    phase_completed:   (m) => s.addFeed(m),
    approval_required: (m) => s.setGate(m),
    error:             (m) => s.addError(m),
    budget_warning:    (m) => s.setBudget(m.budget_spent, m.budget_cap),
    budget_exceeded:   (m) => s.setBudget(m.budget_spent, m.budget_cap),
    session_completed: (m) => s.setPhase('completed'),
    rule_gate_updated: (m) => s.addRule(m),
  }
}

export function routeMessage(msg: WsMessage, handlers: Record<string, Handler>) {
  const fn = handlers[msg.type]
  if (fn) fn(msg)
}
```

### 3.6 `lib/store.ts` — Single State Store (Zustand)

All app state in one store. Components read via selectors. No prop drilling.

```typescript
import { create } from 'zustand'
import type { Session, ApprovalGate, RuleGate, WsMessage, SessionPhase } from './types'

interface FeedItem { id: string; type: string; message: string; phase: string; ts: string }
interface ErrorItem { id: string; message: string; task_id?: number; options?: string[] }

interface Store {
  // Session
  session: Session | null
  phase: SessionPhase | null
  setSession: (s: Session) => void
  setPhase: (p: SessionPhase) => void

  // Gate
  gate: ApprovalGate | null
  gateHistory: ApprovalGate[]
  setGate: (g: WsMessage) => void
  clearGate: () => void

  // Feed
  feed: FeedItem[]
  addFeed: (m: WsMessage) => void
  clearFeed: () => void

  // Budget
  budgetSpent: number
  budgetCap: number
  setBudget: (spent: number, cap: number) => void

  // Errors
  errors: ErrorItem[]
  addError: (m: WsMessage) => void
  dismissError: (id: string) => void

  // Rules
  rules: RuleGate[]
  addRule: (m: WsMessage) => void
  setRules: (r: RuleGate[]) => void

  // Reset
  reset: () => void
}
```

### 3.7 `lib/validate.ts` — All Input Validation (Zod)

Shared between API routes and client forms. One schema per concept.

```typescript
import { z } from 'zod'

export const PinSchema = z.string().length(4).regex(/^\d+$/)

export const SessionConfigSchema = z.object({
  topic:           z.string().min(5).max(500),
  paper_type:      z.enum(['research_article', ...]),
  citation_style:  z.enum(['apa7', ...]),
  budget_cap:      z.number().min(1).max(100),
  deep_read_limit: z.number().min(10).max(500),
  enabled_sources: z.array(z.string()).min(1),
  initial_rules:   z.array(z.object({ text: z.string(), type: z.enum([...]) })).optional(),
})

export const GateActionSchema = z.object({
  action:   z.enum(['approve', 'reject', 'edit', 'revert']),
  comments: z.string().optional(),
  data:     z.any().optional(),
})

export const RuleSchema = z.object({
  rule_text: z.string().min(3),
  rule_type: z.enum(['include', 'exclude', 'constraint', 'methodology']),
})
```

---

## 4. Component Architecture

### 4.1 Layer Model

```
Pages (app/*.tsx)         — Thin: layout + hooks + render. ~30 lines.
  └── Composites          — Dashboard components. ~50-80 lines. Assemble atomics.
       └── Atomics (ui/*) — Button, Card, Badge, etc. ~30-50 lines. Zero logic.
```

Every component receives data via props or Zustand selectors. No component fetches data directly.

### 4.2 Atomic Components (`components/ui/`)

| Component | Props | Lines |
|-----------|-------|-------|
| `button.tsx` | `label, onClick, variant, size, disabled, loading` | ~35 |
| `card.tsx` | `title?, children, footer?` | ~25 |
| `badge.tsx` | `label, color` | ~15 |
| `input.tsx` | `label, value, onChange, type, error?` | ~30 |
| `modal.tsx` | `open, onClose, title, children, actions` | ~40 |
| `table.tsx` | `columns[], rows[], onRowClick?` | ~50 |

### 4.3 Gate System (`components/gates/`)

**ONE `gate-card.tsx`** renders all 8 gate types. It delegates to per-gate renderers.

```
gate-card.tsx (40 lines)
  ├── Reads GATE_REGISTRY from constants.ts to get title + renderer
  ├── Renders: Card → title → renderer(gate_data) → gate-actions.tsx
  └── Never changes when adding a new gate type

gate-actions.tsx (30 lines)
  ├── Renders 4 buttons from GATE_ACTIONS constant
  ├── Calls useGates() hook for approve/reject/edit/revert
  └── Shows comment input for reject/edit/revert

renderers/scout.tsx (50 lines)
  ├── Renders papers table: title, abstract, confidence, source, link
  └── Uses Table + Badge atomics

renderers/triage.tsx (60 lines)
  ├── Renders ranked paper list with checkboxes (user picks which to deep-read)
  └── Uses Table + Input atomics

renderers/hypothesis.tsx (60 lines)
  ├── Renders 20 hypotheses with multi-dimensional score bars
  └── Uses Table + Badge atomics

(... same pattern for all 8)
```

**Gate Registry** in `constants.ts`:

```typescript
import { ScoutRenderer } from '../components/gates/renderers/scout'
import { TriageRenderer } from '../components/gates/renderers/triage'
// ...

export const GATE_REGISTRY: Record<Phase, {
  title: string
  renderer: ComponentType<{ data: any; onEdit?: (data: any) => void }>
}> = {
  scout:          { title: 'Scout Results',        renderer: ScoutRenderer },
  analyst_triage: { title: 'Paper Ranking',        renderer: TriageRenderer },
  analyst:        { title: 'Extracted Claims',     renderer: AnalystRenderer },
  verifier:       { title: 'Verification Results', renderer: VerifierRenderer },
  hypothesis:     { title: 'Hypotheses (1-20)',    renderer: HypothesisRenderer },
  brancher:       { title: 'Cross-Domain Map',     renderer: BrancherRenderer },
  critic:         { title: 'Hypothesis Review',    renderer: CriticRenderer },
  writer:         { title: 'Paper Draft',          renderer: WriterRenderer },
}
```

### 4.4 Dashboard Components (`components/dashboard/`)

| Component | Reads From | Lines |
|-----------|-----------|-------|
| `stepper.tsx` | `PHASES` constant + `store.phase` | ~40 |
| `feed.tsx` | `store.feed` array | ~45 |
| `budget.tsx` | `store.budgetSpent`, `store.budgetCap` | ~30 |
| `errors.tsx` | `store.errors` array | ~45 |
| `rules.tsx` | `store.rules` + `useRules()` hook | ~60 |

---

## 5. Hooks (Thin Wrappers)

Each hook: fetch/subscribe → update store. Max 25 lines.

| Hook | Purpose | Calls |
|------|---------|-------|
| `use-session.ts` | Fetch session by ID, set in store | `callApi('/sessions/{id}')` |
| `use-ws.ts` | Connect WS, route messages via registry | `routeMessage(msg, handlers)` |
| `use-gates.ts` | Approve/reject/edit/revert a gate | `callApi('/gates/{id}', 'POST')` |
| `use-rules.ts` | Add/edit/delete rules | `callApi('/rules', 'POST/PUT/DELETE')` |
| `use-auth.ts` | Validate PIN, set auth state | `callApi('/auth', 'POST')` |

---

## 6. API Routes (Thin Handlers)

Every route: validate → query/mutate → respond. Max 60 lines.

| Route | Methods | Purpose |
|-------|---------|---------|
| `/api/auth` | POST | Validate 4-digit PIN |
| `/api/sessions` | GET, POST | List sessions, create new session |
| `/api/sessions/[id]` | GET | Session detail |
| `/api/gates/[id]` | POST | Resolve gate (approve/reject/edit/revert) → forwards to n8n webhook |
| `/api/rules` | POST, PUT, DELETE | CRUD rule gate entries |
| `/api/webhook` | POST | Receives n8n callbacks → pushes to WS server |

**Pattern** (every route follows this):

```typescript
export async function POST(req, { params }) {
  const body = await req.json()
  const parsed = SomeSchema.safeParse(body)
  if (!parsed.success) return Response.json({ ok: false, error: parsed.error }, { status: 400 })
  const result = await db.query(SQL, [params.id, ...])
  return Response.json({ ok: true, data: result.rows[0] })
}
```

---

## 7. WebSocket Server (`ws-server/index.ts`)

Single file. ~80 lines. Two jobs:

1. **HTTP endpoint** — receives webhook POSTs from n8n
2. **WebSocket server** — pushes messages to UI clients subscribed to a session

```
n8n ──POST /relay──→ [ws-server] ──ws.send()──→ [UI clients]
UI  ──ws message { type: 'subscribe', session_id }──→ [ws-server registers client]
```

No business logic. Just relay.

---

## 8. n8n Code Organization

### 8.1 The Problem

n8n Code nodes don't support imports. Each Code node is a standalone JS sandbox.

### 8.2 The Solution: Snippet Files + Shared Utility Workflow

**`n8n/code/` directory** contains JS snippets. Each snippet is a single function (≤15 lines). These are copy-pasted into n8n Code nodes, with a header comment:

```javascript
// SOURCE: n8n/code/task-creator.js — DO NOT EDIT IN N8N, edit source file
function createTask(sessionId, type, payload, deps = []) {
  // ... 10 lines
}
```

**Shared Utility Workflow** (`shared-utils.json`):
- Contains reusable Code nodes: `embedText`, `callGrobid`, `callUnpaywall`
- Other workflows call it via "Execute Workflow" node
- Single source of truth for embedding, PDF parsing, and API calls

### 8.3 Manager Decision Engine

Split into small, focused Code nodes:

| Code Node | File | Purpose | Lines |
|-----------|------|---------|-------|
| Task Router | `task-router.js` | Registry: `task_type → next action` | ~60 (config) |
| Gate Creator | `gate-creator.js` | `createApprovalGate(session, phase, data)` | 15 |
| Task Creator | `task-creator.js` | `createTask(session, type, payload, deps)` | 15 |
| Budget Checker | `budget-checker.js` | Check budget, return warning/exceeded/ok | 12 |
| Rule Injector | `rule-injector.js` | Query active rules, format as prompt block | 15 |
| Dedup | `dedup.js` | DOI match + title fuzzy + embedding similarity | 15 |
| Embed | `embed.js` | Call Gemini text-embedding-004 API | 10 |

### 8.4 Task Router (Registry Pattern)

The core of the Manager. Maps `completed_task_type → what to do next`.

```javascript
const TASK_ROUTER = {
  scout_scrape:       { check: allScoutsDone,    next: 'gate:scout' },
  analyst_triage:     { check: allTriageDone,    next: 'gate:analyst_triage' },
  analyst_deep_read:  { check: allDeepReadDone,  next: 'gate:analyst' },
  verifier_check:     { check: allVerifiersDone, next: 'gate:verifier' },
  hypothesis_generate:{ check: () => true,       next: 'gate:hypothesis' },
  brancher_explore:   { check: allBranchersDone, next: 'gate:brancher' },
  critic_review:      { check: () => true,       next: 'gate:critic' },
  writer_synthesize:  { check: () => true,       next: 'gate:writer' },
}
```

**Adding a new agent** = add one line to TASK_ROUTER. That's it.

### 8.5 Approval-to-Task Mapping

When user approves a gate, Manager creates next phase tasks:

```javascript
const GATE_TO_TASKS = {
  scout:          (session) => createTriageTasks(session),
  analyst_triage: (session, data) => createDeepReadTasks(session, data.selected_papers),
  analyst:        (session) => createVerifierTasks(session),
  verifier:       (session) => [createTask(session, 'hypothesis_generate', {})],
  hypothesis:     (session, data) => createBrancherTasks(session, data.selected_hypothesis),
  brancher:       (session) => [createTask(session, 'critic_review', {})],
  critic:         (session, data) => handleCriticResult(session, data),
  writer:         (session) => markSessionComplete(session),
}
```

Each function in the map is ≤15 lines.

---

## 9. Extensibility Rules

### Adding a New Agent

| Step | File | Change |
|------|------|--------|
| 1 | `schema/ara_schema.sql` | Add to agents table, update CHECK constraints |
| 2 | `lib/types.ts` | Add to `Phase` union type |
| 3 | `lib/constants.ts` | Add to `PHASES` and `GATE_REGISTRY` |
| 4 | `lib/tokens.ts` | Add to `PHASE_COLOR` |
| 5 | `components/gates/renderers/` | Create one renderer file (~50 lines) |
| 6 | `n8n/code/task-router.js` | Add one line to `TASK_ROUTER` |
| 7 | `n8n/code/task-router.js` | Add one line to `GATE_TO_TASKS` |
| 8 | n8n UI | Create one sub-workflow |

**Total: 6 config lines + 1 renderer + 1 workflow. Zero logic changes.**

### Adding a New WebSocket Event

| Step | File | Change |
|------|------|--------|
| 1 | `lib/types.ts` | Add to `WsEventType` union |
| 2 | `lib/ws.ts` | Add one line to `createHandlers` |
| 3 | `lib/store.ts` | Add handler method if needed |

**Total: 3 lines.**

### Adding a New Approval Gate Action

| Step | File | Change |
|------|------|--------|
| 1 | `lib/types.ts` | Add to `GateAction` union |
| 2 | `lib/constants.ts` | Add to `GATE_ACTIONS` |
| 3 | `components/gates/gate-actions.tsx` | Auto-renders from `GATE_ACTIONS` — no change needed |

**Total: 2 lines.**

---

## 10. Data Flow Diagram

```
USER
  │
  ├── PIN ──→ /api/auth ──→ Postgres (check PIN env var)
  │
  ├── Session Config ──→ /api/sessions POST ──→ Postgres (INSERT research_sessions)
  │                                            ──→ n8n webhook (start Manager)
  │
  ├── Live Dashboard ←── WebSocket ←── ws-server ←── n8n (agent_progress, phase updates)
  │
  ├── Approval Gate ──→ /api/gates POST ──→ n8n webhook (approval_gate_resolved)
  │                                        ──→ Postgres (UPDATE approval_gates)
  │
  ├── Rule Gate ──→ /api/rules POST ──→ Postgres (INSERT rule_gate)
  │                                    ──→ ws-server (rule_gate_updated push)
  │
  └── Export ──→ /api/sessions/[id]/export ──→ Generate ZIP (LaTeX + PDF + index.html + sources)

N8N MANAGER (every 5s)
  │
  ├── Query ready_tasks ──→ Dispatch to agent sub-workflows via webhook
  ├── Query stale_tasks ──→ Retry or mark failed
  ├── Process completed tasks ──→ TASK_ROUTER ──→ Create next tasks or gates
  └── Check budget ──→ Pause if exceeded

AGENTS (sub-workflows)
  │
  ├── Scout ──→ Academic APIs ──→ GROBID ──→ Qdrant + Postgres
  ├── Analyst ──→ Qdrant (RAG) ──→ Claude API ──→ Postgres (claims)
  ├── Verifier ──→ CrossRef + Qdrant ──→ Claude API ──→ Postgres (claim scores)
  ├── Hypothesis ──→ Postgres + Qdrant ──→ Claude API ──→ Postgres (hypotheses)
  ├── Brancher ──→ Academic APIs + Qdrant ──→ Claude API ──→ Postgres (branches)
  ├── Critic ──→ Postgres ──→ Claude API ──→ Postgres (hypothesis_scores)
  └── Writer ──→ Postgres + Qdrant ──→ Claude API ──→ Postgres (LaTeX output)
```

---

## 11. File Budget

| Layer | Files | Avg Lines | Total Lines |
|-------|-------|-----------|-------------|
| `lib/` (7 core files) | 7 | 80 | ~560 |
| `hooks/` | 5 | 25 | ~125 |
| `components/ui/` | 6 | 35 | ~210 |
| `components/gates/` | 10 | 50 | ~500 |
| `components/dashboard/` | 5 | 45 | ~225 |
| `components/session/` | 3 | 60 | ~180 |
| `components/layout/` | 1 | 40 | ~40 |
| `app/` pages | 5 | 35 | ~175 |
| `app/api/` routes | 6 | 55 | ~330 |
| `ws-server/` | 1 | 80 | ~80 |
| `n8n/code/` snippets | 7 | 15 | ~105 |
| **Total** | **56 files** | | **~2,530 lines** |

The entire UI + WS server + n8n code snippets fit in ~2,500 lines of code.

---

## 12. Dependencies

| Package | Purpose | Used In |
|---------|---------|---------|
| `next` | Framework | UI |
| `react` | UI library | UI |
| `zustand` | State management | UI |
| `zod` | Validation | UI + API |
| `tailwindcss` | Styling | UI |
| `pg` | Postgres client | API routes |
| `ws` | WebSocket server | ws-server |
| `express` | HTTP for WS webhook receiver | ws-server |

**8 dependencies.** No utility libraries (lodash, etc.). No CSS frameworks beyond Tailwind. No state management beyond Zustand.
