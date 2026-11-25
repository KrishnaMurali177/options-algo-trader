"""Is the post-stag effect real or look-ahead? Split was_post_stag by whether the
previous same-day trade's stagnation exit had happened by this entry."""
import json, math, numpy as np, pandas as pd
O="/app/data_cache/ez_study"
x=pd.read_csv(f"{O}/ez_triggers.csv")
ex=[]
for s in ("SPY","QQQ"):
    t=pd.DataFrame(json.load(open(f"{O}/{s}_trig.json")))
    t=t[t["mode"].eq("0dte_option")] if "mode" in t else t
    t=t.assign(sym=s)[["sym","date","time","exit_time","outcome"]]
    t=t.sort_values(["date","time"])
    t["prev_exit"]=t.groupby("date").exit_time.shift(1)
    t["prev_out"]=t.groupby("date").outcome.shift(1)
    ex.append(t)
e=pd.concat(ex)
x=x.merge(e[["sym","date","time","prev_exit","prev_out"]],on=["sym","date","time"],how="left")
x["entry_ts"]=pd.to_datetime(x.date+" "+x.time)
x["prev_exit_ts"]=pd.to_datetime(x.prev_exit)
# exit is observable at the scan bar whose start >= exit bar start (replay drain convention)
x["stag_seen"]=(x.post_stag==1)&(x.prev_exit_ts<=x.entry_ts)
x["stag_unseen"]=(x.post_stag==1)&~(x.prev_exit_ts<=x.entry_ts)
x["era"]=np.select([x.date<"2024-01-01",x.date<"2026-08-19",x.date<="2026-10-29"],["2020-23","2024-26pre","DD"],"after")
x["grp"]=np.select([x.stag_seen,x.stag_unseen],["post_stag SEEN (causal)","post_stag UNSEEN (prev still open)"],"normal")
t=lambda v:v.mean()/(v.std()/math.sqrt(len(v))) if len(v)>2 else np.nan
print(x.groupby(["era","grp"]).agg(n=("z6","size"),Ez6=("z6","mean"),t6=("z6",t),Ezh=("z_hold","mean"),R=("realized_r","mean")).round(3).to_string())
print("\nall years:"); print(x.groupby("grp").agg(n=("z6","size"),Ez6=("z6","mean"),t6=("z6",t),Ezh=("z_hold","mean"),R=("realized_r","mean")).round(3).to_string())
# minutes since the seen stag exit
s=x[x.stag_seen].copy(); s["gap_min"]=(s.entry_ts-s.prev_exit_ts).dt.total_seconds()/60
s["gap_b"]=pd.cut(s.gap_min,[-1,5,15,30,60,600])
print("\nSEEN, by minutes since the stag exit:"); print(s.groupby("gap_b",observed=True).agg(n=("z6","size"),Ez6=("z6","mean"),t6=("z6",t),R=("realized_r","mean")).round(3).to_string())
x.drop(columns=["entry_ts","prev_exit_ts"]).to_csv(f"{O}/ez_triggers_causal.csv",index=False)
