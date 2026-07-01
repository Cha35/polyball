"""
일별 costs + 광고 매출 자동 입력 스크립트

사용법:
    python dashboard/add_daily_costs.py 2026-04-30 \
        --myseatcheck-impressions 50000 --myseatcheck-cost 166666 \
        --tenping-signups 50 --tenping-cpa 500 \
        --adpopcorn-excel "C:\\Users\\xxx\\Downloads\\파일명.xlsx"

옵션 일부 생략 가능 (해당 채널 OFF인 경우).
"""

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict


def parse_args():
    p = argparse.ArgumentParser(description="일별 costs + 광고 매출 자동 입력")
    p.add_argument("date", help="YYYY-MM-DD (예: 2026-04-30)")

    # 자리어때
    p.add_argument("--myseatcheck-impressions", type=int, default=None,
                   help="자리어때 노출 수")
    p.add_argument("--myseatcheck-cost", type=int, default=None,
                   help="자리어때 비용 (원)")

    # 텐핑
    p.add_argument("--tenping-signups", type=int, default=None,
                   help="텐핑 어드민 가입 수")
    p.add_argument("--tenping-cpa", type=int, default=500,
                   help="텐핑 CPA 단가 (default 500원)")

    # 애드팝콘
    p.add_argument("--adpopcorn-excel", type=str, default=None,
                   help="애드팝콘 엑셀 파일 경로")

    # 데이터 파일 경로
    p.add_argument("--data-json", type=str, default=None,
                   help="data.json 경로 (default: dashboard/data.json)")

    return p.parse_args()


def add_myseatcheck(d, date, impressions, cost):
    """자리어때 비용 추가"""
    if not impressions or not cost:
        return False
    # 기존 4/30 자리어때 비용 제거
    d["costs"] = [c for c in d["costs"] if not (
        c.get("date") == date and c.get("channel") == "paid"
        and c.get("campaign") == "myseatcheck"
    )]
    d["costs"].append({
        "date": date, "channel": "paid", "campaign": "myseatcheck",
        "ad_group": "popup", "ad_creative": "",
        "spend": cost, "category": "마케팅",
        "note": f"자리어때 노출 {impressions:,}",
    })
    print(f"  [자리어때] {impressions:,} 노출 / {cost:,}원")
    return True


def add_tenping(d, date, signups, cpa):
    """텐핑 비용 추가"""
    if not signups:
        return False
    cost = signups * cpa
    d["costs"] = [c for c in d["costs"] if not (
        c.get("date") == date and c.get("channel") == "paid"
        and c.get("campaign") == "tenping"
    )]
    d["costs"].append({
        "date": date, "channel": "paid", "campaign": "tenping",
        "ad_group": "", "ad_creative": "5500ticket",
        "spend": cost, "category": "마케팅",
        "note": f"텐핑 어드민 {signups}명 × {cpa}원",
    })
    print(f"  [텐핑] {signups}명 × CPA {cpa}원 = {cost:,}원")
    return True


def add_adpopcorn(d, date, excel_path):
    """애드팝콘 매출 갱신 (엑셀 전체 데이터 갱신)"""
    if not excel_path:
        return False
    try:
        import openpyxl
    except ImportError:
        print("  [에러] openpyxl 필요: pip install openpyxl")
        return False

    if not Path(excel_path).exists():
        print(f"  [에러] 엑셀 파일 없음: {excel_path}")
        return False

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active

    # 새 행들
    new_rows = []
    dates_in_excel = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        rd = str(row[0])
        if len(rd) == 8:
            rd = f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}"
        placement_id = row[3]
        placement_name = row[4] or ""
        thirdparty = row[5] or ""
        request = int(row[6] or 0)
        impression = int(row[9] or 0)
        cost_usd = float(row[13] or 0)
        ecpm_usd = float(row[14] or 0)
        cat = "pick" if "예측" in placement_name or "픽" in placement_name else (
            "apply" if "응모" in placement_name else "pick"
        )
        new_rows.append({
            "date": rd, "placement_id": placement_id, "placement_name": placement_name,
            "category": cat, "phase": "initial", "format": "interstitial",
            "os": "iOS" if "iOS" in placement_name else "AOS",
            "thirdparty_name": thirdparty, "request": request, "impression": impression,
            "cost_usd": cost_usd, "cost_krw": int(round(cost_usd * 1480)),
            "ecpm_krw": int(round(ecpm_usd * 1480)),
        })
        dates_in_excel.add(rd)

    # 엑셀에 들어있는 날짜의 기존 행 제거 후 신규 적용
    ar = [r for r in d.get("ad_revenue", []) if r.get("date") not in dates_in_excel]
    ar.extend(new_rows)
    d["ad_revenue"] = sorted(ar, key=lambda x: (x.get("date", ""), x.get("placement_id", "")))

    # 합산 표시
    by_d = defaultdict(float)
    for r in new_rows:
        by_d[r["date"]] += r["cost_usd"]
    print(f"  [애드팝콘] {len(new_rows)}개 행 갱신")
    for k in sorted(by_d):
        print(f"    {k}: ${by_d[k]:.4f} = {int(by_d[k]*1480):,}원")
    return True


def main():
    args = parse_args()

    # data.json 경로
    if args.data_json:
        data_path = Path(args.data_json)
    else:
        data_path = Path(__file__).parent / "data.json"

    if not data_path.exists():
        print(f"[ERROR] data.json 없음: {data_path}")
        sys.exit(1)

    print(f"=== {args.date} costs/매출 입력 ===\n")
    print(f"data.json: {data_path}\n")

    d = json.load(open(data_path, "r", encoding="utf-8"))

    any_changed = False
    any_changed |= add_myseatcheck(d, args.date, args.myseatcheck_impressions, args.myseatcheck_cost)
    any_changed |= add_tenping(d, args.date, args.tenping_signups, args.tenping_cpa)
    any_changed |= add_adpopcorn(d, args.date, args.adpopcorn_excel)

    if not any_changed:
        print("\n[INFO] 변경 사항 없음. 옵션 확인.")
        sys.exit(0)

    # costs 정렬
    d["costs"] = sorted(d["costs"], key=lambda x: (x["date"], x["channel"], x["campaign"]))

    # 저장
    json.dump(d, open(data_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n[OK] data.json 저장 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
