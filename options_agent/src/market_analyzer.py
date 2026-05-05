"""Market Analyzer — fetches data and computes technical indicators + regime classification."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from src.models.market_data import MarketIndicators, MarketRegime

logger = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


class MarketAnalyzer:
    """Compute technical indicators for a symbol and classify the market regime."""

    # Timeframe → (yfinance period, yfinance interval, label)
    TIMEFRAME_CONFIG = {
        "intraday": {"period": "5d", "interval": "5m", "label": "Intraday 5-min"},
        "15min": {"period": "5d", "interval": "5m", "label": "Intraday 5-min"},  # alias
        "1hour": {"period": "5d", "interval": "5m", "label": "Intraday 5-min"},  # alias
        "daily": {"period": "1y", "interval": "1d", "label": "Daily"},
        "weekly": {"period": "2y", "interval": "1wk", "label": "Weekly"},
    }

    # ── Public API ────────────────────────────────────────────────

    def analyze(self, symbol: str, timeframe: str = "daily") -> MarketIndicators:
        """Fetch OHLCV data for the given timeframe and compute all indicators.

        For intraday timeframes (15min, 1hour), uses a hybrid approach:
          - **Regime indicators** (SMAs, Bollinger, MACD) from daily data so that
            concepts like death cross / golden cross remain meaningful.
          - **Trade-timing indicators** (RSI, ATR, volume) from 5-min candles
            for responsive, trader-relevant signals.
        Daily and weekly timeframes use their own candle data throughout.

        Args:
            symbol: Stock ticker (e.g. "AAPL").
            timeframe: One of "15min", "1hour", "daily", "weekly".
        """
        tf = self.TIMEFRAME_CONFIG.get(timeframe, self.TIMEFRAME_CONFIG["daily"])
        logger.info("Analyzing %s on %s timeframe …", symbol, tf["label"])

        is_intraday = timeframe in ("intraday", "15min", "1hour")

        # ── Fetch primary data — try Alpaca first, fallback to yfinance ──
        df = pd.DataFrame()
        if tf["interval"] in ("5m", "15m", "1h"):
            try:
                from src.utils.alpaca_data import fetch_bars as _alpaca_fetch
                _interval_map = {"5m": "5min", "15m": "15min", "1h": "1hour"}
                _days_map = {"5d": 5, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}
                _days = _days_map.get(tf["period"], 5)
                df = _alpaca_fetch(symbol, days_back=_days, interval=_interval_map.get(tf["interval"], "5min"))
            except Exception:
                pass
        if df.empty:
            df = yf.download(symbol, period=tf["period"], interval=tf["interval"], progress=False)
        if df.empty:
            raise ValueError(f"No data returned for {symbol} ({tf['label']})")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # ── VIX ──
        vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_df.columns = vix_df.columns.get_level_values(0)
        vix = float(vix_df["Close"].iloc[-1])

        current_price = float(df["Close"].astype(float).iloc[-1])

        # ── For intraday: fetch daily data for regime indicators, 5-min for trade signals ──
        if is_intraday:
            # Daily data for regime-scale indicators (SMAs, Bollinger, MACD)
            df_daily = yf.download(symbol, period="1y", interval="1d", progress=False)
            if isinstance(df_daily.columns, pd.MultiIndex):
                df_daily.columns = df_daily.columns.get_level_values(0)

            if not df_daily.empty:
                daily_close = df_daily["Close"].astype(float)
            else:
                # Fallback: use primary timeframe data
                daily_close = df["Close"].astype(float)

            # 5-min data for trade-timing indicators (RSI, ATR, volume)
            df_5m = pd.DataFrame()
            try:
                from src.utils.alpaca_data import fetch_bars as _alpaca_fetch
                df_5m = _alpaca_fetch(symbol, days_back=5, interval="5min")
            except Exception:
                pass
            if df_5m.empty:
                df_5m = yf.download(symbol, period="5d", interval="5m", progress=False)
            if isinstance(df_5m.columns, pd.MultiIndex):
                df_5m.columns = df_5m.columns.get_level_values(0)

            if not df_5m.empty:
                close_5m = df_5m["Close"].astype(float)
                high_5m = df_5m["High"].astype(float)
                low_5m = df_5m["Low"].astype(float)
                vol_5m = df_5m["Volume"].astype(float)
            else:
                # Fallback to primary timeframe
                close_5m = df["Close"].astype(float)
                high_5m = df["High"].astype(float)
                low_5m = df["Low"].astype(float)
                vol_5m = df["Volume"].astype(float)

            # Regime indicators from DAILY data
            sma_20 = float(daily_close.rolling(20).mean().iloc[-1])
            sma_50 = float(daily_close.rolling(50).mean().iloc[-1])
            sma_200 = float(daily_close.rolling(min(200, len(daily_close) - 1)).mean().iloc[-1])

            bb_mid = float(daily_close.rolling(20).mean().iloc[-1])
            bb_std = float(daily_close.rolling(20).std().iloc[-1])
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std

            macd_line, macd_signal, macd_hist = self._macd(daily_close)

            # Trade-timing indicators from 5-MIN data
            rsi = self._rsi(close_5m, 14) if len(close_5m) >= 15 else 50.0
            rsi_5min = rsi  # same source for intraday
            atr = self._atr(high_5m, low_5m, close_5m, 14) if len(close_5m) >= 15 else 1.0
            current_volume = int(vol_5m.iloc[-1])
            volume_sma_20 = float(vol_5m.rolling(min(20, len(vol_5m))).mean().iloc[-1])

        else:
            # ── Daily / Weekly: all indicators from same timeframe ──
            close = df["Close"].astype(float)
            high = df["High"].astype(float)
            low = df["Low"].astype(float)
            vol = df["Volume"].astype(float)

            if timeframe == "weekly":
                sma_short, sma_mid, sma_long, bb_period = 10, 20, 40, 10
            else:
                sma_short, sma_mid, sma_long, bb_period = 20, 50, 200, 20

            rsi = self._rsi(close, 14)
            rsi_5min = None

            sma_20 = float(close.rolling(sma_short).mean().iloc[-1])
            sma_50 = float(close.rolling(sma_mid).mean().iloc[-1])
            sma_200 = float(close.rolling(min(sma_long, len(close) - 1)).mean().iloc[-1])

            bb_mid = float(close.rolling(bb_period).mean().iloc[-1])
            bb_std = float(close.rolling(bb_period).std().iloc[-1])
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std

            macd_line, macd_signal, macd_hist = self._macd(close)
            atr = self._atr(high, low, close, 14)
            current_volume = int(vol.iloc[-1])
            volume_sma_20 = float(vol.rolling(min(20, len(vol))).mean().iloc[-1])

        # ── ZLEMA (Zero-Lag EMA) for trend signals ──
        if is_intraday and not df_5m.empty:
            zlema_fast_val = self._zlema(close_5m, 8)
            zlema_slow_val = self._zlema(close_5m, 21)
            if zlema_fast_val > zlema_slow_val * 1.0002:
                zlema_trend = "bullish"
            elif zlema_fast_val < zlema_slow_val * 0.9998:
                zlema_trend = "bearish"
            else:
                zlema_trend = "neutral"
        else:
            close_series = df["Close"].astype(float)
            zlema_fast_val = self._zlema(close_series, 8)
            zlema_slow_val = self._zlema(close_series, 21)
            if zlema_fast_val > zlema_slow_val * 1.0002:
                zlema_trend = "bullish"
            elif zlema_fast_val < zlema_slow_val * 0.9998:
                zlema_trend = "bearish"
            else:
                zlema_trend = "neutral"

        # Earnings date (best-effort)
        next_earnings, days_to_earn = self._next_earnings(symbol)


        indicators = MarketIndicators(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            current_price=current_price,
            timeframe=timeframe,
            vix=vix,
            rsi_14=rsi,
            rsi_5min=rsi_5min,
            sma_20=sma_20,
            sma_50=sma_50,
            sma_200=sma_200,
            bb_upper=bb_upper,
            bb_middle=bb_mid,
            bb_lower=bb_lower,
            macd=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_hist,
            atr_14=atr,
            volume=current_volume,
            volume_sma_20=volume_sma_20,
            next_earnings_date=next_earnings,
            days_to_earnings=days_to_earn,
            zlema_fast=zlema_fast_val,
            zlema_slow=zlema_slow_val,
            zlema_trend=zlema_trend,
        )
        logger.info("Indicators for %s: VIX=%.1f RSI=%.1f SMA50=%.2f", symbol, vix, rsi, sma_50)
        return indicators

    def classify_regime(self, ind: MarketIndicators) -> MarketRegime:
        """Rule-based market regime classification."""

        death_cross = ind.sma_50 < ind.sma_200

        # High-vol bearish
        if ind.vix > 30 and (ind.rsi_14 > 70 or death_cross):
            return MarketRegime.HIGH_VOL_BEARISH

        # Trending bearish (death cross + negative MACD)
        if death_cross and ind.macd_histogram < 0:
            return MarketRegime.TRENDING_BEARISH

        # Range-bound high-vol (VIX 20-35, price within Bollinger, neutral RSI)
        if 20 <= ind.vix <= 35 and ind.bb_lower <= ind.current_price <= ind.bb_upper and 40 <= ind.rsi_14 <= 60:
            return MarketRegime.RANGE_BOUND_HV

        # Low-vol bullish (VIX<20, price above 50-SMA, RSI 40-65)
        if ind.vix < 20 and ind.current_price > ind.sma_50 and 40 <= ind.rsi_14 <= 65:
            return MarketRegime.LOW_VOL_BULLISH

        # Low-vol neutral (fallback low-vol)
        if ind.vix < 20:
            return MarketRegime.LOW_VOL_NEUTRAL

        # Default: range-bound high-vol
        return MarketRegime.RANGE_BOUND_HV

    # ── Private Helpers ───────────────────────────────────────────

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> float:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    @staticmethod
    def _macd(close: pd.Series) -> tuple[float, float, float]:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal
        return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> float:
        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    @staticmethod
    def _next_earnings(symbol: str) -> tuple[date | None, int | None]:
        try:
            ticker = yf.Ticker(symbol)
            cal = ticker.calendar
            if cal is not None and not cal.empty:
                earn_date = pd.Timestamp(cal.iloc[0, 0]).date()
                days = (earn_date - date.today()).days
                return earn_date, days
        except Exception:
            pass
        return None, None

    @staticmethod
    def _zlema(close: pd.Series, period: int) -> float:
        """Zero-Lag EMA — removes inherent EMA lag by compensating with price momentum.

        ZLEMA = EMA(2*close - close[lag], period) where lag = (period-1)//2
        """
        lag = (period - 1) // 2
        compensated = 2 * close - close.shift(lag)
        zlema = compensated.ewm(span=period, adjust=False).mean()
        return float(zlema.iloc[-1])



