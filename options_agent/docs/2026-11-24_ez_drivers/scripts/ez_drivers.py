"""What drives E[z]? 10 years of Config A replay triggers (SPY, QQQ).

z_k   = signed underlying move k bars after entry / VIX-implied sd over that horizon
z_hold= same over the strategy's actual hold (exit rules applied)
Features are all knowable at entry (trigger fields + pre-entry bars + the
strategy's own trailing z from strictly earlier days).
"""
import json, math, sys
import numpy as np, pandas as pd
from scipy.stats import spearmanr

D = "/app/data_cache"
O = f"{D}/ez_study"
YR = 252 * 390
SYMS = ("SPY", "QQQ")
DD0, DD1 = "2026-08-19", "2026-10-29"


def load_bars(s):
    import glob
    f = sorted(glob.glob(f"{D}/{s}_5min_4000d.parquet"))[0]
    b = pd.read_parquet(f)
    if "timestamp" in b.columns:
        b = b.set_index("timestamp")
    b.index = pd.to_datetime(b.index)
    b.index = (b.index.tz_localize("UTC") if b.index.tz is None else b.index).tz_convert("America/New_York").tz_localize(None)
    b = b.rename(columns=str.title)
    b = b[(b.index.time >= pd.Timestamp("09:30").time()) & (b.index.time <= pd.Timestamp("15:55").time())]
    return b[["Open", "High", "Low", "Close", "Volume"]].sort_index()


def build(s):
    t = pd.DataFrame(json.load(open(f"{O}/{s}_trig.json")))
    t = t[t["mode"].eq("0dte_option")].copy() if "mode" in t else t
    b = load_bars(s)
    b["date"] = b.index.strftime("%Y-%m-%d")
    days = b.groupby("date")
    dly = days.agg(o=("Open", "first"), h=("High", "max"), l=("Low", "min"), c=("Close", "last"))
    dly["ret"] = dly.c.pct_change()
    dly["atr"] = (dly.h - dly.l).rolling(14).mean().shift(1)
    dly["sma20"] = dly.c.rolling(20).mean().shift(1)
    lr = np.log(b.Close).diff()
    lr[b.date != b.date.shift()] = np.nan  # drop overnight
    b["lr"] = lr
    dd = b.groupby("date").lr.agg(rv=lambda x: np.sqrt(np.nansum(x ** 2)),
                                  ac1=lambda x: pd.Series(x).autocorr(1))
    dly = dly.join(dd)
    dly["rv5"] = dly.rv.rolling(5).mean().shift(1)   # prior-5-day daily intraday rv
    dly["ac1_5"] = dly.ac1.rolling(5).mean().shift(1)
    dly["ret5"] = dly.c.pct_change(5).shift(1)
    dly["prev_ret"] = dly.ret.shift(1)
    dly["prev_c"] = dly.c.shift(1)
    dly["prev_range"] = ((dly.h - dly.l) / dly.c).shift(1)
    idx = {d: g for d, g in days}
    rows = []
    for r in t.itertuples():
        g = idx.get(r.date)
        if g is None or r.date not in dly.index:
            continue
        tt = pd.Timestamp(f"{r.date} {r.time}")
        # entry = Close of the bar ENDING at the trigger time (bar starting tt-5min); verified 2026-11-24
        pos = g.index.searchsorted(tt - pd.Timedelta(minutes=5))
        if pos >= len(g) or g.index[pos] != tt - pd.Timedelta(minutes=5):
            continue
        # entry = close of the trigger bar (check against trigger entry)
        p0 = r.entry
        sgn = 1 if "call" in r.direction else -1
        vix = r.vix if r.vix and r.vix > 0 else np.nan
        row = dict(sym=s, date=r.date, time=r.time, sgn=sgn, entry=p0, vix=vix,
                   match_close=abs(g.Close.iloc[pos] - p0) / p0 * 1e4,
                   match_open_next=abs(g.Open.iloc[pos + 1] - p0) / p0 * 1e4 if pos + 1 < len(g) else np.nan)
        for k in (3, 6, 12):
            j = min(pos + k, len(g) - 1)
            n = j - pos
            sd = vix / 100 * p0 * math.sqrt(5 * max(n, 1) / YR)
            row[f"z{k}"] = sgn * (g.Close.iloc[j] - p0) / sd if n > 0 else np.nan
            seg = g.iloc[pos + 1:j + 1]
            row[f"mae{k}"] = (sgn * (seg.Low - p0) if sgn > 0 else sgn * (seg.High - p0)).min() / sd if n > 0 else np.nan
        et = pd.Timestamp(r.exit_time) if isinstance(r.exit_time, str) else None
        hold = (et - tt).total_seconds() / 60 if et is not None else np.nan
        row["hold"] = hold
        row["z_hold"] = sgn * (r.exit_price - p0) / (vix / 100 * p0 * math.sqrt(max(hold, 5) / YR)) if hold == hold else np.nan
        row["realized_r"] = r.realized_r
        row["outcome"] = r.outcome
        # ---- trigger features (aligned to direction where signed) ----
        atr = r.atr_14 if r.atr_14 else np.nan
        rw = (r.range_high - r.range_low) if r.range_high and r.range_low else np.nan
        row["exit_ts"] = et
        row.update(quality=r.quality, explosion=r.explosion, chop=r.chop, chop_ci=r.chop_ci_pts,
                   chop_rev=r.chop_rev_pts, chop_bar=r.chop_bar_ratio_pts, chop_consec=r.chop_consec_pts,
                   chop_rejects=r.chop_rejects_before, momentum=r.momentum, recent_mom=r.recent_momentum,
                   rsi_al=(r.rsi_14 - 50) * sgn if r.rsi_14 == r.rsi_14 else np.nan,
                   vwap_dist_atr=sgn * r.price_vs_vwap / atr if atr else np.nan,
                   sma20_dist_atr=sgn * (p0 - r.sma_20) / atr if atr and r.sma_20 else np.nan,
                   sma_stack_atr=sgn * (r.sma_20 - r.sma_50) / atr if atr and r.sma_50 else np.nan,
                   zlema_al=sgn * {"bullish": 1, "bearish": -1}.get(r.zlema_trend, 0),
                   ordir_al=sgn * {"bullish": 1, "bearish": -1}.get(r.or_direction, 0),
                   recentdir_al=sgn * {"bullish": 1, "bearish": -1}.get(r.recent_dir, 0),
                   atr_bps=atr / p0 * 1e4 if atr else np.nan, vol_ratio=r.vol_ratio,
                   risk_atr=r.risk / atr if atr else np.nan, risk_bps=r.risk / p0 * 1e4,
                   target_mult=r.target_mult, is_flip=int(bool(r.is_flip)), post_stag=int(bool(r.was_post_stag)),
                   is_call=int(sgn > 0), minute=int(r.time[:2]) * 60 + int(r.time[3:]) - 570,
                   range_bps=rw / p0 * 1e4 if rw else np.nan,
                   overshoot=((p0 - r.range_high) if sgn > 0 else (r.range_low - p0)) / rw if rw else np.nan,
                   dow=pd.Timestamp(r.date).dayofweek)
        dl = dly.loc[r.date]
        o = g.Open.iloc[0]
        daysd = vix / 100 / math.sqrt(252) if vix == vix else np.nan
        sofar = g.iloc[:pos + 1]
        row.update(gap_al=sgn * (o / dl.prev_c - 1) / daysd, sofar_al=sgn * (p0 / o - 1) / daysd,
                   prev_ret_al=sgn * dl.prev_ret / daysd, ret5_al=sgn * dl.ret5 / daysd,
                   sma20d_al=sgn * (p0 / dl.sma20 - 1) / daysd if dl.sma20 == dl.sma20 else np.nan,
                   day_range_sofar=(sofar.High.max() - sofar.Low.min()) / p0 / daysd,
                   rv5_vix=dl.rv5 * math.sqrt(252) / (vix / 100), ac1_5=dl.ac1_5,
                   rv_today_vix=np.sqrt(np.nansum(sofar.lr ** 2) * 78 / max(len(sofar) - 1, 1)) * math.sqrt(252) / (vix / 100)
                   if "lr" in sofar else np.nan,
                   prev_range_vix=dl.prev_range / daysd)
        rows.append(row)
    x = pd.DataFrame(rows).sort_values(["date", "time"]).reset_index(drop=True)
    x["n_in_day"] = x.groupby(["sym", "date"]).cumcount()
    # strategy's own trailing follow-through, strictly earlier days
    dz = x.groupby("date").z6.mean()
    for n in (5, 20, 60):
        x[f"trail{n}_z6"] = x.date.map(dz.rolling(n, min_periods=max(3, n // 2)).mean().shift(1))
    # previous same-day trade's realized R, only if it had already exited at this entry
    x["entry_ts"] = pd.to_datetime(x.date + " " + x.time)
    pr = x.groupby(["sym", "date"]).realized_r.shift(1)
    pe = x.groupby(["sym", "date"]).exit_ts.shift(1)
    x["prev_trade_r"] = np.where(pd.to_datetime(pe) <= x.entry_ts, pr, np.nan)
    x["prev_trade_r"] = x["prev_trade_r"].astype(float)
    x = x.drop(columns=["entry_ts", "exit_ts"])
    return x


x = pd.concat([build(s) for s in SYMS], ignore_index=True)
x["year"] = x.date.str[:4]
x.to_csv(f"{O}/ez_triggers.csv", index=False)
print("triggers", len(x), x.groupby("sym").date.agg(["min", "max", "count"]).to_string())
print("entry==bar close (bps) median", x.match_close.median().round(2), " next open", x.match_open_next.median().round(2))

TARGETS = ["z3", "z6", "z12", "z_hold"]
FEATS = [c for c in x.columns if c not in TARGETS + ["sym", "date", "time", "sgn", "entry", "match_close", "match_open_next",
                                                     "hold", "realized_r", "outcome", "year", "mae3", "mae6", "mae12"]]


def t_of(v):
    v = v.dropna()
    return v.mean() / (v.std() / math.sqrt(len(v))) if len(v) > 2 else np.nan


print("\n== Base rate E[z] by year (pooled SPY+QQQ) ==")
by = x.groupby("year").agg(n=("z6", "size"), Ez3=("z3", "mean"), Ez6=("z6", "mean"), Ez12=("z12", "mean"), Ezh=("z_hold", "mean"),
                           t6=("z6", t_of), hit6=("z6", lambda v: (v > 0).mean()))
print(by.round(3).to_string())
dd = x[(x.date >= DD0) & (x.date <= DD1)]
print(f"drawdown window {DD0}..{DD1}: n={len(dd)} Ez6={dd.z6.mean():.3f} Ezh={dd.z_hold.mean():.3f} t={t_of(dd.z6):.2f}")
for s in SYMS:
    d = dd[dd.sym == s]
    print(f"  {s}: n={len(d)} Ez6={d.z6.mean():.3f} Ezh={d.z_hold.mean():.3f}")

# --- calibration vs live BS z (same days) ---
try:
    live = pd.read_csv(f"{D}/regime_study/dd_out/fn_trades.csv")
    live = live[live.book == "live"]
    lz = live.groupby(["ticker", "date"]).z.mean().rename("live_z")
    rz = x.groupby(["sym", "date"]).z_hold.mean().rename("rep_z")
    rz.index.names = ["ticker", "date"]
    j = pd.concat([lz, rz], axis=1).dropna()
    print(f"\ncalibration: replay z_hold vs live BS z per (sym,day): n={len(j)} spearman={spearmanr(j.live_z, j.rep_z)[0]:.3f}  "
          f"live mean {j.live_z.mean():.3f} replay mean {j.rep_z.mean():.3f}")
except Exception as e:
    print("calibration skipped", e)

# --- univariate, with per-year stability ---
print("\n== Univariate: spearman with z6 (pooled), per-year sign agreement, Q5−Q1 ==")
res = []
years = sorted(x.year.unique())
for f in FEATS:
    v = x[[f, "z6", "z_hold", "year"]].dropna()
    if v[f].nunique() < 2 or len(v) < 200:
        continue
    rho = spearmanr(v[f], v.z6)[0]
    rhoh = spearmanr(v[f], v.z_hold)[0]
    ys = []
    for y in years:
        w = v[v.year == y]
        if len(w) > 40 and w[f].nunique() > 1:
            ys.append(spearmanr(w[f], w.z6)[0])
    agree = sum(np.sign(r) == np.sign(rho) for r in ys)
    try:
        q = pd.qcut(v[f].rank(method="first"), 5, labels=False)
        spread = v.z6[q == 4].mean() - v.z6[q == 0].mean()
    except Exception:
        spread = np.nan
    res.append(dict(feat=f, n=len(v), rho_z6=rho, t=rho * math.sqrt(len(v)), rho_zhold=rhoh,
                    years_agree=f"{agree}/{len(ys)}", q5_q1=spread))
uni = pd.DataFrame(res).sort_values("t", key=abs, ascending=False)
print(uni.round(3).to_string(index=False))
uni.to_csv(f"{O}/ez_univariate.csv", index=False)

# --- regime persistence over 10 years ---
print("\n== Persistence of daily E[z6] (per symbol) ==")
for s in SYMS:
    d = x[x.sym == s].groupby("date").z6.mean()
    ac = [round(d.autocorr(k), 3) for k in (1, 2, 5, 10)]
    out = []
    for n in (5, 20, 60):
        tr = d.rolling(n, min_periods=n // 2).mean().shift(1)
        nxt = d.rolling(20).mean().shift(-19)  # next 20 trade-days
        k = pd.concat([tr, d, nxt], axis=1).dropna()
        out.append(f"trail{n}->next day {spearmanr(k.iloc[:, 0], k.iloc[:, 1])[0]:+.3f} / ->next20 {spearmanr(k.iloc[:, 0], k.iloc[:, 2])[0]:+.3f}")
    print(f"  {s}: daily autocorr lag1,2,5,10 = {ac};  " + "; ".join(out))

# --- walk-forward ridge (expanding by year) ---
print("\n== Walk-forward ridge on all features → z6 (train years < Y, test Y) ==")
F = [f for f in FEATS if x[f].notna().mean() > 0.9]
X = x[F].copy()


def fit_ridge(Xt, yt, lam=50.0):
    mu, sd = Xt.mean(), Xt.std().replace(0, 1)
    Z = ((Xt - mu) / sd).fillna(0).values
    A = Z.T @ Z + lam * np.eye(Z.shape[1])
    w = np.linalg.solve(A, Z.T @ (yt.values - yt.mean()))
    return lambda Xn: ((Xn - mu) / sd).fillna(0).values @ w + yt.mean(), pd.Series(w, index=Xt.columns)


x["pred"] = np.nan
for y in years[3:]:
    tr = x.year < y
    te = x.year == y
    f, w = fit_ridge(X[tr], x.z6[tr])
    x.loc[te, "pred"] = f(X[te])
oos = x.dropna(subset=["pred"])
print(f"OOS {oos.year.min()}..{oos.year.max()} n={len(oos)} spearman(pred,z6)={spearmanr(oos.pred, oos.z6)[0]:.3f}  "
      f"(pred,z_hold)={spearmanr(oos.pred, oos.z_hold, nan_policy='omit')[0]:.3f}")
oos = oos.assign(terc=pd.qcut(oos.pred.rank(method="first"), 3, labels=["low", "mid", "high"]))
print(oos.groupby("terc", observed=True).agg(n=("z6", "size"), Ez6=("z6", "mean"), Ezh=("z_hold", "mean"),
                                              hit=("z6", lambda v: (v > 0).mean())).round(3).to_string())
print("per-year OOS spearman:", {y: round(spearmanr(g.pred, g.z6)[0], 3) for y, g in oos.groupby("year")})
ddo = oos[(oos.date >= DD0) & (oos.date <= DD1)]
print(f"drawdown window: mean pred {ddo.pred.mean():.3f} vs history mean pred {oos.pred.mean():.3f}; "
      f"pred-pctile of DD-window mean = {(oos.groupby('date').pred.mean() < ddo.pred.mean()).mean():.2f}; "
      f"spearman in window {spearmanr(ddo.pred, ddo.z6)[0]:.3f}")
_, wall = fit_ridge(X, x.z6)
print("full-sample ridge weights (std units), top 15:\n", wall.reindex(wall.abs().sort_values(ascending=False).index)[:15].round(4).to_string())
x.to_csv(f"{O}/ez_triggers.csv", index=False)
