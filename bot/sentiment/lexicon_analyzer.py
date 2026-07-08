"""Offline, dependency-free sentiment analyzer.

This is the "no paid API" option. It scores headlines with a small finance
word list instead of calling an LLM — so it needs no API key and no extra pip
package. It is deliberately simple: it counts bullish vs bearish words in each
headline, handles a few negators ("not", "no", "fails to"), and turns the net
tally into a -10..+10 score.

Trade-off (be aware): a word-list is much cruder than an LLM. It misses
sarcasm, context, and complex phrasing. It is fine for getting the bot running
end-to-end on free data; upgrade to the Claude/OpenAI analyzer when you want
better quality. (A middle ground is the free offline VADER model:
`pip install vaderSentiment` — but the built-in list below needs nothing.)
"""
from __future__ import annotations

import re
from typing import List

from ..news.base import Article
from .base import SentimentAnalyzer, SentimentResult

# Each hit moves an article's tally by 1. Score per net-positive article below.
POSITIVE = {
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally",
    "rallies", "gain", "gains", "rise", "rises", "record", "tops", "upgrade",
    "upgraded", "outperform", "strong", "growth", "profit", "profits", "bullish",
    "breakthrough", "approval", "approved", "wins", "win", "raise", "raises",
    "raised", "boost", "boosts", "soared", "climbs", "rebound", "expands",
    "expansion", "buyback", "dividend", "beat-and-raise", "optimistic", "positive",
    "milestone", "partnership", "demand", "accelerate", "momentum",
}
NEGATIVE = {
    "miss", "misses", "missed", "plunge", "plunges", "plunged", "slump", "slumps",
    "fall", "falls", "fell", "drop", "drops", "dropped", "sink", "sinks", "decline",
    "declines", "downgrade", "downgraded", "cut", "cuts", "lawsuit", "probe",
    "investigation", "recall", "bankruptcy", "warns", "warning", "weak", "loss",
    "losses", "bearish", "halts", "halt", "layoffs", "fraud", "slowdown", "delay",
    "delayed", "concern", "concerns", "risk", "risks", "selloff", "tumble",
    "tumbles", "underperform", "scandal", "default", "shortfall", "disappoints",
    "disappointing", "negative", "crash", "slashes", "slashed",
}
NEGATORS = {"not", "no", "never", "without", "fails", "fail", "failed", "isn't",
            "wasn't", "won't", "lacks", "lacking"}

_WORD_RE = re.compile(r"[a-z][a-z'\-]+")
# How many points each net-positive (or net-negative) headline contributes.
POINTS_PER_HEADLINE = 3.0


def _score_text(text: str) -> int:
    """Return +1 (bullish), -1 (bearish), or 0 (neutral) for one headline."""
    words = _WORD_RE.findall(text.lower())
    pos = neg = 0
    for i, w in enumerate(words):
        negated = i > 0 and words[i - 1] in NEGATORS
        if w in POSITIVE:
            neg += 1 if negated else 0
            pos += 0 if negated else 1
        elif w in NEGATIVE:
            pos += 1 if negated else 0
            neg += 0 if negated else 1
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


class LexiconAnalyzer(SentimentAnalyzer):
    def analyze(self, symbol: str, articles: List[Article]) -> SentimentResult:
        if not articles:
            return SentimentResult(symbol, 0.0, "neutral", "no news", 0)

        positive = negative = 0
        for a in articles:
            verdict = _score_text(f"{a.headline} {a.summary}")
            if verdict > 0:
                positive += 1
            elif verdict < 0:
                negative += 1

        net = positive - negative
        score = max(-10.0, min(10.0, net * POINTS_PER_HEADLINE))
        label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        rationale = f"{positive} bullish vs {negative} bearish of {len(articles)} headlines"
        return SentimentResult(
            symbol=symbol, score=score, label=label, rationale=rationale,
            article_count=len(articles), positive_count=positive, negative_count=negative,
        )
