"""Execution Guide — generates exact trade instructions, entry conditions, and exit plans.

Given an OptionOrder and MarketIndicators, produces a strategy-specific guide with:
  1. Exact contract details (what to trade)
  2. Step-by-step brokerage instructions (how to trade)
  3. Optimal entry conditions with live pass/fail (when to trade)
  4. Exit plan with take-profit, stop-loss, and roll triggers (how to manage)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.models.market_data import MarketIndicators
from src.models.options import OptionOrder


# ── Data classes ──────────────────────────────────────────────────

@dataclass
class EntryCondition:
    name: str
    current_value: str
    ideal_range: str
    met: bool


@dataclass
class ExitPlan:
    take_profit_pct: float          # e.g. 0.50 = close at 50% of max profit
    take_profit_price: float        # dollar amount trigger
    stop_loss_pct: float            # e.g. 2.0 = close if loss hits 200% of premium
    stop_loss_price: float          # dollar amount trigger
    roll_trigger_dte: int           # DTE at which to consider rolling
    roll_conditions: str            # text description
    adjustment_notes: str           # additional management tips


@dataclass
class ExecutionGuide:
    strategy_name: str
    contract_legs: list[dict]
    brokerage_steps: list[str]
    entry_conditions: list[EntryCondition]
    entry_conditions_met: int       # count of conditions met
    entry_conditions_total: int     # total conditions
    exit_plan: ExitPlan
    optimal_entry_price: float | None = None
    notes: list[str] = field(default_factory=list)


# ── Strategy-specific entry condition configs ─────────────────────

def _entry_conditions_covered_call(ind: MarketIndicators) -> list[EntryCondition]:
    return [
        EntryCondition("RSI (40–65)", f"{ind.rsi_14:.1f}", "40 – 65",
                        40 <= ind.rsi_14 <= 65),
        EntryCondition("VIX (< 25)", f"{ind.vix:.1f}", "< 25",
                        ind.vix < 25),
        EntryCondition("Price > SMA 50", f"${ind.current_price:.2f} vs ${ind.sma_50:.2f}",
                        f"> ${ind.sma_50:.2f}", ind.current_price > ind.sma_50),
        EntryCondition("MACD Positive", f"{ind.macd_histogram:.3f}", "> 0",
                        ind.macd_histogram > 0),
        EntryCondition("Price within Bollinger Bands",
                        f"${ind.current_price:.2f}", f"${ind.bb_lower:.0f} – ${ind.bb_upper:.0f}",
                        ind.bb_lower <= ind.current_price <= ind.bb_upper),
    ]


def _entry_conditions_naked_put(ind: MarketIndicators) -> list[EntryCondition]:
    return [
        EntryCondition("RSI (35–60)", f"{ind.rsi_14:.1f}", "35 – 60",
                        35 <= ind.rsi_14 <= 60),
        EntryCondition("VIX (< 25)", f"{ind.vix:.1f}", "< 25",
                        ind.vix < 25),
        EntryCondition("Price > SMA 50", f"${ind.current_price:.2f} vs ${ind.sma_50:.2f}",
                        f"> ${ind.sma_50:.2f}", ind.current_price > ind.sma_50),
        EntryCondition("MACD Non-Negative", f"{ind.macd_histogram:.3f}", "≥ 0",
                        ind.macd_histogram >= 0),
        EntryCondition("Price above lower Bollinger",
                        f"${ind.current_price:.2f}", f"> ${ind.bb_lower:.0f}",
                        ind.current_price > ind.bb_lower),
    ]


def _entry_conditions_naked_call(ind: MarketIndicators) -> list[EntryCondition]:
    return [
        EntryCondition("RSI (> 55, ideally > 65)", f"{ind.rsi_14:.1f}", "> 55",
                        ind.rsi_14 > 55),
        EntryCondition("VIX Elevated (> 20)", f"{ind.vix:.1f}", "> 20",
                        ind.vix > 20),
        EntryCondition("Price near upper Bollinger",
                        f"${ind.current_price:.2f}", f"≥ ${ind.bb_middle:.0f}",
                        ind.current_price >= ind.bb_middle),
        EntryCondition("MACD Fading / Negative", f"{ind.macd_histogram:.3f}", "< 0.5",
                        ind.macd_histogram < 0.5),
        EntryCondition("Price near resistance (SMA50 + ATR)",
                        f"${ind.current_price:.2f}",
                        f"≥ ${ind.sma_50 + ind.atr_14 * 0.5:.2f}",
                        ind.current_price >= ind.sma_50 + ind.atr_14 * 0.5),
    ]


def _entry_conditions_iron_condor(ind: MarketIndicators) -> list[EntryCondition]:
    return [
        EntryCondition("RSI (40–60)", f"{ind.rsi_14:.1f}", "40 – 60",
                        40 <= ind.rsi_14 <= 60),
        EntryCondition("VIX (20–35)", f"{ind.vix:.1f}", "20 – 35",
                        20 <= ind.vix <= 35),
        EntryCondition("Price within Bollinger Bands",
                        f"${ind.current_price:.2f}", f"${ind.bb_lower:.0f} – ${ind.bb_upper:.0f}",
                        ind.bb_lower <= ind.current_price <= ind.bb_upper),
        EntryCondition("MACD near zero", f"{ind.macd_histogram:.3f}", "-0.5 – 0.5",
                        -0.5 <= ind.macd_histogram <= 0.5),
        EntryCondition("No imminent earnings",
                        f"{ind.days_to_earnings or 'N/A'} days",
                        "> 14 days",
                        (ind.days_to_earnings or 999) > 14),
    ]


def _entry_conditions_protective_put(ind: MarketIndicators) -> list[EntryCondition]:
    return [
        EntryCondition("RSI (< 40 or death cross)", f"{ind.rsi_14:.1f}", "< 40",
                        ind.rsi_14 < 40 or ind.sma_50 < ind.sma_200),
        EntryCondition("VIX Elevated (> 25)", f"{ind.vix:.1f}", "> 25",
                        ind.vix > 25),
        EntryCondition("Price below SMA 50", f"${ind.current_price:.2f} vs ${ind.sma_50:.2f}",
                        f"< ${ind.sma_50:.2f}", ind.current_price < ind.sma_50),
        EntryCondition("MACD Bearish", f"{ind.macd_histogram:.3f}", "< 0",
                        ind.macd_histogram < 0),
        EntryCondition("Death Cross (SMA50 < SMA200)",
                        f"SMA50=${ind.sma_50:.0f} vs SMA200=${ind.sma_200:.0f}",
                        "SMA50 < SMA200",
                        ind.sma_50 < ind.sma_200),
    ]


_ENTRY_CONDITION_MAP = {
    "covered_call": _entry_conditions_covered_call,
    "naked_put": _entry_conditions_naked_put,
    "naked_call": _entry_conditions_naked_call,
    "iron_condor": _entry_conditions_iron_condor,
    "protective_put": _entry_conditions_protective_put,
}


def _entry_conditions_buy_call(ind: MarketIndicators) -> list[EntryCondition]:
    """Intraday scalping: bullish breakout conditions for buying calls."""
    atr = ind.atr_14
    range_high = round(ind.current_price + atr * 0.18, 2)
    return [
        EntryCondition("RSI bullish (> 50)", f"{ind.rsi_14:.1f}", "> 50",
                        ind.rsi_14 > 50),
        EntryCondition("MACD positive momentum", f"{ind.macd_histogram:.3f}", "> 0",
                        ind.macd_histogram > 0),
        EntryCondition("Price above VWAP (SMA20)", f"${ind.current_price:.2f} vs ${ind.sma_20:.2f}",
                        f"> ${ind.sma_20:.2f}", ind.current_price > ind.sma_20),
        EntryCondition("Price near/above opening range high",
                        f"${ind.current_price:.2f}", f"≥ ${range_high:.2f}",
                        ind.current_price >= range_high * 0.998),
        EntryCondition("VIX not spiking (< 35)", f"{ind.vix:.1f}", "< 35",
                        ind.vix < 35),
        EntryCondition("RSI not overbought (< 75)", f"{ind.rsi_14:.1f}", "< 75",
                        ind.rsi_14 < 75),
    ]


def _entry_conditions_buy_put(ind: MarketIndicators) -> list[EntryCondition]:
    """Intraday scalping: bearish breakout conditions for buying puts."""
    atr = ind.atr_14
    range_low = round(ind.current_price - atr * 0.18, 2)
    return [
        EntryCondition("RSI bearish (< 50)", f"{ind.rsi_14:.1f}", "< 50",
                        ind.rsi_14 < 50),
        EntryCondition("MACD negative momentum", f"{ind.macd_histogram:.3f}", "< 0",
                        ind.macd_histogram < 0),
        EntryCondition("Price below VWAP (SMA20)", f"${ind.current_price:.2f} vs ${ind.sma_20:.2f}",
                        f"< ${ind.sma_20:.2f}", ind.current_price < ind.sma_20),
        EntryCondition("Price near/below opening range low",
                        f"${ind.current_price:.2f}", f"≤ ${range_low:.2f}",
                        ind.current_price <= range_low * 1.002),
        EntryCondition("VIX not spiking (< 35)", f"{ind.vix:.1f}", "< 35",
                        ind.vix < 35),
        EntryCondition("RSI not oversold (> 25)", f"{ind.rsi_14:.1f}", "> 25",
                        ind.rsi_14 > 25),
    ]


_ENTRY_CONDITION_MAP["buy_call"] = _entry_conditions_buy_call
_ENTRY_CONDITION_MAP["buy_put"] = _entry_conditions_buy_put


# ── Strategy-specific exit plans ──────────────────────────────────

def _exit_plan_covered_call(order: OptionOrder, ind: MarketIndicators) -> ExitPlan:
    premium = (order.limit_price or 0) * 100
    return ExitPlan(
        take_profit_pct=0.50,
        take_profit_price=round(premium * 0.50, 2),
        stop_loss_pct=1.0,
        stop_loss_price=round(ind.sma_50 - ind.atr_14, 2),
        roll_trigger_dte=14,
        roll_conditions="If profitable at 14 DTE, buy back the call and sell a new one 30–45 DTE out at a higher strike.",
        adjustment_notes=(
            "• Buy back the call when it reaches 50% of premium collected.\n"
            "• If stock rallies past strike, decide: let shares be called away or roll up and out.\n"
            f"• Stop-loss: close position if {ind.symbol} drops below ${ind.sma_50 - ind.atr_14:.2f} (SMA50 − 1 ATR)."
        ),
    )


def _exit_plan_naked_put(order: OptionOrder, ind: MarketIndicators) -> ExitPlan:
    premium = (order.limit_price or 0) * 100
    return ExitPlan(
        take_profit_pct=0.50,
        take_profit_price=round(premium * 0.50, 2),
        stop_loss_pct=2.0,
        stop_loss_price=round(premium * 2.0, 2),
        roll_trigger_dte=14,
        roll_conditions="If the put is challenged (stock near strike) at 14 DTE, roll down and out to a lower strike, 30+ DTE.",
        adjustment_notes=(
            "• Buy back the put when it reaches 50% of premium collected (${:.2f}).\n".format(premium * 0.50) +
            "• Stop-loss: close if loss reaches 2× premium collected (${:.2f}).\n".format(premium * 2.0) +
            "• If assigned, you now own 100 shares at strike — switch to covered call strategy."
        ),
    )


def _exit_plan_naked_call(order: OptionOrder, ind: MarketIndicators) -> ExitPlan:
    premium = (order.limit_price or 0) * 100
    return ExitPlan(
        take_profit_pct=0.75,
        take_profit_price=round(premium * 0.75, 2),
        stop_loss_pct=2.0,
        stop_loss_price=round(premium * 2.0, 2),
        roll_trigger_dte=14,
        roll_conditions="If stock is approaching strike, roll up and out to a higher strike, 30+ DTE. Close immediately if stock breaks above strike.",
        adjustment_notes=(
            "⚠️ **UNLIMITED RISK** — manage aggressively.\n"
            "• Buy back the call at 75% of premium collected (${:.2f}).\n".format(premium * 0.75) +
            "• **Hard stop-loss**: close if loss hits 2× premium (${:.2f}).\n".format(premium * 2.0) +
            "• Close immediately if RSI drops below 35 (bounce risk).\n"
            f"• Close if {ind.symbol} breaks above strike with momentum."
        ),
    )


def _exit_plan_iron_condor(order: OptionOrder, ind: MarketIndicators) -> ExitPlan:
    credit = (order.limit_price or 0) * 100
    return ExitPlan(
        take_profit_pct=0.50,
        take_profit_price=round(credit * 0.50, 2),
        stop_loss_pct=2.0,
        stop_loss_price=round(credit * 2.0, 2),
        roll_trigger_dte=21,
        roll_conditions="At 21 DTE, roll the untested side closer to collect more credit. Close the whole position if one side is breached.",
        adjustment_notes=(
            "• Close the entire condor at 50% of credit received (${:.2f}).\n".format(credit * 0.50) +
            "• Stop-loss: close if loss reaches 2× credit (${:.2f}).\n".format(credit * 2.0) +
            "• If only one side is challenged, close that spread and keep the profitable side running.\n"
            "• Roll the untested side closer at 21 DTE to improve the overall credit."
        ),
    )


def _exit_plan_protective_put(order: OptionOrder, ind: MarketIndicators) -> ExitPlan:
    cost = (order.limit_price or 0) * 100
    return ExitPlan(
        take_profit_pct=1.0,
        take_profit_price=round(cost * 1.0, 2),
        stop_loss_pct=1.0,
        stop_loss_price=round(cost, 2),
        roll_trigger_dte=14,
        roll_conditions="At 14 DTE, if the put still has value and you want continued protection, roll to a new put 30–60 DTE out.",
        adjustment_notes=(
            "• This is insurance — max loss is the premium paid (${:.2f}).\n".format(cost) +
            "• If stock drops significantly, the put gains value — sell the put or exercise to sell shares.\n"
            "• If stock recovers, the put expires worthless (that's the cost of insurance).\n"
            f"• Roll at 14 DTE if you still want protection on your {ind.symbol} shares."
        ),
    )


_EXIT_PLAN_MAP = {
    "covered_call": _exit_plan_covered_call,
    "naked_put": _exit_plan_naked_put,
    "naked_call": _exit_plan_naked_call,
    "iron_condor": _exit_plan_iron_condor,
    "protective_put": _exit_plan_protective_put,
}


def _exit_plan_buy_call(order: OptionOrder, ind: MarketIndicators) -> ExitPlan:
    cost = (order.limit_price or 0) * 100
    atr = ind.atr_14
    return ExitPlan(
        take_profit_pct=0.50,
        take_profit_price=round(cost * 0.50, 2),
        stop_loss_pct=0.50,
        stop_loss_price=round(cost * 0.50, 2),
        roll_trigger_dte=0,
        roll_conditions="No rolling — this is a day trade. Close by EOD.",
        adjustment_notes=(
            "⚡ **Intraday scalp management:**\n"
            f"• **Take profit** at 50% gain (${cost * 0.50:.2f}) or at Target 1.\n"
            f"• **Stop-loss** at 50% of premium (${cost * 0.50:.2f}) or if price drops below opening range low.\n"
            f"• **Hard close** by 3:45 PM ET — do NOT hold overnight.\n"
            f"• Trail stop to breakeven after 30%+ gain.\n"
            f"• If {ind.symbol} stalls at resistance, take partial profits."
        ),
    )


def _exit_plan_buy_put(order: OptionOrder, ind: MarketIndicators) -> ExitPlan:
    cost = (order.limit_price or 0) * 100
    atr = ind.atr_14
    return ExitPlan(
        take_profit_pct=0.50,
        take_profit_price=round(cost * 0.50, 2),
        stop_loss_pct=0.50,
        stop_loss_price=round(cost * 0.50, 2),
        roll_trigger_dte=0,
        roll_conditions="No rolling — this is a day trade. Close by EOD.",
        adjustment_notes=(
            "⚡ **Intraday scalp management:**\n"
            f"• **Take profit** at 50% gain (${cost * 0.50:.2f}) or at Target 1.\n"
            f"• **Stop-loss** at 50% of premium (${cost * 0.50:.2f}) or if price rallies above opening range high.\n"
            f"• **Hard close** by 3:45 PM ET — do NOT hold overnight.\n"
            f"• Trail stop to breakeven after 30%+ gain.\n"
            f"• If {ind.symbol} bounces at support, take partial profits."
        ),
    )


_EXIT_PLAN_MAP["buy_call"] = _exit_plan_buy_call
_EXIT_PLAN_MAP["buy_put"] = _exit_plan_buy_put


# ── Brokerage step templates ─────────────────────────────────────

def _brokerage_steps(order: OptionOrder, ind: MarketIndicators) -> list[str]:
    underlying = order.underlying
    legs = order.legs
    strategy = order.strategy_name

    if len(legs) == 1:
        leg = legs[0]
        action_label = leg.action.replace("_", " ").title()
        opt_label = leg.option_type.upper()
        return [
            f"1️⃣  Open your brokerage and search for **{underlying}**.",
            f"2️⃣  Navigate to the **Options Chain** and select the **{leg.expiration}** expiration.",
            f"3️⃣  Find the **${leg.strike:.2f} {opt_label}** contract.",
            f"4️⃣  Click **{action_label}** — quantity: **{leg.quantity}** contract(s).",
            f"5️⃣  Set order type to **Limit** at **${order.limit_price:.2f}** per contract.",
            f"6️⃣  Set duration to **Good for Day (GFD)**.",
            f"7️⃣  Review margin/cash requirement, then **Submit Order**.",
        ]
    else:
        # Multi-leg (iron condor, etc.)
        steps = [
            f"1️⃣  Open your brokerage and search for **{underlying}**.",
            f"2️⃣  Navigate to **Options Chain** → **Multi-Leg / Spread** order entry.",
        ]
        for i, leg in enumerate(legs):
            action_label = leg.action.replace("_", " ").title()
            opt_label = leg.option_type.upper()
            steps.append(
                f"{i+3}\u20e3  Add leg: **{action_label}** the **${leg.strike:.2f} {opt_label}** "
                f"expiring **{leg.expiration}** × {leg.quantity} contract(s)."
            )
        steps.append(
            f"{len(legs)+3}\u20e3  Set the **net limit price** to **${order.limit_price:.2f}** (net credit)."
        )
        steps.append(
            f"{len(legs)+4}\u20e3  Set duration to **Good for Day**, review, and **Submit Order**."
        )
        return steps


# ── Timeframe-specific adjustments ────────────────────────────────

def _timeframe_conditions(
    ind: MarketIndicators, timeframe: str, strategy: str,
) -> list[EntryCondition]:
    """Additional entry conditions based on the analysis timeframe."""
    extra: list[EntryCondition] = []

    if timeframe == "15min":
        # Intraday: need tight bid-ask, high volume context, and momentum alignment
        extra.append(EntryCondition(
            "Intraday RSI not extreme (25–75)", f"{ind.rsi_14:.1f}", "25 – 75",
            25 <= ind.rsi_14 <= 75,
        ))
        extra.append(EntryCondition(
            "Price within tight intraday Bollinger",
            f"${ind.current_price:.2f}", f"${ind.bb_lower:.2f} – ${ind.bb_upper:.2f}",
            ind.bb_lower <= ind.current_price <= ind.bb_upper,
        ))
        # For intraday, prefer low VIX (tighter spreads)
        extra.append(EntryCondition(
            "VIX not spiking (< 35)", f"{ind.vix:.1f}", "< 35",
            ind.vix < 35,
        ))

    elif timeframe == "1hour":
        # Hourly: MACD alignment and BB position matter more
        extra.append(EntryCondition(
            "Hourly MACD aligned with trade direction",
            f"{ind.macd_histogram:.3f}",
            "> 0 (bullish)" if strategy in ("covered_call", "naked_put") else "< 0 (bearish)",
            (ind.macd_histogram > 0) if strategy in ("covered_call", "naked_put") else (ind.macd_histogram < 0)
            if strategy in ("naked_call", "protective_put") else True,
        ))
        extra.append(EntryCondition(
            "Price within hourly Bollinger range",
            f"${ind.current_price:.2f}", f"${ind.bb_lower:.2f} – ${ind.bb_upper:.2f}",
            ind.bb_lower <= ind.current_price <= ind.bb_upper,
        ))

    elif timeframe == "weekly":
        # Weekly: trend alignment is critical for LEAPS
        golden_cross = ind.sma_50 > ind.sma_200
        extra.append(EntryCondition(
            "Weekly trend alignment (SMA50 vs SMA200)",
            f"SMA50=${ind.sma_50:.0f}, SMA200=${ind.sma_200:.0f}",
            "Golden cross" if strategy in ("covered_call", "naked_put") else "Death cross",
            golden_cross if strategy in ("covered_call", "naked_put") else not golden_cross
            if strategy in ("naked_call", "protective_put") else True,
        ))
        extra.append(EntryCondition(
            "Weekly RSI not extreme (30–70)", f"{ind.rsi_14:.1f}", "30 – 70",
            30 <= ind.rsi_14 <= 70,
        ))
        extra.append(EntryCondition(
            "VIX suitable for long-dated options",
            f"{ind.vix:.1f}",
            "< 25 (bullish)" if strategy in ("covered_call", "naked_put") else "> 20 (premium)",
            (ind.vix < 25) if strategy in ("covered_call", "naked_put") else (ind.vix > 20),
        ))

    # daily: no extra conditions (the base strategy conditions are already daily-calibrated)

    return extra


def _adjust_exit_for_timeframe(exit_plan: ExitPlan, timeframe: str) -> ExitPlan:
    """Adjust exit plan parameters based on timeframe."""
    if timeframe == "15min":
        # Intraday: tighter targets, faster management
        exit_plan.take_profit_pct = min(exit_plan.take_profit_pct, 0.25)
        exit_plan.take_profit_price = round(exit_plan.take_profit_price * 0.5, 2)
        exit_plan.roll_trigger_dte = min(exit_plan.roll_trigger_dte, 3)
        exit_plan.adjustment_notes = (
            "⚡ **Intraday management:**\n"
            + exit_plan.adjustment_notes + "\n"
            "• Close all positions before market close if day-trading.\n"
            "• Use tighter stop-losses — intraday moves are amplified.\n"
            "• Monitor every 15–30 minutes."
        )
    elif timeframe == "1hour":
        exit_plan.take_profit_pct = min(exit_plan.take_profit_pct, 0.40)
        exit_plan.take_profit_price = round(exit_plan.take_profit_price * 0.75, 2)
        exit_plan.roll_trigger_dte = min(exit_plan.roll_trigger_dte, 7)
        exit_plan.adjustment_notes = (
            "🕐 **Hourly management:**\n"
            + exit_plan.adjustment_notes + "\n"
            "• Check positions every 1–2 hours during market hours.\n"
            "• Consider closing before weekends if short-dated."
        )
    elif timeframe == "weekly":
        exit_plan.take_profit_pct = min(exit_plan.take_profit_pct + 0.15, 1.0)
        exit_plan.take_profit_price = round(exit_plan.take_profit_price * 1.3, 2)
        exit_plan.roll_trigger_dte = max(exit_plan.roll_trigger_dte, 30)
        exit_plan.adjustment_notes = (
            "📆 **Weekly management (LEAPS):**\n"
            + exit_plan.adjustment_notes + "\n"
            "• Review once per week — no need for daily monitoring.\n"
            "• Roll 30+ DTE before expiration to maintain time value.\n"
            "• Consider fundamental catalysts (earnings, dividends) over technical signals."
        )

    return exit_plan


# ── Main builder ──────────────────────────────────────────────────

def build_execution_guide(
    order: OptionOrder,
    indicators: MarketIndicators,
    timeframe: str = "daily",
) -> ExecutionGuide:
    """Build a complete execution guide for the given order and market state."""
    strategy = order.strategy_name

    # 1. Contract details
    contract_legs = []
    for leg in order.legs:
        contract_legs.append({
            "Action": leg.action.replace("_", " ").title(),
            "Type": leg.option_type.upper(),
            "Strike": f"${leg.strike:.2f}",
            "Expiration": str(leg.expiration),
            "DTE": (leg.expiration - date.today()).days,
            "Qty": leg.quantity,
            "Contract Symbol": leg.symbol,
            "Limit Price": f"${order.limit_price:.2f}" if order.limit_price else "Market",
        })

    # 2. Brokerage steps
    steps = _brokerage_steps(order, indicators)

    # 3. Entry conditions
    cond_fn = _ENTRY_CONDITION_MAP.get(strategy, _entry_conditions_covered_call)
    conditions = cond_fn(indicators)

    # Add timeframe-specific entry conditions
    conditions.extend(_timeframe_conditions(indicators, timeframe, strategy))

    met_count = sum(1 for c in conditions if c.met)

    # 4. Exit plan — adjust for timeframe
    exit_fn = _EXIT_PLAN_MAP.get(strategy, _exit_plan_covered_call)
    exit_plan = exit_fn(order, indicators)
    exit_plan = _adjust_exit_for_timeframe(exit_plan, timeframe)

    # 5. Optimal entry price
    atr = indicators.atr_14
    if strategy == "buy_call":
        # Enter just above the opening range high
        optimal = round(indicators.current_price + atr * 0.2, 2)
    elif strategy == "buy_put":
        # Enter just below the opening range low
        optimal = round(indicators.current_price - atr * 0.2, 2)
    elif strategy in ("covered_call", "naked_put"):
        optimal = round(indicators.sma_50 - atr * 0.5, 2)
    elif strategy in ("naked_call",):
        optimal = round(indicators.sma_50 + atr, 2)
    elif strategy == "iron_condor":
        optimal = round(indicators.current_price, 2)
    else:
        optimal = None

    # 6. Notes
    notes = []

    # Timeframe context note
    _tf_notes = {
        "15min": "⚡ **Intraday (15-min)** — Signals are very short-term. Best for 0–3 DTE or day-trade entries. Monitor continuously.",
        "1hour": "🕐 **Intraday (1-hour)** — Short-term signals for 0–7 DTE entries. Check every 1–2 hours.",
        "daily": "📅 **Daily** — Standard swing-trade timeframe for 14–60 DTE options. Check once per day.",
        "weekly": "📆 **Weekly** — Long-term position signals for 60–365 DTE LEAPS. Check once per week.",
    }
    notes.append(_tf_notes.get(timeframe, _tf_notes["daily"]))

    if met_count == len(conditions):
        notes.append("✅ All entry conditions are met — this is an **ideal entry point**.")
    elif met_count >= len(conditions) * 0.6:
        notes.append(f"🔵 {met_count}/{len(conditions)} conditions met — **acceptable entry**, but not perfect.")
    elif met_count >= len(conditions) * 0.4:
        notes.append(f"🟡 {met_count}/{len(conditions)} conditions met — **consider waiting** for better conditions.")
    else:
        notes.append(f"🔴 Only {met_count}/{len(conditions)} conditions met — **not recommended** to enter now.")

    return ExecutionGuide(
        strategy_name=strategy,
        contract_legs=contract_legs,
        brokerage_steps=steps,
        entry_conditions=conditions,
        entry_conditions_met=met_count,
        entry_conditions_total=len(conditions),
        exit_plan=exit_plan,
        optimal_entry_price=optimal,
        notes=notes,
    )

