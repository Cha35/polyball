"""
폴리볼 데일리 자동화 파이프라인
- 에어브릿지 API + polyball.kr 서버 데이터 수집
- 데이터 검증 (교차 검증, 정합성, 이상치)
- data.json 업데이트
- 데일리 리포트 마크다운 생성
- Claude API로 분석 섹션 자동 작성

GitHub Actions에서 매일 KST 07:00에 실행
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_JSON = SCRIPT_DIR / "data.json"
REPORT_DIR = SCRIPT_DIR.parent / "08_대시보드" / "데일리"
ANALYSIS_DIR = SCRIPT_DIR.parent / "08_대시보드"
SERVICE_START_DATE = "20260326"

KST = timezone(timedelta(hours=9))
SERVER_URL = "https://polyball.kr/api/cron/pub/stat/view"
AB_API_BASE = "https://api.airbridge.io/reports/api/v7/apps"
# 호환성: 단일 계정 호출용 (deprecated, ab_request 사용 권장)
API_URL = f"{AB_API_BASE}/polyball/actuals/query"


# ============================================================
# 에어브릿지 API (이관 기간: 두 계정 합산)
# ============================================================

def _load_secrets():
    """secrets.toml에서 토큰/앱코드 읽기 (환경변수 우선)."""
    from pathlib import Path
    secrets = {}
    secrets_path = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return secrets
    try:
        try:
            import tomllib
            with open(secrets_path, "rb") as f:
                secrets = tomllib.load(f)
        except ImportError:
            import toml
            secrets = toml.load(secrets_path)
    except Exception:
        pass
    return secrets


_SECRETS = _load_secrets()


def _get_secret(key, default=""):
    """환경변수 → secrets.toml 순서로 조회."""
    v = os.environ.get(key, "").strip()
    if v:
        return v
    return _SECRETS.get(key, default)


def get_ab_accounts():
    """에어브릿지 계정 리스트 반환: [(token, app_code), ...]
    이관 기간 = 2개, 평소 = 1개. 두 계정 모두 데이터 합산.
    """
    accounts = []
    # 신규 계정(polyballkr) = primary. 이관 거의 완료, 웹/앱 트래픽 전량 여기로 집계됨.
    # get_ab_token()(첫 계정)을 쓰는 백업/WAU·MAU 스냅샷이 살아있는 계정을 보도록 먼저 둔다.
    t2 = _get_secret("AIRBRIDGE_API_TOKEN_NEW")
    if t2:
        c2 = _get_secret("AIRBRIDGE_APP_CODE_NEW", "polyballkr")
        accounts.append((t2, c2))
    # 기존 계정(polyball) = 잔여 합산용 (이관 후 ~0). ab_request에서 함께 합산.
    t1 = _get_secret("AIRBRIDGE_API_TOKEN")
    if t1:
        c1 = _get_secret("AIRBRIDGE_APP_CODE", "polyball")
        accounts.append((t1, c1))
    return accounts


def get_ab_tokens():
    """호환성 유지: 토큰 리스트만 반환."""
    return [t for t, _ in get_ab_accounts()]


def get_ab_token():
    """호환성 유지: 첫 토큰 반환."""
    accounts = get_ab_accounts()
    return accounts[0][0] if accounts else ""


def _ab_request_single(payload, token, app_code="polyball"):
    """단일 계정 호출 (내부용)."""
    url = f"{AB_API_BASE}/{app_code}/actuals/query"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code != 200:
        return None
    result = r.json()
    task = result.get("task", {})
    if task.get("status") == "SUCCESS":
        return result
    task_id = task.get("taskId")
    if not task_id:
        return None
    for _ in range(30):
        time.sleep(0.5)
        r2 = requests.get(f"{url}/{task_id}", headers=headers, timeout=30)
        if r2.status_code != 200:
            continue
        result2 = r2.json()
        s = result2.get("task", {}).get("status")
        if s == "SUCCESS":
            return result2
        if s == "FAILURE":
            return None
    return None


def _merge_actuals(results):
    """여러 ab_request 결과를 합산 (actuals 구조).
    이관 기간 두 계정 데이터를 단순 합산. groupBys 기준으로 행 매칭.
    ⚠ 유니크 유저 메트릭은 중복 가능성 있음 (이관 기간 한정).
    """
    if len(results) == 1:
        return results[0]
    merged_rows = {}  # tuple(groupBys) -> {"groupBys": [...], "values": {metric: {value: int}}}
    merged_total = {}
    for r in results:
        actuals = r.get("actuals") or r.get("reportData", {}).get("actuals")
        if not actuals:
            continue
        data = actuals.get("data", {})
        for row in data.get("rows", []):
            gbs = tuple(row.get("groupBys", []))
            if gbs not in merged_rows:
                merged_rows[gbs] = {"groupBys": list(gbs), "values": {}}
            for k, v in row.get("values", {}).items():
                cur = merged_rows[gbs]["values"].get(k, {}).get("value", 0) or 0
                add = v.get("value", 0) or 0
                merged_rows[gbs]["values"][k] = {"value": cur + add}
        for k, v in data.get("total", {}).items():
            cur = merged_total.get(k, {}).get("value", 0) or 0
            add = v.get("value", 0) or 0
            merged_total[k] = {"value": cur + add}
    return {
        "actuals": {
            "data": {
                "rows": list(merged_rows.values()),
                "total": merged_total,
            }
        }
    }


def ab_request(payload, token=None):
    """이관 기간 두 계정 자동 합산. token 인자는 호환성 유지용 (무시됨)."""
    accounts = get_ab_accounts()
    if not accounts:
        return None
    if len(accounts) == 1:
        t, c = accounts[0]
        return _ab_request_single(payload, t, c)
    # 두 계정 호출 후 합산
    results = []
    for t, c in accounts:
        r = _ab_request_single(payload, t, c)
        if r:
            results.append(r)
    if not results:
        return None
    return _merge_actuals(results)


def ab_parse_rows(result):
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
    if not actuals:
        return {}
    rows = actuals.get("data", {}).get("rows", [])
    out = {}
    for row in rows:
        gbs = row.get("groupBys", [])
        date_str = gbs[0] if gbs else ""
        vals = row.get("values", {})
        out[date_str] = {k: int(v.get("value", 0)) for k, v in vals.items()}
    return out


def fetch_daily_metrics(token, from_date, to_date):
    metrics = [
        "app_active_users", "web_open_users",
        "web_custom_users_signup", "app_custom_users_signup",
        "web_custom_users_c_match_prediction_completed",
        "app_custom_users_c_match_prediction_completed",
        "web_custom_users_pv_match_application_completed",
        "app_custom_users_pv_match_application_completed",
    ]
    payload = {
        "from": from_date, "to": to_date,
        "metrics": metrics,
        "groupBys": ["event_date"], "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": True,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 100,
    }
    result = ab_request(payload, token)
    return ab_parse_rows(result) if result else {}


def fetch_funnel_metrics(token, from_date, to_date):
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
        "from": from_date, "to": to_date,
        "metrics": metrics,
        "groupBys": ["event_date"], "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": True,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 100,
    }
    result = ab_request(payload, token)
    return ab_parse_rows(result) if result else {}


def fetch_channel_metrics(token, from_date, to_date):
    """채널 데이터를 날짜별 개별 조회 — API size 100 제한으로 7일 한번에 조회 시 누락 발생"""
    from datetime import datetime as _dt, timedelta as _td
    d_start = _dt.strptime(from_date, "%Y-%m-%d").date()
    d_end = _dt.strptime(to_date, "%Y-%m-%d").date()

    all_out = {}
    cur = d_start
    while cur <= d_end:
        ds = cur.strftime("%Y-%m-%d")
        payload = {
            "from": ds, "to": ds,
            "metrics": ["clicks", "web_custom_users_signup", "app_custom_users_signup"],
            "groupBys": ["channel", "campaign"],
            "filters": [],
            "sorts": [],
            "isSummaryAvailable": False,
            "option": {"eventTimestampSource": "event_occurred_date"},
            "size": 100,
        }
        result = ab_request(payload, token)
        if result:
            actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
            rows = actuals.get("data", {}).get("rows", []) if actuals else []
        else:
            rows = []

        all_out[ds] = {}
        for row in rows:
            gbs = row.get("groupBys", [])
            if len(gbs) < 2:
                continue
            ch, camp = gbs[0], gbs[1]
            vals = row.get("values", {})
            cl = int(vals.get("clicks", {}).get("value", 0))
            sig = int(vals.get("web_custom_users_signup", {}).get("value", 0)) + \
                  int(vals.get("app_custom_users_signup", {}).get("value", 0))
            # channel_campaign 키 생성 ($$default$$ 제외)
            if ch == "$$default$$":
                continue
            key = f"{ch}_{camp}" if camp else ch
            if key not in all_out[ds]:
                all_out[ds][key] = {"clicks": 0, "signups": 0}
            all_out[ds][key]["clicks"] += cl
            all_out[ds][key]["signups"] += sig
        print(f"    channels {ds}: {len(rows)} rows")
        cur += _td(days=1)
    return all_out


def fetch_channels_detail(token, from_date, to_date):
    """채널 상세(ad_group/creative 포함) — 날짜별 1일씩 조회해 channels_detail 포맷으로 반환"""
    from datetime import datetime as _dt, timedelta as _td
    import time as _time
    d_start = _dt.strptime(from_date, "%Y-%m-%d").date()
    d_end   = _dt.strptime(to_date,   "%Y-%m-%d").date()

    result_by_date = {}
    cur = d_start
    while cur <= d_end:
        ds = cur.strftime("%Y-%m-%d")
        payload = {
            "from": ds, "to": ds,
            "metrics": ["clicks", "web_custom_users_signup", "app_custom_users_signup"],
            "groupBys": ["channel", "campaign", "ad_group", "ad_creative"],
            "filters": [],
            "isSummaryAvailable": False,
            "option": {"eventTimestampSource": "event_occurred_date"},
            "size": 100,
        }
        _time.sleep(0.5)  # 레이트 리밋 방지
        res = ab_request(payload, token)
        actuals = res.get("actuals") or res.get("reportData", {}).get("actuals") if res else None
        rows = actuals.get("data", {}).get("rows", []) if actuals else []

        detail_rows = []
        for row in rows:
            gbs = row.get("groupBys", [])
            ch   = gbs[0] if len(gbs) > 0 else ""
            camp = gbs[1] if len(gbs) > 1 else ""
            ag   = gbs[2] if len(gbs) > 2 else ""
            ac   = gbs[3] if len(gbs) > 3 else ""
            if ch == "$$default$$":
                continue
            vals = row.get("values", {})
            cl  = int(vals.get("clicks", {}).get("value", 0))
            sig = int(vals.get("web_custom_users_signup", {}).get("value", 0)) + \
                  int(vals.get("app_custom_users_signup", {}).get("value", 0))
            detail_rows.append({
                "channel": ch, "campaign": camp,
                "ad_group": ag, "ad_creative": ac,
                "clicks": cl, "signups": sig,
            })
        result_by_date[ds] = detail_rows
        print(f"    channels_detail {ds}: {len(detail_rows)} rows")
        cur += _td(days=1)
    return result_by_date


def fetch_app_install_channels(token, from_date, to_date):
    """앱 인스톨 채널별 데이터 — event_date + channel + campaign + ad_group + ad_creative"""
    payload = {
        "from": from_date, "to": to_date,
        "metrics": ["app_install_users"],
        "groupBys": ["event_date", "channel", "campaign", "ad_group", "ad_creative"],
        "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": False,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 500,
    }
    result = ab_request(payload, token)
    if not result:
        return []
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
    if not actuals:
        return []
    rows = actuals.get("data", {}).get("rows", [])
    out = []
    for row in rows:
        gbs = row.get("groupBys", [])
        if len(gbs) < 5:
            continue
        date_str, channel, campaign, ad_group, ad_creative = gbs[0], gbs[1], gbs[2], gbs[3], gbs[4]
        vals = row.get("values", {})
        installs = int(vals.get("app_install_users", {}).get("value", 0))
        if installs == 0:
            continue
        out.append({
            "date": date_str,
            "channel": channel or "",
            "campaign": campaign or "",
            "ad_group": ad_group or "",
            "ad_creative": ad_creative or "",
            "installs": installs,
        })
    return out


# ============================================================
# 서버 데이터 (polyball.kr)
# ============================================================

def _parse_server_html(html):
    """서버 크롤링 HTML → {date: {all, app, web}} 전체 날짜 반환"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 3:
        return {}

    def parse_all_rows(table, has_conversion=False):
        result = {}
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            vals = [c.get_text(strip=True).replace(",", "") for c in cells]
            if len(vals) < 5:
                continue
            date_str = vals[0]
            if not date_str.startswith("20"):
                continue
            pred = vals[2].split("/")
            quiz = vals[3].split("/")
            entry = vals[4].split("/")
            row_data = {
                "signup":     int(vals[1]) if vals[1].isdigit() else 0,
                "pred_cnt":   int(pred[0].strip()) if pred[0].strip().isdigit() else 0,
                "pred_user":  int(pred[1].strip()) if len(pred) >= 2 and pred[1].strip().isdigit() else 0,
                "quiz_cnt":   int(quiz[0].strip()) if quiz[0].strip().isdigit() else 0,
                "quiz_user":  int(quiz[1].strip()) if len(quiz) >= 2 and quiz[1].strip().isdigit() else 0,
                "entry_cnt":  int(entry[0].strip()) if entry[0].strip().isdigit() else 0,
                "entry_user": int(entry[1].strip()) if len(entry) >= 2 and entry[1].strip().isdigit() else 0,
            }
            if has_conversion and len(vals) >= 6:
                row_data["app_conversion"] = int(vals[5]) if vals[5].isdigit() else 0
            result[date_str] = row_data
        return result

    all_rows = parse_all_rows(tables[0])
    app_rows = parse_all_rows(tables[1], has_conversion=True)
    web_rows = parse_all_rows(tables[2])
    return {"all": all_rows, "app": app_rows, "web": web_rows}


def fetch_server_data(target_date):
    """단일 날짜용 — 기존 호환성 유지"""
    try:
        r = requests.get(SERVER_URL, timeout=15)
        if r.status_code != 200:
            return None
        parsed = _parse_server_html(r.text)
        if not parsed:
            return None
        return {
            "all": parsed["all"].get(target_date, {}),
            "app": parsed["app"].get(target_date, {}),
            "web": parsed["web"].get(target_date, {}),
        }
    except Exception:
        return None


def fetch_all_server_data():
    """전체 날짜 서버 데이터 반환 — data.json 전체 갱신용"""
    try:
        r = requests.get(SERVER_URL, timeout=15)
        if r.status_code != 200:
            return None
        return _parse_server_html(r.text)
    except Exception:
        return None


# ============================================================
# 데이터 검증
# ============================================================

def validate(target_date, ab_metrics, server_data, data):
    errors = []
    warnings = []

    m = ab_metrics.get(target_date, {})
    sv = server_data or {}
    sv_all = sv.get("all", {})
    sv_web = sv.get("web", {})
    sv_app = sv.get("app", {})

    # 1. 에어브릿지 DAU가 0인지
    dau_web = m.get("web_open_users", 0)
    dau_app = m.get("app_active_users", 0)
    if dau_web == 0 and dau_app == 0:
        errors.append(f"DAU 웹/앱 모두 0 - 에어브릿지 데이터 미수집")

    # 2. 서버 데이터 존재 확인
    if not sv_all:
        errors.append(f"서버 데이터 없음 - polyball.kr 응답 없음")

    # 3. 서버 웹+앱=합계 정합성
    if sv_all and sv_web and sv_app:
        for key in ["signup", "pred_cnt", "pred_user", "quiz_cnt", "quiz_user", "entry_cnt", "entry_user"]:
            total = sv_all.get(key, 0)
            web_val = sv_web.get(key, 0)
            app_val = sv_app.get(key, 0)
            if total != web_val + app_val:
                warnings.append(f"서버 정합성: {key} 합계({total}) != 웹({web_val})+앱({app_val})")

    # 4. 전일 대비 이상치
    existing = data.get("daily", [])
    if existing:
        prev = existing[-1]
        prev_dau = prev.get("dau_total", 0)
        cur_dau = dau_web + dau_app
        if prev_dau > 0 and cur_dau > prev_dau * 10:
            warnings.append(f"DAU 이상치: 전일 {prev_dau:,} -> 당일 {cur_dau:,} (10배 초과)")
        if prev_dau > 0 and cur_dau < prev_dau * 0.1:
            warnings.append(f"DAU 이상치: 전일 {prev_dau:,} -> 당일 {cur_dau:,} (90% 하락)")

    # 5. 날짜 중복 체크
    existing_dates = {d["date"] for d in existing}
    if target_date in existing_dates:
        warnings.append(f"{target_date} 이미 존재 - 덮어쓰기")

    # 6. 기존 data.json 포맷 정합성 - daily 필수 컬럼
    REQUIRED_DAILY_COLS = {
        "date", "dau_web", "dau_app", "dau_total",
        "signup_web", "signup_app", "signup_total",
        "pred_web", "pred_app", "pred_total",
        "entry_web", "entry_app", "entry_total",
        "server_signup", "server_signup_web", "server_signup_app",
        "server_pred_cnt", "server_pred_cnt_web", "server_pred_cnt_app",
        "server_pred_user", "server_pred_user_web", "server_pred_user_app",
        "server_quiz_cnt", "server_quiz_cnt_web", "server_quiz_cnt_app",
        "server_quiz_user", "server_quiz_user_web", "server_quiz_user_app",
        "server_entry_cnt", "server_entry_cnt_web", "server_entry_cnt_app",
        "server_entry_user", "server_entry_user_web", "server_entry_user_app",
    }
    if existing:
        existing_cols = set(existing[0].keys())
        missing = REQUIRED_DAILY_COLS - existing_cols
        if missing:
            errors.append(f"data.json daily 필수 컬럼 누락: {missing}")

    # channels는 동적 키이므로 필수 컬럼 검증 생략 (date만 확인)
    ch_list = data.get("channels", [])
    if ch_list and "date" not in ch_list[0]:
        errors.append("data.json channels에 date 컬럼 없음")

    REQUIRED_FUNNEL_COLS = {"date", "intro", "team_start", "team_view", "team_complete", "ob04", "obs01", "signup", "pred_cta"}
    fn_list = data.get("funnel", [])
    if fn_list:
        fn_cols = set(fn_list[0].keys())
        missing_fn = REQUIRED_FUNNEL_COLS - fn_cols
        if missing_fn:
            errors.append(f"data.json funnel 필수 컬럼 누락: {missing_fn}")

    # 7. 날짜 일치 검증 (daily/channels/funnel 같은 날짜셋)
    daily_dates = {d["date"] for d in existing}
    ch_dates = {c["date"] for c in ch_list}
    fn_dates = {f["date"] for f in fn_list}
    if daily_dates != ch_dates:
        warnings.append(f"daily-channels 날짜 불일치: {daily_dates - ch_dates} / {ch_dates - daily_dates}")
    if daily_dates != fn_dates:
        warnings.append(f"daily-funnel 날짜 불일치: {daily_dates - fn_dates} / {fn_dates - daily_dates}")

    # 8. 합계 = 웹 + 앱 검증 (당일 에어브릿지 데이터)
    dau_total = dau_web + dau_app
    ab_signup_web = m.get("web_custom_users_signup", 0)
    ab_signup_app = m.get("app_custom_users_signup", 0)
    if ab_signup_web + ab_signup_app != m.get("web_custom_users_signup", 0) + m.get("app_custom_users_signup", 0):
        warnings.append("에어브릿지 signup 합계 불일치")

    return errors, warnings


# ============================================================
# data.json 업데이트
# ============================================================

def build_daily_row(date_str, ab, sv_all, sv_web, sv_app):
    m = ab.get(date_str, {})
    dau_web = m.get("web_open_users", 0)
    dau_app = m.get("app_active_users", 0)
    signup_web = m.get("web_custom_users_signup", 0)
    signup_app = m.get("app_custom_users_signup", 0)
    pred_web = m.get("web_custom_users_c_match_prediction_completed", 0)
    pred_app = m.get("app_custom_users_c_match_prediction_completed", 0)
    entry_web = m.get("web_custom_users_pv_match_application_completed", 0)
    entry_app = m.get("app_custom_users_pv_match_application_completed", 0)
    return {
        "date": date_str,
        "dau_web": dau_web, "dau_app": dau_app, "dau_total": dau_web + dau_app,
        "signup_web": signup_web, "signup_app": signup_app, "signup_total": signup_web + signup_app,
        "pred_web": pred_web, "pred_app": pred_app, "pred_total": pred_web + pred_app,
        "entry_web": entry_web, "entry_app": entry_app, "entry_total": entry_web + entry_app,
        "server_signup_web": sv_web.get("signup", 0), "server_signup_app": sv_app.get("signup", 0),
        "server_signup": sv_all.get("signup", 0),
        "server_pred_cnt_web": sv_web.get("pred_cnt", 0), "server_pred_cnt_app": sv_app.get("pred_cnt", 0),
        "server_pred_cnt": sv_all.get("pred_cnt", 0),
        "server_pred_user_web": sv_web.get("pred_user", 0), "server_pred_user_app": sv_app.get("pred_user", 0),
        "server_pred_user": sv_all.get("pred_user", 0),
        "server_quiz_cnt_web": sv_web.get("quiz_cnt", 0), "server_quiz_cnt_app": sv_app.get("quiz_cnt", 0),
        "server_quiz_cnt": sv_all.get("quiz_cnt", 0),
        "server_quiz_user_web": sv_web.get("quiz_user", 0), "server_quiz_user_app": sv_app.get("quiz_user", 0),
        "server_quiz_user": sv_all.get("quiz_user", 0),
        "server_entry_cnt_web": sv_web.get("entry_cnt", 0), "server_entry_cnt_app": sv_app.get("entry_cnt", 0),
        "server_entry_cnt": sv_all.get("entry_cnt", 0),
        "server_entry_user_web": sv_web.get("entry_user", 0), "server_entry_user_app": sv_app.get("entry_user", 0),
        "server_entry_user": sv_all.get("entry_user", 0),
        "server_app_conversion": sv_app.get("app_conversion", 0),
    }


def build_funnel_row(date_str, funnel):
    f = funnel.get(date_str, {})
    return {
        "date": date_str,
        "intro": f.get("web_custom_users_pv_ob_intro", 0),
        "team_start": f.get("web_custom_users_c_ob_intro_start") or None,
        "team_view": f.get("web_custom_users_pv_ob_team_choice") or None,
        "team_complete": f.get("web_custom_users_c_ob_team_choice_completed") or None,
        "ob04": f.get("web_custom_users_pv_ob_team_choice_completed", 0),
        "obs01": f.get("web_custom_users_pv_ob_match_choice_completed", 0),
        "signup": f.get("web_custom_users_signup", 0),
        "pred_cta": f.get("web_custom_users_c_match_prediction", 0),
    }


def build_app_funnel_row(date_str, app_funnel):
    f = app_funnel.get(date_str, {})
    return {
        "date": date_str,
        "intro":    f.get("app_custom_users_pv_ob_intro", 0),
        "ob04":     f.get("app_custom_users_pv_ob_team_choice_completed", 0),
        "obs01":    f.get("app_custom_users_pv_ob_match_choice_completed", 0),
        "signup":   f.get("app_custom_users_signup", 0),
        "pred":     f.get("app_custom_users_c_match_prediction_completed", 0),
    }


def build_channel_row(date_str, channels):
    """에어브릿지에서 온 모든 채널을 그대로 저장 — 하드코딩 키 없음"""
    ch = channels.get(date_str, {})
    row = {"date": date_str}
    for key, d in ch.items():
        row[f"{key}_clicks"] = d.get("clicks", 0)
        row[f"{key}_signups"] = d.get("signups", 0)
    return row


def update_data_json(target_date, daily_row, funnel_row, channel_row, app_funnel_row=None, app_install_channel_rows=None, channels_detail_by_date=None):
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))

    rows_to_update = [("daily", daily_row), ("funnel", funnel_row), ("channels", channel_row)]
    if app_funnel_row is not None:
        rows_to_update.append(("app_funnel", app_funnel_row))

    for list_key, new_row in rows_to_update:
        lst = data.setdefault(list_key, [])
        replaced = False
        for i, d in enumerate(lst):
            if d["date"] == target_date:
                lst[i] = new_row
                replaced = True
                break
        if not replaced:
            lst.append(new_row)
            lst.sort(key=lambda x: x["date"])

    # app_install_channels: 날짜별 다수 행 → 기존 날짜 행 전체 교체
    if app_install_channel_rows is not None:
        lst = data.setdefault("app_install_channels", [])
        data["app_install_channels"] = [r for r in lst if r["date"] != target_date]
        data["app_install_channels"].extend(app_install_channel_rows)
        data["app_install_channels"].sort(key=lambda x: (x["date"], x["channel"]))

    # channels_detail: {date: [rows]} → 날짜별 교체
    if channels_detail_by_date:
        lst = data.setdefault("channels_detail", [])
        for ds, rows in channels_detail_by_date.items():
            data["channels_detail"] = [r for r in lst if r["date"] != ds]
            lst = data["channels_detail"]
            lst.append({"date": ds, "rows": rows})
        data["channels_detail"].sort(key=lambda x: x["date"])

    # insights
    insights = data.setdefault("insights", [])
    if not any(ins["date"] == target_date for ins in insights):
        insights.append({"date": target_date, "summary": "(자동 생성)"})
        insights.sort(key=lambda x: x["date"])

    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


# ============================================================
# 누적 일간분석 txt 업데이트
# ============================================================

def _update_analysis_txt(target_date: str, daily_row: dict, funnel_row: dict, channels: dict):
    """
    폴리볼_일간분석_YYYYMMDD_YYYYMMDD.txt 파일에 오늘 한 줄 추가.
    파일명의 종료일을 갱신하고, 이미 같은 날짜 행이 있으면 덮어씀.
    """
    target_ds = target_date.replace("-", "")

    # 기존 파일 탐색
    existing = sorted(ANALYSIS_DIR.glob("폴리볼_일간분석_*_*.txt"))
    old_file = existing[-1] if existing else None

    # 헤더 + 기존 데이터 줄 읽기
    HEADER = (
        "# 폴리볼 일간 분석 누적\n"
        f"# 기간: {SERVICE_START_DATE[:4]}-{SERVICE_START_DATE[4:6]}-{SERVICE_START_DATE[6:]}(D+0) ~ {target_date}(D+{(datetime.strptime(target_date, '%Y-%m-%d') - datetime(2026, 3, 25)).days})\n"
        f"# 업데이트: {target_date}\n"
        "#\n"
        "# === 컬럼 설명 ===\n"
        "# DAU: 에어브릿지 (합계/웹/앱)\n"
        "# 가입: 서버DB/에어브릿지 (앱가입은 에어브릿지에서 미집계)\n"
        "# 응모User: 서버DB 기준 응모 완료 유저수\n"
        "# 웹CVR: 인트로→가입완료 최종전환율 (4/6부터 신규이벤트 기준)\n"
        "# paid: paid_myseatcheck 채널 (4/1부터 집행, CPA는 4/11부터 집계)\n"
        "# =================\n"
        "\n"
    )

    data_lines = []
    if old_file and old_file.exists():
        for line in old_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            # 같은 날짜 행은 제거 (덮어쓰기)
            if line.startswith(target_ds):
                continue
            data_lines.append(line)

    # 오늘 줄 생성
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    day_num = (dt - datetime(2026, 3, 25)).days
    dau_t = daily_row.get("dau_total", 0)
    dau_w = daily_row.get("dau_web", 0)
    dau_a = daily_row.get("dau_app", 0)
    sv_sg = daily_row.get("server_signup", 0)
    ab_sg = daily_row.get("signup_total", 0)
    sv_eu = daily_row.get("server_entry_user", 0)

    # 웹 CVR (인트로→가입)
    cvr_str = "-"
    if funnel_row:
        intro = funnel_row.get("intro", 0)
        signup = funnel_row.get("signup", 0)
        if intro and signup:
            cvr_str = f"{round(signup / intro * 100, 1)}%"

    # paid 채널
    paid_str = "-"
    today_ch = channels.get(target_date, {})
    paid = today_ch.get("paid_myseatcheck", {})
    if paid.get("clicks") or paid.get("signups"):
        cl = paid.get("clicks", 0)
        sg = paid.get("signups", 0)
        cvr_p = f"CVR{round(sg/cl*100,1)}%" if cl else ""
        paid_str = f"{cl:,}클/{sg:,}가/{cvr_p}"

    # 주요 채널 하이라이트 (상위 2개, paid 제외)
    ch_highlights = []
    for key, val in sorted(today_ch.items(), key=lambda x: x[1].get("signups", 0), reverse=True):
        if key == "paid_myseatcheck":
            continue
        sg_v = val.get("signups", 0)
        cl_v = val.get("clicks", 0)
        if sg_v >= 10:
            cvr_h = f"CVR{round(sg_v/cl_v*100,1)}%" if cl_v else ""
            ch_highlights.append(f"{key}{cl_v:,}클/{sg_v:,}가({cvr_h})")
        if len(ch_highlights) >= 2:
            break
    ch_str = ",".join(ch_highlights) if ch_highlights else "-"

    new_line = (
        f"{target_ds}(D+{day_num}) | "
        f"DAU {dau_t:,}/{dau_w:,}/{dau_a:,} | "
        f"가입 서버{sv_sg:,}/AB{ab_sg:,} | "
        f"응모User{sv_eu:,} | "
        f"웹CVR{cvr_str} | "
        f"paid{paid_str} | "
        f"{ch_str} | "
        f"(분석은 데일리 리포트 참조)"
    )
    data_lines.append(new_line)

    # 새 파일명 (종료일 갱신)
    new_file = ANALYSIS_DIR / f"폴리볼_일간분석_{SERVICE_START_DATE}_{target_ds}.txt"
    new_file.write_text(HEADER + "\n".join(data_lines) + "\n", encoding="utf-8")

    # 구 파일 삭제 (파일명 바뀐 경우)
    if old_file and old_file != new_file and old_file.exists():
        old_file.unlink()

    print(f"  [OK] 일간분석 누적: {new_file.name}")


# 리포트 생성 (기존 포맷 완전 준수)
# ============================================================

def generate_report(target_date, all_daily, all_funnel, all_app_funnel, all_channels, server_data, analysis_text):
    """기존 데일리 리포트 포맷 그대로 생성 (7일 추이 + CVR + 전일比)"""

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    day_num = (dt - datetime(2026, 3, 25)).days
    md = target_date[5:].replace("-", "/")

    # 7일치 날짜 정렬 (target_date 이하로 윈도우 한정 — 과거날짜 재생성 시 후속일 오염 방지)
    _upto = [d for d in sorted(all_daily.keys()) if d <= target_date]
    dates_7d = _upto[-7:]
    target_md = target_date[5:].replace("-", "/")
    prev_date = dates_7d[-2] if len(dates_7d) >= 2 else None

    def fmt(n):
        if n is None or n == 0:
            return "-"
        return f"{n:,}"

    def pct(cur, prev):
        if not prev or prev == 0:
            return "-"
        p = round((cur - prev) / prev * 100)
        arrow = "^" if p > 0 else "v" if p < 0 else "="
        return f"{'>' if p > 0 else '<'} {p:+}%"

    def pct_delta(cur, prev):
        if not prev or prev == 0:
            return "-"
        p = round((cur - prev) / prev * 100)
        d = cur - prev
        arrow = ">" if p > 0 else "<"
        return f"{arrow} {d:+,}"

    def pp_delta(cur_pct, prev_pct):
        diff = round(cur_pct - prev_pct, 1)
        arrow = ">" if diff > 0 else "<" if diff < 0 else "="
        return f"{arrow} {diff:+}pp"

    def cvr(a, b):
        if not b or b == 0:
            return "0.0%"
        return f"{round(a / b * 100, 1)}%"

    # ── 섹션 1: 핵심 지표 7일 추이 ──
    p_data = all_daily.get(prev_date, {}) if prev_date else {}
    t_data = all_daily.get(target_date, {})

    sec1_header = "| 날짜 | DAU(웹) | DAU(앱) | DAU 합계 | 가입(웹) | 가입(앱) | 가입 합계 | 예측완료(웹) | 예측완료(앱) | 예측완료 합계 | 응모완료(웹) | 응모완료(앱) | 응모완료 합계 | 가입(서버) | 예측Cnt(서버) | 예측User(서버) | 퀴즈Cnt(서버) | 퀴즈User(서버) | 응모Cnt(서버) | 응모User(서버) |"
    sec1_sep = "| ------ | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- |"

    sec1_rows = ""
    for d_str in dates_7d:
        m = all_daily[d_str]
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        su_a = m.get("signup_app", 0)
        su_a_str = "-" if su_a == 0 else fmt(su_a)
        sec1_rows += f"\n| {b}{d_md}{b} | {b}{fmt(m.get('dau_web',0))}{b} | {b}{fmt(m.get('dau_app',0))}{b} | {b}{fmt(m.get('dau_total',0))}{b} | {b}{fmt(m.get('signup_web',0))}{b} | {b}{su_a_str}{b} | {b}{fmt(m.get('signup_total',0))}{b} | {b}{fmt(m.get('pred_web',0))}{b} | {b}{fmt(m.get('pred_app',0))}{b} | {b}{fmt(m.get('pred_total',0))}{b} | {b}{fmt(m.get('entry_web',0))}{b} | {b}{fmt(m.get('entry_app',0))}{b} | {b}{fmt(m.get('entry_total',0))}{b} | {b}{fmt(m.get('server_signup',0))}{b} | {b}{fmt(m.get('server_pred_cnt',0))}{b} | {b}{fmt(m.get('server_pred_user',0))}{b} | {b}{fmt(m.get('server_quiz_cnt',0))}{b} | {b}{fmt(m.get('server_quiz_user',0))}{b} | {b}{fmt(m.get('server_entry_cnt',0))}{b} | {b}{fmt(m.get('server_entry_user',0))}{b} |"

    # 전일比
    cols_pct = ["dau_web","dau_app","dau_total","signup_web","signup_app","signup_total","pred_web","pred_app","pred_total","entry_web","entry_app","entry_total","server_signup","server_pred_cnt","server_pred_user","server_quiz_cnt","server_quiz_user","server_entry_cnt","server_entry_user"]
    pct_row = "| 전일比(%) |"
    num_row = "| 전일比(숫자) |"
    for col in cols_pct:
        cur = t_data.get(col, 0)
        prev = p_data.get(col, 0)
        if col == "signup_app" and cur == 0 and prev == 0:
            pct_row += " - |"
            num_row += " - |"
        else:
            pct_row += f" {pct(cur, prev)} |"
            num_row += f" {pct_delta(cur, prev)} |"

    # ── 섹션 2: 웹 퍼널 7일 추이 ──
    sec2_rows = ""
    for d_str in dates_7d:
        f = all_funnel.get(d_str, {})
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        intro = f.get("web_custom_users_pv_ob_intro", 0)
        start = f.get("web_custom_users_c_ob_intro_start", 0)
        ob04 = f.get("web_custom_users_pv_ob_team_choice_completed", 0)
        bs01 = f.get("web_custom_users_pv_ob_match_choice_completed", 0)
        signup = f.get("web_custom_users_signup", 0)
        pred_cta = f.get("web_custom_users_c_match_prediction", 0)
        start_cvr = cvr(start, intro) if start > 0 else "-"
        ob04_cvr = cvr(ob04, intro) if start == 0 else cvr(ob04, start)
        bs01_cvr = cvr(bs01, ob04)
        signup_cvr = cvr(signup, bs01)
        pred_cvr = cvr(pred_cta, signup)
        if start > 0:
            tv = f.get("web_custom_users_pv_ob_team_choice", 0)
            tc = f.get("web_custom_users_c_ob_team_choice_completed", 0)
            tv_cvr = cvr(tv, start)
            tc_cvr = cvr(tc, tv) if tv > 0 else "-"
            sec2_rows += f"\n| {b}{d_md}{b} | {b}{fmt(intro)}{b} | {b}{fmt(start)}{b} | {b}{start_cvr}{b} | {b}{fmt(tv)}{b} | {b}{tv_cvr}{b} | {b}{fmt(tc)}{b} | {b}{tc_cvr}{b} | {b}{fmt(ob04)}{b} | {b}{cvr(ob04, tc) if tc > 0 else cvr(ob04, intro)}{b} | {b}{fmt(bs01)}{b} | {b}{bs01_cvr}{b} | {b}{fmt(signup)}{b} | {b}{signup_cvr}{b} | {b}{fmt(pred_cta)}{b} | {b}{pred_cvr}{b} |"
        else:
            sec2_rows += f"\n| {b}{d_md}{b} | {b}{fmt(intro)}{b} | {b}-{b} | {b}-{b} | {b}-{b} | {b}-{b} | {b}-{b} | {b}-{b} | {b}{fmt(ob04)}{b} | {b}{cvr(ob04, intro)}{b} | {b}{fmt(bs01)}{b} | {b}{bs01_cvr}{b} | {b}{fmt(signup)}{b} | {b}{signup_cvr}{b} | {b}{fmt(pred_cta)}{b} | {b}{pred_cvr}{b} |"

    # 전체 퍼널 CVR 테이블
    cvr_rows = ""
    for d_str in dates_7d:
        f = all_funnel.get(d_str, {})
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        intro = f.get("web_custom_users_pv_ob_intro", 0)
        signup = f.get("web_custom_users_signup", 0)
        pred_cta = f.get("web_custom_users_c_match_prediction", 0)
        cvr_rows += f"\n| {b}{d_md}{b} | {b}{fmt(intro)}{b} | {b}{fmt(signup)}{b} | {b}{cvr(signup, intro)}{b} | {b}{fmt(pred_cta)}{b} | {b}{cvr(pred_cta, intro)}{b} |"

    # ── 섹션 3: 웹 서비스 이용 ──
    sec3_rows = ""
    for d_str in dates_7d:
        m = all_daily[d_str]
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        sec3_rows += f"\n| {b}{d_md}{b} | {b}{fmt(m.get('pred_web',0))}{b} | {b}{fmt(m.get('entry_web',0))}{b} |"

    # ── 섹션 4: 앱 퍼널 ──
    sec4_rows = ""
    for d_str in dates_7d:
        f = all_app_funnel.get(d_str, {})
        m = all_daily.get(d_str, {})
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        intro = f.get("app_custom_users_pv_ob_intro", 0)
        ob04 = f.get("app_custom_users_pv_ob_team_choice_completed", 0)
        bs01 = f.get("app_custom_users_pv_ob_match_choice_completed", 0)
        pred = f.get("app_custom_users_c_match_prediction_completed", 0)
        sec4_rows += f"\n| {b}{d_md}{b} | {b}{fmt(intro)}{b} | {b}{fmt(ob04)}{b} | {b}{cvr(ob04, intro)}{b} | {b}{fmt(bs01)}{b} | {b}{cvr(bs01, ob04)}{b} | {b}-{b} | {b}-{b} | {b}{fmt(pred)}{b} | {b}{cvr(pred, intro)}{b} |"

    # ── 섹션 5: 앱 서비스 이용 ──
    sec5_rows = ""
    for d_str in dates_7d:
        m = all_daily[d_str]
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        sec5_rows += f"\n| {b}{d_md}{b} | {b}{fmt(m.get('pred_app',0))}{b} | {b}{fmt(m.get('entry_app',0))}{b} |"

    # ── 섹션 6: 채널 합산 ──
    CH_GROUPS = {
        "instagram": ["ig_Influencer", "ig_ownedmedia", "ig_fanpage", "ig_Somoim"],
        "kakao_notitalk": ["kakao_notitalk"],
        "kakao_opentalk": ["kakao_opentalk"],
        "paid": ["paid_myseatcheck"],
        "polyball_web": ["polyball_web"],
        "round": ["round_push", "round_popup", "round_banner", "round_step"],
        "unattributed": ["unattributed"],
    }
    CH_DETAIL_KEYS = ["ig_Influencer", "ig_ownedmedia", "ig_fanpage", "ig_Somoim",
                      "kakao_notitalk", "kakao_opentalk", "paid_myseatcheck",
                      "round_push", "round_popup", "round_banner", "round_step",
                      "polyball_web", "unattributed"]

    sec6_rows = ""
    for d_str in dates_7d:
        ch = all_channels.get(d_str, {})
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        vals = []
        for grp_name, grp_keys in CH_GROUPS.items():
            cl = sum(ch.get(k, {}).get("clicks", 0) for k in grp_keys)
            sg = sum(ch.get(k, {}).get("signups", 0) for k in grp_keys)
            vals.append(f"{b}{fmt(cl)}{b} | {b}{fmt(sg)}{b}")
        sec6_rows += f"\n| {b}{d_md}{b} | {' | '.join(vals)} |"

    # ── 섹션 7: 채널 상세 ──
    sec7_rows = ""
    for d_str in dates_7d:
        ch = all_channels.get(d_str, {})
        _dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
        d_md = f"{_dt_obj.month}/{_dt_obj.day}"
        is_target = d_str == target_date
        b = "**" if is_target else ""
        vals = []
        for key in CH_DETAIL_KEYS:
            cl = ch.get(key, {}).get("clicks", 0)
            sg = ch.get(key, {}).get("signups", 0)
            vals.append(f"{b}{fmt(cl)}{b} | {b}{fmt(sg)}{b}")
        sec7_rows += f"\n| {b}{d_md}{b} | {' | '.join(vals)} |"

    # ── 서버 웹/앱 분리 테이블 ──
    sv_all = server_data.get("all", {}) if server_data else {}
    sv_web = server_data.get("web", {}) if server_data else {}
    sv_app = server_data.get("app", {}) if server_data else {}

    report = f"""# 폴리볼 데일리 리포트 -- {target_date} (D+{day_num})

> 에어브릿지 Actuals Report | 퍼널: 유니크 유저 기준

---

## 요약
(분석 작성 시 업데이트)

---

## 1. 일간 핵심 지표

{sec1_header}
{sec1_sep}{sec1_rows}
{pct_row}
{num_row}

**이벤트 매핑**

| 컬럼 | 소스 | 이벤트 / 지표 | 기준 |
|------|------|-------------|------|
| DAU(웹) | 에어브릿지 | `web_open_users` | 웹 세션 오픈 유니크 유저 |
| DAU(앱) | 에어브릿지 | `app_active_users` | 앱 액티브 유니크 유저 |
| 가입(웹) | 에어브릿지 | `web_custom_users_signup` | 온보딩 가입완료 화면 최초 진입 |
| 가입(앱) | 에어브릿지 | `app_custom_users_signup` | 온보딩 가입완료 화면 진입 |
| 예측완료(웹) | 에어브릿지 | `web_custom_users_c_match_prediction_completed` | 예측 완료 버튼 클릭 |
| 예측완료(앱) | 에어브릿지 | `app_custom_users_c_match_prediction_completed` | 토스트 팝업 확정 클릭 |
| 응모완료(웹) | 에어브릿지 | `web_custom_users_pv_match_application_completed` | 응모완료 화면 PV |
| 응모완료(앱) | 에어브릿지 | `app_custom_users_pv_match_application_completed` | 응모완료 화면 PV |
| 가입~응모(서버) | 서버 DB | polyball.kr/api/cron/pub/stat/view | 서버 확정 수치 (웹/앱 분리) |
---
## 2. 웹 온보딩 퍼널

| 날짜 | 인트로 유저 | 시작하기 클릭 | 시작하기 CVR | 팀선택뷰 | 팀선택뷰 CVR | 팀선택완료 클릭 | 팀선택완료 CVR | OB-04 유저 | OB-04 CVR | OB-BS01 유저 | OB-BS01 CVR | 가입완료 유저 | 가입완료 CVR | 예측CTA 유저 | 예측CTA CVR |
| ------ | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- |{sec2_rows}

> CVR: 직전 단계 대비 | 시작하기/팀선택 이벤트: 4/6부터 데이터 집계

**전체 퍼널 최종 전환율 (인트로 기준)**

| 날짜 | 인트로 | 가입완료 | 인트로->가입 CVR | 예측CTA | 인트로->예측CTA CVR |
| ------ | ------- | ------- | ------- | ------- | ------- |{cvr_rows}

**이벤트 매핑**

| 단계 | 이벤트명 | 트리거 |
|------|---------|-------|
| 인트로 노출 | `web_custom_users_pv_ob_intro` | 인트로 화면 진입 시 |
| 시작하기 클릭 | `web_custom_users_c_ob_intro_start` | 인트로 -> 시작하기 버튼 클릭 시 |
| 팀 선택 화면 진입 | `web_custom_users_pv_ob_team_choice` | 팀 선택 화면 진입 시 |
| 팀 선택 버튼 클릭 | `web_custom_users_c_ob_team_choice_completed` | 팀 선택완료 클릭 시 |
| OB-04 경기선택 | `web_custom_users_pv_ob_team_choice_completed` | 경기 선택 화면 진입 시 |
| OB-BS01 로그인 바텀시트 | `web_custom_users_pv_ob_match_choice_completed` | 로그인 바텀시트 노출 시 |
| OB-05 가입완료 | `web_custom_users_signup` | 응모완료 화면 최초 진입 시 |
| OB-05 예측CTA 클릭 | `web_custom_users_c_match_prediction` | 버튼 클릭 시 |
---
## 3. 웹 서비스 이용

| 날짜 | 예측완료 유저 | 응모완료 유저 |
| ------ | ------- | ------- |{sec3_rows}

> 에어브릿지 기준 웹 예측완료/응모완료
---
## 4. 앱 온보딩 퍼널

| 날짜 | 인트로 유저 | OB-04 유저 | OB-04 CVR | OB-BS01 유저 | OB-BS01 CVR | 가입완료 유저 | 가입완료 CVR | 예측완료 유저 | 예측완료 CVR |
| ------ | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- | ------- |{sec4_rows}

> CVR: 직전 단계 대비 | 예측완료 CVR은 인트로 대비 | 앱 유저는 기존 가입자 -- 가입 이벤트 미발생
---
## 5. 앱 서비스 이용

| 날짜 | 예측완료 유저 | 응모완료 유저 |
| ------ | ------- | ------- |{sec5_rows}

> 에어브릿지 기준 앱 예측완료/응모완료
---
## 6. 채널 퍼포먼스 7일 추이

| 날짜 | instagram 클릭 | instagram 가입 | kakao_notitalk 클릭 | kakao_notitalk 가입 | kakao_opentalk 클릭 | kakao_opentalk 가입 | paid 클릭 | paid 가입 | polyball_web 클릭 | polyball_web 가입 | round 클릭 | round 가입 | unattributed 가입 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |{sec6_rows}

> instagram = Influencer+ownedmedia+fanpage+Somoim 합산 | round = push+step+popup_bottom+commerce_big_banner 합산
---
## 7. 채널 퍼포먼스 7일 상세 추이

| 날짜 | ig_Influencer 클릭 | ig_Influencer 가입 | ig_ownedmedia 클릭 | ig_ownedmedia 가입 | ig_fanpage 클릭 | ig_fanpage 가입 | ig_Somoim 클릭 | ig_Somoim 가입 | kakao_notitalk 클릭 | kakao_notitalk 가입 | kakao_opentalk 클릭 | kakao_opentalk 가입 | paid_myseatcheck 클릭 | paid_myseatcheck 가입 | round_push 클릭 | round_push 가입 | round_popup 클릭 | round_popup 가입 | round_banner 클릭 | round_banner 가입 | round_step 클릭 | round_step 가입 | polyball_web 클릭 | polyball_web 가입 | unattributed 가입 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |{sec7_rows}

> 클릭: 에어브릿지 Actuals `clicks` 메트릭 (channel+campaign groupBy)
> 가입: `web_custom_users_signup` + `app_custom_users_signup` 합산 (실질적으로 웹 가입만 집계 -- 앱 가입은 구조상 0)
---
## 8. 분석

{analysis_text}

---

## 9. 이슈 및 액션

| 우선순위 | 내용 | 담당 |
|---------|------|------|
| (분석 작성 후 업데이트) | | |
"""
    return report


# ============================================================
# 분석 플레이스홀더
# ============================================================

def generate_analysis(target_date, daily_row, prev_row, channel_row, funnel_row):
    """분석은 수동 작성 — '04/08 분석 써줘' 요청 시 Claude Code에서 작성"""
    return "### (분석 대기 중 - '분석 써줘' 요청 시 작성)"


# ============================================================
# 메인
# ============================================================

def main(target_date: str = None):
    """
    target_date: YYYY-MM-DD (기본: KST 어제)
    GitHub Actions 자동 실행 및 fetch_daily.py 수동 실행 모두 이 함수를 사용
    """
    if target_date:
        target = target_date
    else:
        now_kst = datetime.now(KST)
        target = (now_kst - timedelta(days=1)).strftime("%Y-%m-%d")
    from_date = (datetime.strptime(target, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")

    print(f"[PIPELINE] {target} daily report")
    print(f"  range: {from_date} ~ {target}")

    # 1. 토큰 확인
    token = get_ab_token()
    if not token:
        print("[ERROR] AIRBRIDGE_API_TOKEN not set")
        sys.exit(1)

    # 2. 데이터 수집
    print("  [1/4] Airbridge metrics...")
    ab_metrics = fetch_daily_metrics(token, from_date, target)
    if not ab_metrics:
        print("[ERROR] Airbridge API failed")
        sys.exit(1)

    print("  [2/4] Airbridge funnel...")
    funnel = fetch_funnel_metrics(token, from_date, target)

    print("  [3/5] Airbridge app funnel...")
    app_funnel_metrics = [
        "app_custom_users_pv_ob_intro",
        "app_custom_users_pv_ob_team_choice_completed",
        "app_custom_users_pv_ob_match_choice_completed",
        "app_custom_users_signup",
        "app_custom_users_c_match_prediction_completed",
    ]
    app_funnel_payload = {
        "from": from_date, "to": target,
        "metrics": app_funnel_metrics,
        "groupBys": ["event_date"], "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": True,
        "option": {"eventTimestampSource": "event_occurred_date"},
        "size": 100,
    }
    app_funnel_result = ab_request(app_funnel_payload, token)
    app_funnel_data = ab_parse_rows(app_funnel_result) if app_funnel_result else {}

    print("  [4/5] Airbridge channels...")
    channels = fetch_channel_metrics(token, from_date, target)

    print("  [4-1] Airbridge channels detail (ad_group/creative)...")
    channels_detail = fetch_channels_detail(token, from_date, target)

    print("  [5/6] Airbridge app install channels...")
    app_install_ch = fetch_app_install_channels(token, from_date, target)

    print("  [6/6] Server data (polyball.kr)...")
    server = fetch_server_data(target)

    # 3. 검증
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    errors, warnings = validate(target, ab_metrics, server, data)

    for w in warnings:
        print(f"  [WARN] {w}")
    if errors:
        for e in errors:
            print(f"  [ERROR] {e}")
        print("[FAIL] Validation failed - not committing")
        sys.exit(1)

    print(f"  [OK] Validation passed ({len(warnings)} warnings)")

    # 4. data.json 업데이트
    sv_all = server.get("all", {}) if server else {}
    sv_web = server.get("web", {}) if server else {}
    sv_app = server.get("app", {}) if server else {}

    daily_row      = build_daily_row(target, ab_metrics, sv_all, sv_web, sv_app)
    funnel_row     = build_funnel_row(target, funnel)
    app_funnel_row = build_app_funnel_row(target, app_funnel_data)
    channel_row    = build_channel_row(target, channels)

    update_data_json(target, daily_row, funnel_row, channel_row, app_funnel_row, app_install_ch, channels_detail)
    print(f"  [OK] data.json updated (daily / funnel / app_funnel / channels / channels_detail / app_install_channels)")

    # 4-1. 기존 전체 날짜 서버 수치 최신화 (서버 DB 사후 업데이트 반영)
    print("  [OK] 전체 날짜 서버 수치 최신화 중...")
    all_sv = fetch_all_server_data()
    if all_sv:
        fresh_data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        updated_count = 0
        for row in fresh_data["daily"]:
            d = row["date"]
            sv_all = all_sv["all"].get(d, {})
            sv_app = all_sv["app"].get(d, {})
            sv_web = all_sv["web"].get(d, {})
            if not sv_all:
                continue
            row.update({
                "server_signup":          sv_all.get("signup", row.get("server_signup", 0)),
                "server_signup_web":      sv_web.get("signup", row.get("server_signup_web", 0)),
                "server_signup_app":      sv_app.get("signup", row.get("server_signup_app", 0)),
                "server_pred_cnt":        sv_all.get("pred_cnt", row.get("server_pred_cnt", 0)),
                "server_pred_cnt_web":    sv_web.get("pred_cnt", row.get("server_pred_cnt_web", 0)),
                "server_pred_cnt_app":    sv_app.get("pred_cnt", row.get("server_pred_cnt_app", 0)),
                "server_pred_user":       sv_all.get("pred_user", row.get("server_pred_user", 0)),
                "server_pred_user_web":   sv_web.get("pred_user", row.get("server_pred_user_web", 0)),
                "server_pred_user_app":   sv_app.get("pred_user", row.get("server_pred_user_app", 0)),
                "server_quiz_cnt":        sv_all.get("quiz_cnt", row.get("server_quiz_cnt", 0)),
                "server_quiz_cnt_web":    sv_web.get("quiz_cnt", row.get("server_quiz_cnt_web", 0)),
                "server_quiz_cnt_app":    sv_app.get("quiz_cnt", row.get("server_quiz_cnt_app", 0)),
                "server_quiz_user":       sv_all.get("quiz_user", row.get("server_quiz_user", 0)),
                "server_quiz_user_web":   sv_web.get("quiz_user", row.get("server_quiz_user_web", 0)),
                "server_quiz_user_app":   sv_app.get("quiz_user", row.get("server_quiz_user_app", 0)),
                "server_entry_cnt":       sv_all.get("entry_cnt", row.get("server_entry_cnt", 0)),
                "server_entry_cnt_web":   sv_web.get("entry_cnt", row.get("server_entry_cnt_web", 0)),
                "server_entry_cnt_app":   sv_app.get("entry_cnt", row.get("server_entry_cnt_app", 0)),
                "server_entry_user":      sv_all.get("entry_user", row.get("server_entry_user", 0)),
                "server_entry_user_web":  sv_web.get("entry_user", row.get("server_entry_user_web", 0)),
                "server_entry_user_app":  sv_app.get("entry_user", row.get("server_entry_user_app", 0)),
                "server_app_conversion":  sv_app.get("app_conversion", row.get("server_app_conversion", 0)),
            })
            updated_count += 1
        DATA_JSON.write_text(json.dumps(fresh_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  [OK] 서버 수치 최신화 완료 ({updated_count}일치)")

    # 5. 분석 생성
    prev_row = None
    for d in data.get("daily", []):
        prev_row = d
    print("  Analysis placeholder...")
    analysis = generate_analysis(target, daily_row, prev_row, channel_row, funnel_row)

    # 6. 리포트 생성 — data.json에서 7일치 읽어서 전달
    updated_data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    all_daily_map = {d["date"]: d for d in updated_data["daily"]}
    all_funnel_map = funnel  # 에어브릿지 raw (메트릭명 그대로)
    all_app_funnel_map = app_funnel_data
    # channels: {date: {key: {clicks, signups}}} 형식으로 변환
    all_channels_map = channels

    report = generate_report(target, all_daily_map, all_funnel_map, all_app_funnel_map, all_channels_map, server, analysis)
    report_file = REPORT_DIR / f"{target.replace('-', '')}.md"
    report_file.write_text(report, encoding="utf-8")
    print(f"  [OK] Report: {report_file.name}")

    # 누적 일간분석 txt 업데이트
    _update_analysis_txt(target, daily_row, funnel_row, channels)

    # 8. 최종 데이터 확인
    print(f"\n[RESULT] {target}")
    print(f"  DAU: {daily_row['dau_total']:,} (web {daily_row['dau_web']:,} / app {daily_row['dau_app']:,})")
    print(f"  Signup: {daily_row['server_signup']:,} (web {daily_row['server_signup_web']:,} / app {daily_row['server_signup_app']:,})")
    print(f"  Pred: {daily_row['server_pred_cnt']:,}/{daily_row['server_pred_user']:,}")
    print(f"  Quiz: {daily_row['server_quiz_cnt']:,}/{daily_row['server_quiz_user']:,}")
    print(f"  Entry: {daily_row['server_entry_cnt']:,}/{daily_row['server_entry_user']:,}")
    print("[DONE]")

    # 7. 에어브릿지 전체 백업 (서비스 시작일 ~ target)
    try:
        import importlib.util, sys as _sys
        _spec = importlib.util.spec_from_file_location("backup_ab", SCRIPT_DIR / "backup_airbridge.py")
        _bk = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_bk)

        SERVICE_START = "2026-03-26"
        print(f"\n[BACKUP] {SERVICE_START} ~ {target} 전체 백업 중...")
        _bk_token = get_ab_token()
        _daily   = _bk.fetch_daily_metrics(_bk_token, SERVICE_START, target)
        _funnel  = _bk.fetch_funnel_metrics(_bk_token, SERVICE_START, target)
        _ch      = _bk.fetch_channel_metrics(_bk_token, SERVICE_START, target)
        _inst    = _bk.fetch_app_install_channels(_bk_token, SERVICE_START, target)
        _app_fn  = _bk.fetch_app_funnel(_bk_token, SERVICE_START, target)
        print(f"[BACKUP] WAU/MAU 유니크 AU 스냅샷 조회 중...")
        _au      = _bk.fetch_au_snapshots(_bk_token, SERVICE_START, target)

        _bk.BACKUP_DIR.mkdir(exist_ok=True)
        _today_str = target.replace("-", "")
        _out = _bk.BACKUP_DIR / f"airbridge_backup_{_today_str}.json"
        import json as _json
        with open(_out, "w", encoding="utf-8") as _f:
            _json.dump({
                "meta": {
                    "backed_up_at": target,
                    "period_start": SERVICE_START,
                    "period_end": target,
                    "app": "polyball",
                },
                "daily_metrics": _daily,
                "funnel_metrics": _funnel,
                "channel_metrics_raw": _ch,
                "app_install_channels_raw": _inst,
                "app_funnel_metrics": _app_fn,
                "au_snapshots": _au,
            }, _f, ensure_ascii=False, indent=2)
        print(f"[BACKUP] 완료: {_out.name} ({_out.stat().st_size/1024:.1f} KB)")
    except Exception as _e:
        print(f"[BACKUP] 실패 (무시): {_e}")


if __name__ == "__main__":
    main()
