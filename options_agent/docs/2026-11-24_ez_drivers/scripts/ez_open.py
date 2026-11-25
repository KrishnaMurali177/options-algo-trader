"""Causal proxy: entering while an earlier same-day position is still open, split by that
position's unrealized R at this entry (known live)."""
import json, math, numpy as np, pandas as pd
O="/app/data_cache/ez_study"
x=pd.read_csv(f"{O}/ez_triggers_causal.csv")
rows=[]
for s in ("SPY","QQQ"):
    t=pd.DataFrame(json.load(open(f"{O}/{s}_trig.json")))
    t=t[t["mode"].eq("0dte_option")] if "mode" in t else t
    for d,g in t.sort_values(["date","time"]).groupby("date"):
        g=g.reset_index(drop=True)
        for i,r in g.iterrows():
            ets=pd.Timestamp(f"{d} {r.time}")
            opn=[q for _,q in g.iloc[:i].iterrows() if pd.Timestamp(q.exit_time)>ets]
            if not opn: rows.append(dict(sym=s,date=d,time=r.time,n_open=0,open_same_dir=np.nan,open_ur=np.nan)); continue
            q=opn[-1]; sq=1 if "call" in q.direction else -1
            rows.append(dict(sym=s,date=d,time=r.time,n_open=len(opn),
                             open_same_dir=int(q.direction==r.direction),
                             open_ur=sq*(r.entry-q.entry)/q.risk if q.risk else np.nan))
o=pd.DataFrame(rows); x=x.merge(o,on=["sym","date","time"],how="left")
x["era"]=np.select([x.date<"2024-01-01",x.date<"2026-08-19",x.date<="2026-10-29"],["2020-23","2024-26pre","DD"],"after")
x["state"]=np.select([x.n_open==0, x.open_ur< -0.1, x.open_ur<=0.2],["no open position","open pos losing (<-0.1R)","open pos flat (-0.1..+0.2R)"],"open pos winning (>0.2R)")
t=lambda v:v.mean()/(v.std()/math.sqrt(len(v))) if len(v)>2 else np.nan
agg=dict(n=("z6","size"),Ez6=("z6","mean"),t6=("z6",t),Ezh=("z_hold","mean"),R=("realized_r","mean"))
print(x.groupby("state").agg(**agg).round(3).to_string())
print(); print(x.groupby(["era","state"]).agg(**agg).round(3).to_string())
print("\nsame-dir vs opposite (open only):"); print(x[x.n_open>0].groupby("open_same_dir").agg(**agg).round(3).to_string())
x.to_csv(f"{O}/ez_triggers_causal.csv",index=False)
