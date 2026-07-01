# -*- coding: utf-8 -*-
"""후보 미니게임/광고 이벤트명 존재여부 + 행수 확인 (5/27~5/28)."""
import sys,time,io,csv,requests
sys.stdout.reconfigure(encoding='utf-8')
TOKEN='2dc61bd3e26747d19581397936fca3d5'; APP='polyballkr'
BASE=f"https://api.airbridge.io/log-export/api/v3/apps/{APP}"
H={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"}
PROPS=["user_id","event_datetime","event_name","event_action","event_label","campaign"]
CAND=["app_custom_pv_minigame","app_custom_c_minigame","app_custom_c_minigame_start",
      "app_custom_pv_mini_game","app_custom_c_mini_game","app_custom_pv_game",
      "app_custom_c_game_ad_entry","app_custom_pv_minigame_ad","app_custom_c_minigame_ad_entry",
      "app_custom_pv_ad_minigame","app_custom_c_ad_minigame","app_custom_pv_quiz",
      "app_custom_c_minigame_ad","app_custom_pv_minigame_ad_entry","app_custom_pv_attendance",
      "app_custom_c_roulette","app_custom_pv_roulette","app_custom_pv_ad_reward_completed"]
def submit(ev):
    p={"dateRange":{"start":"2026-05-27 00:00:00","end":"2026-05-28 23:59:59"},"events":[ev],"properties":PROPS,"filters":[]}
    r=requests.post(f"{BASE}/app/request",headers=H,json=p,timeout=30)
    if r.status_code not in(200,201,202): return ('ERR',r.status_code,r.text[:120])
    b=r.json();return ('OK',(b.get("data") or {}).get("reportID") or b.get("reportID"),'')
def poll_once(rid):
    r=requests.get(f"{BASE}/request/{rid}",headers=H,timeout=30)
    if r.status_code in(404,202): return None
    if r.status_code!=200: return None
    d=r.json().get("data") or r.json()
    u=d.get("url") or d.get("downloadUrl") or (d.get("downloadUrls") or [None])[0]
    if u: return u
    return None
def dl(u):
    c=requests.get(u,timeout=180)
    return list(csv.DictReader(io.StringIO(c.content.decode("utf-8-sig",errors="replace"))))
from collections import Counter
jobs=[]
for ev in CAND:
    st,rid,msg=submit(ev)
    if st=='OK' and rid: jobs.append((ev,rid)); print(f"sub {ev} rid={rid}",flush=True)
    else: print(f"sub {ev} -> {st} {rid} {msg}",flush=True)
    time.sleep(0.4)
print("--- polling (round-robin, 150s deadline) ---",flush=True)
time.sleep(20)
import time as _t
deadline=_t.time()+150; done=set()
while jobs and _t.time()<deadline:
    for ev,rid in list(jobs):
        if ev in done: continue
        u=poll_once(rid)
        if u:
            rows=dl(u); done.add(ev); jobs=[(e,r) for e,r in jobs if e!=ev]
            n=len(rows); print(f"\n{ev}: {n} rows",flush=True)
            if n:
                hdr=rows[0].keys()
                ak=next((k for k in hdr if k.lower().replace(' ','')=='eventaction'),None)
                lk=next((k for k in hdr if k.lower().replace(' ','')=='eventlabel'),None)
                if ak: print("  action:",dict(Counter((r.get(ak) or 'NULL') for r in rows).most_common(8)),flush=True)
                if lk: print("  label:",dict(Counter((r.get(lk) or 'NULL') for r in rows).most_common(8)),flush=True)
    _t.sleep(6)
print("\n=== NOT RESOLVED (likely 0/none):",[e for e,_ in jobs],flush=True)
