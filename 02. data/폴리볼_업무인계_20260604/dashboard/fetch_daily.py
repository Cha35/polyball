"""
폴리볼 데일리 데이터 수집 — 수동 실행용

실제 로직은 daily_pipeline.py에서 관리.
날짜를 인자로 전달하거나 생략하면 어제 날짜로 실행.

사용법:
  python dashboard/fetch_daily.py              # 어제
  python dashboard/fetch_daily.py 2026-04-08  # 특정 날짜

환경변수:
  AIRBRIDGE_API_TOKEN (필수)
"""

import sys
from pathlib import Path

# daily_pipeline을 직접 import해서 사용
sys.path.insert(0, str(Path(__file__).parent))
from daily_pipeline import main

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    main(target_date=target)
