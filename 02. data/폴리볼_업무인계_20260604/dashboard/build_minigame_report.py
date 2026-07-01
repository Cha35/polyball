# -*- coding: utf-8 -*-
"""미니게임 분석 HTML 리포트 생성 (일자별).
데이터: 에어브릿지 actuals(pv_minigame/c_minigame 유저) + log-export(c_minigame 게임별 라벨) + data.json ad_revenue(애드팝콘).
출력: 08_대시보드/미니게임_분석_YYYYMMDD.html
"""
import json, time, io, csv, requests, sys
from collections import defaultdict
from pathlib import Path
from datetime import date
sys.stdout.reconfigure(encoding='utf-8')
import daily_pipeline as p

DASH = Path(__file__).parent
OUT = DASH.parent / "08_대시보드"
TOKEN = '2dc61bd3e26747d19581397936fca3d5'
APP = 'polyballkr'
START, END = '2026-05-27', '2026-06-03'
GAME = {'bbada': '야구빠따 키우기', '4taja': '오늘의 4번타자', 'fishing': '응모권 낚시하기'}

# ── 1) 에어브릿지 actuals: pv/click 유저 ──
metrics = ['web_custom_users_pv_minigame', 'app_custom_users_pv_minigame',
           'web_custom_users_c_minigame', 'app_custom_users_c_minigame']
payload = {'from': START, 'to': END, 'metrics': metrics, 'groupBys': ['event_date'],
           'filters': [], 'sorts': [{'fieldName': 'event_date', 'isAscending': True}],
           'isSummaryAvailable': True, 'option': {'eventTimestampSource': 'event_occurred_date'}, 'size': 100}
au = p.ab_parse_rows(p.ab_request(payload))

# ── 2) log-export: 게임별 클릭(라벨) 일자별 ──
BASE = f'https://api.airbridge.io/log-export/api/v3/apps/{APP}'
H = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}
lp = {'dateRange': {'start': f'{START} 00:00:00', 'end': f'{END} 23:59:59'},
      'events': ['app_custom_c_minigame'], 'properties': ['event_datetime', 'event_label', 'user_id'], 'filters': []}
rid = (requests.post(f'{BASE}/app/request', headers=H, json=lp, timeout=30).json().get('data') or {}).get('reportID')
rows = []
for _ in range(60):
    time.sleep(5)
    rr = requests.get(f'{BASE}/request/{rid}', headers=H, timeout=30)
    if rr.status_code in (404, 202):
        continue
    if rr.status_code != 200:
        continue
    d = rr.json().get('data') or rr.json()
    u = d.get('url') or d.get('downloadUrl') or (d.get('downloadUrls') or [None])[0]
    if u:
        rows = list(csv.DictReader(io.StringIO(requests.get(u, timeout=180).content.decode('utf-8-sig', errors='replace'))))
        break
hdr = list(rows[0].keys())
def K(n): return next((k for k in hdr if k.lower().replace(' ', '').replace('_', '') == n), None)
dk, lk, uk = K('eventdatetime'), K('eventlabel'), K('userid')
game_daily = defaultdict(lambda: defaultdict(int))   # date->game->clicks
game_tot = defaultdict(int)
game_users = defaultdict(set)
for r in rows:
    ds = (r.get(dk) or '')[:10]
    g = r.get(lk) or '?'
    game_daily[ds][g] += 1
    game_tot[g] += 1
    game_users[g].add(r.get(uk))

# ── 3) data.json 애드팝콘 미니게임 광고 ──
dj = json.load(open(DASH / "data.json", encoding="utf-8"))
mg = [x for x in dj['ad_revenue'] if x.get('category') == 'minigame']
def mtyp(nm): return '이어하기' if '이어하기' in nm else '다시하기' if '다시하기' in nm else '기타'
ad_daily = defaultdict(lambda: [0, 0, 0])          # date->[req,imp,krw]
adtype_daily = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # date->type->[req,imp,krw]
for x in mg:
    ds = x['date']; t = mtyp(x.get('placement_name', ''))
    rq, im, kr = int(x.get('request', 0) or 0), int(x.get('impression', 0) or 0), int(x.get('cost_krw', 0) or 0)
    ad_daily[ds][0] += rq; ad_daily[ds][1] += im; ad_daily[ds][2] += kr
    a = adtype_daily[ds][t]; a[0] += rq; a[1] += im; a[2] += kr

dates = sorted(au.keys())

# ── 통합 행 ──
def pv(ds): return au[ds].get('web_custom_users_pv_minigame', 0) + au[ds].get('app_custom_users_pv_minigame', 0)
def cl(ds): return au[ds].get('web_custom_users_c_minigame', 0) + au[ds].get('app_custom_users_c_minigame', 0)

WD = ['월', '화', '수', '목', '금', '토', '일']
def wd(ds): return WD[date.fromisoformat(ds).weekday()]

# 누적
tot_pv = sum(pv(d) for d in dates)
tot_cl = sum(cl(d) for d in dates)
tot_req = sum(ad_daily[d][0] for d in dates)
tot_imp = sum(ad_daily[d][1] for d in dates)
tot_krw = sum(ad_daily[d][2] for d in dates)

# JS용 배열
def jsarr(vals): return '[' + ','.join(str(v) for v in vals) + ']'
labels = jsarr([f'"{d[5:]}({wd(d)})"' for d in dates])
pv_arr = jsarr([pv(d) for d in dates])
cl_arr = jsarr([cl(d) for d in dates])
krw_arr = jsarr([ad_daily[d][2] for d in dates])
bbada_arr = jsarr([game_daily[d].get('bbada', 0) for d in dates])
taja_arr = jsarr([game_daily[d].get('4taja', 0) for d in dates])
fish_arr = jsarr([game_daily[d].get('fishing', 0) for d in dates])

def row(cells, bold=False):
    tag = 'th' if bold else 'td'
    return '<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in cells) + '</tr>'

# 통합 퍼널 테이블
funnel_rows = []
for d in dates:
    p_, c_ = pv(d), cl(d)
    rq, im, kr = ad_daily[d]
    clr = f'{c_/p_*100:.0f}%' if p_ else '-'
    per = f'{rq/c_:.1f}' if c_ else '-'
    krs = f'{kr:,}' if kr else '<span class="muted">미정산</span>'
    ims = f'{im:,}' if im else '<span class="muted">-</span>'
    funnel_rows.append(row([f'{d[5:]} ({wd(d)})', f'{p_:,}', f'{c_:,}', clr, f'{rq:,}', ims, krs, per]))
funnel_rows.append(row(['<b>누적</b>', f'<b>{tot_pv:,}</b>', f'<b>{tot_cl:,}</b>',
                        f'<b>{tot_cl/tot_pv*100:.0f}%</b>', f'<b>{tot_req:,}</b>', f'<b>{tot_imp:,}</b>',
                        f'<b>{tot_krw:,}</b>', f'<b>{tot_req/tot_cl:.1f}</b>']))

# 게임별 일자 테이블
game_rows = []
for d in dates:
    g = game_daily[d]
    game_rows.append(row([f'{d[5:]} ({wd(d)})', f'{g.get("bbada",0):,}', f'{g.get("4taja",0):,}', f'{g.get("fishing",0):,}',
                          f'{sum(g.values()):,}']))
gt = sum(game_tot.values())
game_rows.append(row(['<b>누적</b>', f'<b>{game_tot["bbada"]:,}</b>', f'<b>{game_tot["4taja"]:,}</b>',
                      f'<b>{game_tot["fishing"]:,}</b>', f'<b>{gt:,}</b>']))

# 게임별 요약
gsum_rows = []
for g in ['bbada', '4taja', 'fishing']:
    cval = game_tot[g]; uval = len(game_users[g])
    gsum_rows.append(row([GAME[g], f'{cval:,}', f'{uval:,}', f'{cval/uval:.2f}', f'{cval/gt*100:.1f}%']))

# 광고유형
adtype_rows = []
for t in ['이어하기', '다시하기']:
    rq = sum(adtype_daily[d][t][0] for d in dates)
    im = sum(adtype_daily[d][t][1] for d in dates)
    kr = sum(adtype_daily[d][t][2] for d in dates)
    ec = f'{kr/im*1000:,.0f}' if im else '-'
    adtype_rows.append(row([t, f'{rq:,}', f'{im:,}', f'{kr:,}', ec, f'{kr/tot_krw*100:.1f}%']))

HTML = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>미니게임 분석 리포트 ({START}~{END})</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{{--bg:#0f1117;--card:#1a1d27;--line:#2a2e3a;--txt:#e6e8ee;--mut:#8b90a0;--acc:#5b8def;--g1:#f2a154;--g2:#5b8def;--g3:#4fcfa0;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font-family:'Pretendard',-apple-system,Segoe UI,sans-serif;line-height:1.5;padding:24px}}
.wrap{{max-width:1080px;margin:0 auto}}
h1{{font-size:24px;margin:0 0 4px}}
h2{{font-size:18px;margin:32px 0 12px;border-left:3px solid var(--acc);padding-left:10px}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:20px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.card .k{{color:var(--mut);font-size:12px}}
.card .v{{font-size:22px;font-weight:700;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:13px;margin-bottom:8px}}
th,td{{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line)}}
th:first-child,td:first-child{{text-align:left}}
thead th{{background:#222633;color:var(--mut);font-weight:600;position:sticky;top:0}}
tr:last-child td,tr:last-child th{{border-bottom:none}}
.muted{{color:var(--mut)}}
.chart{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}}
.note{{background:#1a1d27;border:1px solid var(--line);border-left:3px solid var(--g1);border-radius:6px;padding:12px 14px;font-size:13px;color:#cdd2de;margin:8px 0}}
.ins li{{margin:6px 0}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:760px){{.cards{{grid-template-columns:repeat(2,1fr)}}.grid2{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">

<h1>미니게임 분석 리포트</h1>
<div class="sub">기간 {START} ~ {END} · 미니게임 도입 5/27 · 데이터: 에어브릿지(pv/c_minigame) + 애드팝콘(MAX) · 생성 {date.today()}</div>

<div class="cards">
  <div class="card"><div class="k">PV 유저(누적)</div><div class="v">{tot_pv:,}</div></div>
  <div class="card"><div class="k">클릭 유저(누적)</div><div class="v">{tot_cl:,}</div></div>
  <div class="card"><div class="k">광고매출(누적·정산분)</div><div class="v">{tot_krw:,}원</div></div>
  <div class="card"><div class="k">클릭유저 인당 광고시청</div><div class="v">{tot_req/tot_cl:.1f}회</div></div>
</div>

<div class="note">퍼널 단위 주의 — <b>PV/클릭 = 유니크 유저</b>, <b>광고 시청시도/노출 = 이벤트 횟수</b>. 6/3 노출·매출은 MAX 당일 미정산.</div>

<h2>1. 통합 퍼널 (일자별)</h2>
<div class="chart"><canvas id="cFunnel" height="110"></canvas></div>
<table><thead>{row(['날짜','PV유저','클릭유저','PV→클릭','광고시청시도','노출','매출(원)','인당시청'], bold=True)}</thead>
<tbody>{''.join(funnel_rows)}</tbody></table>

<h2>2. 게임별 클릭 (일자별)</h2>
<div class="chart"><canvas id="cGame" height="110"></canvas></div>
<div class="grid2">
<table><thead>{row(['날짜','야구빠따','4번타자','낚시하기','합계'], bold=True)}</thead><tbody>{''.join(game_rows)}</tbody></table>
<table><thead>{row(['게임','클릭수','유니크유저','인당클릭','점유율'], bold=True)}</thead><tbody>{''.join(gsum_rows)}</tbody></table>
</div>
<div class="note">게임 라벨: bbada=야구빠따 키우기, 4taja=오늘의 4번타자, fishing=응모권 낚시하기. (web c_minigame 0 — app 전용 집계)</div>

<h2>3. 광고 유형별 (이어하기 vs 다시하기)</h2>
<table><thead>{row(['유형','시청시도','노출','매출(원)','eCPM','매출비중'], bold=True)}</thead><tbody>{''.join(adtype_rows)}</tbody></table>
<div class="note">매출은 게임별 분리 불가 — 광고 placement에 게임명 미포함. placement는 이어하기/다시하기 트리거 구분만.</div>

<h2>4. 인사이트</h2>
<ul class="ins">
<li><b>PV→클릭 전환 65~75%</b> — 미니게임 진입 시 2/3+ 게임 클릭. 진입 동기 강함.</li>
<li><b>클릭유저 인당 광고 ~5.5회 시청시도</b> — 게임 1세션에 이어하기 반복. 미니게임 = 광고 노출 증폭기.</li>
<li><b>PV가 매출 선행</b> — PV 최고일(6/1 1,025) = 매출 최고(46,807원). PV 유입이 천장.</li>
<li><b>야구빠따 압도</b> — 클릭 46%, 인당 2.99회(최다 반복). 4번타자 2위. 낚시하기 최약(인당 1.98).</li>
<li><b>이어하기가 매출 85%</b> — 다시하기 eCPM 더 높으나 노출량 적음. 다시하기 노출 확대 여지.</li>
</ul>

<h2>5. 액션</h2>
<ul class="ins">
<li><b>P0 — PV 유입 확대</b>: PV가 매출 상한. 홈 진입 동선/배너 노출 강화가 ROI 최대 (PV→클릭은 이미 높아 개선 여지 적음).</li>
<li><b>P1 — 게임별 매출 계측</b>: 광고 placement_name에 게임 식별자 추가 요청 → 게임별 ROI 분석 가능.</li>
<li><b>P2 — 낚시하기 개선/대체</b>: 재방문 약함(인당 1.98). 보상·게임성 개선 또는 신규 게임 교체 검토.</li>
</ul>

<script>
const cm='#8b90a0';
Chart.defaults.color=cm;Chart.defaults.borderColor='#2a2e3a';
new Chart(document.getElementById('cFunnel'),{{type:'bar',data:{{labels:{labels},
datasets:[
{{label:'PV유저',data:{pv_arr},backgroundColor:'#5b8def'}},
{{label:'클릭유저',data:{cl_arr},backgroundColor:'#4fcfa0'}},
{{type:'line',label:'매출(원)',data:{krw_arr},borderColor:'#f2a154',backgroundColor:'#f2a154',yAxisID:'y1',tension:.3}}
]}},options:{{responsive:true,plugins:{{legend:{{labels:{{color:'#e6e8ee'}}}}}},
scales:{{y:{{position:'left',title:{{display:true,text:'유저'}}}},y1:{{position:'right',grid:{{drawOnChartArea:false}},title:{{display:true,text:'매출(원)'}}}}}}}}}});

new Chart(document.getElementById('cGame'),{{type:'bar',data:{{labels:{labels},
datasets:[
{{label:'야구빠따',data:{bbada_arr},backgroundColor:'#f2a154'}},
{{label:'4번타자',data:{taja_arr},backgroundColor:'#5b8def'}},
{{label:'낚시하기',data:{fish_arr},backgroundColor:'#4fcfa0'}}
]}},options:{{responsive:true,plugins:{{legend:{{labels:{{color:'#e6e8ee'}}}}}},
scales:{{x:{{stacked:true}},y:{{stacked:true,title:{{display:true,text:'클릭수'}}}}}}}}}});
</script>
</div></body></html>"""

outpath = OUT / f"미니게임_분석_{END.replace('-','')}.html"
outpath.write_text(HTML, encoding="utf-8")
print(f"[OK] {outpath}  ({len(HTML):,} bytes)")
print(f"  PV {tot_pv:,} / 클릭 {tot_cl:,} / 매출 {tot_krw:,}원 / 게임클릭 {gt:,}")
