"""
DEPRECATED — This module is no longer used.

All LLM logic is now integrated directly into:
    src/strategy_selector.py → StrategySelector._llm_confirm()

The agent is self-contained: algorithmic selection by default,
with optional Gemini LLM confirmation when GEMINI_API_KEY is set.
"""
