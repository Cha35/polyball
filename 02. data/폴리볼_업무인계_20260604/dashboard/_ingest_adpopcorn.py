# -*- coding: utf-8 -*-
"""애드팝콘/MAX xlsx → data.json ad_revenue 업데이트. parse_raw 재사용, 환율 1400(rodata 정합)."""
import json, sys
import pandas as pd
from collections import defaultdict
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
from fetch_ad_revenue import parse_raw

XL=r"C:\Users\rest\Downloads\20260525_20260603.xlsx"
USD_KRW=1400
COLS=["report_date","media_key","media_name","placement_id","placement_name",
      "thirdparty_name","request","response","fill_rate(%)","impression",
      "impression_rate(%)","click","ctr(%)","media_cost(USD)","eCPM(USD)","RPR"]

df=pd.read_excel(XL,engine='openpyxl')
lines=["\t".join(str(r[c]) for c in COLS) for _,r in df.iterrows()]
merged=parse_raw("\n".join(lines))

rows=[]
for r in sorted(merged.values(),key=lambda x:(x["date"],x["placement_id"])):
    krw=int(round(r["cost_usd"]*USD_KRW))
    ecpm=int(round(r["cost_usd"]*USD_KRW/r["impression"]*1000)) if r["impression"]>0 else 0
    rows.append({**r,"cost_krw":krw,"ecpm_krw":ecpm})

P=Path("data.json")
d=json.load(open(P,encoding="utf-8"))
old=len(d.get("ad_revenue",[]))
new_dates={r["date"] for r in rows}
keep=[r for r in d.get("ad_revenue",[]) if r["date"] not in new_dates]
d["ad_revenue"]=sorted(keep+rows,key=lambda x:(x["date"],x["placement_id"]))
m=d.get("ad_revenue_meta",{})
m.update({"krw_rate":USD_KRW,"last_excel_range":"20260525_20260603","last_updated":"2026-06-03"})
d["ad_revenue_meta"]=m
json.dump(d,open(P,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
print(f"ad_revenue {old} -> {len(d['ad_revenue'])} records  (대체 날짜 {sorted(new_dates)})")

by=defaultdict(lambda:[0,0,0.0])  # date->[req,imp,usd]
cat=defaultdict(float)
for r in rows:
    by[r["date"]][0]+=r["request"]; by[r["date"]][1]+=r["impression"]; by[r["date"]][2]+=r["cost_usd"]
    cat[r["category"] or "?"]+=r["cost_usd"]
print("\n[일별 req/imp/USD/KRW]")
for dt in sorted(by):
    rq,im,us=by[dt]; print(f"  {dt}: req {rq:>4} / imp {im:>5} / ${us:6.2f} / {int(us*USD_KRW):>7,}원")
print("\n[영역별 USD]", {k:round(v,2) for k,v in cat.items()})
