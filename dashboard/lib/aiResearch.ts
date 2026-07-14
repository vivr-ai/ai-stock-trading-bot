// AI Research Assistant (Phase 4): turns Pattern Discovery's statistical
// findings into plain-English, prioritized recommendations for human review.
//
// Server-only module (reads ANTHROPIC_API_KEY) - never import this from a
// client component. Deliberately calls the Anthropic Messages API directly
// via fetch rather than adding the @anthropic-ai/sdk dependency, since this
// is the only place in the dashboard that needs it and the request shape is
// simple.
//
// Hard constraint carried over from the product spec: this module NEVER
// writes to strategy_versions or any trading-behaviour table. It only
// produces rows for strategy_recommendations with status='pending' - a
// human must explicitly approve (Recommendations page) and then separately
// deploy a new strategy version (Strategy Versions page) before anything
// the bot does changes. The prompt below repeats this constraint to the
// model itself so it doesn't phrase output as if a change is already live.
//
// Model choice is configurable per the user's explicit requirement: default
// to Haiku for scheduled/cheap runs (Phase 7 will call this on a schedule),
// allow Sonnet on demand for deeper analysis. Model IDs are env-configurable
// so they can be bumped without a code change if Anthropic renames a model.

import type { Finding } from "./patternDiscovery";
import type { PerformanceMetrics } from "./strategyAnalytics";

export type ResearchModel = "haiku" | "sonnet";

const MODEL_IDS: Record<ResearchModel, string> = {
  haiku: process.env.RESEARCH_MODEL_HAIKU || "claude-haiku-4-5-20251001",
  sonnet: process.env.RESEARCH_MODEL_SONNET || "claude-sonnet-5",
};

export type AIRecommendationDraft = {
  title: string;
  observation: string;
  evidence: string;
  statisticalConfidence: string;
  estimatedImpact: string;
  risks: string;
  recommendation: string;
  priority: "low" | "medium" | "high";
};

export type ResearchContext = {
  totalTradesAnalysed: number;
  activeStrategyVersion: string;
  performance: PerformanceMetrics & { maxDrawdownPct: number | null; sharpeRatio: number | null };
  qualifyingFindings: Finding[]; // pre-filtered to meetsMinSample === true
};

const SYSTEM_PROMPT = `You are the AI Research Assistant for an automated stock trading platform's research layer.

Your ONLY job is to read statistically-gated findings about past trading performance and turn them into plain-English recommendations for a human to review. You do not have the ability to change trading behaviour, and you must never write as if a recommendation is already in effect - every recommendation you produce is advisory only and requires explicit human approval, followed by a separate manual "deploy new strategy version" step, before it affects any real trade.

Hard rules:
- Base every recommendation ONLY on the findings and performance data provided to you. Do not invent statistics, sample sizes, or trends not present in the input.
- Do not overfit to a small number of recent trades. If a finding's sample size is marginal, say so plainly in "statisticalConfidence" rather than overstating certainty.
- If the provided findings do not support any actionable recommendation, return an empty array. Do not manufacture a recommendation just to have output.
- Each recommendation must be traceable to at least one specific finding you were given - reference it in "evidence".
- "priority" must be "low", "medium", or "high" based on a combination of statistical confidence and estimated impact - do not default to "medium" for everything.
- Respond with ONLY a JSON array (no markdown code fences, no commentary before or after). Each element must have exactly these string fields: title, observation, evidence, statisticalConfidence, estimatedImpact, risks, recommendation, priority.`;

function buildUserPrompt(ctx: ResearchContext): string {
  const findingsSummary = ctx.qualifyingFindings.map((f) => ({
    category: f.category,
    title: f.title,
    description: f.description,
    sampleSize: f.sampleSize,
    baselineSampleSize: f.baselineSampleSize,
    statisticalMethod: f.statisticalMethod,
    pValue: f.pValue,
    effectSize: f.effectSize,
    isSignificant: f.isSignificant,
    confidenceLevel: f.confidenceLevel,
  }));

  return JSON.stringify(
    {
      context: {
        totalTradesAnalysed: ctx.totalTradesAnalysed,
        activeStrategyVersion: ctx.activeStrategyVersion,
        overallPerformance: {
          totalTrades: ctx.performance.totalTrades,
          winRatePct: ctx.performance.winRatePct,
          profitFactor: ctx.performance.profitFactor,
          expectancy: ctx.performance.expectancy,
          avgWin: ctx.performance.avgWin,
          avgLoss: ctx.performance.avgLoss,
          maxDrawdownPct: ctx.performance.maxDrawdownPct,
          sharpeRatio: ctx.performance.sharpeRatio,
        },
      },
      findingsThatMeetMinimumSampleSize: findingsSummary,
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

function validateDraft(raw: unknown): AIRecommendationDraft | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const requiredStrings = [
    "title", "observation", "evidence", "statisticalConfidence",
    "estimatedImpact", "risks", "recommendation",
  ];
  for (const key of requiredStrings) {
    if (typeof r[key] !== "string" || (r[key] as string).length === 0) return null;
  }
  if (r.priority !== "low" && r.priority !== "medium" && r.priority !== "high") return null;
  return r as unknown as AIRecommendationDraft;
}

export async function generateResearchReport(
  ctx: ResearchContext,
  model: ResearchModel = "haiku"
): Promise<AIRecommendationDraft[]> {
  if (ctx.qualifyingFindings.length === 0) {
    // Nothing meets the minimum sample size yet - don't call the model at
    // all, since there is nothing grounded to ask it to reason about.
    return [];
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new Error(
      "ANTHROPIC_API_KEY is not set for the dashboard service - add it in Railway variables " +
        "(the same key the bot uses for sentiment analysis can be reused)."
    );
  }

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: MODEL_IDS[model],
      max_tokens: 4096,
      temperature: 0.2,
      system: SYSTEM_PROMPT,
      messages: [{ role: "user", content: buildUserPrompt(ctx) }],
    }),
  });

  if (!res.ok) {
    const bodyText = await res.text().catch(() => "");
    throw new Error(`Anthropic API request failed (${res.status}): ${bodyText.slice(0, 500)}`);
  }

  const json = (await res.json()) as { content?: { type: string; text?: string }[] };
  const text = (json.content ?? [])
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text)
    .join("");

  let parsed: unknown;
  try {
    parsed = JSON.parse(stripCodeFences(text));
  } catch {
    throw new Error("AI Research Assistant returned non-JSON output - could not parse a recommendation list.");
  }

  if (!Array.isArray(parsed)) {
    throw new Error("AI Research Assistant did not return a JSON array as instructed.");
  }

  return parsed.map(validateDraft).filter((d): d is AIRecommendationDraft => d !== null);
}

export function modelIdFor(model: ResearchModel): string {
  return MODEL_IDS[model];
}
