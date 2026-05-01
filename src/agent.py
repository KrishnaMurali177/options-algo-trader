"""Agent Orchestrator — a self-contained trading agent with pure algorithmic strategy selection."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from config.settings import settings
from src.market_analyzer import MarketAnalyzer
from src.mcp_client import MCPClient
from src.models.market_data import MarketRegime
from src.models.options import OptionContract, OptionOrder
from src.models.portfolio import (
    AccountInfo,
    OptionPosition,
    PortfolioSummary,
    StockPosition,
)
from src.risk_manager import RiskManager
from src.strategies import STRATEGIES
from src.strategies.base_strategy import BaseStrategy
from src.strategy_selector import StrategySelector

logger = logging.getLogger(__name__)


class TradingAgent:
    """
    Self-contained options trading agent — no external LLM required.

    Orchestrates the full cycle:
      1. Analyse market → classify regime
      2. Fetch portfolio from MCP
      3. Algorithmically select the best strategy for the regime + portfolio
      4. Construct order via the selected strategy
      5. Validate with risk manager
      6. Execute (or dry-run log) via MCP
    """

    def __init__(
        self,
        mcp_client: MCPClient | None = None,
        analyzer: MarketAnalyzer | None = None,
        risk_mgr: RiskManager | None = None,
        selector: StrategySelector | None = None,
    ):
        self.mcp = mcp_client or MCPClient()
        self.analyzer = analyzer or MarketAnalyzer()
        self.risk_mgr = risk_mgr or RiskManager()
        self.selector = selector or StrategySelector()
        self._pending_order: OptionOrder | None = None
        self._pending_summary: dict | None = None

    # ── Main entry point ─────────────────────────────────────────

    async def run(self, symbol: str, dry_run: bool | None = None) -> dict:
        """Execute one full trading cycle for *symbol*. Returns a summary dict."""
        dry_run = dry_run if dry_run is not None else settings.dry_run
        summary: dict = {"symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat(), "dry_run": dry_run}

        try:
            # Step 1 — Market analysis
            logger.info("═══ Step 1: Analysing market for %s ═══", symbol)
            indicators = self.analyzer.analyze(symbol)
            algo_regime = self.analyzer.classify_regime(indicators)
            summary["algo_regime"] = algo_regime.value
            logger.info("Algorithmic regime: %s", algo_regime.value)

            # Step 2 — Portfolio from MCP
            logger.info("═══ Step 2: Fetching portfolio via MCP ═══")
            await self.mcp.connect()
            portfolio = await self._build_portfolio()
            summary["portfolio_value"] = portfolio.account.portfolio_value

            # Step 3 — Strategy selection (algorithmic + optional LLM confirmation)
            logger.info("═══ Step 3: Strategy selection ═══")
            decision = await self.selector.select_async(algo_regime, portfolio, indicators)
            summary["strategy_decision"] = {
                "regime": decision.regime.value,
                "selected_strategy": decision.selected_strategy,
                "rationale": decision.rationale,
                "eligible": decision.eligible,
                "fallback_used": decision.fallback_used,
                "confidence": decision.confidence,
                "llm_used": decision.llm_used,
                "llm_override": decision.llm_override,
            }
            logger.info(
                "Selected: %s (confidence=%.0f%%, fallback=%s)",
                decision.selected_strategy,
                decision.confidence * 100,
                decision.fallback_used,
            )

            if not decision.eligible:
                summary["status"] = "ineligible"
                summary["message"] = decision.rationale
                logger.warning(summary["message"])
                return summary

            # Step 4 — Construct order
            logger.info("═══ Step 4: Constructing order ═══")
            strategy = self._resolve_strategy(decision.selected_strategy)
            chain = await self._fetch_options_chain(symbol, indicators, strategy_name=decision.selected_strategy)
            order = strategy.construct_order(symbol, chain, portfolio, indicators)
            summary["order"] = order.model_dump()
            logger.info("Order: %s", json.dumps(order.model_dump(), indent=2, default=str))

            # Step 5 — Risk validation
            logger.info("═══ Step 5: Risk validation ═══")
            risk = self.risk_mgr.validate(order, portfolio, indicators)
            summary["risk"] = risk.model_dump()

            if not risk.approved:
                summary["status"] = "rejected"
                summary["message"] = f"Risk check failed: {risk.rejection_reasons}"
                logger.warning(summary["message"])
                return summary

            # Step 6 — Execute or dry-run
            logger.info("═══ Step 6: Execution ═══")
            if dry_run:
                summary["status"] = "dry_run"
                summary["message"] = "Order validated but NOT executed (dry-run mode)."
                logger.info("🟡 DRY RUN — order not placed.")
            elif settings.require_confirmation:
                self._pending_order = order
                self._pending_summary = summary
                summary["status"] = "awaiting_confirmation"
                summary["message"] = "Order passed risk checks. Call confirm_and_execute() to place it."
                logger.info("🟠 AWAITING CONFIRMATION — order held pending human approval.")
            else:
                exec_result = await self.mcp.place_option_order(
                    self._order_to_mcp_payload(order)
                )
                summary["status"] = "executed"
                summary["execution_result"] = exec_result
                logger.info("🟢 Order EXECUTED: %s", exec_result)

        except Exception as exc:
            summary["status"] = "error"
            summary["message"] = str(exc)
            logger.exception("Agent error: %s", exc)
        finally:
            try:
                await self.mcp.disconnect()
            except Exception:
                pass

        return summary

    async def confirm_and_execute(self) -> dict:
        """Execute a previously held order after human confirmation."""
        if self._pending_order is None:
            return {"status": "error", "message": "No pending order to execute."}

        order = self._pending_order
        summary = self._pending_summary or {}
        self._pending_order = None
        self._pending_summary = None

        try:
            await self.mcp.connect()
            exec_result = await self.mcp.place_option_order(
                self._order_to_mcp_payload(order)
            )
            summary["status"] = "executed"
            summary["execution_result"] = exec_result
            logger.info("🟢 Order EXECUTED after confirmation: %s", exec_result)
        except Exception as exc:
            summary["status"] = "error"
            summary["message"] = f"Execution failed: {exc}"
            logger.exception("Execution error: %s", exc)
        finally:
            try:
                await self.mcp.disconnect()
            except Exception:
                pass

        return summary

    # ── Helpers ───────────────────────────────────────────────────

    async def _build_portfolio(self) -> PortfolioSummary:
        """Fetch account + positions from MCP and assemble PortfolioSummary."""
        acct_raw = await self.mcp.get_account_info()
        account = AccountInfo(
            account_id=str(acct_raw.get("account_id", "")),
            buying_power=float(acct_raw.get("buying_power", 0)),
            cash=float(acct_raw.get("cash", 0)),
            portfolio_value=float(acct_raw.get("portfolio_value", 0)),
            options_buying_power=float(acct_raw.get("options_buying_power", 0)),
        )

        stock_raw = await self.mcp.get_positions("stock") or []
        stock_positions = [
            StockPosition(
                symbol=p["symbol"],
                quantity=float(p.get("quantity", 0)),
                average_cost=float(p.get("average_cost", 0)),
                current_price=float(p.get("current_price", 0)),
                market_value=float(p.get("market_value", 0)),
                unrealized_pnl=float(p.get("unrealized_pnl", 0)),
            )
            for p in stock_raw
        ]

        opt_raw = await self.mcp.get_positions("options") or []
        option_positions = [
            OptionPosition(
                symbol=p["symbol"],
                underlying=p.get("underlying", ""),
                strike=float(p.get("strike", 0)),
                expiration=date.fromisoformat(p["expiration"]),
                option_type=p.get("option_type", "call"),
                quantity=int(p.get("quantity", 0)),
                average_cost=float(p.get("average_cost", 0)),
                current_price=float(p.get("current_price", 0)),
                market_value=float(p.get("market_value", 0)),
                unrealized_pnl=float(p.get("unrealized_pnl", 0)),
                delta=float(p.get("delta", 0)),
            )
            for p in opt_raw
        ]

        total_opt_alloc = sum(abs(op.market_value) for op in option_positions)

        return PortfolioSummary(
            account=account,
            stock_positions=stock_positions,
            option_positions=option_positions,
            total_options_allocation=total_opt_alloc,
        )

    async def _fetch_options_chain(self, symbol: str, indicators, strategy_name: str = "") -> list[OptionContract]:
        """Fetch call + put chains. For intraday scalp strategies (buy_call, buy_put),
        fetch 0–3 DTE expirations; for all others fetch ~30-45 DTE."""
        from src.models.options import Greeks

        is_scalp = strategy_name in ("buy_call", "buy_put")

        if is_scalp:
            # Fetch multiple near-term expirations: today (0DTE), +1, +2, +3
            target_dates = [date.today() + timedelta(days=d) for d in range(4)]
        else:
            target_dte = 37  # ~midpoint of 30-45
            target_dates = [date.today() + timedelta(days=target_dte)]

        contracts: list[OptionContract] = []
        for target_exp in target_dates:
            exp_str = target_exp.isoformat()
            for opt_type in ("call", "put"):
                raw = await self.mcp.get_options_chain(symbol, exp_str, opt_type)
                if not raw:
                    continue
                for c in raw:
                    contracts.append(
                        OptionContract(
                            symbol=c.get("symbol", ""),
                            underlying=symbol,
                            strike=float(c.get("strike_price", c.get("strike", 0))),
                            expiration=date.fromisoformat(c.get("expiration_date", exp_str)),
                            option_type=opt_type,
                            bid=float(c.get("bid_price", c.get("bid", 0))),
                            ask=float(c.get("ask_price", c.get("ask", 0))),
                            last=float(c.get("last_trade_price", c.get("last", 0))),
                            volume=int(c.get("volume", 0)),
                            open_interest=int(c.get("open_interest", 0)),
                            greeks=Greeks(
                                delta=float(c.get("delta", 0)),
                                gamma=float(c.get("gamma", 0)),
                                theta=float(c.get("theta", 0)),
                                vega=float(c.get("vega", 0)),
                                implied_volatility=float(c.get("implied_volatility", 0)),
                            ),
                        )
                    )
        logger.info("Fetched %d option contracts for %s", len(contracts), symbol)
        return contracts

    @staticmethod
    def _resolve_strategy(name: str) -> BaseStrategy:
        for s in STRATEGIES:
            if s.name == name:
                return s
        raise ValueError(f"Unknown strategy: {name}. Available: {[s.name for s in STRATEGIES]}")

    @staticmethod
    def _order_to_mcp_payload(order: OptionOrder) -> dict:
        """Convert an OptionOrder to the dict expected by the MCP place_option_order tool."""
        return {
            "legs": [
                {
                    "symbol": leg.symbol,
                    "strike": leg.strike,
                    "expiration": leg.expiration.isoformat(),
                    "option_type": leg.option_type,
                    "action": leg.action,
                    "quantity": leg.quantity,
                }
                for leg in order.legs
            ],
            "order_type": order.order_type,
            "limit_price": order.limit_price,
            "duration": order.duration,
        }





