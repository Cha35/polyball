# -*- coding: utf-8 -*-
"""5/26~5/28 c_ad_entry & pv_ad raw 받아 모든 필드 값분포 출력 (미니게임 구분자 탐색)."""
import sys,time,io,csv,requests
from collections import Counter,defaultdict
sys.stdout.reconfigure(encoding='utf-8')
TOKEN='2dc61bd3e26747d19581397936fca3d5'; APP='polyballkr'
BASE=f"https://api.airbridge.io/log-export/api/v3/apps/{APP}"
H={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"}
PROPS=["user_id","event_datetime","event_name","channel","campaign","ad_group","ad_creative","event_action","event_label","event_semantic_attributes"]
def submit(ev,s,e):
    p={"dateRange":{"start":f"{s} 00:00:00","end":f"{e} 23:59:59"},"events":[ev],"properties":PROPS,"filters":[]}
    r=requests.post(f"{BASE}/app/request",headers=H,json=p,timeout=30)
    if r.status_code not in(200,201,202): print('submit',r.status_code,r.text[:200]);return None
    b=r.json();return (b.get("data") or {}).get("reportID") or b.get("reportID")
def get(rid):
    for _ in range(80):
        time.sleep(5)
        r=requests.get(f"{BASE}/request/{rid}",headers=H,timeout=30)
        if r.status_code in(404,202): continue
        if r.status_code!=200: continue
        d=r.json().get("data") or r.json()
        u=d.get("url") or d.get("downloadUrl") or (d.get("downloadUrls") or [None])[0]
        if u:
            c=requests.get(u,timeout=180)
            return list(csv.DictReader(io.StringIO(c.content.decode("utf-8-sig",errors="replace"))))
        if (d.get("status") or "").upper() in("FAILED","ERROR"): return []
    return []
for ev in ["app_custom_c_ad_entry","app_custom_pv_ad"]:
    rid=submit(ev,"2026-05-26","2026-05-28")
    print(f"\n##### {ev} rid={rid}")
    if not rid: continue
    rows=get(rid)
    if not rows: print("  no rows"); continue
    hdr=list(rows[0].keys()); print("  headers:",hdr)
    def K(name): return next((k for k in hdr if k.lower().replace(' ','').replace('_','')==name),None)
    dk=K('eventdatetime'); ak=K('eventaction'); lk=K('eventlabel'); ck=K('campaign'); chk=K('channel'); gk=K('adgroup'); crk=K('adcreative'); sk=K('eventsemanticattributes')
    by=defaultdict(lambda:Counter())
    for r in rows:
        d=(r.get(dk) or '')[:10]
        by[d][(r.get(ak) or 'NULL', r.get(lk) or 'NULL')]+=1
    for d in sorted(by):
        print(f"  [{d}] (action,label) dist:")
        for kv,n in by[d].most_common(20): print(f"     {kv}: {n}")
    # sample semantic attrs for 5/27
    if sk:
        print("  -- 5/27 semantic_attr samples --")
        cnt=0
        for r in rows:
            if (r.get(dk) or '')[:10]=='2026-05-27' and r.get(sk):
                print("    ",r.get(sk)[:300]); cnt+=1
                if cnt>=5: break
    # campaign/adgroup/creative distinct on 5/27
    for label,kk in [('campaign',ck),('ad_group',gk),('ad_creative',crk)]:
        if not kk: continue
        c27=Counter(r.get(kk) or 'NULL' for r in rows if (r.get(dk) or '')[:10]=='2026-05-27')
        print(f"  5/27 {label} dist:",dict(c27.most_common(10)))
