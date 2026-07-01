"""
에어브릿지 전체 데이터 백업 스크립트
======================================
이관 전 에어브릿지의 모든 데이터를 dashboard/backup/ 에 JSON으로 저장.

사용법:
  python dashboard/backup_airbridge.py

  # 날짜 범위 지정 (기본: 서비스 시작일 ~ 오늘)
  python dashboard/backup_airbridge.py --start 2026-03-26 --end 2026-04-10

결과:
  dashboard/backup/airbridge_backup_YYYYMMDD.json

환경변수:
  AIRBRIDGE_API_TOKEN (필수)
"""

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

# ── 설정 ──────────────────────────────────────────────────────
SERVICE_START = "2026-03-26"
# 이관 완료 → primary 앱코드 = polyballkr. main()이 넘기는 polyballkr 토큰과 일치해야 함.
# 환경변수 AIRBRIDGE_BACKUP_APP_CODE 로 override 가능.
_APP_CODE = os.environ.get("AIRBRIDGE_BACKUP_APP_CODE", "polyballkr")
API_URL = f"https://api.airbridge.io/reports/api/v7/apps/{_APP_CODE}/actuals/query"
BACKUP_DIR = Path(__file__).parent / "backup"

# ── 에어브릿지 API 공통 ────────────────────────────────────────

def get_token():
    t = os.environ.get("AIRBRIDGE_API_TOKEN", "")
    if not t:
        print("❌ 환경변수 AIRBRIDGE_API_TOKEN 이 설정되지 않았습니다.")
        sys.exit(1)
    return t

def ab_request(payload, token):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(API_URL, headers=headers, json=payload, timeout=30)
    if r.status_code != 200:
        print(f"  ⚠️  API 오류 {r.status_code}: {r.text[:200]}")
        return None
    result = r.json()
    task = result.get("task", {})
    if task.get("status") == "SUCCESS":
        return result
    task_id = task.get("taskId")
    if not task_id:
        return None
    for _ in range(60):
        time.sleep(1)
        r2 = requests.get(f"{API_URL}/{task_id}", headers=headers, timeout=30)
        if r2.status_code != 200:
            continue
        result2 = r2.json()
        s = result2.get("task", {}).get("status")
        if s == "SUCCESS":
            return result2
        if s == "FAILURE":
            return None
    return None

def parse_rows_by_date(result):
    """날짜별 {metric: value} 딕셔너리 반환"""
    if not result:
        return {}
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals", {})
    rows = actuals.get("data", {}).get("rows", [])
    out = {}
    for row in rows:
        gbs = row.get("groupBys", [])
        date_str = gbs[0] if gbs else ""
        if not date_str:
            continue
        vals = row.get("values", {})
        out[date_str] = {k: int(v.get("value", 0)) for k, v in vals.items()}
    return out

def parse_rows_raw(result):
    """groupBy 포함 원본 rows 반환"""
    if not result:
        return []
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals", {})
    rows = actuals.get("data", {}).get("rows", [])
    out = []
    for row in rows:
        gbs = row.get("groupBys", [])
        vals = row.get("values", {})
        out.append({
            "groupBys": gbs,
            "values": {k: int(v.get("value", 0)) for k, v in vals.items()}
        })
    return out

# ── 데이터 타입별 fetch ────────────────────────────────────────

def fetch_daily_metrics(token, start, end):
    """DAU, 가입, 예측완료, 응모완료"""
    print(f"  [1/5] 데일리 메트릭 조회 ({start} ~ {end})...")
    metrics = [
        "app_active_users", "web_open_users",
        "web_custom_users_signup", "app_custom_users_signup",
        "web_custom_users_c_match_prediction_completed",
        "app_custom_users_c_match_prediction_completed",
        "web_custom_users_pv_match_application_completed",
        "app_custom_users_pv_match_application_completed",
    ]
    payload = {
        "from": start, "to": end,
        "metrics": metrics,
        "groupBys": ["event_date"], "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": False,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 500,
    }
    result = ab_request(payload, token)
    data = parse_rows_by_date(result)
    print(f"       → {len(data)}일치 수신")
    return data

def fetch_funnel_metrics(token, start, end):
    """온보딩 퍼널 단계별"""
    print(f"  [2/5] 퍼널 메트릭 조회 ({start} ~ {end})...")
    metrics = [
        "web_custom_users_pv_ob_intro",
        "web_custom_users_c_ob_intro_start",
        "web_custom_users_pv_ob_team_choice",
        "web_custom_users_c_ob_team_choice_completed",
        "web_custom_users_pv_ob_team_choice_completed",
        "web_custom_users_pv_ob_match_choice_completed",
        "web_custom_users_signup",
        "web_custom_users_c_match_prediction",
    ]
    payload = {
        "from": start, "to": end,
        "metrics": metrics,
        "groupBys": ["event_date"], "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": False,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 500,
    }
    result = ab_request(payload, token)
    data = parse_rows_by_date(result)
    print(f"       → {len(data)}일치 수신")
    return data

def date_range(start, end):
    """start ~ end 날짜 리스트 반환 (YYYY-MM-DD)"""
    from datetime import date as _date, timedelta as _td
    s = _date.fromisoformat(start)
    e = _date.fromisoformat(end)
    cur = s
    while cur <= e:
        yield str(cur)
        cur += _td(days=1)

def fetch_channel_metrics(token, start, end):
    """채널별 클릭 + 가입 — 날짜별 1일씩 쿼리해 100행 제한 우회"""
    print(f"  [3/5] 채널별 메트릭 조회 ({start} ~ {end})...")
    all_rows = []
    for d in date_range(start, end):
        payload = {
            "from": d, "to": d,
            "metrics": ["clicks", "web_custom_users_signup", "app_custom_users_signup"],
            "groupBys": ["event_date", "channel", "campaign"],
            "filters": [],
            "sorts": [{"fieldName": "event_date", "isAscending": True}],
            "isSummaryAvailable": False,
            "option": {"eventTimestampSource": "event_occurred_date"},
            "size": 100,
        }
        result = ab_request(payload, token)
        rows = parse_rows_raw(result)
        all_rows.extend(rows)
        print(f"       {d}: {len(rows)}행 (누적 {len(all_rows)}행)")
    print(f"       → 총 {len(all_rows)}행 완료")
    return all_rows

def fetch_app_install_channels(token, start, end):
    """앱 인스톨 채널별 — 날짜별 1일씩 쿼리해 100행 제한 우회"""
    print(f"  [4/5] 앱 인스톨 채널 조회 ({start} ~ {end})...")
    all_rows = []
    for d in date_range(start, end):
        payload = {
            "from": d, "to": d,
            "metrics": ["app_install_users"],
            "groupBys": ["event_date", "channel", "campaign", "ad_group", "ad_creative"],
            "filters": [],
            "sorts": [{"fieldName": "event_date", "isAscending": True}],
            "isSummaryAvailable": False,
            "option": {"eventTimestampSource": "event_occurred_date"},
            "size": 100,
        }
        result = ab_request(payload, token)
        rows = parse_rows_raw(result)
        all_rows.extend(rows)
        print(f"       {d}: {len(rows)}행 (누적 {len(all_rows)}행)")
    print(f"       → 총 {len(all_rows)}행 완료")
    return all_rows

def fetch_app_funnel(token, start, end):
    """앱 퍼널"""
    print(f"  [5/5] 앱 퍼널 조회 ({start} ~ {end})...")
    metrics = [
        "app_custom_users_signup",
        "app_custom_users_c_match_prediction_completed",
        "app_custom_users_pv_match_application_completed",
    ]
    payload = {
        "from": start, "to": end,
        "metrics": metrics,
        "groupBys": ["event_date"], "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": False,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 500,
    }
    result = ab_request(payload, token)
    data = parse_rows_by_date(result)
    print(f"       → {len(data)}일치 수신")
    return data

# ── AU (기간 유니크) 조회 ──────────────────────────────────────

def fetch_period_au(token, start, end):
    """기간 내 유니크 AU — groupBys:[] 로 중복 제거된 단일 숫자 반환"""
    payload = {
        "from": start, "to": end,
        "metrics": ["app_active_users", "web_open_users"],
        "groupBys": [], "filters": [],
        "isSummaryAvailable": False,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 1,
    }
    result = ab_request(payload, token)
    if not result:
        return {"app_active_users": 0, "web_open_users": 0}
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals", {})
    rows = actuals.get("data", {}).get("rows", [])
    if not rows:
        # summary에 있을 수도
        total = actuals.get("data", {}).get("total", {})
        return {
            "app_active_users": int(total.get("app_active_users", {}).get("value", 0)),
            "web_open_users":   int(total.get("web_open_users",   {}).get("value", 0)),
        }
    vals = rows[0].get("values", {})
    return {
        "app_active_users": int(vals.get("app_active_users", {}).get("value", 0)),
        "web_open_users":   int(vals.get("web_open_users",   {}).get("value", 0)),
    }

def fetch_au_snapshots(token, start, end):
    """
    일별 DAU + 주별 WAU + 월별 MAU + 전체 AU 수집
    start ~ end 기간 기준으로 자동 계산
    """
    from datetime import date as _date, timedelta as _td

    s = _date.fromisoformat(start)
    e = _date.fromisoformat(end)

    # ── 주별 WAU (월~일 기준, 서비스 시작일/오늘로 클리핑)
    weekly_au = {}
    # 첫 번째 월요일 찾기
    first_monday = s - _td(days=s.weekday())  # s가 속한 주 월요일
    week_start = first_monday
    while week_start <= e:
        week_end = week_start + _td(days=6)
        ws = max(week_start, s)
        we = min(week_end, e)
        label = f"{ws}~{we}"
        r = fetch_period_au(token, str(ws), str(we))
        weekly_au[label] = {
            "wau_app": r["app_active_users"],
            "wau_web": r["web_open_users"],
            "wau_total": r["app_active_users"] + r["web_open_users"],
            "week_start": str(ws),
            "week_end": str(we),
        }
        print(f"       WAU {label}: app={r['app_active_users']} web={r['web_open_users']}")
        week_start += _td(days=7)

    # ── 월별 MAU
    monthly_au = {}
    import calendar as _cal
    cur_year, cur_month = s.year, s.month
    while True:
        month_start = _date(cur_year, cur_month, 1)
        last_day = _cal.monthrange(cur_year, cur_month)[1]
        month_end = _date(cur_year, cur_month, last_day)
        ms = max(month_start, s)
        me = min(month_end, e)
        if ms > e:
            break
        label = f"{cur_year}-{cur_month:02d}"
        r = fetch_period_au(token, str(ms), str(me))
        monthly_au[label] = {
            "mau_app": r["app_active_users"],
            "mau_web": r["web_open_users"],
            "mau_total": r["app_active_users"] + r["web_open_users"],
            "period_start": str(ms),
            "period_end": str(me),
        }
        print(f"       MAU {label}: app={r['app_active_users']} web={r['web_open_users']}")
        if cur_month == 12:
            cur_year += 1
            cur_month = 1
        else:
            cur_month += 1
        if _date(cur_year, cur_month, 1) > e:
            break

    # ── 전체 기간 AU
    print(f"       전체 AU {start}~{end}...")
    total_r = fetch_period_au(token, start, end)
    total_au = {
        "au_app": total_r["app_active_users"],
        "au_web": total_r["web_open_users"],
        "au_total": total_r["app_active_users"] + total_r["web_open_users"],
        "period_start": start,
        "period_end": end,
    }
    print(f"       전체: app={total_r['app_active_users']} web={total_r['web_open_users']}")

    return {
        "weekly_wau": weekly_au,
        "monthly_mau": monthly_au,
        "total_au": total_au,
    }

# ── 메인 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="에어브릿지 전체 데이터 백업")
    parser.add_argument("--start", default=SERVICE_START, help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", default=str(date.today()), help="종료일 (YYYY-MM-DD)")
    args = parser.parse_args()

    start, end = args.start, args.end
    token = get_token()

    print(f"\n{'='*55}")
    print(f" 폴리볼 에어브릿지 전체 백업")
    print(f" 기간: {start} ~ {end}")
    print(f"{'='*55}\n")

    # 데이터 수집
    daily    = fetch_daily_metrics(token, start, end)
    funnel   = fetch_funnel_metrics(token, start, end)
    channels = fetch_channel_metrics(token, start, end)
    installs = fetch_app_install_channels(token, start, end)
    app_fn   = fetch_app_funnel(token, start, end)

    print(f"  [6/6] DAU/WAU/MAU 유니크 AU 스냅샷 조회...")
    au_snapshots = fetch_au_snapshots(token, start, end)

    # 검증 출력
    print("\n" + "─"*45)
    print("■ 수신 데이터 요약 (이 숫자를 에어브릿지 UI와 대조하세요)")
    print("─"*45)
    if daily:
        total_signup = sum(
            v.get("web_custom_users_signup", 0) + v.get("app_custom_users_signup", 0)
            for v in daily.values()
        )
        total_dau = sum(
            v.get("web_open_users", 0) + v.get("app_active_users", 0)
            for v in daily.values()
        )
        print(f"  데일리 : {len(daily)}일 | 누적가입 {total_signup:,}명 | 누적DAU합산 {total_dau:,}")
    if channels:
        print(f"  채널   : {len(channels)}행")
    if installs:
        total_inst = sum(r["values"].get("app_install_users", 0) for r in installs)
        print(f"  앱설치 : {len(installs)}행 | 누적 {total_inst:,}건")
    if au_snapshots:
        t = au_snapshots["total_au"]
        print(f"  전체AU : {t['au_total']:,} (앱 {t['au_app']:,} / 웹 {t['au_web']:,}) ← 유니크")
        print(f"  WAU    : {len(au_snapshots['weekly_wau'])}주 저장")
        print(f"  MAU    : {len(au_snapshots['monthly_mau'])}개월 저장")
    print("─"*45)

    # 저장
    BACKUP_DIR.mkdir(exist_ok=True)
    today_str = str(date.today()).replace("-", "")
    out_path = BACKUP_DIR / f"airbridge_backup_{today_str}.json"

    backup = {
        "meta": {
            "backed_up_at": str(date.today()),
            "period_start": start,
            "period_end": end,
            "app": "polyball",
            "note": "에어브릿지 계정 이관 전 전체 백업"
        },
        "daily_metrics": daily,
        "funnel_metrics": funnel,
        "channel_metrics_raw": channels,
        "app_install_channels_raw": installs,
        "app_funnel_metrics": app_fn,
        "au_snapshots": au_snapshots,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)

    size_kb = out_path.stat().st_size / 1024
    print(f"\n[완료] 백업 완료: {out_path}")
    print(f"   파일 크기: {size_kb:.1f} KB")
    print(f"\n다음 단계:")
    print(f"  git add dashboard/backup/")
    print(f"  git commit -m 'backup: 에어브릿지 이관 전 전체 데이터 백업 {today_str}'")
    print(f"  git push origin main")
    print()

if __name__ == "__main__":
    main()
