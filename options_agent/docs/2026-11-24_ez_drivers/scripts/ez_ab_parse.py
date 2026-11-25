"""Score the post-stag block A/B from exported triggers (data_cache/ez_ab/*.json)."""
import json, math, glob, os
import numpy as np, pandas as pd

A = "/app/data_cache/ez_ab"
VARS = ["off", "b6", "b12", "halt"]


def load(f):
    t = pd.DataFrame(json.load(open(f)))
    t = t[t["mode"].eq("0dte_option")] if "mode" in t else t
    sg = np.where(t.direction.str.contains("call"), 1, -1)
    t["bps"] = sg * (t.exit_price - t.entry) / t.entry * 1e4
    t["pnl"] = t.option_pnl_100x
    return t


def stats(t, real):
    if t.empty:
        return {}
    d = t.groupby("date").pnl.sum()
    eq = d.cumsum()
    w, l = t.pnl[t.pnl > 0].sum(), -t.pnl[t.pnl < 0].sum()
    return dict(n=len(t), days=t.date.nunique(), net=round(t.pnl.sum()), per_ct=round(t.pnl.mean(), 2),
                pf=round(w / l, 2) if l else None, wr=round((t.pnl > 0).mean() * 100, 1),
                sharpe=round(d.mean() / d.std() * math.sqrt(252), 2) if d.std() else None,
                mdd=round((eq - eq.cummax()).min()), bps=round(t.bps.mean(), 2),
                real_frac=round((t.pricing == "real").mean(), 3) if "pricing" in t else None)


out = {}
for s in ("SPY", "QQQ"):
    for w in ("real", "syn"):
        base = load(f"{A}/{s}_{w}_off.json")
        print(f"\n### {s} {w}")
        for v in VARS:
            t = load(f"{A}/{s}_{w}_{v}.json")
            st = stats(t, w == "real")
            # what was removed vs added relative to baseline
            k = ["date", "time", "direction"]
            m = base.merge(t[k], on=k, how="left", indicator=True)
            rem = m[m._merge == "left_only"]
            add = t.merge(base[k], on=k, how="left", indicator=True)
            add = add[add._merge == "left_only"]
            st.update(removed=len(rem), removed_pnl=round(rem.pnl.sum()), removed_bps=round(rem.bps.mean(), 2) if len(rem) else None,
                      added=len(add), added_pnl=round(add.pnl.sum()))
            # half-year $ delta vs baseline
            hy = lambda x: x.date.str[:4] + np.where(x.date.str[5:7].astype(int) <= 6, "H1", "H2")
            if v != "off":
                dh = (t.groupby(hy(t)).pnl.sum() - base.groupby(hy(base)).pnl.sum()).fillna(0).round()
                st["halfyear_delta"] = dh.to_dict()
                st["halfyears_better"] = f"{int((dh > 0).sum())}/{int((dh != 0).sum())}"
                bd = (t.groupby(hy(t)).bps.sum() - base.groupby(hy(base)).bps.sum()).fillna(0)
                st["halfyears_better_bps"] = f"{int((bd > 0).sum())}/{int((bd != 0).sum())}"
            out[f"{s}_{w}_{v}"] = st
            print(v, {k2: st[k2] for k2 in st if k2 != "halfyear_delta"})
            if v != "off":
                print("    half-year Δ$:", st["halfyear_delta"])
# DD-window and after, real window
print("\n### 2026-08-19..10-29 / 10-30..11-21 (real)")
for s in ("SPY", "QQQ"):
    for v in VARS:
        t = load(f"{A}/{s}_real_{v}.json")
        a = t[(t.date >= "2026-08-19") & (t.date <= "2026-10-29")]
        b = t[t.date >= "2026-10-30"]
        print(s, v, "DD", stats(a, True), "| after", stats(b, True))
# parity: baseline syn vs the ez_study run (same code, default flag)
for s in ("SPY", "QQQ"):
    p = os.path.join("/app/data_cache/ez_study", f"{s}_trig.json")
    if os.path.exists(p):
        old = load(p)
        old = old[(old.date >= "2020-07-27") & (old.date <= "2024-01-31")]
        new = load(f"{A}/{s}_syn_off.json")
        k = ["date", "time", "direction", "entry", "exit_price"]
        same = len(old.merge(new[k], on=k))
        print(f"parity {s} syn baseline vs ez_study: old {len(old)} new {len(new)} identical {same}")
json.dump(out, open(f"{A}/ab_summary.json", "w"), indent=1, default=str)
