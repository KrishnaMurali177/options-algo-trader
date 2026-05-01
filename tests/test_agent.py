"""Integration-style tests for TradingAgent (all external dependencies mocked)."""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import settings
from src.agent import TradingAgent
from src.models.market_data import MarketIndicators, MarketRegime
from src.models.options import Greeks, OptionContract


def _mock_chain(symbol: str = "AAPL") -> list[dict]:
    """Return a list of raw option dicts as the MCP server would return."""
    return [
        {
            "symbol": f"{symbol}260515C00195000",
            "strike_price": 195.0,
            "expiration_date": "2026-05-15",
            "bid_price": 3.20,
            "ask_price": 3.50,
            "last_trade_price": 3.35,
            "volume": 1200,
            "open_interest": 5400,
            "delta": 0.30,
            "gamma": 0.04,
            "theta": -0.05,
            "vega": 0.12,
            "implied_volatility": 0.25,
        },
    ]


class TestTradingAgent:

    @pytest.mark.asyncio
    async def test_full_cycle_dry_run(self, low_vol_bullish_indicators, sample_portfolio):
        """Full agent cycle in dry-run: analyse → select strategy → construct → risk → log."""

        # Mock MCP client
        mcp = AsyncMock()
        mcp.connect = AsyncMock()
        mcp.disconnect = AsyncMock()
        mcp.get_account_info = AsyncMock(return_value={
            "account_id": "TEST123",
            "buying_power": 50000,
            "cash": 50000,
            "portfolio_value": 100000,
            "options_buying_power": 25000,
        })
        mcp.get_positions = AsyncMock(side_effect=lambda t: [
            {"symbol": "AAPL", "quantity": 200, "average_cost": 150, "current_price": 190,
             "market_value": 38000, "unrealized_pnl": 8000}
        ] if t == "stock" else [])
        mcp.get_options_chain = AsyncMock(return_value=_mock_chain())

        # Mock market analyzer
        analyzer = MagicMock()
        analyzer.analyze = MagicMock(return_value=low_vol_bullish_indicators)
        analyzer.classify_regime = MagicMock(return_value=MarketRegime.LOW_VOL_BULLISH)

        agent = TradingAgent(mcp_client=mcp, analyzer=analyzer)
        summary = await agent.run("AAPL", dry_run=True)

        assert summary["status"] == "dry_run"
        assert summary["algo_regime"] == "low_vol_bullish"
        assert summary["strategy_decision"]["selected_strategy"] == "covered_call"
        assert summary["strategy_decision"]["eligible"] is True
        assert "order" in summary
        assert summary["order"]["strategy_name"] == "covered_call"

    @pytest.mark.asyncio
    async def test_ineligible_all_strategies(self, high_vol_bearish_indicators):
        """If no shares are held, protective_put and covered_call are both ineligible."""

        mcp = AsyncMock()
        mcp.connect = AsyncMock()
        mcp.disconnect = AsyncMock()
        mcp.get_account_info = AsyncMock(return_value={
            "account_id": "X", "buying_power": 10000, "cash": 10000,
            "portfolio_value": 50000, "options_buying_power": 5000,
        })
        mcp.get_positions = AsyncMock(return_value=[])  # no positions

        analyzer = MagicMock()
        analyzer.analyze = MagicMock(return_value=high_vol_bearish_indicators)
        analyzer.classify_regime = MagicMock(return_value=MarketRegime.HIGH_VOL_BEARISH)

        agent = TradingAgent(mcp_client=mcp, analyzer=analyzer)
        summary = await agent.run("AAPL", dry_run=True)

        # protective_put needs shares, covered_call needs shares — only iron_condor may work
        # but iron_condor requires RANGE_BOUND_HV regime to be eligible
        # The selector tries fallbacks; if all fail, status is ineligible
        assert summary["status"] in ("ineligible", "dry_run")

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Agent should catch exceptions and return status=error."""

        mcp = AsyncMock()
        mcp.connect = AsyncMock()
        mcp.disconnect = AsyncMock()

        analyzer = MagicMock()
        analyzer.analyze = MagicMock(side_effect=ValueError("No data for XYZ"))

        agent = TradingAgent(mcp_client=mcp, analyzer=analyzer)
        summary = await agent.run("XYZ", dry_run=True)

        assert summary["status"] == "error"
        assert "No data for XYZ" in summary["message"]

    @pytest.mark.asyncio
    async def test_strategy_decision_in_summary(self, low_vol_bullish_indicators):
        """Summary should contain strategy_decision with rationale (no llm_decision)."""

        mcp = AsyncMock()
        mcp.connect = AsyncMock()
        mcp.disconnect = AsyncMock()
        mcp.get_account_info = AsyncMock(return_value={
            "account_id": "T", "buying_power": 50000, "cash": 50000,
            "portfolio_value": 100000, "options_buying_power": 25000,
        })
        mcp.get_positions = AsyncMock(side_effect=lambda t: [
            {"symbol": "AAPL", "quantity": 200, "average_cost": 150, "current_price": 190,
             "market_value": 38000, "unrealized_pnl": 8000}
        ] if t == "stock" else [])
        mcp.get_options_chain = AsyncMock(return_value=_mock_chain())

        analyzer = MagicMock()
        analyzer.analyze = MagicMock(return_value=low_vol_bullish_indicators)
        analyzer.classify_regime = MagicMock(return_value=MarketRegime.LOW_VOL_BULLISH)

        agent = TradingAgent(mcp_client=mcp, analyzer=analyzer)
        summary = await agent.run("AAPL", dry_run=True)

        # No LLM decision — we have strategy_decision instead
        assert "llm_decision" not in summary
        assert "strategy_decision" in summary
        decision = summary["strategy_decision"]
        assert "rationale" in decision
        assert "confidence" in decision
        assert decision["confidence"] > 0

    @pytest.mark.asyncio
    async def test_live_with_confirmation_holds_order(self, low_vol_bullish_indicators):
        """Live mode with require_confirmation=True should hold the order, not execute."""

        mcp = AsyncMock()
        mcp.connect = AsyncMock()
        mcp.disconnect = AsyncMock()
        mcp.get_account_info = AsyncMock(return_value={
            "account_id": "T", "buying_power": 50000, "cash": 50000,
            "portfolio_value": 100000, "options_buying_power": 25000,
        })
        mcp.get_positions = AsyncMock(side_effect=lambda t: [
            {"symbol": "AAPL", "quantity": 200, "average_cost": 150, "current_price": 190,
             "market_value": 38000, "unrealized_pnl": 8000}
        ] if t == "stock" else [])
        mcp.get_options_chain = AsyncMock(return_value=_mock_chain())
        mcp.place_option_order = AsyncMock(return_value={"order_id": "12345"})

        analyzer = MagicMock()
        analyzer.analyze = MagicMock(return_value=low_vol_bullish_indicators)
        analyzer.classify_regime = MagicMock(return_value=MarketRegime.LOW_VOL_BULLISH)

        agent = TradingAgent(mcp_client=mcp, analyzer=analyzer)

        with patch.object(settings, "require_confirmation", True):
            summary = await agent.run("AAPL", dry_run=False)

        assert summary["status"] == "awaiting_confirmation"
        mcp.place_option_order.assert_not_called()
        assert agent._pending_order is not None

    @pytest.mark.asyncio
    async def test_confirm_and_execute_places_order(self, low_vol_bullish_indicators):
        """confirm_and_execute() should place the held order via MCP."""

        mcp = AsyncMock()
        mcp.connect = AsyncMock()
        mcp.disconnect = AsyncMock()
        mcp.get_account_info = AsyncMock(return_value={
            "account_id": "T", "buying_power": 50000, "cash": 50000,
            "portfolio_value": 100000, "options_buying_power": 25000,
        })
        mcp.get_positions = AsyncMock(side_effect=lambda t: [
            {"symbol": "AAPL", "quantity": 200, "average_cost": 150, "current_price": 190,
             "market_value": 38000, "unrealized_pnl": 8000}
        ] if t == "stock" else [])
        mcp.get_options_chain = AsyncMock(return_value=_mock_chain())
        mcp.place_option_order = AsyncMock(return_value={"order_id": "12345"})

        analyzer = MagicMock()
        analyzer.analyze = MagicMock(return_value=low_vol_bullish_indicators)
        analyzer.classify_regime = MagicMock(return_value=MarketRegime.LOW_VOL_BULLISH)

        agent = TradingAgent(mcp_client=mcp, analyzer=analyzer)

        with patch.object(settings, "require_confirmation", True):
            summary = await agent.run("AAPL", dry_run=False)
        assert summary["status"] == "awaiting_confirmation"

        result = await agent.confirm_and_execute()
        assert result["status"] == "executed"
        mcp.place_option_order.assert_called_once()
        assert agent._pending_order is None

    @pytest.mark.asyncio
    async def test_confirm_without_pending_returns_error(self):
        """confirm_and_execute() with no pending order should return error."""
        agent = TradingAgent(mcp_client=AsyncMock())
        result = await agent.confirm_and_execute()
        assert result["status"] == "error"
        assert "No pending order" in result["message"]

