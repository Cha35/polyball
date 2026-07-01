# -*- coding: utf-8 -*-
"""polyballkr 커스텀 이벤트명 전체 열거 (Actuals/메타 API 탐색)."""
import sys,json,requests
sys.stdout.reconfigure(encoding='utf-8')
TOKEN='2dc61bd3e26747d19581397936fca3d5'; APP='polyballkr'
H={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"}

# 시도1: actuals 메타 - event_name groupBy 로 catalog 뽑기 (한 메트릭 + event_label groupBy)
def try_groupby(gb):
    url=f"https://api.airbridge.io/reports/api/v7/apps/{APP}/actuals/query"
    p={"from":"2026-05-27","to":"2026-05-30","metrics":["app_event_count"],
       "groupBys":gb,"filters":[],"isSummaryAvailable":False,"size":500,
       "option":{"eventTimestampSource":"event_occurred_date"}}
    r=requests.post(url,headers=H,json=p,timeout=40)
    try: res=r.json()
    except: print("non-json",r.status_code,r.text[:200]); return None
    import time
    t=res.get("task",{})
    if t.get("status")!="SUCCESS":
        tid=t.get("taskId")
        if tid:
            for _ in range(40):
                time.sleep(1.5)
                res=requests.get(f"{url}/{tid}",headers=H,timeout=30).json()
                if res.get("task",{}).get("status")=="SUCCESS": break
    ac=res.get("actuals") or res.get("reportData",{}).get("actuals") or {}
    rows=ac.get("data",{}).get("rows",[])
    return rows,res

for gb in [["event_category"],["event_label"],["event_action"]]:
    print(f"\n=== groupBy {gb} ===")
    out=try_groupby(gb)
    if not out: continue
    rows,res=out
    if not rows:
        print("  0 rows; raw:",json.dumps(res)[:300])
        continue
    vals=[]
    for r in rows:
        g=r.get("groupBys",[None])[0]
        v=list(r.get("values",{}).values())
        cnt=int(v[0].get("value",0)) if v else 0
        vals.append((g,cnt))
    for g,cnt in sorted(vals,key=lambda x:-x[1])[:60]:
        print(f"  {g}: {cnt:,}")
