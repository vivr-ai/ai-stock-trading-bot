// Monthly Research Report (Phase 7): a periodic plain-English rollup
// combining Performance Analytics (Phase 1), Pattern Discovery (Phase 3),
// and Strategy Health (Phase 5) into one narrative report - "what happened,
// what we learned, what's worth watching." Purely descriptive; writes only
// to monthly_research_reports, never to strategy_versions or any trading
// config. Recommended improvements mentioned in a report are NOT the same
// as an approved strategy_recommendations row - if a report calls out
// something actionable, it still needs to go through the normal
// Recommendations approval workflow separately.
//
// Server-only module (reads ANTHROPIC_API_KEY) - never import from a client
// component. Deliberately has its own small "call Claude" fetch wrapper
// rather than sharing dashboard/lib/aiResearch.ts's, to avoid touching that
// already-shipped Phase 4 module for an unrelated report type.

import type { ClosedTradeRow, PerformanceMetrics } from "./strategyAnalytics";
import type { Finding } from "./patternDiscovery";
import type { StrategyHealth } from "./strategyHealth";

export type MonthlyReportModel = "haiku" | "sonnet";

const MODEL_IDS: Record<MonthlyReportModel, string> = {
  haiku: process.env.RESEARCH_MODEL_HAIKU || "claude-haiku-4-5-20251001",
  sonnet: process.env.RESEARCH_MODEL_SONNET || "claude-sonnet-5",
};

export type MonthlyReportDraft = {
  overallPerformance: string;
  lessonsLearned: string;
  emergingPatterns: string;
  potentialOptimizations: string;
  marketObservations: string;
  recommendedImprovements: string;
  telegramSummary: string; // short digest, kept under ~500 chars for Telegram
};

export type MonthlyReportContext = {
  periodStart: string;
  periodEnd: string;
  totalTradesAllTime: number;
  totalTradesThisPeriod: number;
  activeStrategyVersion: string;
  periodPerformance: PerformanceMetrics;
  allTimePerformance: PerformanceMetrics;
  strategyHealth: StrategyHealth;
  qualifyingFindings: Finding[];
  pendingRecommendationsCount: number;
};

const SYSTEM_PROMPT = `You are writing the Monthly Research Report for an automated stock trading platform's research layer.

This report is READ-ONLY documentation for a human to review - it never changes trading behaviour by itself, and you must not write as though any change is already in effect. Anything you flag as worth changing still requires a separate recommendation to be created, approved, and deployed through the normal workflow - this report is not that workflow.

Hard rules:
- Base every claim ONLY on the performance data, strategy health score, and findings provided to you. Do not invent statistics or trends not present in the input.
- Do not overfit to a small number of recent trades - if the sample size this period is small, say so plainly rather than drawing a firm conclusion.
- If there isn't much to say for a section (e.g. no emerging patterns this period), say that plainly and briefly rather than padding it.
- Write in plain English, no jargon dumps - a few sentences per section is enough, this isn't an academic paper.
- "recommendedImprovements" should describe what's worth considering, but explicitly note that any of these would need to go through the Recommendations approval workflow before taking effect - never phrase it as already decided.
- "telegramSummary" must be a short, scannable digest (under 500 characters) hitting the highlights only - not a repeat of every section.
- Respond with ONLY a JSON object (no markdown code fences, no commentary before or after) with exactly these string fields: overallPerformance, lessonsLearned, emergingPatterns, potentialOptimizations, marketObservations, recommendedImprovements, telegramSummary.`;

function buildUserPrompt(ctx: MonthlyReportContext): string {
  return JSON.stringify(
    {
      period: { start: ctx.periodStart, end: ctx.periodEnd },
      activeStrategyVersion: ctx.activeStrategyVersion,
      tradesThisPeriod: ctx.totalTradesThisPeriod,
      tradesAllTime: ctx.totalTradesAllTime,
      pendingRecommendationsAwaitingReview: ctx.pendingRecommendationsCount,
      periodPerformance: ctx.periodPerformance,
      allTimePerformance: ctx.allTimePerformance,
      strategyHealth: {
        overallScore: ctx.strategyHealth.overallScore,
        confidenceLevel: ctx.strategyHealth.confidenceLevel,
        components: ctx.strategyHealth.components.map((c) => ({
          label: c.label, score: c.score, meetsMinSample: c.meetsMinSample,
        })),
      },
      findingsThatMeetMinimumSampleSize: ctx.qualifyingFindings.map((f) => ({
        category: f.category, title: f.title, description: f.description,
        sampleSize: f.sampleSize, isSignificant: f.isSignificant, confidenceLevel: f.confidenceLevel,
      })),
    },
    null,
    2
  );
}

function stripCodeFences(text: string): string {
  const trimmed = text.trim();
  const fenceMatch = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/);
  return fenceMatch ? fenceMatch[1] : trimmed;
}

function validateDraft(raw: unknown): MonthlyReportDraft | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const requiredStrings = [
    "overallPerformance", "lessonsLearned", "emergingPatterns", "potentialOptimizations",
    "marketObservations", "recommendedImprovements", "telegramSummary",
  ];
  for (const key of requiredStrings) {
    if (typeof r[key] !== "string" || (r[key] as string).length === 0) return null;
  }
  return r as unknown as MonthlyReportDraft;
}

function fallbackDraft(ctx: MonthlyReportContext): MonthlyReportDraft {
  // Used only if the model call fails outright - still a real, data-grounded
  // report (just unpolished prose), so a transient Anthropic API issue
  // doesn't mean no report at all for the month.
  const perf = ctx.periodPerformance;
  const overallPerformance =
    perf.totalTrades > 0
      ? `${perf.totalTrades} trade(s) closed this period. Win rate ${perf.winRatePct?.toFixed(1) ?? "—"}%, ` +
        `expectancy ${perf.expectancy?.toFixed(2) ?? "—"} per trade.`
      : "No trades closed this period.";
  return {
    overallPerformance,
    lessonsLearned: "AI narrative unavailable this run - see raw performance and findings data above.",
    emergingPatterns:
      ctx.qualifyingFindings.length > 0
        ? `${ctx.qualifyingFindings.length} finding(s) currently meet their minimum sample size - see Pattern Discovery.`
        : "No findings currently meet their minimum sample size.",
    potentialOptimizations: "AI narrative unavailable this run.",
    marketObservations: "AI narrative unavailable this run.",
    recommendedImprovements:
      "AI narrative unavailable this run - review Pattern Discovery and Recommendations directly.",
    telegramSummary: `Monthly report (${ctx.totalTradesThisPeriod} trades this period, strategy health ` +
      `${ctx.strategyHealth.overallScore != null ? ctx.strategyHealth.overallScore.toFixed(0) : "—"}). ` +
      `AI narrative unavailable this run - see dashboard.`,
  };
}

export async function generateMonthlyReport(
  ctx: MonthlyReportContext,
  model: MonthlyReportModel = "haiku"
): Promise<MonthlyReportDraft> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return fallbackDraft(ctx);
  }

  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: MODEL_IDS[model],
        max_tokens: 2048,
        temperature: 0.3,
        system: SYSTEM_PROMPT,
        messages: [{ role: "user", content: buildUserPrompt(ctx) }],
      }),
    });

    if (!res.ok) {
      return fallbackDraft(ctx);
    }

    const json = (await res.json()) as { content?: { type: string; text?: string }[] };
    const text = (json.content ?? [])
      .filter((b) => b.type === "text" && typeof b.text === "string")
      .map((b) => b.text)
      .join("");

    const parsed = JSON.parse(stripCodeFences(text));
    const draft = validateDraft(parsed);
    return draft ?? fallbackDraft(ctx);
  } catch {
    return fallbackDraft(ctx);
  }
}

export function modelIdFor(model: MonthlyReportModel): string {
  return MODEL_IDS[model];
}

// Re-exported for convenience so callers only need one import for the type.
export type { ClosedTradeRow };
