"""Test different signal weight configurations to find optimal weights."""
import sys, os, logging, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.WARNING)

import numpy as np
import pandas as pd
from src.backtester import IntradayBacktester
from src.models.backtest_result import TradeResult


def make_simulate_day(weights):
    def _sim(self, day_df, symbol, trade_date, vix, interval, prev_day, daily_map=None):
        if interval == "1h":
            opening_bars = day_df[day_df.index.hour == 9]
            if opening_bars.empty:
                opening_bars = day_df.iloc[:1]
        else:
            opening_bars = day_df.between_time("09:30", "10:29")
        if len(opening_bars) < 1:
            return self._skip_trade(trade_date, symbol, "insufficient_opening_bars")
        range_high = float(opening_bars["High"].max())
        range_low = float(opening_bars["Low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            return self._skip_trade(trade_date, symbol, "zero_range")
        open_price = float(opening_bars["Open"].iloc[0])
        or_close = float(opening_bars["Close"].iloc[-1])
        or_volume = int(opening_bars["Volume"].sum())
        bars_to_end_or = day_df[day_df.index <= opening_bars.index[-1]]
        close_to_or = bars_to_end_or["Close"].astype(float)
        price = float(close_to_or.iloc[-1])
        indicator_close = close_to_or
        if len(close_to_or) < 26 and daily_map:
            sd = sorted(d for d in daily_map if d < trade_date)
            pc = pd.Series([daily_map[d]["close"] for d in sd[-40:]], dtype=float)
            indicator_close = pd.concat([pc, close_to_or], ignore_index=True)
        intraday_rsi = self._rsi(indicator_close, 14) if len(indicator_close) >= 15 else 50.0
        if len(indicator_close) >= 26:
            _, _, intraday_hist = self._macd(indicator_close)
        else:
            intraday_hist = 0.0
        typical = (bars_to_end_or["High"] + bars_to_end_or["Low"] + bars_to_end_or["Close"]) / 3
        cumvol = bars_to_end_or["Volume"].cumsum()
        lcv = float(cumvol.iloc[-1])
        vwap = float((typical * bars_to_end_or["Volume"]).cumsum().iloc[-1] / lcv) if lcv > 0 else or_close
        avg_bar_vol = float(day_df["Volume"].mean())
        avg_or_bar_vol = float(opening_bars["Volume"].mean())
        volume_surge = avg_or_bar_vol > avg_bar_vol * 1.2
        or_body_pct = (or_close - open_price) / range_width if range_width > 0 else 0
        tr_vals = []
        src = opening_bars if len(opening_bars) > 1 else day_df.iloc[:min(12, len(day_df))]
        for i in range(1, len(src)):
            h, l, pc2 = float(src["High"].iloc[i]), float(src["Low"].iloc[i]), float(src["Close"].iloc[i-1])
            tr_vals.append(max(h-l, abs(h-pc2), abs(l-pc2)))
        atr = np.mean(tr_vals) if tr_vals else range_width * 0.5
        ema9 = indicator_close.ewm(span=min(9, len(indicator_close)), adjust=False).mean()
        ema21 = indicator_close.ewm(span=min(21, len(indicator_close)), adjust=False).mean()
        ema_bullish = float(ema9.iloc[-1]) > float(ema21.iloc[-1])
        gap_pct = 0.0
        if prev_day:
            gap_pct = (open_price - prev_day["close"]) / prev_day["close"] * 100
        range_atr_ratio = range_width / atr if atr > 0 else 1.0

        w = weights
        signals = {}
        momentum = 0
        rp = (price - range_low) / range_width if range_width > 0 else 0.5
        if rp > 0.7: signals["price_vs_range"] = w["pvr"]; momentum += w["pvr"]
        elif rp < 0.3: signals["price_vs_range"] = -w["pvr"]; momentum -= w["pvr"]
        else: signals["price_vs_range"] = 0
        if intraday_rsi > 60: signals["intraday_rsi"] = w["rsi"]; momentum += w["rsi"]
        elif intraday_rsi < 40: signals["intraday_rsi"] = -w["rsi"]; momentum -= w["rsi"]
        else: signals["intraday_rsi"] = 0
        if intraday_hist > 0.05: signals["intraday_macd"] = w["macd"]; momentum += w["macd"]
        elif intraday_hist < -0.05: signals["intraday_macd"] = -w["macd"]; momentum -= w["macd"]
        else: signals["intraday_macd"] = 0
        if price > vwap: signals["vwap"] = w["vwap"]; momentum += w["vwap"]
        else: signals["vwap"] = -w["vwap"]; momentum -= w["vwap"]
        if volume_surge:
            vd = w["vol"] if momentum > 0 else -w["vol"]
            signals["volume"] = abs(vd); momentum += vd
        else: signals["volume"] = 0
        if or_body_pct > 0.3: signals["or_candle"] = w["orc"]; momentum += w["orc"]
        elif or_body_pct < -0.3: signals["or_candle"] = -w["orc"]; momentum -= w["orc"]
        else: signals["or_candle"] = 0
        if vix > 20:
            va = w["vix"] if momentum > 0 else -w["vix"]
            signals["vix"] = abs(va); momentum += va
        else: signals["vix"] = 0
        momentum = max(-100, min(100, momentum))

        if momentum >= 25: direction = "call"
        elif momentum <= -25: direction = "put"
        else:
            return self._skip_trade(trade_date, symbol, "no_breakout",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)

        quality_score = 0
        if range_atr_ratio > 1.2: quality_score += 1
        signals["range_width"] = 8 if range_atr_ratio > 1.2 else 0
        if volume_surge: quality_score += 1
        if vix > 18: quality_score += 1
        gc = (direction == "call" and gap_pct > 0.3) or (direction == "put" and gap_pct < -0.3)
        if gc: quality_score += 1
        signals["gap_open"] = 10 if gap_pct > 0.3 else (-10 if gap_pct < -0.3 else 0)
        vc = (direction == "call" and price > vwap) or (direction == "put" and price < vwap)
        if vc: quality_score += 1
        oc = (direction == "call" and or_body_pct > 0) or (direction == "put" and or_body_pct < 0)
        if oc: quality_score += 1
        ed = (direction == "call" and not ema_bullish) or (direction == "put" and ema_bullish)
        if ed: quality_score -= 1
        signals["ema_cross"] = 10 if ema_bullish else -10
        signals["prev_day_sr"] = 0
        signals["quality_score"] = quality_score
        if quality_score < 2:
            return self._skip_trade(trade_date, symbol, "low_quality",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)

        range_mid = (range_high + range_low) / 2
        if direction == "call":
            et = range_high; ep = round(range_high + atr * 0.05, 2)
            sl = round(range_mid - atr * 0.02, 2); risk = ep - sl
            if risk <= 0: risk = atr * 0.3
            t1 = round(ep + risk * 0.75, 2); t2 = round(ep + risk * 1.5, 2)
        else:
            et = range_low; ep = round(range_low - atr * 0.05, 2)
            sl = round(range_mid + atr * 0.02, 2); risk = sl - ep
            if risk <= 0: risk = atr * 0.3
            t1 = round(ep - risk * 0.75, 2); t2 = round(ep - risk * 1.5, 2)

        post_or = day_df[day_df.index > opening_bars.index[-1]]
        if post_or.empty:
            return self._skip_trade(trade_date, symbol, "no_post_or_bars",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)
        entered = False; ae = ep; etime = None; xp = 0.0; xr = "eod"; xtime = None
        for ts, bar in post_or.iterrows():
            bh, bl, bc = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
            bt2 = ts.strftime("%H:%M")
            if not entered:
                if direction == "call" and bh >= et:
                    entered = True; ae = max(ep, float(bar["Open"])); etime = bt2
                    risk = ae - sl
                    if risk <= 0: risk = atr * 0.3
                    t1 = round(ae + risk * 0.75, 2); t2 = round(ae + risk * 1.5, 2)
                elif direction == "put" and bl <= et:
                    entered = True; ae = min(ep, float(bar["Open"])); etime = bt2
                    risk = sl - ae
                    if risk <= 0: risk = atr * 0.3
                    t1 = round(ae - risk * 0.75, 2); t2 = round(ae - risk * 1.5, 2)
                continue
            if direction == "call":
                if bl <= sl: xp = sl; xr = "stop"; xtime = bt2; break
                if bh >= t2: xp = t2; xr = "target_2"; xtime = bt2; break
                if bh >= t1: xp = t1; xr = "target_1"; xtime = bt2; break
            else:
                if bh >= sl: xp = sl; xr = "stop"; xtime = bt2; break
                if bl <= t2: xp = t2; xr = "target_2"; xtime = bt2; break
                if bl <= t1: xp = t1; xr = "target_1"; xtime = bt2; break
            if bt2 >= "15:00": xp = bc; xr = "time_stop"; xtime = bt2; break
        if not entered:
            return self._skip_trade(trade_date, symbol, "no_entry",
                                    range_high=range_high, range_low=range_low,
                                    momentum=momentum, signals=signals)
        if xp == 0.0:
            xp = float(post_or["Close"].iloc[-1]); xr = "eod"; xtime = post_or.index[-1].strftime("%H:%M")
        pnl = (xp - ae) if direction == "call" else (ae - xp)
        return TradeResult(
            trade_date=trade_date, symbol=symbol, direction=direction,
            entry_price=ae, exit_price=xp, stop_loss=sl, target_1=t1, target_2=t2,
            range_high=range_high, range_low=range_low, exit_reason=xr,
            pnl_dollars=round(pnl, 4), pnl_pct=round((pnl / ae) * 100 if ae > 0 else 0, 4),
            momentum_score=momentum, signal_scores=signals,
            entry_time=etime, exit_time=xtime,
            vwap_at_entry=round(vwap, 2), volume_at_entry=or_volume, is_winner=pnl > 0)
    return _sim


configs = {
    "baseline":          {"pvr": 20, "rsi": 20, "macd": 15, "vwap": 15, "vol":  5, "orc": 10, "vix":  5},
    "boost_vol_vix":     {"pvr": 15, "rsi": 15, "macd": 15, "vwap": 15, "vol": 10, "orc":  5, "vix": 10},
    "vwap_heavy":        {"pvr": 10, "rsi": 15, "macd": 15, "vwap": 25, "vol":  5, "orc": 10, "vix":  5},
    "rsi_down_macd_up":  {"pvr": 20, "rsi": 10, "macd": 25, "vwap": 15, "vol":  5, "orc": 10, "vix":  5},
    "pvr_down":          {"pvr":  5, "rsi": 20, "macd": 20, "vwap": 20, "vol":  5, "orc": 10, "vix":  5},
    "macd_vwap_focus":   {"pvr": 10, "rsi": 10, "macd": 25, "vwap": 25, "vol":  5, "orc":  5, "vix":  5},
    "equal_15":          {"pvr": 15, "rsi": 15, "macd": 15, "vwap": 15, "vol": 15, "orc": 15, "vix": 15},
    "top_signals":       {"pvr":  5, "rsi": 10, "macd": 20, "vwap": 15, "vol": 15, "orc":  5, "vix": 15},
}

print(f"{'Config':<22} {'SPY_PnL':>8} {'SPY_WR':>7} {'SPY_#':>5} {'QQQ_PnL':>8} {'QQQ_WR':>7} {'QQQ_#':>5} {'Combined':>9}")
print("-" * 80)

for name, w in configs.items():
    bt = IntradayBacktester()
    bt._simulate_day = types.MethodType(make_simulate_day(w), bt)
    total_pnl = 0
    parts = []
    for sym in ['SPY', 'QQQ']:
        r = bt.run(sym, period='1y')
        taken = [t for t in r.trades if t.direction != 'skip']
        total_pnl += r.total_pnl
        parts.append(f"{r.total_pnl:+8.2f} {r.win_rate:6.1f}% {len(taken):5d}")
    print(f"{name:<22} {parts[0]} {parts[1]} {total_pnl:+9.2f}")

