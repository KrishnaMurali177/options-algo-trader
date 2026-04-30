"""Strategy Selector — self-contained strategy selection with optional LLM confirmation.

How it works:
  1. Algorithmic: regime → strategy mapping + eligibility check + fallback chain.
  2. LLM (optional): if a Gemini API key is configured, the selector asks the LLM
     to confirm or override the algorithmic pick. If no key, pure algorithmic mode.

All logic lives here — no external adapter module required.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from config.settings import settings
from src.models.market_data import MarketIndicators, MarketRegime
from src.models.portfolio import PortfolioSummary
from src.strategies import STRATEGIES
from src.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# ── Regime → Strategy mapping ─────────────────────────────────────
# Bullish/neutral regimes → buy_call; Bearish/volatile → buy_put

REGIME_STRATEGY_MAP: dict[MarketRegime, str] = {
    MarketRegime.LOW_VOL_BULLISH: "buy_call",
    MarketRegime.LOW_VOL_NEUTRAL: "buy_call",
    MarketRegime.RANGE_BOUND_HV: "buy_put",
    MarketRegime.HIGH_VOL_BEARISH: "buy_put",
    MarketRegime.TRENDING_BEARISH: "buy_put",
}

# Fallback: if primary is ineligible, try the other
FALLBACK_ORDER: dict[str, list[str]] = {
    "buy_call": ["buy_put"],
    "buy_put": ["buy_call"],
}

# ── LLM system prompt (used only when Gemini key is configured) ───

_LLM_SYSTEM_PROMPT = """\
You are an expert options trading analyst. You will receive:
1. Technical market indicators for a stock symbol.
2. The algorithmically classified market regime.
3. The algorithmic strategy recommendation.
4. The current portfolio state.

Your job:
- Confirm or override the regime classification and strategy selection.
- You may ONLY select from: buy_call, buy_put.
- Provide a 2-3 sentence rationale.

Respond ONLY with valid JSON (no markdown fences):
{
  "confirmed_regime": "<regime_enum_value>",
  "regime_override": true|false,
  "selected_strategy": "buy_call|buy_put",
  "rationale": "...",
  "confidence": 0.0-1.0
}
"""


@dataclass
class StrategyDecision:
    """Result of strategy selection."""
    regime: MarketRegime
    selected_strategy: str
    rationale: str
    eligible: bool
    fallback_used: bool = False
    confidence: float = 1.0
    llm_used: bool = False
    llm_override: bool = False


class StrategySelector:
    """
    Self-contained strategy selector.

    - **Algorithmic mode** (default): regime→strategy map + eligibility + fallback.
    - **LLM-enhanced mode** (when ``gemini_api_key`` is set in config):
      after the algorithmic pick, asks Gemini to confirm or override.

    No external adapter module needed — all LLM logic lives here.
    """

    def __init__(self, use_llm: bool | None = None):
        # Auto-detect: use LLM only if an API key is actually configured
        if use_llm is None:
            self._use_llm = bool(settings.gemini_api_key)
        else:
            self._use_llm = use_llm

    # ── Public API ────────────────────────────────────────────────

    def select(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> StrategyDecision:
        """Select the best eligible strategy (synchronous, algorithmic only)."""
        return self._algorithmic_select(regime, portfolio, indicators)

    async def select_async(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> StrategyDecision:
        """Select strategy — with optional LLM confirmation if API key is set."""
        decision = self._algorithmic_select(regime, portfolio, indicators)

        if not decision.eligible:
            return decision

        # If LLM enhancement is enabled, ask Gemini to confirm/override
        if self._use_llm:
            decision = await self._llm_confirm(decision, regime, portfolio, indicators)

        return decision

    # ── Algorithmic Selection ─────────────────────────────────────

    def _algorithmic_select(
        self,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> StrategyDecision:
        primary_name = REGIME_STRATEGY_MAP[regime]
        candidates = [primary_name] + FALLBACK_ORDER.get(primary_name, [])

        for i, name in enumerate(candidates):
            strategy = self._resolve(name)
            eval_regime = self._matching_regime(name)
            if strategy.evaluate_eligibility(eval_regime, portfolio, indicators):
                rationale = self._build_rationale(regime, indicators, name, fallback=(i > 0))
                logger.info("Algorithmic pick: %s (regime=%s, fallback=%s)", name, regime.value, i > 0)
                return StrategyDecision(
                    regime=regime,
                    selected_strategy=name,
                    rationale=rationale,
                    eligible=True,
                    fallback_used=(i > 0),
                    confidence=round(1.0 - i * 0.2, 2),
                )

        logger.warning("No eligible strategy for regime=%s", regime.value)
        return StrategyDecision(
            regime=regime,
            selected_strategy=primary_name,
            rationale=f"Primary strategy '{primary_name}' and all fallbacks are ineligible.",
            eligible=False,
            confidence=0.0,
        )

    # ── LLM Confirmation (Gemini) ─────────────────────────────────

    async def _llm_confirm(
        self,
        algo_decision: StrategyDecision,
        regime: MarketRegime,
        portfolio: PortfolioSummary,
        indicators: MarketIndicators,
    ) -> StrategyDecision:
        """Ask Gemini to confirm or override the algorithmic decision."""
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=settings.gemini_api_key)

            user_message = (
                f"## Market Indicators\n```json\n"
                f"{json.dumps(indicators.model_dump(), indent=2, default=str)}\n```\n\n"
                f"## Algorithmic Regime: `{regime.value}`\n"
                f"## Algorithmic Strategy Pick: `{algo_decision.selected_strategy}`\n"
                f"## Algorithmic Rationale: {algo_decision.rationale}\n\n"
                f"## Portfolio\n```json\n"
                f"{json.dumps(portfolio.model_dump(), indent=2, default=str)}\n```\n\n"
                "Please confirm or override the strategy selection."
            )

            response = await client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=_LLM_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_output_tokens=1024,
                    response_mime_type="application/json",
                ),
            )

            llm_result = json.loads(response.text)
            llm_strategy = llm_result.get("selected_strategy", algo_decision.selected_strategy)
            llm_rationale = llm_result.get("rationale", "")
            llm_confidence = float(llm_result.get("confidence", algo_decision.confidence))
            is_override = llm_strategy != algo_decision.selected_strategy

            logger.info(
                "Gemini response: strategy=%s, override=%s, confidence=%.0f%%",
                llm_strategy, is_override, llm_confidence * 100,
            )

            # Validate the LLM's pick is a known strategy and eligible
            if llm_strategy in ("buy_call", "buy_put"):
                if is_override:
                    # Check eligibility of the LLM's override pick
                    override_strat = self._resolve(llm_strategy)
                    override_regime = self._matching_regime(llm_strategy)
                    if not override_strat.evaluate_eligibility(override_regime, portfolio, indicators):
                        logger.warning(
                            "LLM override to '%s' is ineligible — keeping algorithmic pick '%s'",
                            llm_strategy, algo_decision.selected_strategy,
                        )
                        algo_decision.llm_used = True
                        algo_decision.rationale += f" [Gemini confirmed: {llm_rationale}]"
                        return algo_decision

                return StrategyDecision(
                    regime=regime,
                    selected_strategy=llm_strategy,
                    rationale=llm_rationale,
                    eligible=True,
                    fallback_used=algo_decision.fallback_used,
                    confidence=llm_confidence,
                    llm_used=True,
                    llm_override=is_override,
                )
            else:
                logger.warning("LLM returned unknown strategy '%s' — keeping algorithmic pick", llm_strategy)

        except Exception as exc:
            logger.warning("LLM confirmation failed (%s) — falling back to algorithmic decision", exc)

        # On any failure, return the algorithmic decision with a note
        algo_decision.llm_used = False
        return algo_decision

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _resolve(name: str) -> BaseStrategy:
        for s in STRATEGIES:
            if s.name == name:
                return s
        raise ValueError(f"Unknown strategy: {name}")

    @staticmethod
    def _matching_regime(strategy_name: str) -> MarketRegime:
        """Return a regime under which this strategy is eligible."""
        return {
            "buy_call": MarketRegime.LOW_VOL_BULLISH,
            "buy_put": MarketRegime.HIGH_VOL_BEARISH,
        }[strategy_name]

    @staticmethod
    def _build_rationale(
        regime: MarketRegime,
        ind: MarketIndicators,
        strategy: str,
        fallback: bool,
    ) -> str:
        parts: list[str] = []

        if regime in (MarketRegime.LOW_VOL_BULLISH, MarketRegime.LOW_VOL_NEUTRAL):
            parts.append(f"VIX is low ({ind.vix:.1f}), RSI neutral ({ind.rsi_14:.1f}), price above SMA50.")
        elif regime == MarketRegime.RANGE_BOUND_HV:
            parts.append(f"VIX elevated ({ind.vix:.1f}), price within Bollinger bands, RSI neutral ({ind.rsi_14:.1f}).")
        elif regime in (MarketRegime.HIGH_VOL_BEARISH, MarketRegime.TRENDING_BEARISH):
            parts.append(f"VIX high ({ind.vix:.1f}), bearish signals detected (RSI={ind.rsi_14:.1f}).")

        descriptions = {
            "buy_call": "Buying a call to scalp a bullish intraday breakout.",
            "buy_put": "Buying a put to scalp a bearish intraday breakout.",
        }
        parts.append(descriptions.get(strategy, ""))

        if fallback:
            parts.append("(Primary strategy was ineligible; this is a fallback.)")

        return " ".join(parts)


