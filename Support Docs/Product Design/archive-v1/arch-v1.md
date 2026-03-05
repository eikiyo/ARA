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

**Exception:** n8n Code nodes may exceed 100 lines because they are standalone sandboxes with no import capability. Functions within Code nodes still follow the 15-line limit.

---

## 2. Project Structure

```
ara/
├── ui/                              # Next.js app (deployed to Cloudflare Worker)
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
│   │   │   │   └── page.tsx         # Session list (Phase 2)
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx     # Session detail (Phase 2)
│   │   │   └── api/                 # API routes
│   │   │       ├── auth/
│   │   │       │   └── route.ts     # PIN validation (CF Worker secret)
│   │   │       ├── sessions/
│   │   │       │   └── route.ts     # POST create, GET list
│   │   │       ├── sessions/[id]/
│   │   │       │   └── route.ts     # GET detail, PUT update
│   │   │       ├── gates/[id]/
│   │   │       │   └── route.ts     # POST approve/reject/edit/revert
│   │   │       └── rules/
│   │   │           └── route.ts     # POST add, PUT edit, DELETE remove
│   │   ├── lib/                     # Shared logic (THE core of reuse)
│   │   │   ├── types.ts             # ALL TypeScript types (mirrors DB schema)
│   │   │   ├── constants.ts         # ALL strings, configs, registries
│   │   │   ├── tokens.ts            # ALL colors, spacing, typography
│   │   │   ├── api.ts               # ONE function for all HTTP calls
│   │   │   ├── realtime.ts          # Supabase Realtime event handler registry
│   │   │   ├── store.ts             # Single Zustand store
│   │   │   ├── supabase.ts          # Supabase client (replaces db.ts)
│   │   │   └── validate.ts          # Zod schemas for all inputs
│   │   ├── hooks/                   # Custom React hooks (thin wrappers)
│   │   │   ├── use-session.ts       # Fetch + cache session data
│   │   │   ├── use-realtime.ts      # Supabase Realtime subscribe + event routing
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
│   │       │   ├── session-list.tsx  # History table (Phase 2)
│   │       │   └── session-detail.tsx # Read-only detail (Phase 2)
│   │       └── layout/
│   │           └── shell.tsx         # App shell (header, nav)
│   ├── wrangler.toml                # Cloudflare Worker config
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
│   └── ara_schema.sql               # Database schema (Supabase + pgvector)
│
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

Mirrors `ara_schema.sql` exactly. Every component, hook, API route, and Realtime handler imports from here.

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

// Realtime Events (Supabase Realtime on events table)
export type EventType = 'phase_started' | 'agent_progress' | 'task_completed'
  | 'phase_completed' | 'approval_required' | 'error'
  | 'budget_warning' | 'budget_exceeded' | 'session_completed' | 'rule_gate_updated'
export interface RealtimeEvent { event_id: number; session_id: number; event_type: EventType; payload: Record<string, any>; created_at: string }
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

### 3.5 `lib/realtime.ts` — Supabase Realtime Event Handler Registry

Maps event types to store actions. Adding a new event = add one line. Supabase Realtime pushes new rows from the `events` table automatically.

```typescript
import type { RealtimeEvent } from './types'
import { useStore } from './store'

type Handler = (e: RealtimeEvent) => void
type StoreApi = ReturnType<typeof useStore.getState>

export function createHandlers(s: StoreApi): Record<string, Handler> {
  return {
    phase_started:     (e) => s.setPhase(e.payload.phase),
    agent_progress:    (e) => s.addFeed(e),
    task_completed:    (e) => s.addFeed(e),
    phase_completed:   (e) => s.addFeed(e),
    approval_required: (e) => s.setGate(e),
    error:             (e) => s.addError(e),
    budget_warning:    (e) => s.setBudget(e.payload.budget_spent, e.payload.budget_cap),
    budget_exceeded:   (e) => s.setBudget(e.payload.budget_spent, e.payload.budget_cap),
    session_completed: (e) => s.setPhase('completed'),
    rule_gate_updated: (e) => s.addRule(e),
  }
}

export function routeEvent(event: RealtimeEvent, handlers: Record<string, Handler>) {
  const fn = handlers[event.event_type]
  if (fn) fn(event)
}
```

### 3.6 `lib/store.ts` — Single State Store (Zustand)

All app state in one store. Components read via selectors. No prop drilling.

```typescript
import { create } from 'zustand'
import type { Session, ApprovalGate, RuleGate, RealtimeEvent, SessionPhase } from './types'

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
  setGate: (e: RealtimeEvent) => void
  clearGate: () => void

  // Feed
  feed: FeedItem[]
  addFeed: (e: RealtimeEvent) => void
  clearFeed: () => void

  // Budget
  budgetSpent: number
  budgetCap: number
  setBudget: (spent: number, cap: number) => void

  // Errors
  errors: ErrorItem[]
  addError: (e: RealtimeEvent) => void
  dismissError: (id: string) => void

  // Rules
  rules: RuleGate[]
  addRule: (e: RealtimeEvent) => void
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
| `use-realtime.ts` | Subscribe to Supabase Realtime, route events | `routeEvent(event, handlers)` |
| `use-gates.ts` | Approve/reject/edit/revert a gate | `callApi('/gates/{id}', 'POST')` |
| `use-rules.ts` | Add/edit/delete rules | `callApi('/rules', 'POST/PUT/DELETE')` |
| `use-auth.ts` | Validate PIN, set auth state | `callApi('/auth', 'POST')` |

---

## 6. API Routes (Thin Handlers)

Every route: validate → query/mutate → respond. Max 60 lines.

| Route | Methods | Purpose |
|-------|---------|---------|
| `/api/auth` | POST | Validate 4-digit PIN (against CF Worker secret) |
| `/api/sessions` | GET, POST | List sessions, create new session |
| `/api/sessions/[id]` | GET | Session detail |
| `/api/gates/[id]` | POST | Resolve gate (approve/reject/edit/revert) → forwards to n8n webhook |
| `/api/rules` | POST, PUT, DELETE | CRUD rule gate entries |

**Pattern** (every route follows this):

```typescript
import { supabase } from '@/lib/supabase'

export async function POST(req, { params }) {
  const body = await req.json()
  const parsed = SomeSchema.safeParse(body)
  if (!parsed.success) return Response.json({ ok: false, error: parsed.error }, { status: 400 })
  const { data, error } = await supabase.from('table').insert(parsed.data).select().single()
  if (error) return Response.json({ ok: false, error: error.message }, { status: 500 })
  return Response.json({ ok: true, data })
}
```

---

## 7. Real-Time Architecture (Supabase Realtime)

**No dedicated server.** n8n inserts rows into the `events` table in Supabase. Supabase Realtime automatically pushes new rows to subscribed UI clients.

```
n8n ──INSERT INTO events──→ [Supabase Postgres] ──Realtime push──→ [UI clients]
UI  ──supabase.channel('events').on('INSERT')──→ [routeEvent() → store update]
```

No relay server. No WebSocket server to maintain. Supabase handles the connection.

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
- Contains reusable Code nodes: `embedText`, `callOpenRouter`, `callUnpaywall`
- Other workflows call it via "Execute Workflow" node
- Single source of truth for embedding, LLM calls (OpenRouter), and API calls
- GROBID (`callGrobid`) deferred to Phase 2

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

### Adding a New Realtime Event

| Step | File | Change |
|------|------|--------|
| 1 | `lib/types.ts` | Add to `EventType` union |
| 2 | `lib/realtime.ts` | Add one line to `createHandlers` |
| 3 | `lib/store.ts` | Add handler method if needed |
| 4 | `schema/ara_schema.sql` | Add to `events.valid_event_type` CHECK |

**Total: 4 lines.**

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
USER (Cloudflare Worker — Next.js)
  │
  ├── PIN ──→ /api/auth ──→ CF Worker secret check
  │
  ├── Session Config ──→ /api/sessions POST ──→ Supabase (INSERT research_sessions)
  │                                            ──→ n8n webhook (start Manager)
  │
  ├── Live Dashboard ←── Supabase Realtime ←── events table ←── n8n (INSERT events)
  │
  ├── Approval Gate ──→ /api/gates POST ──→ n8n webhook (approval_gate_resolved)
  │                                        ──→ Supabase (UPDATE approval_gates)
  │
  ├── Rule Gate ──→ /api/rules POST ──→ Supabase (INSERT rule_gate)
  │                                    ──→ Supabase Realtime (auto-push)
  │
  └── Export ──→ Phase 2

N8N MANAGER (every 5s, only while session active)
  │
  ├── Query ready_tasks ──→ Dispatch to agent sub-workflows via webhook
  ├── Query stale_tasks ──→ Retry or mark failed
  ├── Process completed tasks ──→ TASK_ROUTER ──→ Create next tasks or gates
  └── Check budget ──→ Pause if exceeded

AGENTS (sub-workflows)
  │
  ├── Scout ──→ Academic APIs ──→ Supabase (papers) + pgvector (embeddings)
  ├── Analyst ──→ pgvector (RAG) ──→ OpenRouter LLM ──→ Supabase (claims)
  ├── Verifier ──→ CrossRef + pgvector ──→ OpenRouter LLM ──→ Supabase (claim scores)
  ├── Hypothesis ──→ Supabase + pgvector ──→ OpenRouter LLM ──→ Supabase (hypotheses)
  ├── Brancher ──→ Academic APIs + pgvector ──→ OpenRouter LLM ──→ Supabase (branches)
  ├── Critic ──→ Supabase ──→ OpenRouter LLM ──→ Supabase (hypothesis_scores)
  └── Writer ──→ Supabase + pgvector ──→ OpenRouter LLM ──→ Supabase (draft output)
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
| `app/api/` routes | 5 | 55 | ~275 |
| `n8n/code/` snippets | 7 | 15 | ~105 |
| **Total** | **54 files** | | **~2,395 lines** |

The entire UI + n8n code snippets fit in ~2,400 lines of code. No WebSocket server to maintain.

---

## 12. Dependencies

| Package | Purpose | Used In |
|---------|---------|---------|
| `next` | Framework | UI |
| `react` | UI library | UI |
| `zustand` | State management | UI |
| `zod` | Validation | UI + API |
| `tailwindcss` | Styling | UI |
| `@supabase/supabase-js` | Supabase client (DB + Realtime) | UI + API |

**6 dependencies.** No utility libraries (lodash, etc.). No CSS frameworks beyond Tailwind. No state management beyond Zustand. No WebSocket libraries — Supabase client handles real-time.
