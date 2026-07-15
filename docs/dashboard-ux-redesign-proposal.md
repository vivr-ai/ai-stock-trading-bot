# Dashboard UX & Information Architecture Redesign — Phase 1 Proposal

Status: **awaiting approval before Phase 2 implementation begins.**

## 1. Current state audit

19 top-level pages sit in a single flat sidebar today (Home + 18): Portfolio,
Trade History, Performance, AI Decision Log, Trading Strategy, Strategy
Intelligence, Strategy Versions, Pattern Discovery, Strategy Health,
Recommendations, Risk Dashboard, System Health, Live Readiness,
Notifications, Notification Settings, Accountant Export, Monthly Report.

Home is a single undifferentiated grid of 10 `StatCard`s in build order
(Bot Status, Last Heartbeat, Portfolio Value, Cash, Today's P/L, Total
Return, Open Positions, Last Trade, Market Status, Scheduler Status) — no
sectioning, no priority ordering, no alerts, no actions.

### What's actually wrong (not just "too many items")

1. **Flat lists don't scale past ~10 items** — human recognition-based
   scanning (not search) breaks down well before 19, and this is heading to
   ~50. The sidebar is the symptom; the root cause is that there's no
   grouping layer at all yet.
2. **Pages are ordered by when they were built, not by what question they
   answer.** Strategy Versions, Recommendations, Pattern Discovery, Strategy
   Health, and Strategy Intelligence are all "how is the AI's strategy
   evolving" — but they're scattered through the list alongside Portfolio
   and Risk Dashboard with equal visual weight.
3. **Home answers "what are the numbers" but not "is everything OK."** A
   user has to read all 10 cards and mentally synthesize an answer to the
   five questions this project's own brief poses. There's no single glance
   that confirms "green across the board."
4. **No visual hierarchy between daily-use and occasional pages.**
   Notification Settings and Accountant Export get the same sidebar
   real-estate as Portfolio and Risk.
5. **Naming drifted as pages were added incrementally** — "AI Decision Log"
   vs "AI Decisions", "Trading Strategy" vs "Current Strategy", "Strategy
   Intelligence" vs "Learning & Insights" are all symptoms of no naming
   convention, not one-off mistakes.
6. **No mechanism exists for what happens when a *section* itself grows
   past ~8 pages** (Intelligence already has 7 and is the most active area
   of development). Sidebar → tabs alone just moves the same scaling
   problem one level down; it needs an answer too.

## 2. Proposed architecture

### 2.1 Three-level, config-driven navigation tree

```
Section (sidebar, ~6 fixed items)
  └─ Group (optional — only appears once a section needs it)
       └─ Page (tab / list item)
```

- **Sidebar = sections only**, and sections are chosen to map to the
  *questions* a trading-firm operator asks, not to feature history:
  **Trading · Intelligence · Risk & Safety · Reports · System** (5 sections
  + Home, pinned separately, always first). This list is deliberately
  small and deliberately *not* going to grow — new pages get a new group or
  a new page inside an existing section, never a 7th sidebar item, unless
  a genuinely new question category emerges (rare — Trading firms have had
  the same ~5-6 functions for decades).
- **Secondary nav = horizontal tabs under the section header**, exactly the
  pattern you proposed (Stripe/Linear/GitHub project-settings style). This
  is correct for sections with up to ~7-8 pages, which covers every section
  for the foreseeable future except Intelligence.
- **Tertiary layer = tab *groups*, config data, not new UI.** Once a
  section's page list exceeds ~7, its pages are clustered into named groups
  (e.g. Intelligence → *Decisions* / *Research* / *Governance* / *Advanced*)
  and the tab bar becomes two tiers: group selector, then pages within the
  active group. This is the actual mechanism that prevents "another
  redesign in 3 years" — it's pure configuration, not a new navigation
  paradigm, so it can be turned on per-section the day it's needed and
  ignored everywhere else.
- **One config file drives everything**: sidebar, tab bar, group tiers,
  mobile drawer, and (recommended, see 2.4) a command palette all read
  from a single typed `NavSection[]` tree in `dashboard/lib/navigation.ts`.
  Adding a page is a one-line array entry, never a new component.

### 2.2 Full information architecture at ~50-page scale

Every current page placed, every page from your "future" list placed, with
explicit call-outs where two items likely overlap or will merge later
(flagging these now, not silently deciding — worth a conscious decision
when you actually build them).

**Home** — pinned, not a section.

**Trading** — capital and execution.
| Now | Future |
|---|---|
| Dashboard (renamed from bare "Portfolio" landing — see 2.3) | Orders |
| Portfolio | Positions *(likely = Portfolio evolving, not a separate page — decide when you build it)* |
| Performance | Brokers |
| Trade History | Executions |
| | Watchlists |

**Intelligence** — what the AI decided, why, and how it's evolving. Heaviest
section (7 now, 7 future) — first to use tab-groups.
| Group | Now | Future |
|---|---|---|
| Decisions | Current Strategy *(was Trading Strategy)*, AI Decisions *(was AI Decision Log)* | |
| Research | Learning & Insights *(was Strategy Intelligence)*, Pattern Discovery | Backtesting *(engine already exists inside Recommendations — promote it)*, Model Performance |
| Governance | Strategy Versions, Strategy Health, Recommendations | Strategy Comparison *(Strategy Versions already has a 2-way compare — likely grows in place, not a new page)*, Strategy Optimisation *(overlaps Recommendations — clarify scope later)* |
| Advanced | | AI Memory, Prompt Management, Reinforcement Learning |

**Risk & Safety** — is this safe.
| Now | Future |
|---|---|
| Risk *(was Risk Dashboard)* | Exposure Analysis *(Risk page already computes sector/position exposure — likely the same page deepens rather than forking)* |
| Live Readiness | Correlation Matrix, Position Limits *(riskConfig is already shown on Risk — may just be a tab there)*, Kill Switch, Drawdown Analysis *(overlaps Strategy Health's drawdown component)*, Stress Testing |

**Reports** — what happened, for humans.
| Now | Future |
|---|---|
| Monthly Report | Annual Reports |
| Accountant Export | Tax Reports *(Accountant Export may already BE this — confirm before building a duplicate)*, CSV Exports *(likely a capability on existing pages, not its own page)*, Audit Trail *(Recommendations' approval history + Notifications already form the raw data)*, Investor Reports |

**System** — is the plumbing healthy.
| Now | Future |
|---|---|
| System Health | Logs, API Connections, Railway Status *(System Health already has a stubbed `railway.configured` field — natural extension)*, Database, AI Usage, API Costs, Integrations |
| Notifications | |
| Notification Settings | Environment Settings |

Total at maturity: ~6 (Trading) + ~14 (Intelligence) + ~7 (Risk) + ~7
(Reports) + ~9 (System) + Home ≈ **44 pages**, matching your ~50 estimate,
inside a sidebar that never exceeds 6 items.

### 2.3 Naming convention (so future additions don't drift again)

- **Sections**: one word, the function, no suffix ("Trading" not "Trading
  Hub").
- **Pages**: the noun for the artifact you're looking at, not the action
  ("Current Strategy" not "View Strategy", "AI Decisions" not "Decision
  Log").
- Adopt all three of your suggested renames: AI Decision Log → **AI
  Decisions**, Trading Strategy → **Current Strategy**, Strategy
  Intelligence → **Learning & Insights**. Also rename Risk Dashboard →
  **Risk** (avoids "Risk & Safety > Risk Dashboard" stutter) and give the
  Trading section's landing page (today's Portfolio-flavoured "Home") a
  clear identity — see 2.5.

### 2.4 Recommended addition you didn't ask for: a command palette (⌘K)

This is the single highest-leverage piece for "won't need another redesign
in 3-5 years." Hierarchical nav (sidebar → tabs → groups) optimizes for
*orientation* — knowing where you are and what exists. It always costs a
few clicks to reach a specific deep page. At 50 pages, the actual fast path
power users (and you) will want is fuzzy search: type "correlation", hit
enter. Linear, Vercel, GitHub, and Stripe all converge on this because
hierarchy and search solve different problems and both are needed.

Because the same nav-tree config already has every page's label, section,
and group, a command palette is close to free to add on top — it's a
`Cmd+K` overlay that fuzzy-searches the same array already powering the
sidebar. I'd build a minimal version in Phase 2 (list + fuzzy filter, no
fancy AI features) so the pattern exists from day one and simply keeps
working as pages are added — no future redesign required. If you'd rather
defer this to a later phase, the nav config is built to support it either
way; nothing about deferring it costs rework later.

### 2.5 Home: from stat grid to cockpit

Keep your proposed sections (Portfolio, Trading Activity, Bot Status,
Market Status, AI Activity, Risk Snapshot, Alerts, Quick Actions) — they're
the right content. Two structural additions:

1. **A one-line status strip pinned above everything else**: Bot ●
   Market ● Risk ● Today's P/L, each a coloured dot + short label. This is
   the literal answer to "within 5 seconds" — it's the Datadog/Grafana
   "everything green" pattern, and it's the one thing that lets you *not*
   read the rest of the page on a normal day.
2. **Alerts get a distinct, non-card treatment** (a banner strip, not
   another `StatCard`), and only render when something actually needs
   attention — an empty Alerts section should visually disappear, not show
   an empty card.

Section-by-section, mapped to data that already exists vs. what's new:

| Section | Already available today | New work needed |
|---|---|---|
| Portfolio | Value, cash, today's P/L (`/api/status`) | Weekly/monthly/lifetime return (derivable from `portfolio_snapshots`, same math `/api/performance` already uses) |
| Trading Activity | Open positions, last trade (`/api/status`) | Open orders (not currently tracked — Alpaca has no resting limit orders in this bot's model today, flag as N/A until Orders page exists), trades today count, win rate (from `closed_trades`) |
| Bot Status | Running/stopped, heartbeat, scheduler status (`/api/system-health`) | Next scheduled run (computable from the known cron rule, not stored) |
| Market Status | Market open/closed (`heartbeats.market_open`) | Time until next session / current session label (needs a lightweight market-calendar heuristic — the dashboard has no direct Alpaca connection today, only Postgres, so this is an approximation, disclosed as such, same pattern as the existing market-regime heuristic) |
| AI Activity | Latest decision, confidence, reason (`/api/decisions`), active strategy version (`strategy_versions`) | Market sentiment as a labelled summary (data exists per-trade; a "current" sentiment read needs a small aggregation) |
| Risk Snapshot | Exposure, largest position, alerts (`/api/risk`) | Drawdown (exists in Strategy Health/Performance, needs surfacing here), a single "current risk level" label (new, small derived rollup — same spirit as Strategy Health's confidence tiers) |
| Alerts | Risk alerts, notifications already exist | Consolidate risk + system + recommendation alerts into one feed |
| Quick Actions | — | Pause/Resume Trading and Emergency Stop need a real backend hook (currently nothing pauses the bot remotely — this is a genuinely new capability, worth flagging as the one item here that's more than a UI change; recommend scoping it carefully in Phase 2 kickoff) |

## 3. What I'm validating vs. changing from your brief

- **Validating**: sidebar for sections, horizontal tabs for pages within a
  section, your Home content sections, your renames, your ~50-page
  scalability framing.
- **Adding**: the group-tier mechanism inside a section (so tabs don't hit
  the same wall in 3 years), a command palette (the actual long-horizon
  answer to 50 pages), a top-of-page status strip on Home, and a few
  explicit "these will likely merge" flags in the future page list so you
  don't accidentally build two pages that do the same thing.
- **Deferring / flagging as non-trivial**: Pause/Resume/Emergency Stop need
  a real control path into the bot process, not just a button — I'll scope
  this properly at Phase 2 kickoff rather than stub a button that does
  nothing.

## 4. Technical approach for Phase 2

- `dashboard/lib/navigation.ts` — single typed config (`NavSection[]`),
  the one file you edit to add a page.
- `Sidebar.tsx` (sections, replaces today's flat `Nav.tsx` link list),
  `SectionTabs.tsx` (horizontal tabs + optional group tier), both pure
  renderers over the config — zero duplicated route lists.
- `Shell.tsx` composes Sidebar + SectionTabs + content, derives the active
  section/page from the current route against the same config.
- Mobile: bottom icon bar for sections (thumb-reachable, native-app
  convention already used by Linear/Vercel mobile web) + a slide-up sheet
  for that section's pages, same config, different renderer.
- Every existing route/page keeps its current URL and content — this is a
  navigation and Home-layout refactor, not a page rewrite. No functionality
  regresses.
