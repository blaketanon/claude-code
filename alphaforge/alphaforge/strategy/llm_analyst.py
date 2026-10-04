"""Claude-powered market analyst.

Two jobs, both returning validated structured output:
1. `sentiment_scores`: per-symbol sentiment tilt in [-1, 1] from recent headlines.
2. `regime_commentary`: a short natural-language read of the tape for the admin dashboard.

The analyst is advisory: the policy caps its influence at ±30% of position size.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from alphaforge.config import get_settings

log = logging.getLogger(__name__)


class SymbolSentiment(BaseModel):
    symbol: str
    score: float = Field(ge=-1, le=1, description="-1 very bearish ... +1 very bullish for the next few hours")
    confidence: float = Field(ge=0, le=1)
    rationale: str


class SentimentReport(BaseModel):
    as_of: str
    items: list[SymbolSentiment]
    market_summary: str


class LLMAnalyst:
    def __init__(self, model: str | None = None, headline_fetcher=None):
        import anthropic

        s = get_settings()
        if not s.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        self.model = model or s.llm_model
        self.fetch_headlines = headline_fetcher or default_headline_fetcher

    def analyze(self, symbols: list[str]) -> SentimentReport:
        headlines = self.fetch_headlines(symbols)
        now = datetime.now(timezone.utc).isoformat()
        system = (
            "You are a disciplined intraday equity analyst supporting an automated trading system. "
            "Score only on information in the provided headlines; if there is nothing material for a symbol, "
            "return score 0 with low confidence. Never speculate beyond the evidence."
        )
        user = f"As of {now}. Headlines by symbol:\n" + "\n".join(
            f"## {sym}\n" + ("\n".join(f"- {h}" for h in headlines.get(sym, [])) or "- (no recent headlines)") for sym in symbols
        )
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=SentimentReport,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"analyst returned no output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def sentiment_scores(self, symbols: list[str]) -> dict[str, float]:
        rep = self.analyze(symbols)
        return {i.symbol.upper(): i.score * i.confidence for i in rep.items}


def default_headline_fetcher(symbols: list[str]) -> dict[str, list[str]]:
    """Best-effort free headlines via yfinance; swap for a paid news feed in production."""
    out: dict[str, list[str]] = {}
    try:
        import yfinance as yf

        for s in symbols:
            try:
                news = yf.Ticker(s).news or []
                out[s] = [n.get("content", {}).get("title") or n.get("title", "") for n in news[:8]]
                out[s] = [h for h in out[s] if h]
            except Exception:
                out[s] = []
    except Exception as e:  # pragma: no cover
        log.warning("headline fetch failed: %s", e)
    return out
