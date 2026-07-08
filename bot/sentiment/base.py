"""Abstract sentiment analyzer interface and shared result type.

Sentiment is scored on a -10 .. +10 integer-ish scale:
  +10 = overwhelmingly bullish news, -10 = overwhelmingly bearish, 0 = mixed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from ..news.base import Article


@dataclass
class SentimentResult:
    symbol: str
    score: float  # -10.0 .. 10.0
    label: str  # "positive" | "neutral" | "negative"
    rationale: str
    article_count: int
    positive_count: int = 0
    negative_count: int = 0


SYSTEM_PROMPT = (
    "You are a financial news sentiment classifier. You are given recent "
    "headlines and summaries about a single stock. Judge the likely near-term "
    "impact on that stock's price. Respond with ONLY a JSON object, no prose, "
    "of the form: "
    '{"score": <integer -10..10>, "label": "<positive|neutral|negative>", '
    '"positive_count": <int>, "negative_count": <int>, '
    '"rationale": "<one short sentence>"}. '
    "score 10 = overwhelmingly bullish, 0 = neutral/mixed, -10 = overwhelmingly "
    "bearish. positive_count and negative_count are how many of the supplied "
    "headlines are clearly bullish vs clearly bearish."
)


def build_user_prompt(symbol: str, articles: List[Article]) -> str:
    lines = [f"Stock: {symbol}", "Recent headlines:"]
    for i, a in enumerate(articles, 1):
        snippet = (a.headline + " — " + a.summary).strip(" —")
        lines.append(f"{i}. {snippet[:400]}")
    return "\n".join(lines)


def parse_sentiment_json(symbol: str, text: str, count: int) -> SentimentResult:
    """Extract the JSON object from a model response into a SentimentResult."""
    import json
    import re

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return SentimentResult(symbol, 0.0, "neutral", "unparseable response", count)
    try:
        data = json.loads(match.group(0))
        score = max(-10.0, min(10.0, float(data.get("score", 0.0))))
        return SentimentResult(
            symbol=symbol,
            score=score,
            label=str(data.get("label", "neutral")),
            rationale=str(data.get("rationale", "")),
            article_count=count,
            positive_count=int(data.get("positive_count", 0) or 0),
            negative_count=int(data.get("negative_count", 0) or 0),
        )
    except (ValueError, TypeError) as exc:
        return SentimentResult(symbol, 0.0, "neutral", f"parse error: {exc}", count)


class SentimentAnalyzer(ABC):
    @abstractmethod
    def analyze(self, symbol: str, articles: List[Article]) -> SentimentResult:
        raise NotImplementedError


def get_sentiment_analyzer(cfg) -> SentimentAnalyzer:
    from .claude_analyzer import ClaudeAnalyzer
    from .openai_analyzer import OpenAIAnalyzer
    from .lexicon_analyzer import LexiconAnalyzer

    retry_attempts = cfg.retry.max_attempts
    retry_base_delay = cfg.retry.base_delay_seconds

    if cfg.sentiment.provider == "lexicon":
        # Offline word-list scorer: no API key, no extra dependency, nothing to retry.
        return LexiconAnalyzer()

    if cfg.sentiment.provider == "claude":
        if not cfg.sentiment.claude_api_key or cfg.sentiment.claude_api_key.startswith("YOUR_"):
            raise ValueError("sentiment.provider is 'claude' but ANTHROPIC_API_KEY is not set")
        return ClaudeAnalyzer(
            cfg.sentiment.claude_api_key, cfg.sentiment.claude_model, cfg.sentiment.temperature,
            retry_attempts=retry_attempts, retry_base_delay=retry_base_delay,
        )

    if cfg.sentiment.provider == "openai":
        if not cfg.sentiment.openai_api_key or cfg.sentiment.openai_api_key.startswith("YOUR_"):
            raise ValueError("sentiment.provider is 'openai' but OPENAI_API_KEY is not set")
        return OpenAIAnalyzer(
            cfg.sentiment.openai_api_key, cfg.sentiment.openai_model, cfg.sentiment.temperature,
            retry_attempts=retry_attempts, retry_base_delay=retry_base_delay,
        )

    raise ValueError(f"Unknown sentiment provider: {cfg.sentiment.provider}")
