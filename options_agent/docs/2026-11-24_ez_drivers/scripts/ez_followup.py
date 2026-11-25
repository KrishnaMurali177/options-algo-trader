import numpy as np, pandas as pd, math
x=pd.read_csv("/app/data_cache/ez_study/ez_triggers.csv")
x["era"]=np.select([x.date<"2024-01-01",x.date<"2026-08-19",x.date<="2026-10-29"],["2020-23","2024-26pre","DD"],"after")
t=lambda v:(v.mean()/(v.std()/math.sqrt(len(v)))) if len(v)>2 else np.nan
g=lambda d: pd.Series(dict(n=len(d),Ez6=d.z6.mean(),Ezh=d.z_hold.mean(),t6=t(d.z6),hit=(d.z6>0).mean(),R=d.realized_r.mean()))
print("== post_stag by era =="); print(x.groupby(["era","post_stag"]).apply(g).round(3).to_string())
print("\npost_stag share by era:",x.groupby("era").post_stag.mean().round(3).to_dict())
print("\n== ex-post_stag base E[z6] by era =="); print(x[x.post_stag==0].groupby("era").apply(g).round(3).to_string())
for f in ["atr_bps","rv5_vix","ret5_al","prev_ret_al","vix"]:
    x["q"]=x.groupby("era")[f].transform(lambda v: pd.qcut(v.rank(method="first"),3,labels=False))
    print(f"\n== {f} terciles (within era) E[z6] =="); print(x.pivot_table(index="era",columns="q",values="z6",aggfunc="mean").round(3).to_string())
o=x.dropna(subset=["pred"]).copy()
cut=o[o.date<"2026-08-19"].pred.quantile(1/3)
o["skip"]=o.pred<cut
print("\n== skip walk-forward low tercile (cut from pre-DD OOS) ==")
print(o.groupby(["era","skip"]).apply(g).round(3).to_string())
