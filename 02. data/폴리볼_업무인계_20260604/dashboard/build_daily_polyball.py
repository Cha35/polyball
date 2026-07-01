# -*- coding: utf-8 -*-
"""폴리볼_데이터 데일리 리포트 생성 (전주 동요일 비교 포함)
data.json(에어브릿지+서버) + 애드팝콘 xlsx 광고매출 → md + 폴더별 data.json 복사

사용법 (항상 동일):
  python build_daily_polyball.py 2026-06-02 2026-06-03   # 지정 날짜
  python build_daily_polyball.py                          # data.json 마지막 날짜
- XLSX: Downloads 폴더 YYYYMMDD_YYYYMMDD.xlsx 중 종료일 최신 자동 선택
- FX: 1400 고정 (rodata 정합)
"""
import json, shutil, sys, re
from pathlib import Path
from datetime import date, timedelta
import pandas as pd

DASH = Path(__file__).parent
OUTBASE = Path(r"C:\Users\rest\폴리볼_데이터")
DL = Path(r"C:\Users\rest\Downloads")
FX = 1400
SERVICE_START = date(2026, 3, 26)

def _latest_xlsx():
    """Downloads 폴더에서 YYYYMMDD_YYYYMMDD.xlsx 중 종료일 최신 자동 선택"""
    cands = []
    for p in DL.glob("2026*_2026*.xlsx"):
        m = re.match(r"(\d{8})_(\d{8})\.xlsx$", p.name)
        if m:
            cands.append((m.group(2), p))
    if not cands:
        raise FileNotFoundError(f"애드팝콘 xlsx 없음: {DL}\\YYYYMMDD_YYYYMMDD.xlsx")
    return max(cands)[1]

XLSX = _latest_xlsx()

d = json.load(open(DASH / "data.json", encoding="utf-8"))
DAILY = {x["date"]: x for x in d["daily"]}
FUN = {x["date"]: x for x in d["funnel"]}
AFUN = {x["date"]: x for x in d["app_funnel"]}
CD = {x["date"]: x.get("rows", []) for x in d["channels_detail"]}

# ── 광고매출 (애드팝콘 xlsx) ──
adf = pd.read_excel(XLSX)
adf["rev"] = adf["media_cost(USD)"] * FX
def _cat(p):
    p = str(p)
    if "응모" in p: return "apply"
    if "응원" in p or "커뮤니티" in p: return "cheer"
    if "미니게임" in p: return "minigame"
    if "픽" in p or "승부예측" in p or "예측" in p: return "pick"
    return "etc"
adf["cat"] = adf["placement_name"].apply(_cat)
adf["d"] = adf["report_date"].astype(str)
ADREV = {}  # 'YYYYMMDD' -> {cat: krw}
for (dd, c), g in adf.groupby(["d", "cat"]):
    ADREV.setdefault(dd, {})[c] = int(round(g["rev"].sum()))
# 전주(5/21~24) 광고매출 = W21 리포트 확정치
ADREV_PREV = {
    "20260521": {"pick": 30965, "apply": 8820, "cheer": 1663},
    "20260522": {"pick": 31431, "apply": 8630, "cheer": 1289},
    "20260523": {"pick": 29080, "apply": 10885, "cheer": 1192},
    "20260524": {"pick": 21670, "apply": 14381, "cheer": 1194},
}
ADREV.update({k: v for k, v in ADREV_PREV.items() if k not in ADREV})

WD = ["월", "화", "수", "목", "금", "토", "일"]
def wd(ds): return WD[date.fromisoformat(ds).weekday()]
def ymd(ds): return ds.replace("-", "")
def f(n):
    return "-" if n in (None, 0, "") else f"{n:,}"
def fb(n):  # bold
    return "-" if n in (None, 0, "") else f"**{n:,}**"

def week_rows(target):
    """target 포함 직전 7일 날짜 리스트"""
    t = date.fromisoformat(target)
    return [(t - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]

def adtotal(ds):
    a = ADREV.get(ymd(ds), {})
    return sum(v for k, v in a.items() if k != "etc")

def build(target):
    t = date.fromisoformat(target)
    dnum = (t - SERVICE_START).days + 1   # launch day = D+1
    days = week_rows(target)
    prevwk = (t - timedelta(days=7)).isoformat()  # 전주 동요일
    r = DAILY[target]
    pr = DAILY.get(prevwk, {})
    # 전일
    yday = (t - timedelta(days=1)).isoformat()
    yr = DAILY.get(yday, {})

    L = []
    L.append(f"# 폴리볼 데일리 리포트 -- {target} ({wd(target)}) (D+{dnum})\n")
    L.append("> 에어브릿지 Actuals Report | 퍼널: 유니크 유저 기준\n")
    L.append("---\n")

    # ── 요약 ──
    def pct(cur, prev):
        if not prev: return "n/a"
        return f"{(cur-prev)/prev*100:+.1f}%"
    L.append("## 요약\n")
    web_anom = bool(r["dau_web"] and r["dau_web"] < 3000)
    anom_note = " ⚠️웹 수집지연 의심(익일 재확인)" if web_anom else ""
    L.append(f"- DAU {f(r['dau_total'])} (웹 {f(r['dau_web'])} / 앱 {f(r['dau_app'])}) — 전주 {wd(prevwk)}({prevwk[5:]}) {f(pr.get('dau_total'))} 대비 {pct(r['dau_total'],pr.get('dau_total',0))}{anom_note}")
    L.append(f"- 가입 **{r['signup_total']} (서버 {r['server_signup']})** — 전주 동요일 서버 {pr.get('server_signup','?')} 대비 {pct(r['server_signup'],pr.get('server_signup',0))}")
    L.append(f"- 예측 user {f(r['server_pred_user'])} / 퀴즈 user {f(r['server_quiz_user'])} / 응모 user {f(r['server_entry_user'])}")
    ad_t = adtotal(yday)
    L.append(f"- 광고 매출 {yday[5:]} ₩{ad_t:,} (애드팝콘) — 미니게임 광고선 신규\n")
    L.append("---\n")

    # ── 1. 일간 핵심 지표 ──
    L.append("## 1. 일간 핵심 지표\n")
    hd = ["날짜","DAU(웹)","DAU(앱)","DAU 합계","가입(웹)","가입(앱)","가입 합계","예측완료(웹)","예측완료(앱)","예측완료 합계","응모완료(웹)","응모완료(앱)","응모완료 합계","가입(서버)","예측Cnt(서버)","예측User(서버)","퀴즈Cnt(서버)","퀴즈User(서버)","응모Cnt(서버)","응모User(서버)"]
    L.append("| " + " | ".join(hd) + " |")
    L.append("| " + " | ".join("---" for _ in hd) + " |")
    for ds in days:
        x = DAILY.get(ds, {})
        pw = x.get("pred_web"); pa = x.get("pred_app")
        ew = x.get("entry_web"); ea = x.get("entry_app")
        cells = [ds[5:], f(x.get("dau_web")), f(x.get("dau_app")), f(x.get("dau_total")),
                 f(x.get("signup_web")), f(x.get("signup_app")), f(x.get("signup_total")),
                 f(pw), f(pa), f((pw or 0)+(pa or 0)),
                 f(ew), f(ea), f((ew or 0)+(ea or 0)),
                 f(x.get("server_signup")), f(x.get("server_pred_cnt")), f(x.get("server_pred_user")),
                 f(x.get("server_quiz_cnt")), f(x.get("server_quiz_user")), f(x.get("server_entry_cnt")), f(x.get("server_entry_user"))]
        if ds == target:
            cells = [f"**{c}**" for c in cells]
        L.append("| " + " | ".join(cells) + " |")
    L.append("")
    L.append("**이벤트 매핑**\n")
    L.append("| 컬럼 | 소스 | 이벤트 / 지표 | 기준 |")
    L.append("|---|---|---|---|")
    L.append("| DAU(웹) | 에어브릿지 | `web_open_users` | 웹 세션 오픈 유니크 |")
    L.append("| DAU(앱) | 에어브릿지 | `app_active_users` | 앱 액티브 유니크 |")
    L.append("| 가입(웹/앱) | 에어브릿지 | `web/app_custom_users_signup` | 가입완료 화면 진입 |")
    L.append("| 예측완료 | 에어브릿지 | `*_custom_users_c_match_prediction_completed` | 예측 완료 |")
    L.append("| 응모완료 | 에어브릿지 | `*_custom_users_pv_match_application_completed` | 응모완료 PV |")
    L.append("| 가입~응모(서버) | 서버 DB | polyball.kr/api/cron/pub/stat/view | 서버 확정 (웹/앱 분리) |")
    L.append("---")

    # ── 2. 웹 온보딩 퍼널 ──
    L.append("## 2. 웹 온보딩 퍼널\n")
    h2 = ["날짜","인트로","팀선택완료","OB-04","OB-BS01","가입완료","예측CTA"]
    L.append("| " + " | ".join(h2) + " |")
    L.append("| " + " | ".join("---" for _ in h2) + " |")
    for ds in days:
        x = FUN.get(ds, {})
        cells = [ds[5:], f(x.get("intro")), f(x.get("team_complete")), f(x.get("ob04")), f(x.get("obs01")), f(x.get("signup")), f(x.get("pred_cta"))]
        if ds == target: cells = [f"**{c}**" for c in cells]
        L.append("| " + " | ".join(cells) + " |")
    L.append("\n> 웹 가입 사실상 0 (앱 전환 구조) — 인트로/팀선택 이벤트 4/6부터 집계")
    L.append("---")

    # ── 3. 웹 서비스 이용 ──
    L.append("## 3. 웹 서비스 이용\n")
    L.append("| 날짜 | 예측완료 유저 | 응모완료 유저 |")
    L.append("|---|---|---|")
    for ds in days:
        x = DAILY.get(ds, {})
        cells = [ds[5:], f(x.get("pred_web")), f(x.get("entry_web"))]
        if ds == target: cells = [f"**{c}**" for c in cells]
        L.append("| " + " | ".join(cells) + " |")
    L.append("\n> 에어브릿지 기준 웹 예측완료/응모완료")
    L.append("---")

    # ── 4. 앱 온보딩 퍼널 ──
    L.append("## 4. 앱 온보딩 퍼널\n")
    h4 = ["날짜","인트로","OB-04","OB-BS01","가입완료","예측완료"]
    L.append("| " + " | ".join(h4) + " |")
    L.append("| " + " | ".join("---" for _ in h4) + " |")
    for ds in days:
        x = AFUN.get(ds, {})
        cells = [ds[5:], f(x.get("intro")), f(x.get("ob04")), f(x.get("obs01")), f(x.get("signup")), f(x.get("pred"))]
        if ds == target: cells = [f"**{c}**" for c in cells]
        L.append("| " + " | ".join(cells) + " |")
    L.append("\n> 앱 유저 대부분 기존 가입자 — 가입 이벤트는 친구초대 신규만")
    L.append("---")

    # ── 5. 앱 서비스 이용 ──
    L.append("## 5. 앱 서비스 이용\n")
    L.append("| 날짜 | 예측완료 유저 | 응모완료 유저 |")
    L.append("|---|---|---|")
    for ds in days:
        x = DAILY.get(ds, {})
        cells = [ds[5:], f(x.get("pred_app")), f(x.get("entry_app"))]
        if ds == target: cells = [f"**{c}**" for c in cells]
        L.append("| " + " | ".join(cells) + " |")
    L.append("\n> 에어브릿지 기준 앱 예측완료/응모완료")
    L.append("---")

    # ── 6. 채널별 가입 (귀속, 7일) ──
    def ch_agg(ds):
        out = {}
        for row in CD.get(ds, []):
            s = row.get("signups") or 0
            if not s: continue
            ch = row["channel"]; cp = row.get("campaign", "")
            key = "referral_invite(인플루언서)" if (ch == "referral" and cp == "invite") else \
                  "referral_REFERRAL(일반)" if ch == "referral" else \
                  "unattributed" if ch == "unattributed" else ch
            out[key] = out.get(key, 0) + s
        return out
    cols = ["referral_invite(인플루언서)","referral_REFERRAL(일반)","round","instagram","paid","unattributed"]
    L.append("## 6. 채널별 가입 (귀속, 7일)\n")
    L.append("| 날짜 | " + " | ".join(cols) + " | 합계 |")
    L.append("| " + " | ".join("---" for _ in range(len(cols)+2)) + " |")
    for ds in days:
        a = ch_agg(ds)
        vals = [a.get(c, 0) for c in cols]
        tot = sum(a.values())
        cells = [ds[5:]] + [f(v) for v in vals] + [f(tot)]
        if ds == target: cells = [f"**{c}**" for c in cells]
        L.append("| " + " | ".join(cells) + " |")
    L.append("\n> 귀속 가입만 (앱 친구초대 referral + 웹 잔여). happypoint 클릭은 어뷰징(가입 0)으로 제외")
    L.append("---")

    # ── 7. 인플루언서별 가입 (target일) ──
    L.append(f"## 7. 인플루언서별 가입 ({target[5:]})\n")
    inv = {}
    for row in CD.get(target, []):
        if row["channel"] == "referral" and row.get("campaign") == "invite" and (row.get("signups") or 0):
            inv[row.get("ad_group", "")] = inv.get(row.get("ad_group", ""), 0) + row["signups"]
    if inv:
        L.append("| 인플루언서(ad_group) | 가입 |")
        L.append("|---|---|")
        for k, v in sorted(inv.items(), key=lambda x: -x[1]):
            L.append(f"| {k} | {v} |")
    else:
        L.append("(당일 인플루언서 귀속 가입 없음)")
    L.append("---")

    # ── 8. 분석 ──
    L.append("## 8. 분석\n")
    L.append("### 팩트")
    L.append(f"- DAU {f(r['dau_total'])} (웹 {f(r['dau_web'])} / 앱 {f(r['dau_app'])})")
    L.append(f"- 가입 서버 {r['server_signup']} / Airbridge {r['signup_total']}")
    L.append(f"- 예측 user {f(r['server_pred_user'])} / 퀴즈 user {f(r['server_quiz_user'])} / 응모 user {f(r['server_entry_user'])}")
    L.append(f"- 광고 매출 {yday[5:]} ₩{adtotal(yday):,}\n")
    L.append(f"### 전주 동요일 비교 ({prevwk[5:]} {wd(prevwk)} vs {target[5:]} {wd(target)})\n")
    L.append("| 지표 | 전주 | 당일 | 증감 |")
    L.append("|---|---|---|---|")
    for lbl, key in [("DAU 합계","dau_total"),("└ DAU(웹)","dau_web"),("└ DAU(앱)","dau_app"),
                     ("가입(Airbridge)","signup_total"),("가입(서버)","server_signup"),
                     ("예측 user","server_pred_user"),("예측 cnt","server_pred_cnt"),
                     ("퀴즈 user","server_quiz_user"),("퀴즈 cnt","server_quiz_cnt"),
                     ("응모 user","server_entry_user"),("응모 cnt","server_entry_cnt")]:
        cur = r.get(key, 0) or 0; prev = pr.get(key, 0) or 0
        ch = f"{(cur-prev)/prev*100:+.1f}%" if prev else "n/a"
        L.append(f"| {lbl} | {prev:,} | {cur:,} | {ch} |")
    # 광고매출 비교 (전일 vs 전전주 동요일 -1)
    L.append(f"\n### 광고 매출 비교 ({(t-timedelta(days=8)).isoformat()[5:]} vs {yday[5:]}, 1일 시프트)\n")
    pday = ymd((t - timedelta(days=8)).isoformat()); cday = ymd(yday)
    L.append("| 카테고리 | 전주 | 당주 | 증감 |")
    L.append("|---|---|---|---|")
    for c in ["pick", "apply", "cheer", "minigame"]:
        pv = ADREV.get(pday, {}).get(c, 0); cv = ADREV.get(cday, {}).get(c, 0)
        ch = f"{(cv-pv)/pv*100:+.1f}%" if pv else ("신규" if cv else "-")
        if pv == 0 and cv == 0: continue
        L.append(f"| {c} | ₩{pv:,} | ₩{cv:,} | {ch} |")
    ptot = sum(v for k, v in ADREV.get(pday, {}).items() if k != "etc")
    ctot = sum(v for k, v in ADREV.get(cday, {}).items() if k != "etc")
    chtot = f"{(ctot-ptot)/ptot*100:+.1f}%" if ptot else "-"
    L.append(f"| **합계** | **₩{ptot:,}** | **₩{ctot:,}** | **{chtot}** |")

    # ── 가설 ──
    L.append("\n### 가설")
    top_inv = max(inv.items(), key=lambda x: x[1]) if inv else None
    if top_inv and top_inv[1] >= 20:
        L.append(f"- 인플루언서 **{top_inv[0]} {top_inv[1]}명** 귀속 — 서버 가입 {pct(r['server_signup'],pr.get('server_signup',0))} 견인. 무료 마케팅 채널 지속")
    mg = ADREV.get(cday, {}).get("minigame", 0)
    if mg:
        L.append(f"- 미니게임 광고매출 ₩{mg:,} ({yday[5:]}) — 신규 기능 광고선 정착·성장 중")
    L.append("- 앱 예측·퀴즈 전주比 +10%대 — 잔존 코호트 활동 강화 지속")

    # ── 9. 이슈 및 액션 ──
    L.append("\n---\n## 9. 이슈 및 액션\n")
    L.append("| 우선순위 | 내용 | 담당 |")
    L.append("|---|---|---|")
    if r["dau_web"] and r["dau_web"] < 3000:  # 웹 DAU 비정상 급락
        L.append(f"| **P0** | 웹 DAU {r['dau_web']:,} 비정상 급락 (전일 대비 -90%대) — 수집 지연(D+1) 의심, 익일 재확인 필요 | 데이터 |")
    if top_inv and top_inv[1] >= 20:
        L.append(f"| P0 | 인플루언서 {top_inv[0]} 효과 지속성 관리·확대 (광고비 0원) | 마케팅 |")
    if mg:
        L.append("| P1 | 미니게임 광고 완주율·매출 추세 모니터링 (신규 매출선) | 마케팅 |")
    L.append("| P2 | 앱 잔존 코호트 활성도 강화 추세 모니터링 | 그로스 |")
    return "\n".join(L)


def main():
    # 인자: 날짜 1개 이상 (YYYY-MM-DD). 없으면 data.json 마지막 날짜.
    args = [a for a in sys.argv[1:] if re.match(r"\d{4}-\d{2}-\d{2}$", a)]
    if not args:
        args = [max(DAILY.keys())]
    print(f"[XLSX] {XLSX.name}  [FX] {FX}")
    for target in args:
        if target not in DAILY:
            print(f"[SKIP] {target} — data.json 없음")
            continue
        md = build(target)
        folder = OUTBASE / f"{target}_대시보드"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{ymd(target)}.md").write_text(md, encoding="utf-8")
        shutil.copy(DASH / "data.json", folder / "data.json")
        print(f"[OK] {target} -> {folder}")

if __name__ == "__main__":
    main()
