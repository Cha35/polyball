import streamlit as st
import pandas as pd
import plotly.express as px  # noqa: F401
import plotly.graph_objects as go
import requests
import json
import base64
import os
import re
import shutil
from pathlib import Path

st.set_page_config(
    page_title="폴리볼 그로스 대시보드",
    page_icon="⚾",
    layout="wide"
)

# 검색엔진 인덱싱 차단
st.markdown('<meta name="robots" content="noindex, nofollow">', unsafe_allow_html=True)

# 탭 2열 그리드 + 선택 탭 강조
st.markdown("""
<style>
/* 탭 버튼 2행 — 자동 줄바꿈 */
div[data-baseweb="tab-list"] {
    display: flex !important;
    flex-wrap: wrap !important;
    gap: 4px !important;
}
div[data-baseweb="tab-list"] button {
    font-size: 13px !important;
    padding: 8px 12px !important;
    border-radius: 8px !important;
    border: 1px solid #E2E8F0 !important;
    background: #F8FAFC !important;
    white-space: nowrap !important;
}
div[data-baseweb="tab-list"] button[aria-selected="true"] {
    background: #3B82F6 !important;
    color: #fff !important;
    border-color: #3B82F6 !important;
    font-weight: 700 !important;
}
</style>
""", unsafe_allow_html=True)

# ── 사용자 인증 시스템 ─────────────────────────────────────────
import hashlib as _hashlib
import hmac as _hmac
import datetime as _auth_dt
import time as _time
import pyotp as _pyotp
import io as _io
import qrcode as _qrcode

_USERS_PATH       = "users.json"
_SESSION_SEC      = 3600  # 1시간

def _local_read_file(file_path):
    """로컬 파일 읽기. 성공 시 내용 str, 실패 시 None 반환."""
    import os
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None

def _local_write_file(file_path, content_str, commit_msg):
    """로컬 파일 쓰기."""
    import os
    try:
        dir_name = os.path.dirname(file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content_str)
        return True, "저장 완료"
    except Exception as e:
        return False, f"저장 실패: {str(e)}"

def _local_list_tree(path):
    """로컬 폴더 파일 목록 반환."""
    import os
    if not os.path.exists(path) or not os.path.isdir(path):
        return []
    
    results = []
    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        results.append({
            "name": entry,
            "path": full_path,
            "type": "tree" if os.path.isdir(full_path) else "blob"
        })
    return results

def _get_secret():
    return (st.secrets.get("SECRET_KEY", "") or "polyball_hmac_secret_v1").encode()

def _hash_pw(pw: str) -> str:
    return _hashlib.sha256(pw.encode()).hexdigest()

_SESSION_CFG_PATH = "session_config.json"

@st.cache_data(ttl=60, show_spinner=False)
def _load_session_config():
    try:
        raw = _local_read_file(_SESSION_CFG_PATH)
        if raw is None:
            return {"force_logout_at": 0}
        return json.loads(raw)
    except Exception:
        return {"force_logout_at": 0}

def _save_session_config(cfg: dict):
    _local_write_file(_SESSION_CFG_PATH, json.dumps(cfg, ensure_ascii=False, indent=2), "auth: 세션 설정 업데이트")

def _make_token(user: dict) -> str:
    """사용자 정보를 HMAC 서명 후 base64url 인코딩 (iat 포함)"""
    now = int(_time.time())
    exp = now + _SESSION_SEC
    payload = f"{exp}|{now}|{user['username']}|{user.get('name','')}|{user.get('team','')}|{user.get('role','user')}|{user.get('status','approved')}"
    sig = _hmac.new(_get_secret(), payload.encode(), _hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode().rstrip("=")

def _verify_token(token: str, users: list):
    """토큰 검증 → 유효하면 user dict 반환"""
    try:
        padding = (4 - len(token) % 4) % 4
        raw = base64.urlsafe_b64decode((token + "=" * padding).encode()).decode()
        *p_parts, sig = raw.split("|")
        # 신규 포맷(7개) / 구버전(6개) 모두 허용
        if len(p_parts) == 7:
            exp_str, iat_str, username, name, team, role, status = p_parts
            iat = int(iat_str)
        elif len(p_parts) == 6:
            exp_str, username, name, team, role, status = p_parts
            iat = 0  # 구버전 토큰은 force_logout_at 체크 불가 → 만료 대기
        else:
            return None
        payload = "|".join(p_parts)
        expected = _hmac.new(_get_secret(), payload.encode(), _hashlib.sha256).hexdigest()[:32]
        if sig != expected:
            return None
        if _time.time() > int(exp_str):
            return None
        if status != "approved":
            return None
        # 전체 강제 로그아웃 체크
        cfg = _load_session_config()
        force_ts = int(cfg.get("force_logout_at", 0))
        if iat < force_ts:
            return None
        # 계정 취소 여부 확인
        if users:
            u = _find_user(users, username)
            if u and u["status"] != "approved":
                return None
        return {"username": username, "name": name, "team": team, "role": role, "status": status, "password_hash": ""}
    except Exception:
        return None

@st.cache_data(ttl=60, show_spinner=False)
def _load_users():
    try:
        raw = _local_read_file(_USERS_PATH)
        if raw is None:
            return []
        return json.loads(raw)
    except Exception:
        return []

def _save_users(users_list):
    return _local_write_file(_USERS_PATH, json.dumps(users_list, ensure_ascii=False, indent=2), "auth: 사용자 정보 업데이트")

def _find_user(users, username):
    return next((u for u in users if u["username"] == username), None)

# ── OTP 2차 인증 ─────────────────────────────────────────────────
def _generate_otp_secret() -> str:
    return _pyotp.random_base32()

def _verify_otp(secret: str, code: str) -> bool:
    try:
        return _pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False

def _get_otp_uri(secret: str, username: str) -> str:
    return _pyotp.totp.TOTP(secret).provisioning_uri(
        name=username, issuer_name="폴리볼 대시보드"
    )

def _render_otp_setup(secret: str, uri: str):
    """QR코드 + 설명을 표준 레이아웃으로 렌더링."""
    qr = _qrcode.QRCode(box_size=6, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    col_qr, col_guide = st.columns([1, 1])
    with col_qr:
        st.image(buf, width=200, caption="Google Authenticator로 스캔")
    with col_guide:
        st.markdown("""
**설정 방법**

1. 스마트폰에서 **Google Authenticator** 앱 실행
   - 없으면 앱스토어에서 "Google Authenticator" 검색 후 설치
2. 앱 우측 하단 **＋** 버튼 탭
3. **QR 코드 스캔** 선택 → 왼쪽 QR코드 스캔
4. 앱에 **폴리볼 대시보드** 계정이 추가되면 완료
5. 앱에 표시된 **6자리 숫자**를 아래 입력란에 입력

> QR 스캔이 안 되면 수동 입력:
> 앱 → ＋ → **키 수동 입력** → 아래 비밀키 입력
""")
        st.code(secret, language=None)

# ── 비밀번호 정책 ────────────────────────────────────────────────
_PW_MIN_LEN = 8
_PW_MAX_AGE_DAYS = 90

def _check_pw_policy(pw: str):
    """3종 이상(영문 대/소/숫자/특수) 8자 이상 검증. 통과하면 None, 실패하면 에러 문자열."""
    if len(pw) < _PW_MIN_LEN:
        return f"비밀번호는 {_PW_MIN_LEN}자 이상이어야 합니다."
    categories = [
        any(c.isupper() for c in pw),
        any(c.islower() for c in pw),
        any(c.isdigit() for c in pw),
        any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in pw),
    ]
    if sum(categories) < 3:
        return "영문 대문자·소문자·숫자·특수문자 중 3종류 이상 포함해야 합니다."
    return None

def _is_pw_expired(user: dict) -> bool:
    """비밀번호 변경일로부터 90일 초과 여부."""
    changed = user.get("pw_changed_at")
    if not changed:
        return False  # 기록 없으면 미적용
    try:
        changed_date = _auth_dt.date.fromisoformat(changed)
        return (_auth_dt.date.today() - changed_date).days >= _PW_MAX_AGE_DAYS
    except Exception:
        return False

# ── 세션 복원 (URL 토큰으로 자동 로그인) ────────────────────────
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None
if "otp_pending" not in st.session_state:
    st.session_state["otp_pending"] = None
if "otp_setup_pending" not in st.session_state:
    st.session_state["otp_setup_pending"] = None   # 첫 OTP 설정 대기
if "pw_change_required" not in st.session_state:
    st.session_state["pw_change_required"] = None
if "my_otp_setup" not in st.session_state:
    st.session_state["my_otp_setup"] = False       # 로그인 후 OTP 자가 설정

if st.session_state["auth_user"] is None:
    _url_tok = st.query_params.get("auth", "")
    if _url_tok:
        _r = _verify_token(_url_tok, _load_users())
        if _r:
            st.session_state["auth_user"] = _r

# ── 미로그인 → 로그인/회원가입 페이지 ────────────────────────────
if st.session_state["auth_user"] is None:
    st.markdown("""
    <style>.auth-wrap { max-width: 420px; margin: 80px auto; }</style>
    """, unsafe_allow_html=True)
    st.markdown('<div class="auth-wrap">', unsafe_allow_html=True)
    st.title("⚾ 폴리볼 그로스 대시보드")
    st.caption("내부 전용 — 허가된 사용자만 접근 가능합니다")
    st.markdown("")

    # ── Step 2: OTP 인증 화면 ──────────────────────────────────
    if st.session_state["otp_pending"]:
        _otp_user = st.session_state["otp_pending"]
        st.markdown("### 🔐 2단계 인증")
        st.caption(f"**{_otp_user.get('name', _otp_user['username'])}**님, Google Authenticator 앱의 6자리 코드를 입력하세요")
        _otp_code = st.text_input("인증 코드", max_chars=6, placeholder="000000", key="otp_input")
        _oc1, _oc2 = st.columns(2)
        if _oc1.button("인증", type="primary", use_container_width=True, key="otp_btn"):
            if _verify_otp(_otp_user["otp_secret"], _otp_code):
                st.session_state["otp_pending"] = None
                _tok = _make_token(_otp_user)
                st.session_state["auth_user"] = _otp_user
                st.query_params["auth"] = _tok
                st.rerun()
            else:
                st.error("❌ 인증 코드가 올바르지 않습니다. 앱에서 코드를 다시 확인하세요.")
        if _oc2.button("← 뒤로", use_container_width=True, key="otp_back"):
            st.session_state["otp_pending"] = None
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop()

    # ── Step 2c: OTP 최초 설정 화면 (otp_required이나 secret 없는 경우) ──
    if st.session_state["otp_setup_pending"]:
        _osp_user = st.session_state["otp_setup_pending"]
        if not st.session_state.get("_otp_setup_secret"):
            st.session_state["_otp_setup_secret"] = _generate_otp_secret()
        _osp_secret = st.session_state["_otp_setup_secret"]
        _osp_uri    = _get_otp_uri(_osp_secret, _osp_user["username"])

        st.markdown("### 🔐 OTP 2차 인증 설정")
        st.warning("보안 정책에 따라 OTP 설정을 완료해야 대시보드에 접근할 수 있습니다.")
        _render_otp_setup(_osp_secret, _osp_uri)
        st.caption("앱에 등록한 뒤 표시된 6자리 코드를 입력하세요")
        _osp_code = st.text_input("확인 코드 (6자리)", max_chars=6, placeholder="000000", key="osp_code")
        _os1, _os2 = st.columns(2)
        if _os1.button("설정 완료", type="primary", use_container_width=True, key="osp_confirm"):
            if _verify_otp(_osp_secret, _osp_code):
                _users = _load_users()
                for _u in _users:
                    if _u["username"] == _osp_user["username"]:
                        _u["otp_secret"] = _osp_secret
                _save_users(_users)
                _load_users.clear()
                _osp_user["otp_secret"] = _osp_secret
                st.session_state["otp_setup_pending"] = None
                st.session_state.pop("_otp_setup_secret", None)
                _tok = _make_token(_osp_user)
                st.session_state["auth_user"] = _osp_user
                st.query_params["auth"] = _tok
                st.rerun()
            else:
                st.error("❌ 코드가 올바르지 않습니다. 앱에서 코드를 다시 확인하세요.")
        if _os2.button("← 뒤로", use_container_width=True, key="osp_back"):
            st.session_state["otp_setup_pending"] = None
            st.session_state.pop("_otp_setup_secret", None)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop()

    # ── Step 2b: 비밀번호 만료 → 강제 변경 화면 ─────────────────
    if st.session_state["pw_change_required"]:
        _pcr_user = st.session_state["pw_change_required"]
        st.markdown("### 🔑 비밀번호 변경 필요")
        st.warning(f"마지막 변경 후 {_PW_MAX_AGE_DAYS}일이 지났습니다. 새 비밀번호를 설정하세요.")
        _new_pw  = st.text_input("새 비밀번호", type="password", key="pcr_new")
        _new_pw2 = st.text_input("새 비밀번호 확인", type="password", key="pcr_new2")
        if st.button("변경 후 로그인", type="primary", use_container_width=True, key="pcr_btn"):
            _perr = _check_pw_policy(_new_pw)
            if _perr:
                st.error(_perr)
            elif _new_pw != _new_pw2:
                st.error("비밀번호가 일치하지 않습니다.")
            elif _hash_pw(_new_pw) == _pcr_user["password_hash"]:
                st.error("이전 비밀번호와 동일합니다. 새로운 비밀번호를 사용하세요.")
            else:
                _users = _load_users()
                for _u in _users:
                    if _u["username"] == _pcr_user["username"]:
                        _u["password_hash"] = _hash_pw(_new_pw)
                        _u["pw_changed_at"] = str(_auth_dt.date.today())
                _save_users(_users)
                _load_users.clear()
                _pcr_user["password_hash"] = _hash_pw(_new_pw)
                _pcr_user["pw_changed_at"] = str(_auth_dt.date.today())
                st.session_state["pw_change_required"] = None
                _tok = _make_token(_pcr_user)
                st.session_state["auth_user"] = _pcr_user
                st.query_params["auth"] = _tok
                st.rerun()
        if st.button("← 취소", use_container_width=True, key="pcr_cancel"):
            st.session_state["pw_change_required"] = None
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop()

    _tab_login, _tab_register = st.tabs(["🔐 로그인", "📝 회원가입 신청"])

    with _tab_login:
        _lg_user = st.text_input("아이디", key="lg_user", placeholder="아이디 입력")
        _lg_pw   = st.text_input("비밀번호", type="password", key="lg_pw", placeholder="비밀번호 입력")
        if st.button("로그인", type="primary", use_container_width=True, key="lg_btn"):
            _users = _load_users()
            _u = _find_user(_users, _lg_user)
            if _u is None:
                st.error("존재하지 않는 아이디입니다.")
            elif _u["password_hash"] != _hash_pw(_lg_pw):
                st.error("비밀번호가 틀렸습니다.")
            elif _u["status"] == "pending":
                st.warning("⏳ 승인 대기 중입니다. 마스터 승인 후 로그인 가능합니다.")
            elif _u["status"] == "rejected":
                st.error("❌ 가입이 거절되었습니다. 관리자에게 문의하세요.")
            elif _u["status"] == "approved":
                # 비밀번호 만료 체크
                if _is_pw_expired(_u):
                    st.session_state["pw_change_required"] = _u
                    st.rerun()
                # OTP 강제 적용됐는데 아직 미설정 → 설정 화면
                elif _u.get("otp_required") and not _u.get("otp_secret"):
                    st.session_state["otp_setup_pending"] = _u
                    st.rerun()
                # OTP 설정된 경우 → 코드 입력
                elif _u.get("otp_secret"):
                    st.session_state["otp_pending"] = _u
                    st.rerun()
                else:
                    _tok = _make_token(_u)
                    st.session_state["auth_user"] = _u
                    st.query_params["auth"] = _tok
                    st.rerun()

    with _tab_register:
        _rg_user = st.text_input("아이디", key="rg_user", placeholder="영문/숫자 조합")
        _rg_name = st.text_input("이름", key="rg_name", placeholder="홍길동")
        _rg_team = st.text_input("팀명", key="rg_team", placeholder="그로스마케팅팀")
        _rg_pw   = st.text_input("비밀번호", type="password", key="rg_pw")
        _rg_pw2  = st.text_input("비밀번호 확인", type="password", key="rg_pw2")
        st.caption("영문 대/소문자·숫자·특수문자 중 3종류 이상, 8자 이상")
        if st.button("가입 신청", type="primary", use_container_width=True, key="rg_btn"):
            if not all([_rg_user, _rg_name, _rg_team, _rg_pw, _rg_pw2]):
                st.warning("모든 항목을 입력해주세요.")
            elif _rg_pw != _rg_pw2:
                st.error("비밀번호가 일치하지 않습니다.")
            else:
                _pw_err = _check_pw_policy(_rg_pw)
                if _pw_err:
                    st.error(_pw_err)
                else:
                    _users = _load_users()
                    if _find_user(_users, _rg_user):
                        st.error("이미 사용 중인 아이디입니다.")
                    else:
                        _users.append({
                            "username": _rg_user,
                            "password_hash": _hash_pw(_rg_pw),
                            "name": _rg_name,
                            "team": _rg_team,
                            "role": "user",
                            "status": "pending",
                            "created_at": str(_auth_dt.date.today()),
                            "pw_changed_at": str(_auth_dt.date.today())
                        })
                        _ok, _msg = _save_users(_users)
                        if _ok:
                            _load_users.clear()
                            st.success("✅ 신청 완료! 마스터 승인 후 로그인 가능합니다.")
                        else:
                            st.error(_msg)

    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# ── 로그인 후: 사이드바 세션 정보 + 관리자 패널 ───────────────────
_cur_user  = st.session_state["auth_user"]
_is_master = _cur_user.get("role") == "master"

# ── OTP 강제 설정 게이트 (토큰 복원 포함, 대시보드 진입 전 차단) ──
if _cur_user.get("otp_required") and not _cur_user.get("otp_secret"):
    if not st.session_state.get("_my_otp_secret_temp"):
        st.session_state["_my_otp_secret_temp"] = _generate_otp_secret()
    _gate_secret = st.session_state["_my_otp_secret_temp"]
    _gate_uri    = _get_otp_uri(_gate_secret, _cur_user["username"])

    st.markdown('<style>.auth-wrap { max-width: 420px; margin: 80px auto; }</style>', unsafe_allow_html=True)
    st.markdown('<div class="auth-wrap">', unsafe_allow_html=True)
    st.title("⚾ 폴리볼 그로스 대시보드")
    st.markdown("### 🔐 OTP 2차 인증 설정 필요")
    st.warning("보안 정책에 따라 OTP 설정을 완료해야 대시보드에 접근할 수 있습니다.")
    _render_otp_setup(_gate_secret, _gate_uri)
    _gate_code = st.text_input("확인 코드 (6자리)", max_chars=6, placeholder="000000", key="gate_otp_code")
    _gp1, _gp2 = st.columns(2)
    if _gp1.button("설정 완료", type="primary", use_container_width=True, key="gate_otp_btn"):
        if _verify_otp(_gate_secret, _gate_code):
            _gu_list = _load_users()
            for _gu in _gu_list:
                if _gu["username"] == _cur_user["username"]:
                    _gu["otp_secret"] = _gate_secret
                    _gu.pop("otp_required", None)
            _save_users(_gu_list)
            _load_users.clear()
            _cur_user["otp_secret"] = _gate_secret
            _cur_user.pop("otp_required", None)
            st.session_state["auth_user"] = _cur_user
            st.session_state.pop("_my_otp_secret_temp", None)
            st.rerun()
        else:
            st.error("❌ 코드가 올바르지 않습니다. 앱의 코드를 다시 확인하세요.")
    if _gp2.button("로그아웃", use_container_width=True, key="gate_logout"):
        st.session_state["auth_user"] = None
        st.session_state.pop("_my_otp_secret_temp", None)
        st.query_params.clear()
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# URL에 토큰 없으면 즉시 발급
_cur_tok = st.query_params.get("auth", "")
if not _cur_tok:
    _cur_tok = _make_token(_cur_user)
    st.query_params["auth"] = _cur_tok

# 현재 토큰 만료 시각 파싱
_exp_ts = 0
try:
    _pad = (4 - len(_cur_tok) % 4) % 4
    _tok_decoded = base64.urlsafe_b64decode((_cur_tok + "=" * _pad).encode()).decode()
    _exp_ts = int(_tok_decoded.split("|")[0])
except Exception:
    pass

with st.sidebar:
    st.markdown("---")
    st.markdown(
        f"**{_cur_user['name']}** ({_cur_user['team']})"
        f"{'  👑' if _is_master else ''}"
    )
    if st.button("🔄 1시간 연장", use_container_width=True, key="extend_btn"):
        _new_tok = _make_token(_cur_user)
        st.query_params["auth"] = _new_tok
        st.rerun()
    if st.button("🚪 로그아웃", use_container_width=True, key="logout_btn"):
        st.session_state["auth_user"] = None
        st.query_params.clear()
        st.rerun()
    # 세션 남은 시간 (버튼 아래)
    _rem_sec = max(0, _exp_ts - int(_time.time()))
    _rem_min = _rem_sec // 60
    if _rem_sec == 0:
        st.caption("⚠️ 세션 만료 — 연장 버튼을 눌러주세요")
    elif _rem_sec <= 600:
        st.caption(f"⏳ 만료까지 {_rem_min}분 남음")
    else:
        st.caption(f"🔐 세션 {_rem_min}분 남음")

    # ── 내 OTP 설정 ───────────────────────────────────────────
    st.markdown("---")
    _has_my_otp = bool(_cur_user.get("otp_secret"))
    if _has_my_otp:
        st.caption("🔐 OTP 2차 인증 활성화됨")
        if st.button("OTP 재설정", use_container_width=True, key="my_otp_reset_btn"):
            st.session_state["my_otp_setup"] = True
            st.session_state.pop("_my_otp_secret_temp", None)
            st.rerun()
    # otp_required 없는 유저는 자발적 설정 버튼 표시
    elif not _cur_user.get("otp_required"):
        if st.button("🔐 OTP 설정하기", use_container_width=True, key="my_otp_setup_btn"):
            st.session_state["my_otp_setup"] = True
            st.session_state.pop("_my_otp_secret_temp", None)
            st.rerun()

    # ── 관리자 패널 (마스터 전용) ─────────────────────────────
    if _is_master:
        st.markdown("---")
        with st.expander("👑 관리자 패널", expanded=False):
            _all_users = _load_users()
            _pending   = [u for u in _all_users if u["status"] == "pending"]
            _approved  = [u for u in _all_users if u["status"] == "approved" and u["role"] != "master"]

            # ── 전체 강제 로그아웃 ────────────────────────────
            st.markdown("**🚨 전체 강제 로그아웃**")
            st.caption("현재 로그인된 모든 유저의 세션을 즉시 무효화합니다 (최대 1분 내 적용)")
            if st.button("전체 강제 로그아웃", use_container_width=True, key="force_logout_all",
                         type="primary"):
                _fl_cfg = _load_session_config()
                _fl_cfg["force_logout_at"] = int(_time.time())
                _save_session_config(_fl_cfg)
                _load_session_config.clear()
                st.success("✅ 완료 — 모든 세션이 무효화됐습니다. (본인 세션 제외)")
            st.markdown("---")
            st.markdown("**⏳ 승인 대기**")
            if not _pending:
                st.caption("대기 중인 신청 없음")
            for _pu in _pending:
                st.markdown(
                    f"**{_pu['name']}** ({_pu['team']}) · `{_pu['username']}`  \n"
                    f"<span style='font-size:11px;color:#94A3B8'>신청일 {_pu.get('created_at','')}</span>",
                    unsafe_allow_html=True
                )
                _pa_col, _pr_col = st.columns(2)
                if _pa_col.button("✅ 승인", key=f"approve_{_pu['username']}", use_container_width=True):
                    for _u in _all_users:
                        if _u["username"] == _pu["username"]:
                            _u["status"] = "approved"
                    _save_users(_all_users)
                    _load_users.clear()
                    st.rerun()
                if _pr_col.button("❌ 거절", key=f"reject_{_pu['username']}", use_container_width=True):
                    for _u in _all_users:
                        if _u["username"] == _pu["username"]:
                            _u["status"] = "rejected"
                    _save_users(_all_users)
                    _load_users.clear()
                    st.rerun()

            st.markdown("**✅ 승인된 유저**")
            if not _approved:
                st.caption("승인된 유저 없음")
            for _au in _approved:
                _ac1, _ac2 = st.columns([3, 1])
                _ac1.markdown(
                    f"**{_au['name']}** ({_au['team']})  \n"
                    f"<span style='font-size:11px;color:#94A3B8'>`{_au['username']}`</span>",
                    unsafe_allow_html=True
                )
                if _ac2.button("🗑️", key=f"revoke_{_au['username']}", help="접근 취소"):
                    for _u in _all_users:
                        if _u["username"] == _au["username"]:
                            _u["status"] = "rejected"
                    _save_users(_all_users)
                    _load_users.clear()
                    st.rerun()

            # ── OTP 2차 인증 관리 ──────────────────────────────
            st.markdown("---")
            st.markdown("**🔐 OTP 2차 인증 관리**")
            st.caption("비밀키는 각 유저가 직접 로그인 후 사이드바에서 설정합니다")
            _otp_targets = [u for u in _all_users if u["status"] == "approved" and u.get("role") != "master"]
            # 전체 강제 적용 / 해제
            _all_otp_req = all(u.get("otp_required") for u in _otp_targets) if _otp_targets else False
            _ma1, _ma2 = st.columns(2)
            if _ma1.button("전체 OTP 강제 적용", use_container_width=True, key="otp_all_on",
                           type="primary" if not _all_otp_req else "secondary"):
                for _u in _all_users:
                    if _u["status"] == "approved" and _u.get("role") != "master":
                        _u["otp_required"] = True
                _save_users(_all_users)
                _load_users.clear()
                st.rerun()
            if _ma2.button("전체 OTP 강제 해제", use_container_width=True, key="otp_all_off"):
                for _u in _all_users:
                    _u.pop("otp_required", None)
                _save_users(_all_users)
                _load_users.clear()
                st.rerun()
            # 개별 상태 + 리셋
            st.markdown("")
            if not _otp_targets:
                st.caption("승인된 유저 없음")
            for _ou in _otp_targets:
                _has_otp    = bool(_ou.get("otp_secret"))
                _otp_req    = bool(_ou.get("otp_required"))
                if _has_otp:
                    _otp_status = "🔐 OTP 완료"
                    _otp_color  = "#16a34a"
                elif _otp_req:
                    _otp_status = "⏳ 설정 대기"
                    _otp_color  = "#d97706"
                else:
                    _otp_status = "🔓 미설정"
                    _otp_color  = "#94A3B8"
                _ot1, _ot2 = st.columns([3, 1])
                _ot1.markdown(
                    f"**{_ou['name']}** (`{_ou['username']}`)  \n"
                    f"<span style='font-size:11px;color:{_otp_color}'>{_otp_status}</span>",
                    unsafe_allow_html=True
                )
                if _has_otp:
                    if _ot2.button("초기화", key=f"otp_reset_{_ou['username']}", use_container_width=True,
                                   help="OTP 초기화 — 유저가 기기 분실 시 사용"):
                        for _u in _all_users:
                            if _u["username"] == _ou["username"]:
                                _u.pop("otp_secret", None)
                        _save_users(_all_users)
                        _load_users.clear()
                        st.rerun()
                else:
                    _ot2.caption("—")

    st.markdown("---")

# ── 로그인 후 OTP 자가 설정 화면 ────────────────────────────────
if st.session_state.get("my_otp_setup"):
    if not st.session_state.get("_my_otp_secret_temp"):
        st.session_state["_my_otp_secret_temp"] = _generate_otp_secret()
    _ms_secret = st.session_state["_my_otp_secret_temp"]
    _ms_uri    = _get_otp_uri(_ms_secret, _cur_user["username"])

    st.title("🔐 OTP 2차 인증 설정")
    _render_otp_setup(_ms_secret, _ms_uri)
    _ms_code = st.text_input("확인 코드 (6자리)", max_chars=6, placeholder="000000", key="ms_confirm_code")
    _ms1, _ms2 = st.columns(2)
    if _ms1.button("설정 완료", type="primary", use_container_width=True, key="ms_confirm_btn"):
        if _verify_otp(_ms_secret, _ms_code):
            _ms_users = _load_users()
            for _u in _ms_users:
                if _u["username"] == _cur_user["username"]:
                    _u["otp_secret"] = _ms_secret
                    _u.pop("otp_required", None)
            _save_users(_ms_users)
            _load_users.clear()
            _cur_user["otp_secret"] = _ms_secret
            _cur_user.pop("otp_required", None)
            st.session_state["auth_user"] = _cur_user
            st.session_state["my_otp_setup"] = False
            st.session_state.pop("_my_otp_secret_temp", None)
            st.success("✅ OTP 설정 완료! 다음 로그인부터 2차 인증이 적용됩니다.")
            st.rerun()
        else:
            st.error("❌ 코드가 올바르지 않습니다. 앱의 코드를 다시 확인하세요.")
    if _ms2.button("취소", use_container_width=True, key="ms_cancel_btn"):
        st.session_state["my_otp_setup"] = False
        st.session_state.pop("_my_otp_secret_temp", None)
        st.rerun()
    st.stop()

st.markdown("""
<style>
/* ── 데이터 출처 뱃지 ── */
.badge-ab {
    display: inline-block;
    background: #4F86F7;
    color: white;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
    margin-left: 4px;
    vertical-align: middle;
    letter-spacing: 0.3px;
}
.badge-srv {
    display: inline-block;
    background: #27AE60;
    color: white;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
    margin-left: 4px;
    vertical-align: middle;
    letter-spacing: 0.3px;
}

/* ── 섹션 헤더 (합계 / 웹 / 앱) ── */
.metrics-section {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 12px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 700;
    margin: 10px 0 4px 0;
}
.sec-total { background: #f0f4ff; border-left: 4px solid #4F86F7; color: #1a3a8f; }
.sec-web   { background: #f0faf4; border-left: 4px solid #2ECC71; color: #1a5c35; }
.sec-app   { background: #fff8ee; border-left: 4px solid #F39C12; color: #7d4d00; }
.sec-cost  { background: #fdf4ff; border-left: 4px solid #9B59B6; color: #4a1070; }

/* ── 메트릭 그리드 래퍼 ── */
.metric-grid-wrap {
    background: #fafafa;
    border: 1px solid #e8eaed;
    border-radius: 10px;
    padding: 12px 14px 8px 14px;
    margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)

# ─── 에어브릿지 Actuals API로 기간 유니크 AU 조회 ─────────────
AIRBRIDGE_ACTUALS_URL = "https://api.airbridge.io/reports/api/v7/apps/polyballkr/actuals/query"

def _airbridge_request(payload):
    """에어브릿지 Actuals API 요청 + 폴링"""
    import time
    token = st.secrets.get("AIRBRIDGE_API_TOKEN", "")
    if not token:
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(AIRBRIDGE_ACTUALS_URL, headers=headers, json=payload, timeout=30)
        if r.status_code != 200:
            return None
        result = r.json()
        task = result.get("task", {})
        task_id = task.get("taskId")
        if not task_id:
            return None
        if task.get("status") == "SUCCESS":
            return result
        for _ in range(20):
            time.sleep(0.5)
            r2 = requests.get(f"{AIRBRIDGE_ACTUALS_URL}/{task_id}", headers=headers, timeout=30)
            if r2.status_code != 200:
                continue
            result2 = r2.json()
            status = result2.get("task", {}).get("status")
            if status == "SUCCESS":
                return result2
            if status == "FAILURE":
                return None
    except Exception:
        pass
    return None

def _get_actuals_total(result):
    """Actuals 응답에서 total dict 추출"""
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
    if not actuals:
        return {}
    return actuals.get("data", {}).get("total", {})

_HIDE_CHANNELS = {"$$default$$", "test", ""}

def _parse_channel_rows(rows):
    out = []
    for row in rows:
        gbs = row.get("groupBys", [])
        dt_val   = gbs[0] if len(gbs) > 0 else ""
        channel  = gbs[1] if len(gbs) > 1 else ""
        campaign = gbs[2] if len(gbs) > 2 else ""
        if channel in _HIDE_CHANNELS:
            continue
        label = f"{channel} / {campaign}" if campaign else channel
        vals    = row.get("values", {})
        clicks  = int(vals.get("clicks", {}).get("value", 0))
        web_sg  = int(vals.get("web_custom_users_signup", {}).get("value", 0))
        app_sg  = int(vals.get("app_custom_users_signup", {}).get("value", 0))
        signups = web_sg + app_sg
        out.append({"날짜": dt_val, "채널": label, "클릭": clicks, "가입": signups})
    return out

def _query_channel_rows(from_date, to_date):
    payload = {
        "from": from_date, "to": to_date,
        "metrics": ["clicks", "web_custom_users_signup", "app_custom_users_signup"],
        "groupBys": ["event_date", "channel", "campaign"],
        "filters": [], "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": False,
        "option": {"timezone": "Asia/Seoul", "eventTimestampSource": "event_occurred_date"},
        "size": 2000,
    }
    result = _airbridge_request(payload)
    if not result:
        return []
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
    if not actuals:
        return []
    return actuals.get("data", {}).get("rows", [])

@st.cache_data(ttl=600, show_spinner=False)
def fetch_channel_daily(from_date, to_date):
    """에어브릿지에서 channel × campaign × 일별 클릭 + 가입 조회.
    배치 파이프라인 lag 보완: 최근 3일은 개별 1일 쿼리로 추가 조회."""
    import datetime as _dt_ch
    rows = _query_channel_rows(from_date, to_date)
    out = _parse_channel_rows(rows)

    # 최근 3일은 배치 데이터를 버리고 항상 개별 쿼리로 덮어씀
    # (배치 lag로 인해 같은 날짜라도 채널별 누락 발생하는 문제 방지)
    end_d = _dt_ch.date.fromisoformat(to_date)
    start_d = _dt_ch.date.fromisoformat(from_date)
    for i in range(3):
        day = end_d - _dt_ch.timedelta(days=i)
        if day < start_d:
            break
        day_str = day.isoformat()
        out = [r for r in out if r["날짜"] != day_str]  # 배치 데이터 제거
        day_rows = _query_channel_rows(day_str, day_str)
        out.extend(_parse_channel_rows(day_rows))

    return sorted(out, key=lambda r: r["날짜"])


def _fetch_airbridge_au_raw(from_date, to_date):
    """실제 Airbridge API 호출 (캐시 없음)."""
    payload = {
        "from": from_date, "to": to_date,
        "metrics": ["app_active_users", "web_open_users"],
        "groupBys": [], "filters": [], "sorts": [],
        "isSummaryAvailable": True,
        "option": {"timezone": "Asia/Seoul", "eventTimestampSource": "event_occurred_date"},
    }
    result = _airbridge_request(payload)
    if not result:
        return None
    total = _get_actuals_total(result)
    return {
        "app_au": int(total.get("app_active_users", {}).get("value", 0)),
        "web_au": int(total.get("web_open_users", {}).get("value", 0)),
    }

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_airbridge_au_historical(from_date, to_date):
    """과거 AU 조회 — 24시간 캐시 (값이 바뀌지 않는 과거 구간용)."""
    return _fetch_airbridge_au_raw(from_date, to_date)

@st.cache_data(ttl=600, show_spinner="AU 데이터 로딩 중...")
def fetch_airbridge_au(from_date, to_date):
    """기간 내 유니크 AU (에어브릿지 Actuals) — 10분 캐시."""
    return _fetch_airbridge_au_raw(from_date, to_date)

@st.cache_data(ttl=600, show_spinner="DAU 데이터 로딩 중...")
def fetch_airbridge_dau(from_date, to_date):
    """일별 DAU (에어브릿지 Actuals, event_date groupBy)"""
    payload = {
        "from": from_date, "to": to_date,
        "metrics": ["app_active_users", "web_open_users"],
        "groupBys": ["event_date"], "filters": [],
        "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": True,
        "option": {"timezone": "Asia/Seoul", "eventTimestampSource": "event_occurred_date"},
        "size": 100,
    }
    result = _airbridge_request(payload)
    if not result:
        return None
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
    if not actuals:
        return None
    data = actuals.get("data", {})
    total = data.get("total", {})
    avg = data.get("average", {})
    rows = data.get("rows", [])
    # 일별 데이터 파싱
    daily = []
    for row in rows:
        gbs = row.get("groupBys", [])
        vals = row.get("values", {})
        date_str = gbs[0] if gbs else ""
        daily.append({
            "date": date_str,
            "dau_app": int(vals.get("app_active_users", {}).get("value", 0)),
            "dau_web": int(vals.get("web_open_users", {}).get("value", 0)),
        })
    return {
        "daily": daily,
        "avg_app": int(avg.get("app_active_users", {}).get("value", 0)),
        "avg_web": int(avg.get("web_open_users", {}).get("value", 0)),
        "total_app": int(total.get("app_active_users", {}).get("value", 0)),
        "total_web": int(total.get("web_open_users", {}).get("value", 0)),
    }

# ─── 앱 리텐션 — Airbridge Retention API v5 ─────
AIRBRIDGE_RETENTION_URL_V5 = "https://api.airbridge.io/reports/api/v5/apps/polyballkr/retention/query"

@st.cache_data(ttl=3600, show_spinner="앱 리텐션 조회 중...")
def fetch_airbridge_retention_report(from_date: str, to_date: str, group_by: str = "channel", return_events = None):
    """Airbridge Retention API v5 호출.
    Payload 구조 (대시보드 F12로 확인):
      granularity: "day", measurementOption: "general_retention",
      retentionType: "return_on", startEvents: ["app_installs"],
      returnEvents: 기본 ["any_event"], custom event 전달 시 해당 이벤트 기반 리텐션
    - return_events: None=기본, list=커스텀 (예: ["app_custom_events_pv_ad_reward_completed"])
    반환: {"raw": {...}, "ok": True} or {"error": "...", "status": N, "body": "..."}
    """
    token = st.secrets.get("AIRBRIDGE_API_TOKEN", "")
    if not token:
        return {"error": "AIRBRIDGE_API_TOKEN 없음"}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # granularityOption origin = from_date 하루 전 00:00:00
    import datetime as _dtR
    try:
        _origin_dt = _dtR.date.fromisoformat(from_date) - _dtR.timedelta(days=1)
        _origin = f"{_origin_dt.isoformat()} 00:00:00"
    except Exception:
        _origin = f"{from_date} 00:00:00"

    payload = {
        "from": from_date,
        "to": to_date,
        "granularity": "day",
        "granularityOption": {"origin": _origin},
        "groupBy": {"dimensions": [group_by] if group_by else [], "cohorts": []},
        "intervalsPeriod": 30,
        "keyword": "",
        "measurementOption": "general_retention",
        "retentionType": "return_on",
        "returnEvents": return_events if return_events else ["any_event"],
        "sorts": [{"fieldName": "totalValue", "isAscending": False}],
        "startEvents": ["app_installs"],
        "filters": [],
    }

    import time
    try:
        r = requests.post(AIRBRIDGE_RETENTION_URL_V5, headers=headers, json=payload, timeout=30)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "status": r.status_code, "body": r.text[:500], "payload": payload}

        result = r.json()
        task = result.get("task", {})
        task_id = task.get("taskId")
        status = task.get("status")

        # 즉시 성공 케이스
        if status == "SUCCESS" and (result.get("data") or result.get("reportData")):
            return {"ok": True, "raw": result, "payload": payload}

        if not task_id:
            return {"error": "taskId 없음", "body": str(result)[:500], "payload": payload}

        # polling (최대 30회 × 1초 = 30초)
        for _ in range(30):
            time.sleep(1.0)
            r2 = requests.get(f"{AIRBRIDGE_RETENTION_URL_V5}/{task_id}", headers=headers, timeout=30)
            if r2.status_code != 200:
                continue
            result2 = r2.json()
            status2 = result2.get("task", {}).get("status")
            if status2 == "SUCCESS":
                return {"ok": True, "raw": result2, "payload": payload}
            if status2 == "FAILURE":
                return {"error": "task FAILURE", "body": str(result2)[:500], "payload": payload}

        return {"error": "polling timeout (30s)", "task_id": task_id, "payload": payload}

    except Exception as e:
        return {"error": f"exception: {str(e)[:200]}", "payload": payload}

# 07_LTV/앱_리텐션_API_요청서.md 참조 (서버 API 대안)


def parse_retention_v5(raw: dict) -> dict:
    """Airbridge Retention API v5 응답 → 채널별 매트릭스.
    반환: {
      channel_name: {
        'total_size': int,
        'total_values': [{day, count, rate}],
        'cohorts': {date_str: {'size': N, 'values': [{day, count, rate, incomplete}]}}
      }
    }
    """
    channels = {}
    rows = raw.get("retention", {}).get("data", {}).get("rows", [])
    for row in rows:
        meta = row.get("metadata", [])
        channel = meta[0] if meta else "unknown"
        total = row.get("total", {})
        tot_size = total.get("totalValue", {}).get("value", 0)
        tot_values = [
            {"day": i, "count": v.get("value", 0), "rate": v.get("rate", 0)}
            for i, v in enumerate(total.get("values", []))
        ]
        cohorts = {}
        for cr in row.get("rows", []):
            date_str = cr.get("date", "").split("T")[0]
            size = cr.get("totalValue", {}).get("value", 0)
            vals = [
                {"day": i, "count": v.get("value", 0), "rate": v.get("rate", 0),
                 "incomplete": v.get("isIncomplete", False)}
                for i, v in enumerate(cr.get("values", []))
            ]
            cohorts[date_str] = {"size": size, "values": vals}
        channels[channel] = {
            "total_size": tot_size,
            "total_values": tot_values,
            "cohorts": cohorts,
        }
    return channels


# ─── 광고 퍼널 이벤트 (c_ad_entry / pv_ad / pv_ad_reward_completed) ─
@st.cache_data(ttl=600, show_spinner="광고 퍼널 조회 중...")
def fetch_ad_funnel(from_date: str, to_date: str):
    """Airbridge Actuals에서 광고 퍼널 3개 이벤트 조회.
    action(pick/apply) × label(01/02/03/04/05_up) 브레이크다운 시도.
    반환: {
      'by_action_label': {(action, label): {entry, ad, reward, entry_u, ad_u, reward_u}},
      'daily': {date: {entry, ad, reward}},
      'total': {entry, ad, reward, entry_u, ad_u, reward_u},
      'source': 'airbridge',
    } or None
    """
    # 중요: Airbridge Actuals API metric 네이밍
    # - `app_custom_<event>` = 이벤트 수 (대시보드 UI에서 보는 값)
    # - `app_custom_users_<event>` = 유니크 유저 수
    # - `app_custom_events_<event>` = 항상 0 반환 (유효하지 않음, 호환 위해 유지)
    metrics = [
        "app_custom_c_ad_entry",                       # 이벤트 수
        "app_custom_pv_ad",                            # 이벤트 수
        "app_custom_pv_ad_reward_completed",           # 이벤트 수
        "app_custom_users_c_ad_entry",                 # 유니크 유저
        "app_custom_users_pv_ad",                      # 유니크 유저
        "app_custom_users_pv_ad_reward_completed",     # 유니크 유저
    ]

    # --- 일별 총계 (event_date groupBy) ---
    payload_daily = {
        "from": from_date, "to": to_date,
        "metrics": metrics,
        "groupBys": ["event_date"],
        "filters": [], "sorts": [{"fieldName": "event_date", "isAscending": True}],
        "isSummaryAvailable": True,
        "option": {"timezone": "Asia/Seoul", "eventTimestampSource": "event_occurred_date"},
        "size": 1000,
    }
    result = _airbridge_request(payload_daily)
    if not result:
        return None
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
    if not actuals:
        return None

    data = actuals.get("data", {})
    rows = data.get("rows", [])
    total = data.get("total", {})

    daily = {}
    for row in rows:
        gbs = row.get("groupBys", [])
        if not gbs:
            continue
        d = gbs[0]
        vals = row.get("values", {})
        daily[d] = {
            "entry": int(vals.get("app_custom_c_ad_entry", {}).get("value", 0)),
            "ad":    int(vals.get("app_custom_pv_ad", {}).get("value", 0)),
            "reward": int(vals.get("app_custom_pv_ad_reward_completed", {}).get("value", 0)),
            "entry_u":  int(vals.get("app_custom_users_c_ad_entry", {}).get("value", 0)),
            "ad_u":     int(vals.get("app_custom_users_pv_ad", {}).get("value", 0)),
            "reward_u": int(vals.get("app_custom_users_pv_ad_reward_completed", {}).get("value", 0)),
        }

    total_vals = {
        "entry":    int(total.get("app_custom_c_ad_entry", {}).get("value", 0)),
        "ad":       int(total.get("app_custom_pv_ad", {}).get("value", 0)),
        "reward":   int(total.get("app_custom_pv_ad_reward_completed", {}).get("value", 0)),
        "entry_u":  int(total.get("app_custom_users_c_ad_entry", {}).get("value", 0)),
        "ad_u":     int(total.get("app_custom_users_pv_ad", {}).get("value", 0)),
        "reward_u": int(total.get("app_custom_users_pv_ad_reward_completed", {}).get("value", 0)),
    }

    # --- date × action 브레이크다운 (일별 × 픽/응모) ---
    by_date_action = {}  # {(date, action): {entry_u, ad_u, reward_u}}
    payload_da = {
        "from": from_date, "to": to_date,
        "metrics": metrics,
        "groupBys": ["event_date", "event_action"],
        "filters": [], "sorts": [],
        "isSummaryAvailable": False,
        "option": {"timezone": "Asia/Seoul", "eventTimestampSource": "event_occurred_date"},
        "size": 500,
    }
    result_da = _airbridge_request(payload_da)
    if result_da:
        actuals_da = result_da.get("actuals") or result_da.get("reportData", {}).get("actuals")
        if actuals_da:
            for row in actuals_da.get("data", {}).get("rows", []):
                gbs = row.get("groupBys", [])
                if len(gbs) < 2:
                    continue
                dt, action = gbs[0], gbs[1]
                vals = row.get("values", {})
                by_date_action[(dt, action)] = {
                    "entry_u":  int(vals.get("app_custom_users_c_ad_entry", {}).get("value", 0)),
                    "ad_u":     int(vals.get("app_custom_users_pv_ad", {}).get("value", 0)),
                    "reward_u": int(vals.get("app_custom_users_pv_ad_reward_completed", {}).get("value", 0)),
                }

    # --- action별 유니크 유저 (라벨 차원 없이) ---
    # 중요: Airbridge users 메트릭은 차원마다 유니크 집계되어 차원 합산 시 중복됨.
    # action별 진짜 유니크 유저 수를 얻으려면 groupBys=["event_action"]로 별도 쿼리 필수.
    by_action = {}  # {action: {entry, ad, reward, entry_u, ad_u, reward_u}}
    payload_act = {
        "from": from_date, "to": to_date,
        "metrics": metrics,
        "groupBys": ["event_action"],
        "filters": [], "sorts": [],
        "isSummaryAvailable": False,
        "option": {"timezone": "Asia/Seoul", "eventTimestampSource": "event_occurred_date"},
        "size": 50,
    }
    result_act = _airbridge_request(payload_act)
    if result_act:
        actuals_act = result_act.get("actuals") or result_act.get("reportData", {}).get("actuals")
        if actuals_act:
            for row in actuals_act.get("data", {}).get("rows", []):
                gbs = row.get("groupBys", [])
                if not gbs:
                    continue
                action = gbs[0]
                vals = row.get("values", {})
                by_action[action] = {
                    "entry":    int(vals.get("app_custom_c_ad_entry", {}).get("value", 0)),
                    "ad":       int(vals.get("app_custom_pv_ad", {}).get("value", 0)),
                    "reward":   int(vals.get("app_custom_pv_ad_reward_completed", {}).get("value", 0)),
                    "entry_u":  int(vals.get("app_custom_users_c_ad_entry", {}).get("value", 0)),
                    "ad_u":     int(vals.get("app_custom_users_pv_ad", {}).get("value", 0)),
                    "reward_u": int(vals.get("app_custom_users_pv_ad_reward_completed", {}).get("value", 0)),
                }

    # --- action × label 브레이크다운 (Airbridge 정식 dimension: event_action, event_label) ---
    by_action_label = {}
    payload_al = {
        "from": from_date, "to": to_date,
        "metrics": metrics,
        "groupBys": ["event_action", "event_label"],
        "filters": [], "sorts": [],
        "isSummaryAvailable": False,
        "option": {"timezone": "Asia/Seoul", "eventTimestampSource": "event_occurred_date"},
        "size": 100,
    }
    result_al = _airbridge_request(payload_al)
    if result_al:
        actuals_al = result_al.get("actuals") or result_al.get("reportData", {}).get("actuals")
        if actuals_al:
            for row in actuals_al.get("data", {}).get("rows", []):
                gbs = row.get("groupBys", [])
                if len(gbs) < 2:
                    continue
                action, label = gbs[0], gbs[1]
                vals = row.get("values", {})
                by_action_label[(action, label)] = {
                    "entry":    int(vals.get("app_custom_c_ad_entry", {}).get("value", 0)),
                    "ad":       int(vals.get("app_custom_pv_ad", {}).get("value", 0)),
                    "reward":   int(vals.get("app_custom_pv_ad_reward_completed", {}).get("value", 0)),
                    "entry_u":  int(vals.get("app_custom_users_c_ad_entry", {}).get("value", 0)),
                    "ad_u":     int(vals.get("app_custom_users_pv_ad", {}).get("value", 0)),
                    "reward_u": int(vals.get("app_custom_users_pv_ad_reward_completed", {}).get("value", 0)),
                }

    return {
        "daily": daily,
        "total": total_vals,
        "by_action": by_action,           # action별 진짜 유니크 유저 (중복 없음)
        "by_action_label": by_action_label,  # label별 (이벤트 수 집계용)
        "by_date_action": by_date_action,
        "source": "airbridge",
    }

# ─── GitHub에서 data.json 로드 ──────────────────────────────
DATA_PATH = "data.json"
ISSUE_LOG_PATH = "issue_log.json"
MILESTONE_PATH = "milestone.json"
RAW_DATA_DB_PATH = "raw_data.db"
_UPLOAD_STAGING_DIR = ".dashboard_upload_staging"


def _resolve_upload_target(filename: str):
    """업로드 파일명 → 프로젝트 루트 기준 상대 경로. 허용되지 않으면 None."""
    name = os.path.basename((filename or "").strip())
    if not name or name in (".", ".."):
        return None
    lower = name.lower()
    if lower == "data.json":
        return DATA_PATH
    if lower == "raw_data.db":
        return RAW_DATA_DB_PATH
    if lower == "app.py":
        return "app.py"
    if name.endswith(".md") and len(name) == 11 and name[:8].isdigit():
        return os.path.join("daily", name)
    if name.endswith(".md") and "_주간리포트" in name:
        return os.path.join("weekly", name)
    if name.endswith(".md") and "_월간리포트" in name:
        return os.path.join("monthly", name)
    return None


def _staging_base() -> Path:
    return Path(_UPLOAD_STAGING_DIR)


def _list_staging_pending():
    """스테이징에 올라온 파일 상대 경로 목록 (POSIX 스타일)."""
    base = _staging_base()
    if not base.is_dir():
        return []
    out = []
    for p in base.rglob("*"):
        if p.is_file():
            out.append(str(p.relative_to(base)).replace("\\", "/"))
    return sorted(out)


def _stage_uploaded_files(uploaded_files) -> tuple[list, list]:
    """Streamlit UploadedFile 목록을 스테이징 디렉터리에 저장. (성공 경로들, 오류 메시지들)"""
    ok, errs = [], []
    if not uploaded_files:
        return ok, errs
    base = _staging_base()
    base.mkdir(parents=True, exist_ok=True)
    for uf in uploaded_files:
        rel = _resolve_upload_target(uf.name)
        if not rel:
            errs.append(
                f"{uf.name}: 허용되지 않는 파일명입니다. "
                "(data.json, raw_data.db, app.py, daily YYYYMMDD.md, weekly/monthly 리포트 .md)"
            )
            continue
        try:
            data = uf.getvalue()
        except Exception as e:
            errs.append(f"{uf.name}: 읽기 실패 ({e})")
            continue
        if rel == RAW_DATA_DB_PATH:
            if not data.startswith(b"SQLite format 3"):
                errs.append(f"{uf.name}: SQLite DB(raw_data.db)가 아닙니다.")
                continue
        elif rel == DATA_PATH:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                errs.append(f"{uf.name}: UTF-8 인코딩이 아닙니다.")
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as e:
                errs.append(f"{uf.name}: JSON이 아닙니다 ({e})")
                continue
            if not isinstance(parsed, dict):
                errs.append(f"{uf.name}: data.json 루트는 JSON 객체여야 합니다.")
                continue
            _need = ("daily", "channels", "paid")
            _missing = [k for k in _need if k not in parsed]
            if _missing:
                errs.append(f"{uf.name}: 대시보드 필수 키 누락: {', '.join(_missing)}")
                continue
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                errs.append(f"{uf.name}: UTF-8 인코딩이 아닙니다.")
                continue
        dest = base / Path(rel)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            ok.append(str(Path(rel)).replace("\\", "/"))
        except Exception as e:
            errs.append(f"{uf.name}: 스테이징 저장 실패 — {e}")
    return ok, errs


def _apply_staging_to_live() -> tuple[list, list]:
    """스테이징 파일을 작업 디렉터리(실제 데이터 경로)로 복사한 뒤 스테이징에서 제거."""
    applied, errors = [], []
    base = _staging_base()
    if not base.is_dir():
        return applied, errors
    files = sorted([p for p in base.rglob("*") if p.is_file()])
    for src in files:
        rel = src.relative_to(base)
        rel_s = str(rel).replace("\\", "/")
        dest = Path(rel_s)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            src.unlink()
            applied.append(rel_s)
        except Exception as e:
            errors.append(f"{rel_s}: {e}")
    # 빈 디렉터리 정리
    if base.exists():
        for p in sorted(base.rglob("*"), reverse=True):
            if p.is_dir():
                try:
                    p.rmdir()
                except OSError:
                    pass
        try:
            base.rmdir()
        except OSError:
            pass
    return applied, errors


@st.cache_data(ttl=300, show_spinner=False)
def load_daily_report_list():
    """daily/ 폴더의 파일 목록을 날짜→경로 dict로 반환"""
    result = {}
    for f in _local_list_tree("daily"):
        name = f["name"]
        if name.endswith(".md") and len(name) == 11:  # YYYYMMDD.md
            date_str = f"{name[:4]}-{name[4:6]}-{name[6:8]}"
            result[date_str] = f["path"]
    return result

@st.cache_data(ttl=300, show_spinner=False)
def load_report_list(folder_encoded, pattern):
    """folder 내 파일 목록을 {표시명: path} dict로 반환. pattern='weekly'|'monthly'"""
    import urllib.parse as _ul
    folder = _ul.unquote(folder_encoded)
    result = {}
    for f in _local_list_tree(folder):
        name = f["name"]
        if not name.endswith(".md") or name == ".gitkeep":
            continue
        if pattern == "weekly" and "_주간리포트" in name:
            result[name.split("_")[0]] = f["path"]
        elif pattern == "monthly" and "_월간리포트" in name:
            result[name.split("_")[0]] = f["path"]
    return result

@st.cache_data(ttl=300, show_spinner=False)
def load_daily_report(file_path):
    """개별 데일리 리포트 파일 내용 반환"""
    return _local_read_file(file_path)

_ANALYSIS_HEADING = re.compile(r"^## \d+\. 분석\b")

def extract_analysis_section(content):
    """마크다운에서 ## {N}. 분석 섹션만 추출 (예: §8 분석, §10 분석)."""
    if not content:
        return ""
    lines = content.split("\n")
    in_section = False
    section_lines = []
    for line in lines:
        if _ANALYSIS_HEADING.match(line):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            section_lines.append(line)
    return "\n".join(section_lines).strip()

def strip_analysis_section(content):
    """마크다운에서 ## {N}. 분석 섹션을 제외한 본문 반환."""
    if not content:
        return ""
    lines = content.split("\n")
    out = []
    in_analysis = False
    for line in lines:
        if _ANALYSIS_HEADING.match(line):
            in_analysis = True
            continue
        if in_analysis:
            if line.startswith("## "):
                in_analysis = False
            else:
                continue
        out.append(line)
    return "\n".join(out).strip()

@st.cache_data(ttl=300, show_spinner=False)
def load_data():
    raw = _local_read_file(DATA_PATH)
    if raw is None:
        return None
    return json.loads(raw)

def save_data(new_data, commit_msg="update: data.json 업데이트"):
    """data.json을 로컬에 덮어씀. 성공 시 True 반환."""
    return _local_write_file(DATA_PATH, json.dumps(new_data, ensure_ascii=False, indent=2), commit_msg)

@st.cache_data(ttl=60, show_spinner=False)
def load_issue_log():
    """issue_log.json을 로컬에서 로드."""
    raw = _local_read_file(ISSUE_LOG_PATH)
    if raw is None:
        return []
    return json.loads(raw)

def save_issue_log(log_list, commit_msg):
    """issue_log.json을 로컬에 덮어씀."""
    return _local_write_file(ISSUE_LOG_PATH, json.dumps(log_list, ensure_ascii=False, indent=2), commit_msg)

@st.cache_data(ttl=60, show_spinner=False)
def load_milestone():
    """milestone.json을 로컬에서 로드."""
    raw = _local_read_file(MILESTONE_PATH)
    if raw is None:
        return {"phases": ["기획", "UX", "FE", "서버"], "items": []}
    return json.loads(raw)

def _save_milestone_sync(ms_data, commit_msg):
    """milestone.json을 로컬에 덮어씀 (동기)."""
    return _local_write_file(MILESTONE_PATH, json.dumps(ms_data, ensure_ascii=False, indent=2), commit_msg)

import threading as _threading

def save_milestone(ms_data, commit_msg):
    """세션 캐시 즉시 갱신 + GitHub 백그라운드 저장."""
    st.session_state["_ms_cache"] = ms_data
    load_milestone.clear()
    _t = _threading.Thread(target=_save_milestone_sync, args=(ms_data, commit_msg), daemon=True)
    _t.start()
    return True, "저장 완료"

data = load_data()

if not data:
    st.error("데이터를 불러오지 못했습니다. 데이터 파일이 존재하는지 확인해주세요.")
    st.stop()

df_daily    = pd.DataFrame(data["daily"])
df_channel  = pd.DataFrame(data["channels"])
df_paid     = pd.DataFrame(data["paid"])
df_funnel   = pd.DataFrame(data.get("funnel", []))
df_insights = pd.DataFrame(data.get("insights", []))

for df in [df_daily, df_channel, df_paid, df_funnel, df_insights]:
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])

# ─── Sidebar ──────────────────────────────────────────────
with st.sidebar:
    st.title("⚾ 폴리볼")
    st.caption("그로스 대시보드")
    st.divider()

    _apply_notice = st.session_state.pop("_upload_apply_notice", None)
    if _apply_notice:
        st.success(_apply_notice)
    _apply_warn = st.session_state.pop("_upload_apply_warn", None)
    if _apply_warn:
        st.warning(_apply_warn)

    with st.expander("📤 데이터 업로드", expanded=False):
        _st_ok = st.session_state.pop("_upload_stage_notice", None)
        if _st_ok:
            st.success(_st_ok)
        _st_er = st.session_state.pop("_upload_stage_warn", None)
        if _st_er:
            st.error(_st_er)
        st.caption(
            "data.json · raw_data.db · app.py · daily/YYYYMMDD.md · weekly/*_주간리포트.md · monthly/*_월간리포트.md 만 허용합니다. "
            "업로드 직후에는 디스크의 기존 파일을 바꾸지 않습니다. 반영은 아래 **데이터 새로고침**에서 합니다."
        )
        _pending = _list_staging_pending()
        if _pending:
            st.info(f"반영 대기 **{len(_pending)}**개: `{', '.join(_pending[:8])}`" + (" …" if len(_pending) > 8 else ""))
        else:
            st.caption("반영 대기 파일 없음")
        _batch = st.file_uploader(
            "파일 선택",
            type=["json", "db", "md", "py"],
            accept_multiple_files=True,
            key="dashboard_data_upload_batch",
        )
        if st.button("대기열에 올리기", use_container_width=True, key="dashboard_stage_upload_btn"):
            if not _batch:
                st.session_state["_upload_stage_warn"] = "파일을 선택하세요."
            else:
                _ok_paths, _errs = _stage_uploaded_files(_batch)
                if _ok_paths:
                    st.session_state["_upload_stage_notice"] = (
                        f"스테이징 완료 ({len(_ok_paths)}개): " + ", ".join(_ok_paths)
                    )
                if _errs:
                    st.session_state["_upload_stage_warn"] = "\n".join(_errs)
            st.rerun()

    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        today = _dt.datetime.now(ZoneInfo("Asia/Seoul")).date()
    except Exception:
        today = _dt.date.today()
    dates = df_daily["date"].dt.date.tolist()
    max_date = max(dates)
    min_date = min(dates)

    st.markdown("**조회 기간**")

    def _set_range(s, e):
        st.session_state["_start"] = s
        st.session_state["_end"] = e

    yesterday = today - _dt.timedelta(days=1)
    col_a, col_b = st.columns(2)
    col_c, col_d = st.columns(2)
    if col_a.button("어제", use_container_width=True):
        _set_range(yesterday, yesterday)
        st.rerun()
    if col_b.button("7일", use_container_width=True):
        _set_range(max(min_date, yesterday - _dt.timedelta(days=6)), yesterday)
        st.rerun()
    if col_c.button("14일", use_container_width=True):
        _set_range(max(min_date, yesterday - _dt.timedelta(days=13)), yesterday)
        st.rerun()
    if col_d.button("전체", use_container_width=True):
        _set_range(min_date, yesterday)
        st.rerun()

    date_max = max(max_date, yesterday)
    start = st.date_input("시작일", value=st.session_state.get("_start", min_date), min_value=min_date, max_value=date_max, key="_start")
    end   = st.date_input("종료일", value=st.session_state.get("_end", yesterday), min_value=min_date, max_value=date_max, key="_end")

    if end < start:
        st.warning("종료일이 시작일보다 빠릅니다.")
        end = start

    if st.button("🔄 데이터 새로고침", use_container_width=True):
        _applied_paths, _apply_errs = _apply_staging_to_live()
        if _applied_paths:
            parts = ", ".join(_applied_paths)
            st.session_state["_upload_apply_notice"] = f"업로드 반영 완료 ({len(_applied_paths)}개): {parts}"
        if _apply_errs:
            st.session_state["_upload_apply_warn"] = "일부 반영 실패:\n" + "\n".join(_apply_errs)
        st.cache_data.clear()
        st.rerun()

    st.divider()
    last = df_daily.iloc[-1]
    st.caption(f"마지막 데이터: {last['date'].strftime('%Y-%m-%d')}")
    st.caption("매일 리포트 생성 시 자동 업데이트")

    # Tab 9 리텐션 날짜는 탭 안에서 선택

# ─── 날짜 필터 ─────────────────────────────────────────────
mask_d = (df_daily["date"].dt.date >= start) & (df_daily["date"].dt.date <= end)
mask_c = (df_channel["date"].dt.date >= start) & (df_channel["date"].dt.date <= end)
mask_p = (df_paid["date"].dt.date >= start) & (df_paid["date"].dt.date <= end)

d  = df_daily[mask_d].copy()
ch = df_channel[mask_c].copy()
pa = df_paid[mask_p].copy()

# ─── 채널 한글 이름 ─────────────────────────────────────────
CHANNEL_LABELS = {
    "ig_Influencer":   "IG 인플루언서",
    "ig_ownedmedia":   "IG 자사미디어",
    "ig_fanpage":      "IG 팬페이지 (유료)",
    "ig_Somoim":       "IG 소모임",
    "kakao_notitalk":  "카카오 알림톡",
    "kakao_opentalk":  "카카오 오픈톡",
    "paid_myseatcheck":"자리어때 DA",
    "round_push":      "라운드 푸시",
    "round_popup":     "라운드 팝업",
    "round_banner":    "라운드 배너",
    "round_step":      "라운드 스텝",
    "polyball_web":    "폴리볼 웹",
    "naverblog":       "네이버 블로그",
    "unattributed":    "미귀속",
}

# ─── Tabs ─────────────────────────────────────────────────
tab1, tab2, tab3, tab5, tab6, tab7, tab10, tab11, tab12, tab13 = st.tabs([
    "📊 핵심 지표",
    "📣 채널별 유입 성과",
    "🔻 온보딩 퍼널",
    "📝 일별 분석",
    "📅 주간 분석",
    "📆 월간 분석",
    "📥 앱 전환 채널",
    "🗓️ 이슈 캘린더",
    "💸 비용 관리",
    "📈 매출귀속 & LTV",
])

# ══════════════════════════════════════════════════════════
# TAB 1: 핵심 지표
# ══════════════════════════════════════════════════════════
with tab1:
    st.subheader("핵심 지표 요약")
    n_days = max(len(d), 1)
    st.caption(f"{start.strftime('%m/%d')} ~ {end.strftime('%m/%d')} ({n_days}일)")

    # ── 에어브릿지 AU 조회 ──
    au_data = fetch_airbridge_au(start.isoformat(), end.isoformat())

    # ── 1행: 방문 & 가입 ──
    st.markdown('##### 방문 & 가입 <span class="badge-ab">AB</span>', unsafe_allow_html=True)
    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    if au_data:
        total_au = au_data["web_au"] + au_data["app_au"]
        r1c1.metric(
            "AU (기간 유니크)",
            f"{total_au:,}명",
            f"웹 {au_data['web_au']:,} / 앱 {au_data['app_au']:,}",
            help="조회 기간 내 1회 이상 활동한 유니크 유저 수 (에어브릿지 KST 기준)\n\n"
                 "• 웹: web_open_users — 브라우저로 polyball.kr 접속한 유저\n"
                 "• 앱: app_active_users — 앱에서 이벤트 발생한 유저\n"
                 "• 기간 유니크: 같은 유저가 여러 날 방문해도 1명으로 카운트\n"
                 "• AU ≥ DAU: DAU 합산과 다름 (중복 제거)\n\n"
                 "▶ 현재 서비스 구조:\n"
                 "  - 웹이 주 가입/이용 채널\n"
                 "  - 앱은 알림톡(당첨 발표 등)으로 기존 웹 유저를 앱 전환 유도 중\n"
                 "  - 앱 가입 = 0이 정상 (웹에서 이미 가입한 유저)"
        )
    else:
        r1c1.metric("AU (기간 유니크)", "—", help="AIRBRIDGE_API_TOKEN 설정 필요")

    # DAU — data.json에서 읽기 (AU만 API 실시간)
    if not d.empty:
        avg_dau_total = int(d['dau_total'].mean())
        avg_dau_web = int(d['dau_web'].mean())
        avg_dau_app = int(d['dau_app'].mean())
        r1c2.metric("DAU (평균)", f"{avg_dau_total:,}명",
                     help="data.json 기준 일평균 활성 유저\n• 웹: web_open_users\n• 앱: app_active_users")
        r1c3.metric("DAU 웹 / 앱 (평균)", f"{avg_dau_web:,} / {avg_dau_app:,}")
    else:
        r1c2.metric("DAU (평균)", "—")
        r1c3.metric("DAU 웹 / 앱 (평균)", "—")
    # 웹/앱 분리 지원 (신규 필드 없으면 총합만 표시)
    has_split = "server_signup_web" in d.columns
    if has_split:
        signup_web = int(d['server_signup_web'].sum())
        signup_app = int(d['server_signup_app'].sum())
    else:
        signup_web = signup_app = 0
    r1c4.metric(
        "가입 합계 (서버)",
        f"{int(d['server_signup'].sum()):,}명",
        f"웹 {signup_web:,} / 앱 {signup_app:,}" if has_split and (signup_web + signup_app) > 0 else None,
        help="서버 DB 기준 실제 가입 수"
    )

    # ── 2행: 참여 지표 ──
    st.markdown('##### 참여 (일평균) <span class="badge-srv">서버</span>', unsafe_allow_html=True)
    r2c1, r2c2, r2c3, r2c4 = st.columns(4)

    if d.empty:
        r2c1.metric("예측 일평균", "—")
        r2c2.metric("퀴즈 일평균", "—")
        r2c3.metric("응모 일평균", "—")
    else:

        pred_total = int(d['server_pred_user'].sum())
        pred_web = int(d['server_pred_user_web'].sum()) if has_split else 0
        pred_app = int(d['server_pred_user_app'].sum()) if has_split else 0
        pred_cnt_avg = int(d['server_pred_cnt'].mean())
        pred_cnt_web_avg = int(d['server_pred_cnt_web'].mean()) if 'server_pred_cnt_web' in d.columns else 0
        pred_cnt_app_avg = int(d['server_pred_cnt_app'].mean()) if 'server_pred_cnt_app' in d.columns else 0
        r2c1.metric(
            "예측 일평균 (서버)",
            f"{int(d['server_pred_user'].mean()):,}명",
            f"웹 {pred_web:,} / 앱 {pred_app:,}" if has_split and (pred_web + pred_app) > 0 else f"합계 {pred_total:,}명",
            help="서버 DB 기준 예측 참여 유니크 유저 수 (일평균)\n• 기간 합계 유저: {0:,}명 · 합계 횟수: {1:,}건".format(pred_total, int(d['server_pred_cnt'].sum()))
        )
        r2c1.caption(f"일평균 {pred_cnt_avg:,}건 (웹 {pred_cnt_web_avg:,} / 앱 {pred_cnt_app_avg:,})" if (pred_cnt_web_avg + pred_cnt_app_avg) > 0 else f"일평균 {pred_cnt_avg:,}건")

        quiz_total = int(d['server_quiz_user'].sum())
        quiz_web = int(d['server_quiz_user_web'].sum()) if has_split else 0
        quiz_app = int(d['server_quiz_user_app'].sum()) if has_split else 0
        quiz_cnt_avg = int(d['server_quiz_cnt'].mean())
        quiz_cnt_web_avg = int(d['server_quiz_cnt_web'].mean()) if 'server_quiz_cnt_web' in d.columns else 0
        quiz_cnt_app_avg = int(d['server_quiz_cnt_app'].mean()) if 'server_quiz_cnt_app' in d.columns else 0
        r2c2.metric(
            "퀴즈 일평균 (서버)",
            f"{int(d['server_quiz_user'].mean()):,}명",
            f"웹 {quiz_web:,} / 앱 {quiz_app:,}" if has_split and (quiz_web + quiz_app) > 0 else f"합계 {quiz_total:,}명",
            help="서버 DB 기준 퀴즈 참여 유니크 유저 수 (일평균)\n• 기간 합계 유저: {0:,}명 · 합계 횟수: {1:,}건".format(quiz_total, int(d['server_quiz_cnt'].sum()))
        )
        r2c2.caption(f"일평균 {quiz_cnt_avg:,}건 (웹 {quiz_cnt_web_avg:,} / 앱 {quiz_cnt_app_avg:,})" if (quiz_cnt_web_avg + quiz_cnt_app_avg) > 0 else f"일평균 {quiz_cnt_avg:,}건")

        entry_total = int(d['server_entry_user'].sum())
        entry_web = int(d['server_entry_user_web'].sum()) if has_split else 0
        entry_app = int(d['server_entry_user_app'].sum()) if has_split else 0
        entry_cnt_avg = int(d['server_entry_cnt'].mean())
        entry_cnt_web_avg = int(d['server_entry_cnt_web'].mean()) if 'server_entry_cnt_web' in d.columns else 0
        entry_cnt_app_avg = int(d['server_entry_cnt_app'].mean()) if 'server_entry_cnt_app' in d.columns else 0
        r2c3.metric(
            "응모 일평균 (서버)",
            f"{int(d['server_entry_user'].mean()):,}명",
            f"웹 {entry_web:,} / 앱 {entry_app:,}" if has_split and (entry_web + entry_app) > 0 else f"합계 {entry_total:,}명",
            help="서버 DB 기준 응모 참여 유니크 유저 수 (일평균)\n• 기간 합계 유저: {0:,}명 · 합계 횟수: {1:,}건".format(entry_total, int(d['server_entry_cnt'].sum()))
        )
        r2c3.caption(f"일평균 {entry_cnt_avg:,}건 (웹 {entry_cnt_web_avg:,} / 앱 {entry_cnt_app_avg:,})" if (entry_cnt_web_avg + entry_cnt_app_avg) > 0 else f"일평균 {entry_cnt_avg:,}건")

        app_conv_total = int(d['server_app_conversion'].sum()) if 'server_app_conversion' in d.columns else 0
        r2c4.metric(
            "앱 전환수 (서버)",
            f"{app_conv_total:,}명",
            help="웹 가입 후 앱으로 전환한 유저 수 (기간 합계)\n서버 크롤링 앱 탭 기준"
        )

    # ── 비용 & 매출 (전체 기간 — 조회기간 무관) ──
    st.divider()
    st.markdown("##### 비용 & 매출 (전체 누적)")

    df_costs = pd.DataFrame(data.get("costs", []))
    total_spend = 0
    daily_avg = 0
    cpa_all = 0
    cost_days = 0
    if not df_costs.empty and "date" in df_costs.columns:
        # 미래 예약 비용 제외 — 오늘까지만 '집행된' 비용으로 집계
        import datetime as _dt_cost
        _today_str = _dt_cost.date.today().isoformat()
        df_costs = df_costs[df_costs["date"] <= _today_str]
        total_spend = int(df_costs["spend"].sum())
        cost_days = df_costs["date"].nunique()
        daily_avg = int(total_spend / cost_days) if cost_days > 0 else 0
        # CAC는 UTM 채널 연결된 유입 비용만 — 기타비용·알림톡(CRM) 제외
        _cac_mask = (df_costs["channel"].notna() & (df_costs["channel"] != "") &
                     (~df_costs.get("category", pd.Series(dtype=str)).fillna("").str.contains("알림톡")))
        cac_spend = int(df_costs[_cac_mask]["spend"].sum())
        total_signup_all = int(df_daily["server_signup"].sum())
        cpa_all = int(cac_spend / total_signup_all) if total_signup_all > 0 else 0

    r3c1, r3c2, r3c3, r3c4 = st.columns(4)
    r3c1.metric("총 비용", f"{total_spend:,}원" if total_spend > 0 else "—")
    r3c2.metric("일평균 비용", f"{daily_avg:,}원" if daily_avg > 0 else "—")
    r3c3.metric("CAC (비용/가입)", f"{cpa_all:,}원" if cpa_all > 0 else "—", help="채널 연결 비용(UTM) / 전체 서버 가입자 수 — 기타비용 제외")
    r3c4.empty()

    # 광고 매출 (애드팝콘, 전체 누적)
    _ad_rev_all = data.get("ad_revenue", [])
    _ad_meta_all = data.get("ad_revenue_meta", {"exchange_rate_usd_krw": 1480})
    _fx_all = _ad_meta_all.get("exchange_rate_usd_krw", 1480)
    _ad_usd_all = 0.0
    _ad_krw_all = 0
    _ad_days_all = 0
    _ad_daily_avg = 0
    if _ad_rev_all:
        _df_ad_all = pd.DataFrame(_ad_rev_all)
        _ad_usd_all = float(_df_ad_all["cost_usd"].sum())
        _ad_krw_all = int(round(_ad_usd_all * _fx_all))
        _ad_days_all = _df_ad_all["date"].nunique()
        _ad_daily_avg = int(round(_ad_krw_all / _ad_days_all)) if _ad_days_all > 0 else 0

    # 기존 revenue(placement_attribution 기반) + ad_revenue 합산
    revenue_data = data.get("revenue", [])
    total_rev_other = 0
    if revenue_data:
        df_rev = pd.DataFrame(revenue_data)
        total_rev_other = int(df_rev["amount"].sum()) if "amount" in df_rev.columns else 0
    total_rev = total_rev_other + _ad_krw_all

    r4c1, r4c2, r4c3, r4c4 = st.columns(4)
    r4c1.metric(
        "광고 매출 (애드팝콘)",
        f"{_ad_krw_all:,}원" if _ad_krw_all > 0 else "—",
        f"${_ad_usd_all:.2f} · 환율 {_fx_all:,}" if _ad_krw_all > 0 else None,
        help="BM 광고 매출 (애드팝콘 전체 누적)"
    )
    r4c2.metric(
        "일평균 광고 매출",
        f"{_ad_daily_avg:,}원" if _ad_daily_avg > 0 else "—",
        f"{_ad_days_all}일" if _ad_days_all > 0 else None
    )
    r4c3.metric(
        "총 매출",
        f"{total_rev:,}원" if total_rev > 0 else "—",
        help="광고 매출 + 기타 매출(placement_attribution)"
    )
    # ROAS = 매출 / 비용 × 100
    _roas = round(total_rev / total_spend * 100, 1) if total_spend > 0 else 0
    r4c4.metric(
        "ROAS",
        f"{_roas}%" if total_spend > 0 and total_rev > 0 else "—",
        help="광고매출 / 마케팅비용 × 100 — 채널 귀속 매출 붙기 전 전체 비율"
    )

    profit = total_rev - total_spend
    if total_spend > 0 or total_rev > 0:
        profit_color = "#10B981" if profit >= 0 else "#EF4444"
        profit_sign = "+" if profit >= 0 else ""
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{profit_color}15,{profit_color}05);'
            f'border:2px solid {profit_color};border-radius:12px;padding:16px 24px;margin:8px 0;text-align:center">'
            f'<span style="font-size:14px;color:#64748B;font-weight:600">매출 - 비용</span><br>'
            f'<span style="font-size:32px;font-weight:800;color:{profit_color}">{profit_sign}{profit:,}원</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # 범례 설명
    st.info(
        "**데이터 출처 안내**\n\n"
        "**에어브릿지** — 방문자 수 (웹: web_open_users / 앱: app_active_users)\n\n"
        "- 비로그인 방문자 포함 — 가입자보다 항상 많음\n"
        "- 웹: 브라우저로 polyball.kr 접속한 유저\n"
        "- 앱: 앱 실행 또는 푸시/딥링크로 앱이 활성화된 유저\n"
        "- **AU (기간 유니크)**: 조회 기간 전체에서 중복 제거한 유저 수. "
        "하루 조회 시 DAU와 동일\n"
        "- **DAU (일평균)**: 일별 방문 유저 수의 기간 평균\n"
        "- 여러 날 조회 시 AU < DAU 합산 (AU는 중복 제거, DAU 합산은 중복 포함)\n\n"
        "**서버 DB** — 가입 / 예측 / 퀴즈 / 응모 (가장 정확한 수치)\n\n"
        "- Cnt = 이벤트 총 발생 횟수, User = 유니크 유저 수\n\n"
        "**서비스 구조**: 웹이 주 가입 채널. 앱 유저는 대부분 웹 기가입자 → 앱 가입 = 0 정상"
    )

    # DAU / 가입 차트
    st.markdown("#### 일별 방문자 수(DAU) & 신규 가입자")
    st.caption("막대: 신규 가입자 수 | 선: 일별 활성 유저(DAU) — 에어브릿지 API 기준")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="신규 가입 (에어브릿지)", x=d["date"], y=d["signup_total"],
        marker_color="#4F86F7"
    ))
    fig.add_trace(go.Bar(
        name="신규 가입 (서버 실측)", x=d["date"], y=d["server_signup"],
        marker_color="#A8C8F8"
    ))
    # DAU 라인: data.json에서
    fig.add_trace(go.Scatter(
        name="DAU", x=d["date"], y=d["dau_total"],
        line=dict(color="#FF6B6B", width=2),
        yaxis="y2", mode="lines+markers"
    ))
    fig.update_layout(
        barmode="group",
        yaxis=dict(title="가입자 수"),
        yaxis2=dict(title="DAU", overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.12),
        height=380, margin=dict(t=20)
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── 1단: 예측 / 퀴즈 ──
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### 일별 승부 예측 참여")
        st.caption("막대: 예측 횟수 | 점선: 예측 유저 수 (서버 기준)")
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            name="예측 횟수 (서버)", x=d["date"], y=d["server_pred_cnt"],
            marker_color="#2ECC71"
        ))
        fig2.add_trace(go.Scatter(
            name="예측 유저 (서버)", x=d["date"], y=d["server_pred_user"],
            line=dict(color="#1A9B57", width=2, dash="dot"),
            mode="lines+markers"
        ))
        fig2.update_layout(legend=dict(orientation="h", y=1.12), height=320, margin=dict(t=10))
        st.plotly_chart(fig2, use_container_width=True)

    with col_b:
        st.markdown("#### 일별 퀴즈 참여")
        st.caption("막대: 퀴즈 횟수 | 점선: 서버 실측 유저")
        fig_q = go.Figure()
        fig_q.add_trace(go.Bar(
            name="퀴즈 횟수 (서버)", x=d["date"], y=d["server_quiz_cnt"],
            marker_color="#9B59B6"
        ))
        fig_q.add_trace(go.Scatter(
            name="퀴즈 유저 (서버 실측)", x=d["date"], y=d["server_quiz_user"],
            line=dict(color="#6C3483", width=2, dash="dot"),
            mode="lines+markers"
        ))
        fig_q.update_layout(legend=dict(orientation="h", y=1.12), height=320, margin=dict(t=10))
        st.plotly_chart(fig_q, use_container_width=True)

    # ── 2단: 응모 / 앱 전환 ──
    col_c, col_d = st.columns(2)
    with col_c:
        st.markdown("#### 일별 티켓 응모 참여")
        st.caption("막대: 응모 횟수 | 점선: 응모 유저 수 (서버 기준)")
        fig3 = go.Figure()
        fig3.add_trace(go.Bar(
            name="응모 횟수 (서버)", x=d["date"], y=d["server_entry_cnt"],
            marker_color="#F39C12"
        ))
        fig3.add_trace(go.Scatter(
            name="응모 유저 (서버)", x=d["date"], y=d["server_entry_user"],
            line=dict(color="#C0700A", width=2, dash="dot"),
            mode="lines+markers"
        ))
        fig3.update_layout(legend=dict(orientation="h", y=1.12), height=320, margin=dict(t=10))
        st.plotly_chart(fig3, use_container_width=True)

    with col_d:
        st.markdown("#### 일별 앱 전환수")
        st.caption("서버 앱 탭 기준 — 웹 가입 후 앱으로 전환한 유저 수")
        if "server_app_conversion" in d.columns:
            fig_ac = go.Figure()
            fig_ac.add_trace(go.Bar(
                name="앱 전환수 (서버)", x=d["date"], y=d["server_app_conversion"],
                marker_color="#E74C3C"
            ))
            fig_ac.update_layout(legend=dict(orientation="h", y=1.12), height=320, margin=dict(t=10))
            st.plotly_chart(fig_ac, use_container_width=True)
        else:
            st.caption("데이터 없음 — 리포트 업데이트 후 표시됩니다")

    with st.expander("웹 / 앱 플랫폼별 분리"):
        st.caption("폴리볼은 웹(브라우저)이 주 서비스이고, 앱은 알림톡을 통해 설치 유도 중 — 에어브릿지 API 기준")
        col1, col2, col3 = st.columns(3)
        with col1:
            df_dau = d[["date","dau_web","dau_app"]].melt("date", var_name="플랫폼", value_name="DAU")
            df_dau["플랫폼"] = df_dau["플랫폼"].map({"dau_web":"웹(브라우저)","dau_app":"앱(네이티브)"})
            fig4 = px.line(df_dau, x="date", y="DAU", color="플랫폼",
                           title="웹 vs 앱 일별 방문자(DAU)", markers=True)
            st.plotly_chart(fig4, use_container_width=True)
        with col2:
            df_pred = d[["date","pred_web","pred_app"]].melt("date", var_name="플랫폼", value_name="예측")
            df_pred["플랫폼"] = df_pred["플랫폼"].map({"pred_web":"웹(브라우저)","pred_app":"앱(네이티브)"})
            fig5 = px.line(df_pred, x="date", y="예측", color="플랫폼",
                           title="웹 vs 앱 일별 예측 완료", markers=True)
            st.plotly_chart(fig5, use_container_width=True)
        with col3:
            _has_entry_split = "entry_web" in d.columns and "entry_app" in d.columns
            if _has_entry_split:
                df_entry = d[["date","entry_web","entry_app"]].melt("date", var_name="플랫폼", value_name="응모")
                df_entry["플랫폼"] = df_entry["플랫폼"].map({"entry_web":"웹(브라우저)","entry_app":"앱(네이티브)"})
            else:
                df_entry = d[["date","server_entry_user"]].rename(columns={"server_entry_user":"응모"})
                df_entry["플랫폼"] = "합계(서버)"
                df_entry = df_entry[["date","플랫폼","응모"]]
            fig6 = px.line(df_entry, x="date", y="응모", color="플랫폼",
                           title="웹 vs 앱 일별 응모 완료", markers=True)
            st.plotly_chart(fig6, use_container_width=True)

    with st.expander("원본 수치 테이블"):
        d_table = d.copy()
        # DAU 컬럼을 맨 앞으로 이동
        dau_cols = ["date", "dau_total", "dau_web", "dau_app"]
        other_cols = [c for c in d_table.columns if c not in dau_cols]
        d_table = d_table[dau_cols + other_cols]
        rename_map = {
            "date":"날짜",
            "dau_web":"DAU_웹","dau_app":"DAU_앱","dau_total":"DAU_합계",
            "signup_web":"가입_웹(AB)","signup_app":"가입_앱(AB)","signup_total":"가입_합계(AB)",
            "server_signup":"가입_서버","server_signup_web":"가입_서버_웹","server_signup_app":"가입_서버_앱",
            "pred_web":"예측완료_웹(AB)","pred_app":"예측완료_앱(AB)","pred_total":"예측완료_합계(AB)",
            "server_pred_user":"예측유저_서버","server_pred_cnt":"예측횟수_서버",
            "server_pred_user_web":"예측유저_서버_웹","server_pred_user_app":"예측유저_서버_앱",
            "server_pred_cnt_web":"예측횟수_서버_웹","server_pred_cnt_app":"예측횟수_서버_앱",
            "entry_web":"응모완료_웹(AB)","entry_app":"응모완료_앱(AB)","entry_total":"응모완료_합계(AB)",
            "server_entry_user":"응모유저_서버","server_entry_cnt":"응모횟수_서버",
            "server_entry_user_web":"응모유저_서버_웹","server_entry_user_app":"응모유저_서버_앱",
            "server_entry_cnt_web":"응모횟수_서버_웹","server_entry_cnt_app":"응모횟수_서버_앱",
            "server_quiz_cnt":"퀴즈횟수_서버","server_quiz_user":"퀴즈유저_서버",
            "server_quiz_cnt_web":"퀴즈횟수_서버_웹","server_quiz_cnt_app":"퀴즈횟수_서버_앱",
            "server_quiz_user_web":"퀴즈유저_서버_웹","server_quiz_user_app":"퀴즈유저_서버_앱",
            "server_app_conversion":"앱설치전환_서버",
        }
        existing = {k: v for k, v in rename_map.items() if k in d_table.columns}
        d_table = d_table.sort_values("date", ascending=False)
        st.dataframe(d_table.rename(columns=existing).set_index("날짜"), use_container_width=True)

# ══════════════════════════════════════════════════════════
# 채널 퍼널 공통 데이터 (tab2, tab3 공유) — 탭보다 먼저 실행
# ══════════════════════════════════════════════════════════
import datetime as _dt4_pre
_NEW_STEP_DATE_PRE = _dt4_pre.date(2026, 4, 6)
_has_pre = start < _NEW_STEP_DATE_PRE

_PRE_STEPS = [
    ("인트로 진입",        "web_custom_users_pv_ob_intro"),
    ("OB-04 팀선택완료뷰", "web_custom_users_pv_ob_team_choice_completed"),
    ("경기선택 완료",       "web_custom_users_pv_ob_match_choice_completed"),
    ("가입 완료",          "web_custom_users_signup"),
    ("예측 CTA 클릭",      "web_custom_users_c_match_prediction"),
]
_POST_STEPS = [
    ("인트로 진입",     "web_custom_users_pv_ob_intro"),
    ("시작하기 클릭",   "web_custom_users_c_ob_intro_start"),
    ("팀선택 완료",     "web_custom_users_pv_ob_team_choice_completed"),
    ("로그인 바텀시트", "web_custom_users_pv_ob_match_choice_completed"),
    ("가입 완료",       "web_custom_users_signup"),
    ("예측 CTA 클릭",   "web_custom_users_c_match_prediction"),
]

@st.cache_data(ttl=600, show_spinner="채널별 퍼널 조회 중...")
def fetch_all_channel_funnels(from_date, to_date):
    metrics = [
        "clicks",
        "web_custom_users_pv_ob_intro",
        "web_custom_users_c_ob_intro_start",
        "web_custom_users_pv_ob_team_choice_completed",
        "web_custom_users_pv_ob_match_choice_completed",
        "web_custom_users_signup",
        "web_custom_users_c_match_prediction",
    ]
    payload = {
        "from": from_date, "to": to_date,
        "metrics": metrics,
        "groupBys": ["channel", "campaign", "ad_group", "ad_creative"],
        "filters": [], "sorts": [],
        "isSummaryAvailable": True,
        "viewFormat": True,
        "skip": 0,
        "option": {"timezone": None, "eventTimestampSource": "event_occurred_date"},
        "size": 500,
    }
    result = _airbridge_request(payload)
    if not result:
        return {}
    actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
    if not actuals:
        return {}
    rows = actuals.get("data", {}).get("rows", [])
    out = {}
    for row in rows:
        gbs = row.get("groupBys", [])
        ch_name    = (gbs[0] if len(gbs) > 0 else "").lower().strip()
        camp       = (gbs[1] if len(gbs) > 1 else "").lower().strip()
        adgroup    = (gbs[2] if len(gbs) > 2 else "").lower().strip()
        adcreative = (gbs[3] if len(gbs) > 3 else "").lower().strip()
        vals = {k: int(v.get("value", 0)) for k, v in row.get("values", {}).items()}
        key = (ch_name, camp, adgroup, adcreative)
        if key in out:
            for k, v in vals.items():
                out[key][k] = out[key].get(k, 0) + v
        else:
            out[key] = vals
    return out

all_ch_funnels = fetch_all_channel_funnels(start.isoformat(), end.isoformat())

_ch_camp_seen = {}
for (ch_name, camp, adg, adc), vals in all_ch_funnels.items():
    if ch_name in _HIDE_CHANNELS:
        continue
    key = (ch_name, camp)
    _ch_camp_seen[key] = _ch_camp_seen.get(key, 0) + vals.get("web_custom_users_signup", 0)
_ch_camp_list  = sorted(_ch_camp_seen.items(), key=lambda x: x[1], reverse=True)
_dyn_labels    = [f"{ch} / {cp}" if cp else ch for (ch, cp), _ in _ch_camp_list]
_dyn_ab_list   = [{"channel": ch, "campaign": cp if cp else None} for (ch, cp), _ in _ch_camp_list]

_full_combo_seen = {}
for (ch_name, camp, adg, adc), vals in all_ch_funnels.items():
    if ch_name in _HIDE_CHANNELS:
        continue
    _full_combo_seen[(ch_name, camp, adg, adc)] = vals.get("web_custom_users_signup", 0)
_full_combo_list   = sorted(_full_combo_seen.items(), key=lambda x: x[1], reverse=True)
_full_combo_labels = [
    " / ".join(filter(None, [ch, cp, adg, adc])) or ch
    for (ch, cp, adg, adc), _ in _full_combo_list
]

# ══════════════════════════════════════════════════════════
# TAB 2: 채널별 유입 성과
# ══════════════════════════════════════════════════════════
with tab2:
    st.subheader("채널별 유입 성과")
    st.caption(f"{start.strftime('%m/%d')} ~ {end.strftime('%m/%d')} | 클릭·가입·CVR·비용·CAC")

    # data.json channels에서 채널 데이터 로드 (API 실시간 호출 대신 — size 제한 누락 방지)
    # data.json 키 → 에어브릿지 channel/campaign 매핑
    # data.json channels에서 모든 채널 동적 로드 (하드코딩 없음)
    _all_ch_keys = set()
    for _cr in data.get("channels", []):
        for k in _cr.keys():
            if k.endswith("_clicks"):
                _all_ch_keys.add(k.replace("_clicks", ""))
    _ch_rows = []
    for _cr in data.get("channels", []):
        _cd = _cr["date"]
        _cd_dt = pd.to_datetime(_cd)
        if _cd_dt.date() < start or _cd_dt.date() > end:
            continue
        for _ck in _all_ch_keys:
            _cl = _cr.get(f"{_ck}_clicks", 0)
            _sg = _cr.get(f"{_ck}_signups", 0)
            # 키를 channel / campaign 형식으로 변환
            _label = _ck.replace("_", " / ", 1) if "_" in _ck else _ck
            _ch_rows.append({"날짜": _cd_dt, "채널": _label, "클릭": _cl, "가입": _sg})

    if not _ch_rows:
        st.info("선택한 기간에 채널 데이터가 없습니다.")
    else:
        df_chd = pd.DataFrame(_ch_rows)
        df_chd["channel"] = df_chd["채널"].str.split(" / ").str[0]
        df_chd["campaign"] = df_chd["채널"].str.split(" / ").str[1].fillna("")

        # 비용 데이터 준비
        df_costs_mkt = pd.DataFrame(data.get("costs", []))
        if not df_costs_mkt.empty and "category" in df_costs_mkt.columns:
            df_costs_mkt = df_costs_mkt[
                df_costs_mkt["channel"].notna() & (df_costs_mkt["channel"] != "") &
                (~df_costs_mkt["category"].fillna("").str.contains("알림톡"))
            ].copy()
            df_costs_mkt["date"] = pd.to_datetime(df_costs_mkt["date"])
            mask_mkt = (df_costs_mkt["date"].dt.date >= start) & (df_costs_mkt["date"].dt.date <= end)
            df_costs_mkt = df_costs_mkt[mask_mkt]
        else:
            df_costs_mkt = pd.DataFrame()

        # ── 1. 기간 요약 카드 ──
        tot_click = int(df_chd["클릭"].sum())
        tot_signup = int(df_chd["가입"].sum())
        tot_cvr = round(tot_signup / tot_click * 100, 1) if tot_click > 0 else 0
        tot_cost = int(df_costs_mkt["spend"].sum()) if not df_costs_mkt.empty else 0
        tot_cac = int(tot_cost / tot_signup) if tot_signup > 0 and tot_cost > 0 else 0

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("클릭", f"{tot_click:,}")
        c2.metric("가입", f"{tot_signup:,}")
        c3.metric("CVR", f"{tot_cvr}%")
        c4.metric("비용", f"{tot_cost:,}원")
        c5.metric("CAC", f"{tot_cac:,}원" if tot_cac > 0 else "—")

        # 일별 + 채널/캠페인 집계 + 비용 매칭 (공통 데이터)
        df_daily_ch = df_chd.groupby(["날짜", "채널"])[["클릭", "가입"]].sum().reset_index()
        df_daily_ch["CVR(%)"] = df_daily_ch.apply(
            lambda r: round(r["가입"] / r["클릭"] * 100, 1) if r["클릭"] > 0 else 0, axis=1)

        if not df_costs_mkt.empty:
            cost_daily = df_costs_mkt.copy()
            cost_daily["채널"] = cost_daily["channel"] + " / " + cost_daily["campaign"].fillna("")
            cost_daily["채널"] = cost_daily["채널"].str.rstrip(" / ")
            cost_by_day_ch = cost_daily.groupby([cost_daily["date"].dt.date, "채널"])["spend"].sum().reset_index()
            cost_by_day_ch.columns = ["날짜_date", "채널", "비용"]
            df_daily_ch["날짜_date"] = df_daily_ch["날짜"].dt.date
            df_daily_ch = df_daily_ch.merge(cost_by_day_ch, on=["날짜_date", "채널"], how="left")
            df_daily_ch.drop(columns=["날짜_date"], inplace=True)
        else:
            df_daily_ch["비용"] = 0
        df_daily_ch["비용"] = df_daily_ch["비용"].fillna(0).astype(int)
        df_daily_ch["CAC"] = df_daily_ch.apply(
            lambda r: int(r["비용"] / r["가입"]) if r["가입"] > 0 and r["비용"] > 0 else ("—" if r["비용"] > 0 else 0), axis=1)

        # ── 2. 필터: 채널 / 캠페인 / Ad Group / Creative ──
        st.divider()
        st.markdown("#### 채널별 일별 추이")

        # channels_detail에서 기간 내 전체 rows 수집 (dimension 소문자 정규화)
        _det_rows = []
        for _cd in data.get("channels_detail", []):
            _cdt = pd.to_datetime(_cd["date"]).date()
            if _cdt < start or _cdt > end:
                continue
            for r in _cd.get("rows", []):
                _rch = (r.get("channel", "") or "").lower().strip()
                if _rch in ("$$default$$", "test", ""):
                    continue
                _det_rows.append({
                    "date": _cd["date"],
                    "channel": _rch,
                    "campaign": (r.get("campaign", "") or "").lower().strip(),
                    "ad_group": (r.get("ad_group", "") or "").lower().strip(),
                    "ad_creative": (r.get("ad_creative", "") or "").lower().strip(),
                    "clicks": r.get("clicks", 0),
                    "signups": r.get("signups", 0),
                })

        sc1, sc2, sc3, sc4 = st.columns(4)

        # 셀렉트박스 1: 채널
        _ch_list = ["전체"] + sorted(set(r["channel"] for r in _det_rows))
        if st.session_state.get("tab2_sel_ch") not in _ch_list:
            st.session_state["tab2_sel_ch"] = "전체"
        sel_ch = sc1.selectbox("채널", _ch_list, key="tab2_sel_ch")

        # 셀렉트박스 2: 캠페인 (채널 필터 적용)
        _rows_ch = [r for r in _det_rows if sel_ch == "전체" or r["channel"] == sel_ch]
        _cp_list = ["전체"] + sorted(set(r["campaign"] for r in _rows_ch if r["campaign"]))
        if st.session_state.get("tab2_sel_cp") not in _cp_list:
            st.session_state["tab2_sel_cp"] = "전체"
        sel_cp = sc2.selectbox("캠페인", _cp_list, key="tab2_sel_cp")

        # 셀렉트박스 3: Ad Group (채널+캠페인 필터 적용)
        _rows_cp = [r for r in _rows_ch if sel_cp == "전체" or r["campaign"] == sel_cp]
        _ag_list = ["전체"] + sorted(set(r["ad_group"] for r in _rows_cp if r["ad_group"]))
        if st.session_state.get("tab2_sel_ag") not in _ag_list:
            st.session_state["tab2_sel_ag"] = "전체"
        sel_ag = sc3.selectbox("Ad Group", _ag_list, key="tab2_sel_ag")

        # 셀렉트박스 4: Creative (채널+캠페인+Ad Group 필터 적용)
        _rows_ag = [r for r in _rows_cp if sel_ag == "전체" or r["ad_group"] == sel_ag]
        _ac_list = ["전체"] + sorted(set(r["ad_creative"] for r in _rows_ag if r["ad_creative"]))
        if st.session_state.get("tab2_sel_ac") not in _ac_list:
            st.session_state["tab2_sel_ac"] = "전체"
        sel_ac = sc4.selectbox("Creative", _ac_list, key="tab2_sel_ac")

        # 최종 필터 적용
        _filtered = [r for r in _rows_ag if sel_ac == "전체" or r["ad_creative"] == sel_ac]

        # 제목 표시
        _title_parts = [p for p in [
            sel_ch  if sel_ch  != "전체" else None,
            sel_cp  if sel_cp  != "전체" else None,
            sel_ag  if sel_ag  != "전체" else None,
            sel_ac  if sel_ac  != "전체" else None,
        ] if p]
        st.markdown(f"##### {' > '.join(_title_parts) if _title_parts else '전체 채널'}")

        # 날짜별 집계
        import collections as _col2, datetime as _dt_f2
        _by_date2 = _col2.defaultdict(lambda: {"클릭": 0, "가입": 0})
        for r in _filtered:
            _by_date2[r["date"]]["클릭"] += r["clicks"]
            _by_date2[r["date"]]["가입"] += r["signups"]

        _all_dates2 = [
            (start + _dt_f2.timedelta(days=i)).isoformat()
            for i in range((end - start).days + 1)
        ]
        _drill2_rows = [{"날짜": d, "클릭": _by_date2[d]["클릭"], "가입": _by_date2[d]["가입"]} for d in _all_dates2]

        # 비용 매칭
        _cost_by_date2 = {}
        if not df_costs_mkt.empty:
            for _, cr in df_costs_mkt.iterrows():
                if sel_ch != "전체" and cr["channel"] != sel_ch:
                    continue
                if sel_cp != "전체" and (cr.get("campaign", "") or "") != sel_cp:
                    continue
                if sel_ag != "전체" and (cr.get("ad_group", "") or "") != sel_ag:
                    continue
                if sel_ac != "전체" and (cr.get("ad_creative", "") or "") != sel_ac:
                    continue
                _cd2 = cr["date"].strftime("%Y-%m-%d")
                _cost_by_date2[_cd2] = _cost_by_date2.get(_cd2, 0) + int(cr["spend"])

        df_drill2 = pd.DataFrame(_drill2_rows)
        df_drill2["비용"] = df_drill2["날짜"].map(_cost_by_date2).fillna(0).astype(int)
        df_drill2["CVR(%)"] = df_drill2.apply(lambda r: round(r["가입"]/r["클릭"]*100, 1) if r["클릭"] > 0 else 0, axis=1)
        df_drill2["CAC_num"] = df_drill2.apply(lambda r: int(r["비용"]/r["가입"]) if r["가입"] > 0 and r["비용"] > 0 else 0, axis=1)
        df_drill2["CAC"] = df_drill2.apply(lambda r: int(r["비용"]/r["가입"]) if r["가입"] > 0 and r["비용"] > 0 else ("—" if r["비용"] > 0 else 0), axis=1)

        _d2_cl   = int(df_drill2["클릭"].sum())
        _d2_sg   = int(df_drill2["가입"].sum())
        _d2_cvr  = round(_d2_sg / _d2_cl * 100, 1) if _d2_cl > 0 else 0
        _d2_cost = int(df_drill2["비용"].sum())
        _d2_cac  = int(_d2_cost / _d2_sg) if _d2_sg > 0 and _d2_cost > 0 else 0

        dc1, dc2, dc3, dc4, dc5 = st.columns(5)
        dc1.metric("클릭", f"{_d2_cl:,}")
        dc2.metric("가입", f"{_d2_sg:,}")
        dc3.metric("CVR", f"{_d2_cvr}%")
        dc4.metric("비용", f"{_d2_cost:,}원" if _d2_cost > 0 else "—")
        dc5.metric("CAC", f"{_d2_cac:,}원" if _d2_cac > 0 else "—")

        _disp2 = df_drill2.sort_values("날짜", ascending=False).copy()
        _disp2["날짜"] = pd.to_datetime(_disp2["날짜"]).dt.strftime("%m/%d")
        st.dataframe(
            _disp2[["날짜", "클릭", "가입", "CVR(%)", "비용", "CAC"]].rename(
                columns={"비용": "비용(원)", "CAC": "CAC(원)"}
            ).reset_index(drop=True),
            use_container_width=True, hide_index=True
        )

        _fig2 = go.Figure()
        _fig2.add_trace(go.Bar(name="클릭", x=df_drill2["날짜"], y=df_drill2["클릭"], marker_color="#93C5FD"))
        _fig2.add_trace(go.Scatter(name="가입", x=df_drill2["날짜"], y=df_drill2["가입"],
                                    mode="lines+markers", line=dict(color="#2563EB", width=2), yaxis="y2"))
        if _d2_cost > 0:
            _fig2.add_trace(go.Scatter(name="CAC", x=df_drill2["날짜"], y=df_drill2["CAC_num"],
                                        mode="lines+markers", line=dict(color="#EF4444", width=2, dash="dot"), yaxis="y3"))
        _fig2.update_layout(
            yaxis=dict(title="클릭"),
            yaxis2=dict(title="가입", overlaying="y", side="right"),
            yaxis3=dict(title="CAC(원)", overlaying="y", side="right", position=0.95, showgrid=False) if _d2_cost > 0 else {},
            barmode="group",
            legend=dict(orientation="h", y=1.08), height=340, margin=dict(t=10, r=60 if _d2_cost > 0 else 10)
        )
        st.plotly_chart(_fig2, use_container_width=True, key="tab2_drill2_chart")

        # ── 3. 채널 성과 (조회기간 합산) ──
        st.divider()
        st.markdown(f"#### 채널 성과 ({start.strftime('%m/%d')}~{end.strftime('%m/%d')} 합산)")

        # 집계 단위 선택 — 4단계
        _grp_mode = st.radio("집계 단위", ["채널", "캠페인", "Ad Group", "Ad Creative"], horizontal=True, key="tab2_grp_mode")

        # channels_detail 기반 4레벨 raw 데이터 구축
        _detail_rows = []
        for _cd in data.get("channels_detail", []):
            _cd_date = _cd["date"]
            if str(start) <= _cd_date <= str(end):
                for r in _cd.get("rows", []):
                    if r.get("channel") in ("$$default$$", "test", ""):
                        continue
                    _detail_rows.append({
                        "채널": r["channel"], "캠페인": r.get("campaign", ""),
                        "Ad Group": r.get("ad_group", ""), "Ad Creative": r.get("ad_creative", ""),
                        "클릭": r.get("clicks", 0), "가입": r.get("signups", 0),
                    })

        # channels_detail 없으면 channels 데이터 fallback (채널/캠페인만)
        if not _detail_rows:
            for _, _r in df_daily_ch.iterrows():
                _parts = str(_r["채널"]).split(" / ", 1)
                _detail_rows.append({
                    "채널": _parts[0], "캠페인": _parts[1] if len(_parts) > 1 else "",
                    "Ad Group": "", "Ad Creative": "",
                    "클릭": int(_r["클릭"]), "가입": int(_r["가입"]),
                })

        if _detail_rows:
            df_detail = pd.DataFrame(_detail_rows)

            # 비용 raw (조회기간)
            _cost_raw = []
            if not df_costs_mkt.empty:
                for _, cr in df_costs_mkt.iterrows():
                    _cost_raw.append({
                        "채널": cr["channel"], "캠페인": cr.get("campaign", ""),
                        "Ad Group": cr.get("ad_group", ""), "Ad Creative": cr.get("ad_creative", ""),
                        "비용": int(cr["spend"]),
                    })
            df_cost_raw = pd.DataFrame(_cost_raw) if _cost_raw else pd.DataFrame(columns=["채널","캠페인","Ad Group","Ad Creative","비용"])

            # 집계 단위별 그룹핑 컬럼
            _grp_cols_map = {
                "채널": ["채널"],
                "캠페인": ["채널", "캠페인"],
                "Ad Group": ["채널", "캠페인", "Ad Group"],
                "Ad Creative": ["채널", "캠페인", "Ad Group", "Ad Creative"],
            }
            _grp_cols = _grp_cols_map[_grp_mode]

            df_agg = df_detail.groupby(_grp_cols, as_index=False)[["클릭", "가입"]].sum()
            df_agg["CVR(%)"] = df_agg.apply(lambda r: round(r["가입"]/r["클릭"]*100,1) if r["클릭"]>0 else 0, axis=1)

            # 비용 매칭 (동일 그룹핑)
            if not df_cost_raw.empty:
                df_cost_agg = df_cost_raw.groupby(_grp_cols, as_index=False)["비용"].sum()
                df_agg = df_agg.merge(df_cost_agg, on=_grp_cols, how="left")
            else:
                df_agg["비용"] = 0
            df_agg["비용"] = df_agg["비용"].fillna(0).astype(int)
            df_agg["CAC"] = df_agg.apply(lambda r: int(r["비용"]/r["가입"]) if r["가입"]>0 and r["비용"]>0 else ("—" if r["비용"]>0 else 0), axis=1)
            df_agg = df_agg.sort_values("가입", ascending=False)

            _t_click = int(df_agg["클릭"].sum())
            _t_signup = int(df_agg["가입"].sum())
            _t_cvr = round(_t_signup / _t_click * 100, 1) if _t_click > 0 else 0
            _t_cost = int(df_agg["비용"].sum())
            _t_cac = int(_t_cost / _t_signup) if _t_signup > 0 and _t_cost > 0 else 0

            dc1, dc2, dc3, dc4, dc5 = st.columns(5)
            dc1.metric("클릭", f"{_t_click:,}")
            dc2.metric("가입", f"{_t_signup:,}")
            dc3.metric("CVR", f"{_t_cvr}%")
            dc4.metric("비용", f"{_t_cost:,}원")
            dc5.metric("CAC", f"{_t_cac:,}원" if _t_cac > 0 else "—")

            show_cols = _grp_cols + ["클릭", "가입", "CVR(%)", "비용", "CAC"]
            st.dataframe(
                df_agg[show_cols].rename(columns={"비용": "비용(원)", "CAC": "CAC(원)"}).reset_index(drop=True),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("조회기간에 채널 데이터가 없습니다.")

# ══════════════════════════════════════════════════════════
# TAB 3: 온보딩 퍼널
# ══════════════════════════════════════════════════════════
with tab3:
    st.subheader("웹 온보딩 퍼널")
    st.caption("신규 유저가 처음 방문해서 가입 완료까지 가는 단계별 전환율")

    # ── 전체 퍼널 ──────────────────────────────────────────
    if True:
        if not df_funnel.empty:
            mask_f = (df_funnel["date"].dt.date >= start) & (df_funnel["date"].dt.date <= end)
            f = df_funnel[mask_f].copy()

            if not f.empty:
                import datetime as _dt3
                NEW_STEP_DATE = _dt3.date(2026, 4, 6)
                has_pre_new = f["date"].dt.date.min() < NEW_STEP_DATE

                if has_pre_new:
                    st.info(
                        "**온보딩 단계 (4/5 이전 기준)**\n\n"
                        "1. **인트로 화면 진입** — 폴리볼에 처음 들어온 유저\n"
                        "2. **OB-04 팀선택완료뷰** — 팀 선택 완료 안내 화면을 본 유저\n"
                        "3. **경기선택 완료** — 예측할 경기를 고른 유저\n"
                        "4. **가입 완료** — 실제로 회원가입을 마친 유저\n"
                        "5. **예측 CTA 클릭** — 가입 후 바로 승부 예측 화면으로 이동한 유저"
                    )
                else:
                    st.info(
                        "**온보딩 단계 (4/6 이후 기준)**\n\n"
                        "1. **인트로 화면 진입** — 폴리볼에 처음 들어온 유저\n"
                        "2. **시작하기 클릭** — 인트로에서 '시작하기' 버튼을 누른 유저\n"
                        "3. **팀선택 완료** — 응원 팀을 선택한 유저\n"
                        "4. **경기선택 완료** — 예측할 경기를 고른 유저\n"
                        "5. **가입 완료** — 실제로 회원가입을 마친 유저\n"
                        "6. **예측 CTA 클릭** — 가입 후 바로 승부 예측 화면으로 이동한 유저"
                    )

                all_steps = [
                    ("인트로 화면 진입",   "intro"),
                    ("시작하기 클릭",      "team_start"),
                    ("팀선택 뷰",         "team_view"),
                    ("팀선택 완료",       "team_complete"),
                    ("OB-04 팀선택완료뷰","ob04"),
                    ("경기선택 완료",     "obs01"),
                    ("가입 완료",         "signup"),
                    ("예측 CTA 클릭",     "pred_cta"),
                ]
                CIRCLE_NUMS = "①②③④⑤⑥⑦⑧⑨⑩"
                NEW_COLS = {"team_start", "team_view", "team_complete"}
                POST_EXCLUDE = {"team_view"}

                funnel_rows = []
                for label, col in all_steps:
                    if has_pre_new and col in NEW_COLS:
                        continue
                    if not has_pre_new and col in POST_EXCLUDE | {"ob04"}:
                        continue
                    if col in f.columns:
                        val = int(f[col].sum(skipna=True))
                    else:
                        val = 0
                    if val > 0:
                        funnel_rows.append({"단계": label, "컬럼": col, "유저 수": val})

                for i, row in enumerate(funnel_rows):
                    row["단계"] = f"{CIRCLE_NUMS[i]} {row['단계']}"

                df_f = pd.DataFrame(funnel_rows)
                intro_val = df_f["유저 수"].iloc[0]
                df_f["전체 대비 (인트로 기준)"] = (df_f["유저 수"] / intro_val * 100).round(1).astype(str) + "%"
                df_f["직전 단계 대비"] = ["100%"] + [
                    f"{round(df_f['유저 수'].iloc[i] / df_f['유저 수'].iloc[i-1] * 100, 1)}%"
                    if df_f["유저 수"].iloc[i-1] > 0 else "—"
                    for i in range(1, len(df_f))
                ]

                n_steps = len(df_f)
                colors = px.colors.sample_colorscale("Blues", [0.4 + 0.5 * i / max(n_steps - 1, 1) for i in range(n_steps)])

                col1, col2 = st.columns([3, 2])
                with col1:
                    fig_f = go.Figure(go.Funnel(
                        y=df_f["단계"], x=df_f["유저 수"],
                        textinfo="value+percent initial",
                        marker=dict(color=colors)
                    ))
                    fig_f.update_layout(
                        title=f"온보딩 퍼널 ({start.strftime('%m/%d')}~{end.strftime('%m/%d')} 누계)",
                        height=max(400, n_steps * 55), margin=dict(t=40, l=10)
                    )
                    st.plotly_chart(fig_f, use_container_width=True)

                with col2:
                    st.markdown("##### 단계별 전환율")
                    st.dataframe(df_f[["단계","유저 수","전체 대비 (인트로 기준)","직전 단계 대비"]].set_index("단계"),
                                 use_container_width=True)
                    st.divider()
                    signup_rows = df_f[df_f["단계"].str.contains("가입")]
                    pred_rows   = df_f[df_f["단계"].str.contains("예측")]
                    if not signup_rows.empty:
                        signup_val = signup_rows["유저 수"].values[0]
                        st.metric("인트로 → 가입 전환율", f"{round(signup_val/intro_val*100,1)}%")
                    if not pred_rows.empty:
                        pred_cta_val = pred_rows["유저 수"].values[0]
                        st.metric("인트로 → 예측CTA 전환율", f"{round(pred_cta_val/intro_val*100,1)}%")

                st.divider()
                st.markdown("#### 일별 퍼널 단계별 유저 수 추이")
                trend_col_map = {row["컬럼"]: row["단계"] for _, row in df_f.iterrows()}
                trend_cols = ["date"] + [col for col in trend_col_map if col in f.columns]
                df_funnel_daily = f[trend_cols].copy()
                df_funnel_daily = df_funnel_daily.rename(columns={"date": "날짜", **trend_col_map})
                df_funnel_daily = df_funnel_daily.melt("날짜", var_name="단계", value_name="유저 수")
                df_funnel_daily = df_funnel_daily.dropna(subset=["유저 수"])
                fig_trend = px.line(df_funnel_daily, x="날짜", y="유저 수", color="단계",
                                    markers=True, color_discrete_sequence=px.colors.qualitative.Set2)
                fig_trend.update_layout(height=380, legend=dict(orientation="h", y=1.1))
                st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.warning("퍼널 데이터가 없습니다.")

    def _extract_ch_funnel(all_funnels, ab_info):
        """채널 전체 집계 (ad_group/creative 합산)"""
        out = {}
        for (ch_name, camp, adgroup, adcreative), vals in all_funnels.items():
            if ch_name == ab_info["channel"]:
                if ab_info.get("campaign") is None or camp == ab_info.get("campaign"):
                    for k, v in vals.items():
                        out[k] = out.get(k, 0) + v
        return out

    def _extract_ch_by_creative(all_funnels, ab_info):
        """ad_creative 단위 집계 → {creative: {metric: val}}"""
        out = {}
        for (ch_name, camp, adgroup, adcreative), vals in all_funnels.items():
            if ch_name == ab_info["channel"]:
                if ab_info.get("campaign") is None or camp == ab_info.get("campaign"):
                    label = adcreative or "(없음)"
                    if label not in out:
                        out[label] = {}
                    for k, v in vals.items():
                        out[label][k] = out[label].get(k, 0) + v
        return out

    def _extract_ch_detail(all_funnels, ab_info):
        """ad_group + ad_creative 단위 집계 → {(adgroup, creative): {metric: val}}"""
        out = {}
        for (ch_name, camp, adgroup, adcreative), vals in all_funnels.items():
            if ch_name == ab_info["channel"]:
                if ab_info.get("campaign") is None or camp == ab_info.get("campaign"):
                    key = (adgroup or "(없음)", adcreative or "(없음)")
                    if key not in out:
                        out[key] = {}
                    for k, v in vals.items():
                        out[key][k] = out[key].get(k, 0) + v
        return out

    def _render_ch_funnel(ch_funnel, step_defs, title, detail):
        CIRCLE_NUMS = "①②③④⑤⑥⑦⑧"
        steps = [(label, ch_funnel.get(key, 0)) for label, key in step_defs]
        steps = [(f"{CIRCLE_NUMS[i]} {label}", val) for i, (label, val) in enumerate(steps) if val > 0]
        if not steps:
            st.info("해당 기간 데이터가 없습니다.")
            return
        df_cf = pd.DataFrame(steps, columns=["단계", "유저 수"])
        base = df_cf["유저 수"].iloc[0]
        n = len(df_cf)
        colors = px.colors.sample_colorscale("Blues", [0.4 + 0.5 * i / max(n - 1, 1) for i in range(n)])

        # ── 퍼널 차트 + 소재별 비교표 ──────────────────────────
        col1, col2 = st.columns([3, 2])
        with col1:
            fig_cf = go.Figure(go.Funnel(
                y=df_cf["단계"], x=df_cf["유저 수"],
                textinfo="value+percent initial",
                marker=dict(color=colors)
            ))
            fig_cf.update_layout(title=title, height=max(350, n * 55), margin=dict(t=40, l=10))
            st.plotly_chart(fig_cf, use_container_width=True)

        with col2:
            st.markdown("##### 단계별 전환율 (채널)")
            df_cf2 = df_cf.copy()
            df_cf2["전체 대비"] = (df_cf2["유저 수"] / base * 100).round(1).astype(str) + "%"
            prev = None
            step_cvr = []
            for _, r in df_cf2.iterrows():
                if prev is not None and prev > 0:
                    step_cvr.append(f"{round(r['유저 수']/prev*100,1)}%")
                else:
                    step_cvr.append("—")
                prev = r["유저 수"]
            df_cf2["전단계 대비"] = step_cvr
            st.dataframe(df_cf2[["단계", "유저 수", "전체 대비", "전단계 대비"]].set_index("단계"),
                         use_container_width=True)
            signup_row = df_cf[df_cf["단계"].str.contains("가입")]
            if not signup_row.empty:
                st.metric("클릭 → 가입 CVR",
                          f"{round(signup_row['유저 수'].values[0] / base * 100, 1)}%")

        # ── 상세 보기 — ad_group × ad_creative 집계표 ──
        if detail:
            st.markdown("##### 📋 상세 보기 — ad_group × ad_creative")
            intro_mkey  = "web_custom_users_pv_ob_intro"
            signup_mkey = "web_custom_users_signup"
            detail_rows = []
            for (ag, ac), dvals in sorted(detail.items()):
                intro_v  = dvals.get(intro_mkey,  0)
                signup_v = dvals.get(signup_mkey, 0)
                row = {"ad_group": ag, "ad_creative": ac}
                for label, mkey in step_defs:
                    row[label] = dvals.get(mkey, 0)
                row["인트로→가입 CVR"] = f"{round(signup_v/intro_v*100,1)}%" if intro_v > 0 else "—"
                detail_rows.append(row)
            if detail_rows:
                df_detail = pd.DataFrame(detail_rows).set_index("ad_group")
                st.dataframe(df_detail, use_container_width=True)

    # ── 채널별 퍼널 (4개 셀렉트박스) ───────────────────────────
    if all_ch_funnels:
        st.divider()
        st.markdown("#### 채널별 퍼널")

        _fn_all_rows = [
            (ch, cp, adg, adc, vals)
            for (ch, cp, adg, adc), vals in all_ch_funnels.items()
            if ch not in _HIDE_CHANNELS
        ]

        _fn_sc1, _fn_sc2, _fn_sc3, _fn_sc4 = st.columns(4)

        # Selectbox 1: 채널
        _fn_ch_list = ["전체"] + sorted(set(ch for ch, cp, adg, adc, vals in _fn_all_rows))
        if st.session_state.get("fn_sel_ch") not in _fn_ch_list:
            st.session_state["fn_sel_ch"] = "전체"
        fn_sel_ch = _fn_sc1.selectbox("채널", _fn_ch_list, key="fn_sel_ch")

        # Selectbox 2: 캠페인 (filtered by ch)
        _fn_rows_ch = [(ch, cp, adg, adc, vals) for ch, cp, adg, adc, vals in _fn_all_rows
                       if fn_sel_ch == "전체" or ch == fn_sel_ch]
        _fn_cp_list = ["전체"] + sorted(set(cp for ch, cp, adg, adc, vals in _fn_rows_ch if cp))
        if st.session_state.get("fn_sel_cp") not in _fn_cp_list:
            st.session_state["fn_sel_cp"] = "전체"
        fn_sel_cp = _fn_sc2.selectbox("캠페인", _fn_cp_list, key="fn_sel_cp")

        # Selectbox 3: Ad Group (filtered by ch + cp)
        _fn_rows_cp = [(ch, cp, adg, adc, vals) for ch, cp, adg, adc, vals in _fn_rows_ch
                       if fn_sel_cp == "전체" or cp == fn_sel_cp]
        _fn_ag_list = ["전체"] + sorted(set(adg for ch, cp, adg, adc, vals in _fn_rows_cp if adg))
        if st.session_state.get("fn_sel_ag") not in _fn_ag_list:
            st.session_state["fn_sel_ag"] = "전체"
        fn_sel_ag = _fn_sc3.selectbox("Ad Group", _fn_ag_list, key="fn_sel_ag")

        # Selectbox 4: Creative (filtered by ch + cp + ag)
        _fn_rows_ag = [(ch, cp, adg, adc, vals) for ch, cp, adg, adc, vals in _fn_rows_cp
                       if fn_sel_ag == "전체" or adg == fn_sel_ag]
        _fn_ac_list = ["전체"] + sorted(set(adc for ch, cp, adg, adc, vals in _fn_rows_ag if adc))
        if st.session_state.get("fn_sel_ac") not in _fn_ac_list:
            st.session_state["fn_sel_ac"] = "전체"
        fn_sel_ac = _fn_sc4.selectbox("Creative", _fn_ac_list, key="fn_sel_ac")

        # 최종 필터 적용
        _fn_rows_final = [(ch, cp, adg, adc, vals) for ch, cp, adg, adc, vals in _fn_rows_ag
                          if fn_sel_ac == "전체" or adc == fn_sel_ac]

        # 제목
        _fn_parts = []
        if fn_sel_ch != "전체": _fn_parts.append(fn_sel_ch)
        if fn_sel_cp != "전체": _fn_parts.append(fn_sel_cp)
        if fn_sel_ag != "전체": _fn_parts.append(fn_sel_ag)
        if fn_sel_ac != "전체": _fn_parts.append(fn_sel_ac)
        _fn_title = " > ".join(_fn_parts) if _fn_parts else "전체"
        st.markdown(f"##### {_fn_title}")

        step_defs = _PRE_STEPS if _has_pre else _POST_STEPS

        # 데이터 집계
        ch_sel = {}
        for ch, cp, adg, adc, vals in _fn_rows_final:
            for k, v in vals.items():
                ch_sel[k] = ch_sel.get(k, 0) + v

        # 상세 보기 (ad_group × ad_creative): 채널 선택은 됐고 ag/ac는 전체일 때
        if fn_sel_ch != "전체" and fn_sel_ag == "전체" and fn_sel_ac == "전체":
            detail_for_render = {}
            for ch, cp, adg, adc, vals in _fn_rows_final:
                key = (adg or "(없음)", adc or "(없음)")
                if key not in detail_for_render:
                    detail_for_render[key] = {}
                for k, v in vals.items():
                    detail_for_render[key][k] = detail_for_render[key].get(k, 0) + v
        else:
            detail_for_render = None

        # 상단: 기간 누계 퍼널 그래프
        if ch_sel:
            _render_ch_funnel(ch_sel, step_defs,
                              f"{_fn_title} ({start.strftime('%m/%d')}~{end.strftime('%m/%d')} 누계)",
                              detail_for_render)

            # 하단: 날짜별 퍼널 단계 (에어브릿지 API — 채널 필터 + event_date groupBy)
            st.divider()
            st.markdown("##### 날짜별 퍼널 단계")

            @st.cache_data(ttl=600, show_spinner="날짜별 퍼널 조회 중...")
            def _fetch_daily_ch_funnel(from_d, to_d, ch_val, cp_val, ag_val, ac_val):
                """날짜별 개별 조회 — size 제한 회피"""
                import datetime as _dt_fn
                d_start = _dt_fn.date.fromisoformat(from_d)
                d_end = _dt_fn.date.fromisoformat(to_d)
                all_rows = []
                cur = d_start
                while cur <= d_end:
                    ds = cur.isoformat()
                    payload = {
                        "from": ds, "to": ds,
                        "metrics": [
                            "web_custom_users_pv_ob_intro",
                            "web_custom_users_c_ob_intro_start",
                            "web_custom_users_pv_ob_team_choice_completed",
                            "web_custom_users_pv_ob_match_choice_completed",
                            "web_custom_users_signup",
                            "web_custom_users_c_match_prediction",
                        ],
                        "groupBys": ["channel", "campaign", "ad_group", "ad_creative"],
                        "filters": [], "sorts": [],
                        "isSummaryAvailable": False,
                        "option": {"eventTimestampSource": "event_occurred_date"},
                        "size": 200,
                    }
                    result = _airbridge_request(payload)
                    day_vals = {}
                    if result:
                        actuals = result.get("actuals") or result.get("reportData", {}).get("actuals")
                        for row in (actuals.get("data", {}).get("rows", []) if actuals else []):
                            gbs = row.get("groupBys", [])
                            if len(gbs) < 4:
                                continue
                            rch  = gbs[0].lower().strip()
                            rcp  = gbs[1].lower().strip()
                            radg = gbs[2].lower().strip()
                            radc = gbs[3].lower().strip()
                            if ch_val and rch != ch_val:
                                continue
                            if cp_val and (rcp or "") != cp_val:
                                continue
                            if ag_val and (radg or "") != ag_val:
                                continue
                            if ac_val and (radc or "") != ac_val:
                                continue
                            vals = row.get("values", {})
                            for k, v in vals.items():
                                day_vals[k] = day_vals.get(k, 0) + int(v.get("value", 0))
                    all_rows.append({"날짜": ds, **day_vals})
                    cur += _dt_fn.timedelta(days=1)
                return all_rows

            _fn_ch_val = fn_sel_ch if fn_sel_ch != "전체" else ""
            _fn_cp_val = fn_sel_cp if fn_sel_cp != "전체" else ""
            _fn_ag_val = fn_sel_ag if fn_sel_ag != "전체" else ""
            _fn_ac_val = fn_sel_ac if fn_sel_ac != "전체" else ""

            _fn_daily_data = _fetch_daily_ch_funnel(
                start.isoformat(), end.isoformat(),
                _fn_ch_val, _fn_cp_val, _fn_ag_val, _fn_ac_val
            )

            if _fn_daily_data:
                df_fn_d = pd.DataFrame(_fn_daily_data)
                _col_map = {
                    "web_custom_users_pv_ob_intro": "인트로",
                    "web_custom_users_c_ob_intro_start": "시작하기",
                    "web_custom_users_pv_ob_team_choice_completed": "경기선택",
                    "web_custom_users_pv_ob_match_choice_completed": "로그인BS",
                    "web_custom_users_signup": "가입",
                    "web_custom_users_c_match_prediction": "예측CTA",
                }
                df_fn_d = df_fn_d.rename(columns=_col_map)
                # 0이 아닌 단계만 추출
                _step_order = ["인트로", "시작하기", "경기선택", "로그인BS", "가입", "예측CTA"]
                _active_steps = [c for c in _step_order if c in df_fn_d.columns and df_fn_d[c].sum() > 0]

                # 각 단계 옆에 전환율 컬럼 추가
                show_cols = ["날짜"]
                for i, col in enumerate(_active_steps):
                    show_cols.append(col)
                    if i > 0:
                        prev = _active_steps[i - 1]
                        cvr_col = f"{prev}→{col}"
                        df_fn_d[cvr_col] = df_fn_d.apply(
                            lambda r, p=prev, c=col: f"{round(r[c]/r[p]*100,1)}%" if r.get(p, 0) > 0 else "—", axis=1)
                        show_cols.append(cvr_col)

                # 전체 전환율 (인트로→가입)
                if "인트로" in _active_steps and "가입" in _active_steps:
                    df_fn_d["전체CVR"] = df_fn_d.apply(
                        lambda r: f"{round(r['가입']/r['인트로']*100,1)}%" if r.get('인트로', 0) > 0 else "—", axis=1)
                    show_cols.append("전체CVR")

                df_fn_d = df_fn_d.sort_values("날짜", ascending=False)
                st.dataframe(df_fn_d[show_cols].reset_index(drop=True), use_container_width=True, hide_index=True)
            else:
                st.caption("날짜별 퍼널 데이터가 없습니다.")
        else:
            st.info("선택한 기간에 해당 채널 데이터가 없습니다.")

# ══════════════════════════════════════════════════════════
# TAB 5: 일별 분석
# ══════════════════════════════════════════════════════════
with tab5:
    import datetime as _dt2
    st.subheader("일별 분석")
    st.caption("날짜를 클릭하면 해당 날의 핵심 지표 + 리포트 분석 내용을 확인할 수 있어요")

    report_map = load_daily_report_list()
    mask_d2 = (df_daily["date"].dt.date >= start) & (df_daily["date"].dt.date <= end)
    d2 = df_daily[mask_d2].copy()

    # 조회 기간 내 날짜를 내림차순으로
    dates_in_range = sorted(
        [d for d in report_map if start <= _dt2.date.fromisoformat(d) <= end],
        reverse=True
    )
    if "analysis_daily_lazy_loaded" not in st.session_state:
        st.session_state["analysis_daily_lazy_loaded"] = set()
    _daily_lazy_loaded = st.session_state["analysis_daily_lazy_loaded"]
    _eager_daily_dates = set(dates_in_range[:7])  # 최근 7일만 MD·상세를 즉시 로드

    def _delta_str(cur, prev, period="전일"):
        if prev == 0:
            return None
        diff = cur - prev
        pct = round(diff / prev * 100)
        return f"{diff:+,} ({pct:+}%) · {period} {prev:,}"

    if not dates_in_range:
        st.info("선택한 기간에 해당하는 리포트 파일이 없습니다.")
    else:
        st.caption(
            "최근 7일은 핵심 지표·리포트를 바로 보여드려요. "
            "그 이전 날짜는 날짜만 표시되며, '불러오기'를 누르면 지표와 리포트(마크다운)를 읽어옵니다."
        )
        for date_str in dates_in_range:
            day_dt = _dt2.date.fromisoformat(date_str)
            prev_dt = day_dt - _dt2.timedelta(days=1)
            kor_day = ["월","화","수","목","금","토","일"][day_dt.weekday()]

            day_data = d2[d2["date"].dt.date == day_dt]
            prev_data = df_daily[df_daily["date"].dt.date == prev_dt]

            show_full_daily = (date_str in _eager_daily_dates) or (date_str in _daily_lazy_loaded)

            # 요약 라벨: 즉시 로드 구간만 DAU 요약, 그 외는 날짜만
            if show_full_daily and not day_data.empty:
                r = day_data.iloc[0]
                dau = int(r["dau_total"])
                signup = int(r["server_signup"])
                label = f"{date_str} ({kor_day}) — DAU {dau:,} · 가입 {signup:,}"
            else:
                label = f"{date_str} ({kor_day})"

            with st.expander(f"**{label}**", expanded=False):
                if not show_full_daily:
                    st.caption("아직 이 날짜의 지표·리포트(파일)를 불러오지 않았어요.")
                    if st.button("지표·리포트 분석 불러오기", key=f"daily_lazy_load_{date_str}"):
                        _daily_lazy_loaded.add(date_str)
                        st.rerun()
                    continue

                _report_content = None
                if date_str in report_map:
                    _report_content = load_daily_report(report_map[date_str])

                if not day_data.empty:
                    r = day_data.iloc[0]
                    pr = prev_data.iloc[0] if not prev_data.empty else None

                    def _v(col):
                        return int(r.get(col, 0))
                    def _pv(col):
                        return int(pr[col]) if pr is not None and col in pr.index else 0

                    # 합계
                    st.markdown('<div class="metrics-section sec-total">전체</div>', unsafe_allow_html=True)
                    with st.container():
                        c1, c2, c3, c4, c5, c6 = st.columns(6)
                        c1.metric("DAU (AB)", f"{_v('dau_total'):,}", _delta_str(_v('dau_total'), _pv('dau_total')) if pr is not None else None)
                        c2.metric("가입 합계 (서버)", f"{_v('server_signup'):,}", _delta_str(_v('server_signup'), _pv('server_signup')) if pr is not None else None)
                        c3.metric("예측 유저 (서버)", f"{_v('server_pred_user'):,}", _delta_str(_v('server_pred_user'), _pv('server_pred_user')) if pr is not None else None)
                        c4.metric("퀴즈 유저 (서버)", f"{_v('server_quiz_user'):,}", _delta_str(_v('server_quiz_user'), _pv('server_quiz_user')) if pr is not None else None)
                        c5.metric("응모 유저 (서버)", f"{_v('server_entry_user'):,}", _delta_str(_v('server_entry_user'), _pv('server_entry_user')) if pr is not None else None)
                        c6.metric("앱 설치 전환 (서버)", f"{_v('server_app_conversion'):,}", _delta_str(_v('server_app_conversion'), _pv('server_app_conversion')) if pr is not None else None, help="웹 가입 유저가 앱을 설치해 전환한 수 (서버 기준)")

                    # 웹
                    st.markdown('<div class="metrics-section sec-web">웹</div>', unsafe_allow_html=True)
                    with st.container():
                        w1, w2, w3, w4, w5 = st.columns(5)
                        w1.metric("DAU (AB)", f"{_v('dau_web'):,}", _delta_str(_v('dau_web'), _pv('dau_web')) if pr is not None else None)
                        w2.metric("가입 합계 (서버)", f"{_v('server_signup_web'):,}", _delta_str(_v('server_signup_web'), _pv('server_signup_web')) if pr is not None else None)
                        w3.metric("예측 유저 (서버)", f"{_v('server_pred_user_web'):,}", _delta_str(_v('server_pred_user_web'), _pv('server_pred_user_web')) if pr is not None else None)
                        w4.metric("퀴즈 유저 (서버)", f"{_v('server_quiz_user_web'):,}", _delta_str(_v('server_quiz_user_web'), _pv('server_quiz_user_web')) if pr is not None else None)
                        w5.metric("응모 유저 (서버)", f"{_v('server_entry_user_web'):,}", _delta_str(_v('server_entry_user_web'), _pv('server_entry_user_web')) if pr is not None else None)

                    # 앱
                    st.markdown('<div class="metrics-section sec-app">앱</div>', unsafe_allow_html=True)
                    with st.container():
                        a1, a2, a3, a4, a5 = st.columns(5)
                        a1.metric("DAU (AB)", f"{_v('dau_app'):,}", _delta_str(_v('dau_app'), _pv('dau_app')) if pr is not None else None)
                        a2.metric("가입 합계 (서버)", f"{_v('server_signup_app'):,}", _delta_str(_v('server_signup_app'), _pv('server_signup_app')) if pr is not None else None)
                        a3.metric("예측 유저 (서버)", f"{_v('server_pred_user_app'):,}", _delta_str(_v('server_pred_user_app'), _pv('server_pred_user_app')) if pr is not None else None)
                        a4.metric("퀴즈 유저 (서버)", f"{_v('server_quiz_user_app'):,}", _delta_str(_v('server_quiz_user_app'), _pv('server_quiz_user_app')) if pr is not None else None)
                        a5.metric("응모 유저 (서버)", f"{_v('server_entry_user_app'):,}", _delta_str(_v('server_entry_user_app'), _pv('server_entry_user_app')) if pr is not None else None)

                    # 비용 & 매출
                    st.markdown('<div class="metrics-section sec-cost">비용 & 매출</div>', unsafe_allow_html=True)
                    with st.container():
                        # 해당일 비용 (UTM 채널 비용만 CAC 계산)
                        _day_costs_all = 0
                        _day_cac_spend = 0
                        if not df_costs.empty and "date" in df_costs.columns:
                            _dc = df_costs[df_costs["date"] == date_str]
                            _day_costs_all = int(_dc["spend"].sum())
                            _cac_mask_d = (_dc["channel"].notna() & (_dc["channel"] != "") &
                                           (~_dc.get("category", pd.Series(dtype=str)).fillna("").str.contains("알림톡")))
                            _day_cac_spend = int(_dc[_cac_mask_d]["spend"].sum())
                        _day_signup = _v('server_signup')
                        _day_cac = int(_day_cac_spend / _day_signup) if _day_signup > 0 and _day_cac_spend > 0 else 0
                        # 해당일 매출
                        _day_rev = 0
                        if revenue_data:
                            _df_rev_d = pd.DataFrame(revenue_data)
                            if "date" in _df_rev_d.columns and "amount" in _df_rev_d.columns:
                                _day_rev = int(_df_rev_d[_df_rev_d["date"] == date_str]["amount"].sum())
                        x1, x2, x3, x4 = st.columns(4)
                        x1.metric("총 비용", f"{_day_costs_all:,}원" if _day_costs_all > 0 else "—")
                        x2.metric("CAC (UTM채널/가입)", f"{_day_cac:,}원" if _day_cac > 0 else "—", help="알림톡·기타비용 제외 UTM 채널 비용 ÷ 서버 가입수")
                        x3.metric("매출", f"{_day_rev:,}원" if _day_rev > 0 else "—")
                        _day_profit = _day_rev - _day_costs_all
                        x4.metric("수익", f"{_day_profit:+,}원" if (_day_costs_all > 0 or _day_rev > 0) else "—")
                    st.divider()
                elif _report_content:
                    _report_body = strip_analysis_section(_report_content)
                    if _report_body:
                        st.warning(
                            f"`data.json`에 {date_str} 데이터가 없어 지표 카드를 표시할 수 없습니다. "
                            "아래는 리포트 파일(마크다운) 내용입니다. "
                            "`daily_pipeline.py` 실행 후 지표 카드·전일比가 표시됩니다."
                        )
                        st.markdown(_report_body)
                        st.divider()
                    else:
                        st.caption(
                            f"`data.json`에 {date_str} 데이터가 없습니다. "
                            "`daily_pipeline.py`를 실행해 주세요."
                        )

                # 분석 섹션 (리포트 파일 ## {N}. 분석)
                if _report_content:
                    analysis_section = extract_analysis_section(_report_content)
                    if analysis_section:
                        st.markdown(analysis_section)
                    else:
                        st.caption("리포트에 분석 섹션이 없습니다.")
                elif date_str in report_map:
                    st.caption("리포트 파일을 불러오지 못했습니다.")
                elif day_data.empty:
                    st.caption(f"{date_str} — `data.json`·리포트 파일 모두 없습니다.")

# ══════════════════════════════════════════════════════════
# TAB 6: 주간 분석
# ══════════════════════════════════════════════════════════
with tab6:
    st.subheader("주간 분석")
    st.caption("주간 리포트의 분석 섹션을 확인합니다")

    weekly_map = load_report_list("weekly","weekly")

    if not weekly_map:
        st.info("주간 리포트 파일이 없습니다.")
    else:
        if "analysis_weekly_lazy_loaded" not in st.session_state:
            st.session_state["analysis_weekly_lazy_loaded"] = set()
        _weekly_lazy_loaded = st.session_state["analysis_weekly_lazy_loaded"]
        sorted_weeks = sorted(weekly_map.keys(), reverse=True)
        _eager_weeks = set(sorted_weeks[:5])  # 최근 5주만 MD·상세·AB 호출

        st.caption(
            "최근 5주는 핵심 지표·리포트를 바로 보여드려요. "
            "그 이전 주는 주차만 표시되며, '불러오기'를 누르면 지표와 리포트(마크다운)를 읽어옵니다."
        )
        for wi, label in enumerate(sorted_weeks):
            show_full_week = (label in _eager_weeks) or (label in _weekly_lazy_loaded)
            with st.expander(f"**{label}**", expanded=False):
                if not show_full_week:
                    st.caption("아직 이 주의 지표·리포트(파일)를 불러오지 않았어요.")
                    if st.button("지표·리포트 분석 불러오기", key=f"weekly_lazy_load_{wi}"):
                        _weekly_lazy_loaded.add(label)
                        st.rerun()
                    continue
                # 주간 핵심 지표 + 전주比
                # 주차에서 날짜 범위 추출 (예: 2026-W14 → 03/30~04/05)
                import re as _re
                wm = _re.match(r"(\d{4})-W(\d+)", label)
                if wm:
                    yr, wk = int(wm.group(1)), int(wm.group(2))
                    w_start = _dt2.date.fromisocalendar(yr, wk, 1)
                    w_end = _dt2.date.fromisocalendar(yr, wk, 7)
                    mask_w = (df_daily["date"].dt.date >= w_start) & (df_daily["date"].dt.date <= w_end)
                    w_data = df_daily[mask_w]

                    prev_start = w_start - _dt2.timedelta(days=7)
                    prev_end = w_end - _dt2.timedelta(days=7)
                    mask_pw = (df_daily["date"].dt.date >= prev_start) & (df_daily["date"].dt.date <= prev_end)
                    pw_data = df_daily[mask_pw]

                    if not w_data.empty:
                        def _ws(col):
                            return int(w_data[col].sum()) if col in w_data.columns else 0
                        def _pws(col):
                            return int(pw_data[col].sum()) if not pw_data.empty and col in pw_data.columns else 0
                        def _wm(col):
                            return int(round(w_data[col].mean())) if col in w_data.columns and len(w_data) > 0 else 0
                        def _pwm(col):
                            return int(round(pw_data[col].mean())) if not pw_data.empty and col in pw_data.columns else 0

                        # 주간 AU — 완료된 과거 주는 24시간 캐시, 당주는 10분
                        _today = _dt2.date.today()
                        _au_fn_w = fetch_airbridge_au_historical if w_end < _today else fetch_airbridge_au
                        _au_fn_p = fetch_airbridge_au_historical if prev_end < _today else fetch_airbridge_au
                        w_au = _au_fn_w(w_start.isoformat(), w_end.isoformat())
                        pw_au = _au_fn_p(prev_start.isoformat(), prev_end.isoformat())
                        w_au_total = (w_au["web_au"] + w_au["app_au"]) if w_au else None
                        pw_au_total = (pw_au["web_au"] + pw_au["app_au"]) if pw_au else None

                        st.markdown('<div class="metrics-section sec-total">전체 합계</div>', unsafe_allow_html=True)
                        with st.container():
                            c1, c2, c3, c4, c5 = st.columns(5)
                            if w_au_total is not None:
                                c1.metric("AU 유니크 (AB)", f"{w_au_total:,}명",
                                    _delta_str(w_au_total, pw_au_total, "전주") if pw_au_total else None)
                            else:
                                c1.metric("AU 유니크 (AB)", "—", help="AB API 연결 필요")
                            c2.metric("가입 합계 (서버)", f"{_ws('server_signup'):,}", _delta_str(_ws('server_signup'), _pws('server_signup'), "전주") if _pws('server_signup') > 0 else None)
                            c3.metric("예측 일평균 (서버)", f"{_wm('server_pred_user'):,}명", _delta_str(_wm('server_pred_user'), _pwm('server_pred_user'), "전주") if _pwm('server_pred_user') > 0 else None)
                            c4.metric("퀴즈 일평균 (서버)", f"{_wm('server_quiz_user'):,}명", _delta_str(_wm('server_quiz_user'), _pwm('server_quiz_user'), "전주") if _pwm('server_quiz_user') > 0 else None)
                            c5.metric("응모 일평균 (서버)", f"{_wm('server_entry_user'):,}명", _delta_str(_wm('server_entry_user'), _pwm('server_entry_user'), "전주") if _pwm('server_entry_user') > 0 else None)

                        st.markdown('<div class="metrics-section sec-web">웹</div>', unsafe_allow_html=True)
                        with st.container():
                            w1, w2, w3, w4, w5 = st.columns(5)
                            if w_au:
                                w1.metric("AU 유니크 (AB)", f"{w_au['web_au']:,}명",
                                    _delta_str(w_au["web_au"], pw_au["web_au"], "전주") if pw_au else None)
                            else:
                                w1.metric("AU 유니크 (AB)", "—")
                            w2.metric("가입 합계 (서버)", f"{_ws('server_signup_web'):,}", _delta_str(_ws('server_signup_web'), _pws('server_signup_web'), "전주") if _pws('server_signup_web') > 0 else None)
                            w3.metric("예측 일평균 (서버)", f"{_wm('server_pred_user_web'):,}명", _delta_str(_wm('server_pred_user_web'), _pwm('server_pred_user_web'), "전주") if _pwm('server_pred_user_web') > 0 else None)
                            w4.metric("퀴즈 일평균 (서버)", f"{_wm('server_quiz_user_web'):,}명", _delta_str(_wm('server_quiz_user_web'), _pwm('server_quiz_user_web'), "전주") if _pwm('server_quiz_user_web') > 0 else None)
                            w5.metric("응모 일평균 (서버)", f"{_wm('server_entry_user_web'):,}명", _delta_str(_wm('server_entry_user_web'), _pwm('server_entry_user_web'), "전주") if _pwm('server_entry_user_web') > 0 else None)

                        st.markdown('<div class="metrics-section sec-app">앱</div>', unsafe_allow_html=True)
                        with st.container():
                            a1, a2, a3, a4, a5 = st.columns(5)
                            if w_au:
                                a1.metric("AU 유니크 (AB)", f"{w_au['app_au']:,}명",
                                    _delta_str(w_au["app_au"], pw_au["app_au"], "전주") if pw_au else None)
                            else:
                                a1.metric("AU 유니크 (AB)", "—")
                            a2.metric("가입 합계 (서버)", f"{_ws('server_signup_app'):,}", _delta_str(_ws('server_signup_app'), _pws('server_signup_app'), "전주") if _pws('server_signup_app') > 0 else None)
                            a3.metric("예측 일평균 (서버)", f"{_wm('server_pred_user_app'):,}명", _delta_str(_wm('server_pred_user_app'), _pwm('server_pred_user_app'), "전주") if _pwm('server_pred_user_app') > 0 else None)
                            a4.metric("퀴즈 일평균 (서버)", f"{_wm('server_quiz_user_app'):,}명", _delta_str(_wm('server_quiz_user_app'), _pwm('server_quiz_user_app'), "전주") if _pwm('server_quiz_user_app') > 0 else None)
                            a5.metric("응모 일평균 (서버)", f"{_wm('server_entry_user_app'):,}명", _delta_str(_wm('server_entry_user_app'), _pwm('server_entry_user_app'), "전주") if _pwm('server_entry_user_app') > 0 else None)

                        st.caption(f"{w_start.strftime('%m/%d')} ~ {w_end.strftime('%m/%d')} ({len(w_data)}일)")
                        st.divider()

                content = load_daily_report(weekly_map[label])
                if content:
                    analysis_section = extract_analysis_section(content)
                    if analysis_section:
                        st.markdown(analysis_section)
                    else:
                        st.caption("분석 섹션이 없습니다.")
                else:
                    st.caption("파일을 불러오지 못했습니다.")

# ══════════════════════════════════════════════════════════
# TAB 7: 월간 분석
# ══════════════════════════════════════════════════════════
with tab7:
    st.subheader("월간 분석")
    st.caption("월간 리포트의 분석 섹션을 확인합니다")

    monthly_map = load_report_list("monthly", "monthly")

    if not monthly_map:
        st.info("월간 리포트 파일이 없습니다.")
    else:
        sorted_months = sorted(monthly_map.keys(), reverse=True)
        for mi, label in enumerate(sorted_months):
            with st.expander(f"**{label}**", expanded=False):
                # 월간 핵심 지표 + 전월比
                import re as _re2
                mm = _re2.match(r"(\d{4})-(\d{2})", label)
                if mm:
                    yr, mo = int(mm.group(1)), int(mm.group(2))
                    import calendar
                    m_start = _dt2.date(yr, mo, 1)
                    m_end = _dt2.date(yr, mo, calendar.monthrange(yr, mo)[1])
                    mask_m = (df_daily["date"].dt.date >= m_start) & (df_daily["date"].dt.date <= m_end)
                    m_data = df_daily[mask_m]

                    if mo == 1:
                        pm_start = _dt2.date(yr - 1, 12, 1)
                        pm_end = _dt2.date(yr - 1, 12, 31)
                    else:
                        pm_start = _dt2.date(yr, mo - 1, 1)
                        pm_end = _dt2.date(yr, mo - 1, calendar.monthrange(yr, mo - 1)[1])
                    mask_pm = (df_daily["date"].dt.date >= pm_start) & (df_daily["date"].dt.date <= pm_end)
                    pm_data = df_daily[mask_pm]

                    if not m_data.empty:
                        def _ms(col):
                            return int(m_data[col].sum()) if col in m_data.columns else 0
                        def _pms(col):
                            return int(pm_data[col].sum()) if not pm_data.empty and col in pm_data.columns else 0
                        def _mm(col):
                            return int(round(m_data[col].mean())) if col in m_data.columns and len(m_data) > 0 else 0
                        def _pmm(col):
                            return int(round(pm_data[col].mean())) if not pm_data.empty and col in pm_data.columns else 0

                        # 월간 AU — 완료된 과거 월은 24시간 캐시
                        _today_m = _dt2.date.today()
                        _au_fn_m = fetch_airbridge_au_historical if m_end < _today_m else fetch_airbridge_au
                        _au_fn_pm = fetch_airbridge_au_historical if pm_end < _today_m else fetch_airbridge_au
                        m_au = _au_fn_m(m_start.isoformat(), m_end.isoformat())
                        pm_au = _au_fn_pm(pm_start.isoformat(), pm_end.isoformat())
                        m_au_total = (m_au["web_au"] + m_au["app_au"]) if m_au else None
                        pm_au_total = (pm_au["web_au"] + pm_au["app_au"]) if pm_au else None

                        st.markdown('<div class="metrics-section sec-total">전체 합계</div>', unsafe_allow_html=True)
                        with st.container():
                            c1, c2, c3, c4, c5 = st.columns(5)
                            if m_au_total is not None:
                                c1.metric("AU 유니크 (AB)", f"{m_au_total:,}명",
                                    _delta_str(m_au_total, pm_au_total, "전월") if pm_au_total else None)
                            else:
                                c1.metric("AU 유니크 (AB)", "—", help="AB API 연결 필요")
                            c2.metric("가입 합계 (서버)", f"{_ms('server_signup'):,}", _delta_str(_ms('server_signup'), _pms('server_signup'), "전월") if _pms('server_signup') > 0 else None)
                            c3.metric("예측 일평균 (서버)", f"{_mm('server_pred_user'):,}명", _delta_str(_mm('server_pred_user'), _pmm('server_pred_user'), "전월") if _pmm('server_pred_user') > 0 else None)
                            c4.metric("퀴즈 일평균 (서버)", f"{_mm('server_quiz_user'):,}명", _delta_str(_mm('server_quiz_user'), _pmm('server_quiz_user'), "전월") if _pmm('server_quiz_user') > 0 else None)
                            c5.metric("응모 일평균 (서버)", f"{_mm('server_entry_user'):,}명", _delta_str(_mm('server_entry_user'), _pmm('server_entry_user'), "전월") if _pmm('server_entry_user') > 0 else None)

                        st.markdown('<div class="metrics-section sec-web">웹</div>', unsafe_allow_html=True)
                        with st.container():
                            w1, w2, w3, w4, w5 = st.columns(5)
                            if m_au:
                                w1.metric("AU 유니크 (AB)", f"{m_au['web_au']:,}명",
                                    _delta_str(m_au["web_au"], pm_au["web_au"], "전월") if pm_au else None)
                            else:
                                w1.metric("AU 유니크 (AB)", "—")
                            w2.metric("가입 합계 (서버)", f"{_ms('server_signup_web'):,}")
                            w3.metric("예측 일평균 (서버)", f"{_mm('server_pred_user_web'):,}명")
                            w4.metric("퀴즈 일평균 (서버)", f"{_mm('server_quiz_user_web'):,}명")
                            w5.metric("응모 일평균 (서버)", f"{_mm('server_entry_user_web'):,}명")

                        st.markdown('<div class="metrics-section sec-app">앱</div>', unsafe_allow_html=True)
                        with st.container():
                            a1, a2, a3, a4, a5 = st.columns(5)
                            if m_au:
                                a1.metric("AU 유니크 (AB)", f"{m_au['app_au']:,}명",
                                    _delta_str(m_au["app_au"], pm_au["app_au"], "전월") if pm_au else None)
                            else:
                                a1.metric("AU 유니크 (AB)", "—")
                            a2.metric("가입 합계 (서버)", f"{_ms('server_signup_app'):,}")
                            a3.metric("예측 일평균 (서버)", f"{_mm('server_pred_user_app'):,}명")
                            a4.metric("퀴즈 일평균 (서버)", f"{_mm('server_quiz_user_app'):,}명")
                            a5.metric("응모 일평균 (서버)", f"{_mm('server_entry_user_app'):,}명")

                        st.caption(f"{m_start.strftime('%Y/%m/%d')} ~ {m_end.strftime('%m/%d')} ({len(m_data)}일)")
                        st.divider()

                content = load_daily_report(monthly_map[label])
                if content:
                    analysis_section = extract_analysis_section(content)
                    if analysis_section:
                        st.markdown(analysis_section)
                    else:
                        st.caption("분석 섹션이 없습니다.")
                else:
                    st.caption("파일을 불러오지 못했습니다.")
# ══════════════════════════════════════════════════════════
# TAB 10: 앱 전환 채널
# ══════════════════════════════════════════════════════════
with tab10:
    st.subheader("앱 전환 채널")
    st.caption(
        f"{start.strftime('%m/%d')} ~ {end.strftime('%m/%d')} | "
        "에어브릿지 app_install_users 기준"
    )

    _aic_raw = data.get("app_install_channels", [])
    if not _aic_raw:
        st.info("데이터 없음 — daily_pipeline.py 실행 필요")
    else:
        df_aic = pd.DataFrame(_aic_raw)
        df_aic["date"] = pd.to_datetime(df_aic["date"]).dt.date
        df_aic = df_aic[(df_aic["date"] >= start) & (df_aic["date"] <= end)].copy()

        if df_aic.empty:
            st.info("선택한 기간에 데이터가 없습니다.")
        else:
            # ── 주요 유입 경로 안내 ───────────────────────────
            st.markdown("""
| 경로 | 채널 / 캠페인 | 설명 |
|------|-------------|------|
| 🟡 **카카오 알림톡** | `kakao_notitalk` | 응모결과·당첨 알림 수신 후 앱 전환 |
| 🔵 **응모 상단 (apply_top)** | `polyball_web / apply_top` | 웹 응모 페이지 상단 앱 유도 배너 클릭 |
| 🟢 **승부예측 상단 (pick_top)** | `polyball_web / pick_top` | 웹 승부예측 페이지 상단 앱 유도 배너 클릭 |
""")

            # ── 일별 × 채널+캠페인 stacked bar ───────────────
            df_aic["ch_camp"] = df_aic.apply(
                lambda r: f"{r['channel']} / {r['campaign']}" if r["campaign"] else r["channel"],
                axis=1,
            )
            df_dc = df_aic.groupby(["date", "ch_camp"])["installs"].sum().reset_index()
            df_dc["date"] = pd.to_datetime(df_dc["date"])
            ch_order = df_dc.groupby("ch_camp")["installs"].sum().sort_values(ascending=False).index.tolist()

            # 주요 경로 강조 색상, 나머지 회색 계열
            _KEY_COLORS = {
                "kakao_notitalk":              "#F5C518",   # 노랑
                "kakao_notitalk / ticket_error":            "#F5C518",
                "kakao_notitalk / ticket_winners_announce": "#E8A800",
                "polyball_web / apply_top":    "#2979FF",   # 파랑
                "polyball_web / pick_top":     "#00C853",   # 초록
            }
            _GRAY_PALETTE = ["#BDBDBD", "#9E9E9E", "#757575", "#616161",
                             "#A5D6A7", "#90CAF9", "#FFCC80", "#EF9A9A",
                             "#CE93D8", "#80DEEA"]
            _gray_idx = 0
            _color_map = {}
            for seg in ch_order:
                if seg in _KEY_COLORS:
                    _color_map[seg] = _KEY_COLORS[seg]
                else:
                    # kakao_notitalk 접두사 처리
                    if seg.startswith("kakao_notitalk"):
                        _color_map[seg] = "#F5C518"
                    else:
                        _color_map[seg] = _GRAY_PALETTE[_gray_idx % len(_GRAY_PALETTE)]
                        _gray_idx += 1

            fig_stack = px.bar(
                df_dc, x="date", y="installs", color="ch_camp",
                category_orders={"ch_camp": ch_order},
                color_discrete_map=_color_map,
                barmode="stack",
            )
            fig_stack.update_layout(
                height=440,
                xaxis_title="", yaxis_title="인스톨",
                legend=dict(title="채널 / 캠페인", orientation="h", y=-0.35),
                margin=dict(t=10, b=10),
            )
            st.plotly_chart(fig_stack, use_container_width=True)

            # ── 날짜 × 채널+캠페인 피벗 테이블 ──────────────
            st.divider()
            pivot = df_aic.groupby(["date", "ch_camp"])["installs"].sum().unstack(fill_value=0)
            pivot = pivot[[c for c in ch_order if c in pivot.columns]]
            pivot["합계"] = pivot.sum(axis=1)
            pivot.index = pivot.index.astype(str)
            pivot.index.name = "날짜"

            # 주요 경로 컬럼 강조
            _KEY_BG = {
                "polyball_web / apply_top": "background-color: #DBEAFE; font-weight: bold",
                "polyball_web / pick_top":  "background-color: #DCFCE7; font-weight: bold",
            }
            def _highlight_cols(df):
                styles = pd.DataFrame("", index=df.index, columns=df.columns)
                for col, css in _KEY_BG.items():
                    if col in styles.columns:
                        styles[col] = css
                for col in styles.columns:
                    if str(col).startswith("kakao_notitalk"):
                        styles[col] = "background-color: #FEF9C3; font-weight: bold"
                return styles

            st.dataframe(pivot.style.apply(_highlight_cols, axis=None), use_container_width=True)

            # ── 채널 → 캠페인 → Ad Group → Ad Creative 드릴다운 ──
            st.divider()
            st.markdown("#### 상세 분석")
            st.caption("채널 선택 → 캠페인 → Ad Group → Ad Creative 순으로 쪼개서 조회")

            _d1, _d2, _d3, _d4 = st.columns(4)

            _ch_opts = df_aic.groupby("channel")["installs"].sum().sort_values(ascending=False).index.tolist()
            _sel_ch10 = _d1.selectbox(
                "채널",
                options=["(전체)"] + _ch_opts,
                key="t10_ch",
            )
            df_drill = df_aic if _sel_ch10 == "(전체)" else df_aic[df_aic["channel"] == _sel_ch10]

            _camp_opts = sorted(df_drill["campaign"].dropna().unique().tolist())
            _camp_opts = [c for c in _camp_opts if c != ""]
            _sel_camp10 = _d2.selectbox(
                "캠페인",
                options=["(전체)"] + _camp_opts,
                key="t10_camp",
            )
            if _sel_camp10 != "(전체)":
                df_drill = df_drill[df_drill["campaign"] == _sel_camp10]

            _ag_opts = sorted(df_drill["ad_group"].dropna().unique().tolist())
            _ag_opts = [a for a in _ag_opts if a != ""]
            _sel_ag10 = _d3.selectbox(
                "Ad Group",
                options=["(전체)"] + _ag_opts,
                key="t10_ag",
            )
            if _sel_ag10 != "(전체)":
                df_drill = df_drill[df_drill["ad_group"] == _sel_ag10]

            _ac_opts = sorted(df_drill["ad_creative"].dropna().unique().tolist())
            _ac_opts = [a for a in _ac_opts if a != ""]
            _sel_ac10 = _d4.selectbox(
                "Ad Creative",
                options=["(전체)"] + _ac_opts,
                key="t10_ac",
            )
            if _sel_ac10 != "(전체)":
                df_drill = df_drill[df_drill["ad_creative"] == _sel_ac10]

            # 드릴다운 결과: 일별 인스톨
            df_drill_daily = df_drill.groupby("date")["installs"].sum().reset_index()
            df_drill_daily["date"] = pd.to_datetime(df_drill_daily["date"])

            _fig_drill = px.bar(
                df_drill_daily, x="date", y="installs",
                text="installs",
                color_discrete_sequence=["#4C78A8"],
            )
            _fig_drill.update_traces(textposition="outside")
            _fig_drill.update_layout(
                height=320,
                xaxis_title="", yaxis_title="인스톨",
                margin=dict(t=30),
                title=f"{_sel_ch10} / {_sel_camp10} / {_sel_ag10} / {_sel_ac10}  합계: {int(df_drill['installs'].sum()):,}",
            )
            st.plotly_chart(_fig_drill, use_container_width=True)

            # 드릴다운 상세 테이블 (channel~ad_creative 단위)
            df_drill_tbl = (
                df_drill.groupby(["channel", "campaign", "ad_group", "ad_creative"])["installs"]
                .sum().reset_index()
                .sort_values("installs", ascending=False)
            )
            df_drill_tbl.columns = ["채널", "캠페인", "Ad Group", "Ad Creative", "인스톨"]
            st.dataframe(df_drill_tbl, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════
# TAB 12: 비용 관리
# ══════════════════════════════════════════════════════════
with tab12:
    st.subheader("비용 관리")
    st.caption("일별 채널별 비용 기록 — 데이터는 수동 입력 ('비용 추가해줘'로 요청) · 미래 예약 비용은 해당 날짜 도래 시 자동 포함")

    df_costs_all = pd.DataFrame(data.get("costs", []))
    if not df_costs_all.empty and "date" in df_costs_all.columns:
        df_costs_all["date"] = pd.to_datetime(df_costs_all["date"])
        # 미래 예약 비용 제외 — 오늘까지만 '집행된' 비용으로 집계
        import datetime as _dt_cost12
        _today_ts = pd.Timestamp(_dt_cost12.date.today())
        _future_count = int((df_costs_all["date"] > _today_ts).sum())
        df_costs_all = df_costs_all[df_costs_all["date"] <= _today_ts]
        df_costs_all = df_costs_all.sort_values("date", ascending=False)
        if _future_count > 0:
            st.caption(f"ℹ️ 미래 예약 비용 {_future_count}건은 해당 날짜 도래 시 자동 반영")

        # 구분별 요약
        if "category" in df_costs_all.columns:
            cat_tabs = st.tabs(["전체"] + sorted(df_costs_all["category"].unique().tolist()))
        else:
            cat_tabs = [st.container()]

        for ci, ct in enumerate(cat_tabs):
            with ct:
                if ci == 0:
                    df_view = df_costs_all
                    st.markdown("##### 전체 비용 요약")
                else:
                    cat_name = sorted(df_costs_all["category"].unique().tolist())[ci - 1]
                    df_view = df_costs_all[df_costs_all["category"] == cat_name]
                    st.markdown(f"##### {cat_name} 비용")

                total_v = int(df_view["spend"].sum())
                st.metric("합계", f"{total_v:,}원")

                # 채널별 집계
                ch_col = "channel"
                if "campaign" in df_view.columns:
                    df_view = df_view.copy()
                    df_view["채널상세"] = df_view["channel"].fillna("") + " / " + df_view["campaign"].fillna("")
                    ch_col = "채널상세"
                cost_by = df_view.groupby(ch_col)["spend"].sum().sort_values(ascending=False)
                fig_c = px.bar(x=cost_by.index, y=cost_by.values,
                               labels={"x": "채널", "y": "비용(원)"},
                               color_discrete_sequence=["#EF4444"])
                fig_c.update_layout(height=280, margin=dict(t=10))
                st.plotly_chart(fig_c, use_container_width=True, key=f"cost_chart_{ci}")

                # 상세 테이블
                rename_cols = {"date": "날짜", "channel": "채널", "campaign": "캠페인",
                               "ad_group": "Ad Group", "ad_creative": "Ad Creative",
                               "spend": "비용(원)", "category": "구분", "note": "비고"}
                show_cols = [c for c in ["날짜","채널","캠페인","Ad Group","Ad Creative","비용(원)","비고"] if c in [rename_cols.get(k,k) for k in df_view.columns]]
                st.dataframe(
                    df_view.rename(columns=rename_cols)[show_cols].set_index("날짜"),
                    use_container_width=True
                )

        # 일별 추이
        st.divider()
        st.markdown("##### 일별 비용 추이")
        if "category" in df_costs_all.columns:
            daily_cost = df_costs_all.groupby(["date", "category"])["spend"].sum().reset_index()
            fig_dc = px.bar(daily_cost, x="date", y="spend", color="category",
                            labels={"date": "날짜", "spend": "비용(원)", "category": "구분"},
                            color_discrete_sequence=["#EF4444", "#3B82F6", "#94A3B8"])
        else:
            daily_cost = df_costs_all.groupby("date")["spend"].sum().reset_index()
            fig_dc = px.bar(daily_cost, x="date", y="spend",
                            labels={"date": "날짜", "spend": "비용(원)"},
                            color_discrete_sequence=["#EF4444"])
        fig_dc.update_layout(height=300, margin=dict(t=10))
        st.plotly_chart(fig_dc, use_container_width=True)
    else:
        st.info("비용 데이터가 없습니다.")

    # 매출 섹션
    st.divider()
    st.markdown("##### 매출")
    revenue_data = data.get("revenue", [])
    if revenue_data:
        df_rev_all = pd.DataFrame(revenue_data)
        df_rev_all["date"] = pd.to_datetime(df_rev_all["date"])
        df_rev_all = df_rev_all.sort_values("date", ascending=False)
        st.dataframe(df_rev_all.rename(columns={"date": "날짜", "amount": "매출(원)", "source": "출처", "note": "메모"}).set_index("날짜"),
                      use_container_width=True)
    else:
        st.info("매출 데이터 미등록 — 데이터가 생기면 '매출 추가해줘'로 요청")

# ══════════════════════════════════════════════════════════
# TAB 11: 이슈 캘린더
# ══════════════════════════════════════════════════════════
with tab11:
    import calendar as _cal
    import datetime as _dt11
    import plotly.graph_objects as _go11

    _today11 = _dt11.date.today()

    # ── issue_log.json 로드 ───────────────────────────────
    _raw_log = load_issue_log()
    _issue_log: dict = {}
    for _e in _raw_log:
        _issue_log.setdefault(_e["date"], []).append(_e)

    # ── 헤더 + 월 네비게이션 ─────────────────────────────
    st.subheader("🗓️ 이슈 캘린더")
    st.caption("조회 기간 무관 — 앱 변경 / 마케팅 집행 / CRM / 외부 이벤트 일지")

    _nav_l, _nav_title, _nav_r = st.columns([1, 4, 1])
    if "cal_yr" not in st.session_state:
        st.session_state["cal_yr"] = _today11.year
    if "cal_mo" not in st.session_state:
        st.session_state["cal_mo"] = _today11.month

    if _nav_l.button("◀", key="cal_prev"):
        if st.session_state["cal_mo"] == 1:
            st.session_state["cal_yr"] -= 1
            st.session_state["cal_mo"] = 12
        else:
            st.session_state["cal_mo"] -= 1

    if _nav_r.button("▶", key="cal_next"):
        if st.session_state["cal_mo"] == 12:
            st.session_state["cal_yr"] += 1
            st.session_state["cal_mo"] = 1
        else:
            st.session_state["cal_mo"] += 1

    _sel_yr = st.session_state["cal_yr"]
    _sel_mo = st.session_state["cal_mo"]
    _nav_title.markdown(
        f"<h3 style='text-align:center; margin:0; padding-top:4px'>"
        f"{_sel_yr}년 {_sel_mo}월</h3>",
        unsafe_allow_html=True,
    )

    # ── 캘린더 그리드 ────────────────────────────────────
    _TAG_COLORS = {
        "앱 업데이트":  "#3B82F6",
        "마케팅 집행":  "#10B981",
        "CRM 발송":    "#F59E0B",
        "외부 이벤트":  "#8B5CF6",
        "버그/장애":    "#EF4444",
        "정책 변경":    "#6B7280",
        "기타":        "#94A3B8",
    }

    _first_day, _days_in_month = _cal.monthrange(_sel_yr, _sel_mo)
    _start_offset = (_first_day + 1) % 7  # 일요일 시작
    _WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
    _cells = [None] * _start_offset + list(range(1, _days_in_month + 1))
    while len(_cells) % 7:
        _cells.append(None)
    _n_weeks = len(_cells) // 7

    # HTML 캘린더 렌더링 (plotly 대신 더 예쁜 HTML)
    _cal_html = """
<style>
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; font-family:'Segoe UI',sans-serif; }
.cal-header { text-align:center; font-size:11px; font-weight:700; color:#94A3B8;
              padding:6px 0; letter-spacing:.5px; }
.cal-cell { min-height:72px; border-radius:10px; padding:6px 8px; position:relative;
            background:#F8FAFC; border:1px solid #E2E8F0; }
.cal-cell.empty { background:transparent; border:none; }
.cal-cell.today { background:#FEF9C3; border:2px solid #FBBF24; }
.cal-cell.has-issue { background:#EFF6FF; border:1px solid #BFDBFE; }
.cal-cell.today.has-issue { background:#FEF9C3; border:2px solid #FBBF24; }
.cal-day { font-size:13px; font-weight:700; color:#1E293B; }
.cal-cell.today .cal-day { color:#D97706; }
.cal-tags { display:flex; flex-wrap:wrap; gap:2px; margin-top:4px; }
.cal-tag { font-size:9px; padding:1px 5px; border-radius:20px;
           color:#fff; font-weight:600; white-space:nowrap; }
.cal-snippet { font-size:10px; color:#64748B; margin-top:3px;
               line-height:1.3; overflow:hidden;
               display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
</style>
<div class="cal-grid">
"""
    for wd in _WEEKDAYS:
        _cal_html += f'<div class="cal-header">{wd}</div>'

    for day in _cells:
        if day is None:
            _cal_html += '<div class="cal-cell empty"></div>'
            continue
        _ds = f"{_sel_yr}-{_sel_mo:02d}-{day:02d}"
        _entries = _issue_log.get(_ds, [])
        _is_today = (_ds == str(_today11))
        _cls = "cal-cell"
        if _is_today:   _cls += " today"
        if _entries:    _cls += " has-issue"
        _tags_html = ""
        _seen_tags: set = set()
        for _e in _entries:
            for _t in _e.get("tags", []):
                if _t not in _seen_tags:
                    _c = _TAG_COLORS.get(_t, "#94A3B8")
                    _tags_html += f'<span class="cal-tag" style="background:{_c}">{_t}</span>'
                    _seen_tags.add(_t)
        _snippet = ""
        if _entries:
            _cnt = len(_entries)
            _cnt_badge = (f'<span style="float:right;font-size:9px;background:#CBD5E1;'
                          f'color:#475569;padding:1px 4px;border-radius:8px">{_cnt}건</span>'
                          ) if _cnt > 1 else ""
            _first_content = _entries[0].get("content", "")
            _first_line = _first_content.split("\n")[0]
            _snippet = (f'<div class="cal-snippet">{_cnt_badge}'
                        f'{_first_line[:40]}{"…" if len(_first_content) > 40 else ""}'
                        f'</div>')
        _cal_html += (
            f'<div class="{_cls}">'
            f'<span class="cal-day">{day}</span>'
            f'<div class="cal-tags">{_tags_html}</div>'
            f'{_snippet}'
            f'</div>'
        )

    _cal_html += "</div>"
    st.html(_cal_html)

    # ── 이슈 작성 폼 ────────────────────────────────────
    st.divider()
    _form_l, _form_r = st.columns([3, 2])

    with _form_l:
        st.markdown("#### 이슈 기록")
        _sel_date11 = st.date_input(
            "날짜",
            value=_today11,
            min_value=_dt11.date(2026, 3, 25),
            max_value=_dt11.date(2026, 12, 31),
            key="cal_date_sel",
        )
        _sel_date_str = str(_sel_date11)
        _existing_entries = _issue_log.get(_sel_date_str, [])

        _TAG_OPTIONS = list(_TAG_COLORS.keys())
        _sel_tags = st.multiselect(
            "태그",
            options=_TAG_OPTIONS,
            default=[],
            key="cal_tags",
        )
        _content_input = st.text_area(
            "내용",
            value="",
            height=120,
            placeholder="ex) 인트로 CTA 버튼명 변경 배포 / 카카오 알림톡 발송 3,200명 / …",
            key="cal_content",
        )
        if st.button("💾 이슈 추가", type="primary", key="cal_save", use_container_width=True):
            if _content_input.strip():
                _log = list(_raw_log)
                _log.append({"date": _sel_date_str, "tags": _sel_tags, "content": _content_input.strip()})
                _log.sort(key=lambda x: x["date"])
                _ok, _msg = save_issue_log(_log, f"log: {_sel_date_str} 이슈 추가")
                if _ok:
                    st.success(f"{_sel_date_str} 저장 완료 — 깃에 반영됨")
                    load_issue_log.clear()
                    st.rerun()
                else:
                    st.error(_msg)
            else:
                st.warning("내용을 입력해주세요.")

        # ── 선택 날짜 기존 이슈 목록 + 개별 삭제 ─────────
        if _existing_entries:
            st.markdown(f"**{_sel_date_str} 등록된 이슈 ({len(_existing_entries)}건)**")
            for _i, _raw_idx in enumerate(
                [i for i, e in enumerate(_raw_log) if e["date"] == _sel_date_str]
            ):
                _e = _raw_log[_raw_idx]
                _tag_pills = " ".join(
                    f'<span style="background:{_TAG_COLORS.get(t,"#94A3B8")};color:#fff;'
                    f'font-size:9px;padding:1px 5px;border-radius:10px;font-weight:600">{t}</span>'
                    for t in _e.get("tags", [])
                )
                _dc1, _dc2 = st.columns([5, 1])
                _dc1.markdown(_tag_pills or "_(태그 없음)_", unsafe_allow_html=True)
                _dc1.markdown(_e.get("content", "").replace("\n", "  \n"))
                if _dc2.button("🗑️", key=f"cal_del_{_raw_idx}", help="이 이슈 삭제"):
                    _log = [e for i, e in enumerate(_raw_log) if i != _raw_idx]
                    _ok, _msg = save_issue_log(_log, f"log: {_sel_date_str} 이슈 삭제")
                    if _ok:
                        st.success("삭제됨")
                        load_issue_log.clear()
                        st.rerun()
                    else:
                        st.error(_msg)

    with _form_r:
        st.markdown("#### 이번 달 이슈")
        _month_prefix = f"{_sel_yr}-{_sel_mo:02d}-"
        _month_issues = [e for e in _raw_log if e["date"].startswith(_month_prefix)]
        _month_issues.sort(key=lambda x: x["date"])
        if not _month_issues:
            st.caption("기록 없음")
        else:
            for _iss in _month_issues:
                _tag_badges = " ".join(
                    f'<span style="background:{_TAG_COLORS.get(t,"#94A3B8")};'
                    f'color:#fff;font-size:9px;padding:1px 5px;border-radius:10px;'
                    f'font-weight:600">{t}</span>'
                    for t in _iss.get("tags", [])
                )
                _iss_date_short = _iss['date'][5:]
                _kor_day = ["월","화","수","목","금","토","일"][_dt11.date.fromisoformat(_iss['date']).weekday()]
                with st.expander(f"{_iss_date_short} ({_kor_day})", expanded=False):
                    st.markdown(_tag_badges, unsafe_allow_html=True)
                    st.markdown(_iss.get("content", "").replace("\n", "  \n"))

# ══════════════════════════════════════════════════════════
# TAB 13: 매출귀속 & LTV
# ══════════════════════════════════════════════════════════
with tab13:
    st.subheader("매출귀속 & LTV")

    _rev = data.get("revenue", [])
    _pa = data.get("placement_attribution", [])
    _costs_all = data.get("costs", [])
    _ad_rev = data.get("ad_revenue", [])
    _ad_meta = data.get("ad_revenue_meta", {"exchange_rate_usd_krw": 1480})

    # ══════════════════════════════════════════════════════════
    # 광고 매출 (애드팝콘) — 기간 필터 적용
    # ══════════════════════════════════════════════════════════
    if isinstance(_ad_rev, list) and len(_ad_rev) > 0 and isinstance(_ad_rev[0], dict):
        _df_ad = pd.DataFrame(_ad_rev)
        if "date" in _df_ad.columns and "cost_usd" in _df_ad.columns:
            _df_ad["date"] = pd.to_datetime(_df_ad["date"])
            _mask_ad = (_df_ad["date"].dt.date >= start) & (_df_ad["date"].dt.date <= end)
            _df_ad = _df_ad[_mask_ad].copy()
        else:
            _df_ad = pd.DataFrame()

        st.markdown(f"### 💰 광고 매출 (애드팝콘) — {start.strftime('%m/%d')}~{end.strftime('%m/%d')}")
        _fx = _ad_meta.get("exchange_rate_usd_krw", 1480)
        st.caption(f"환율 고정 1 USD = {_fx:,}원 · test placement 제외")

        if _df_ad.empty:
            st.info("조회 기간 내 광고 매출 데이터가 없습니다.")
        else:
            _t_usd = float(_df_ad["cost_usd"].sum())
            _t_krw = int(round(_t_usd * _fx))
            _t_imp = int(_df_ad["impression"].sum())
            _t_req = int(_df_ad["request"].sum())
            _t_ecpm = int(round(_t_usd * _fx / _t_imp * 1000)) if _t_imp > 0 else 0
            _days = (_df_ad["date"].dt.date.max() - _df_ad["date"].dt.date.min()).days + 1
            _daily_avg = int(round(_t_krw / _days)) if _days > 0 else 0

            a1, a2, a3, a4, a5 = st.columns(5)
            a1.metric("총 매출", f"{_t_krw:,}원", f"${_t_usd:.2f}",
                help="📌 **애드팝콘 광고 매출 총합 (원화)**\n\n"
                     "유저가 앱 안에서 광고 시청해서 발생한 돈.\n"
                     "· 달러 매출 × 환율(1,480원)로 환산\n"
                     "· test placement 제외 금액\n\n"
                     "**해석:** 크면 클수록 광고 BM 수익 ↑")
            a2.metric("일평균 매출", f"{_daily_avg:,}원", f"{_days}일",
                help="📌 **하루 평균 광고 매출**\n\n"
                     "= 총 매출 ÷ 광고 집행 일수\n\n"
                     "**왜 중요?** 광고 매출은 일마다 들쭉날쭉. "
                     "일평균으로 봐야 '평상시 얼마 버는지' 감 잡힘.\n\n"
                     "**해석:** 꾸준히 오르면 좋음")
            a3.metric("노출 수", f"{_t_imp:,}",
                help="📌 **광고가 실제로 재생된 횟수**\n\n"
                     "유저가 광고를 보기 시작한 순간 카운트.\n"
                     "(AppLovin Bidding 네트워크의 impression)\n\n"
                     "**해석:** 노출 × eCPM = 매출 구조")
            a4.metric("시청 시도", f"{_t_req:,}",
                help="📌 **유저가 '광고 볼게' 버튼을 누른 횟수**\n\n"
                     "실제 노출되기 전 단계 — 광고 요청 (ADPOPCORN request).\n"
                     "이 중 일부는 광고가 안 떠서 노출 안 될 수도 있음.\n\n"
                     "**해석:** 시청 시도 대비 노출 = Fill Rate")
            a5.metric("eCPM", f"{_t_ecpm:,}원",
                help="📌 **노출 1,000번당 매출 (원)**\n\n"
                     "= 총 매출 ÷ 노출 × 1,000\n\n"
                     "**왜 중요?** 광고 단가의 지표. 같은 노출 수여도 eCPM이 높으면 더 많이 벎.\n\n"
                     "**업계 평균:** 리워드 광고 ~5,000~15,000원, 전면 광고 ~10,000~30,000원")

            # 일별 매출 추이
            st.markdown("##### 일별 매출 추이")
            _daily = _df_ad.groupby(_df_ad["date"].dt.date).agg(
                impression=("impression", "sum"),
                request=("request", "sum"),
                cost_usd=("cost_usd", "sum"),
            ).reset_index().rename(columns={"date": "날짜"})
            _daily["매출(원)"] = (_daily["cost_usd"] * _fx).round().astype(int)
            _daily["eCPM(원)"] = _daily.apply(
                lambda r: int(round(r["cost_usd"] * _fx / r["impression"] * 1000)) if r["impression"] > 0 else 0, axis=1)

            import plotly.graph_objects as _goAD
            _fig_ad = _goAD.Figure()
            _fig_ad.add_trace(_goAD.Bar(
                x=_daily["날짜"], y=_daily["매출(원)"],
                name="매출(원)", marker_color="#10B981",
                text=[f"{v:,}" for v in _daily["매출(원)"]], textposition="outside",
            ))
            _fig_ad.add_trace(_goAD.Scatter(
                x=_daily["날짜"], y=_daily["eCPM(원)"],
                name="eCPM(원)", yaxis="y2",
                mode="lines+markers", line=dict(color="#F59E0B", width=2),
            ))
            _fig_ad.update_layout(
                height=340, hovermode="x unified",
                yaxis=dict(title="매출(원)"),
                yaxis2=dict(title="eCPM(원)", overlaying="y", side="right"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(_fig_ad, use_container_width=True, key="tab13_ad_daily")

            # 카테고리 / OS / 포맷별 분해
            _cc1, _cc2, _cc3 = st.columns(3)
            with _cc1:
                st.markdown("##### 카테고리")
                _by_cat = _df_ad.groupby("category")["cost_usd"].sum().reset_index()
                _by_cat["매출(원)"] = (_by_cat["cost_usd"] * _fx).round().astype(int)
                _by_cat["비중"] = (_by_cat["cost_usd"] / _t_usd * 100).round(1).astype(str) + "%"
                _by_cat["category"] = _by_cat["category"].map({"pick": "픽(예측)", "apply": "응모", "cheer": "응원(커뮤니티)"}).fillna(_by_cat["category"])
                _by_cat = _by_cat.sort_values("cost_usd", ascending=False)
                st.dataframe(_by_cat[["category", "매출(원)", "비중"]].rename(columns={"category": "구분"}),
                             use_container_width=True, hide_index=True)
            with _cc2:
                st.markdown("##### OS")
                _by_os = _df_ad.groupby("os")["cost_usd"].sum().reset_index()
                _by_os["매출(원)"] = (_by_os["cost_usd"] * _fx).round().astype(int)
                _by_os["비중"] = (_by_os["cost_usd"] / _t_usd * 100).round(1).astype(str) + "%"
                _by_os = _by_os.sort_values("cost_usd", ascending=False)
                st.dataframe(_by_os[["os", "매출(원)", "비중"]].rename(columns={"os": "OS"}),
                             use_container_width=True, hide_index=True)
            with _cc3:
                st.markdown("##### 포맷")
                _by_fmt = _df_ad.groupby("format")["cost_usd"].sum().reset_index()
                _by_fmt["매출(원)"] = (_by_fmt["cost_usd"] * _fx).round().astype(int)
                _by_fmt["비중"] = (_by_fmt["cost_usd"] / _t_usd * 100).round(1).astype(str) + "%"
                _by_fmt["format"] = _by_fmt["format"].map({"interstitial": "전면비디오", "rv": "리워드비디오"}).fillna(_by_fmt["format"])
                _by_fmt = _by_fmt.sort_values("cost_usd", ascending=False)
                st.dataframe(_by_fmt[["format", "매출(원)", "비중"]].rename(columns={"format": "포맷"}),
                             use_container_width=True, hide_index=True)

            # placement별 누적 테이블 (애드팝콘 원본 컬럼명 유지)
            st.markdown("##### placement별 누적")
            _by_pid = _df_ad.groupby(["placement_id", "placement_name", "os"]).agg(
                request=("request", "sum"),
                impression=("impression", "sum"),
                cost_usd=("cost_usd", "sum"),
            ).reset_index()
            _by_pid["media_name"] = _by_pid["os"].map(lambda x: "폴리볼(iOS)" if x == "iOS" else "폴리볼(Android)")
            _by_pid["thirdparty_name"] = "ADPOPCORN+AppLovin(Bidding)"  # 우리는 두 네트워크 병합 저장
            _by_pid["response"] = "-"
            _by_pid["fill_rate(%)"] = "-"
            _by_pid["impression_rate(%)"] = "-"
            _by_pid["click"] = "-"
            _by_pid["ctr(%)"] = "-"
            _by_pid["RPR"] = "-"
            _by_pid["media_cost(USD)"] = _by_pid["cost_usd"].round(4)
            _by_pid["eCPM(USD)"] = _by_pid.apply(
                lambda r: round(r["cost_usd"] / r["impression"] * 1000, 4) if r["impression"] > 0 else 0, axis=1)
            _by_pid = _by_pid.sort_values("cost_usd", ascending=False)

            _cols_order = ["media_name", "placement_id", "placement_name", "thirdparty_name",
                           "request", "response", "fill_rate(%)", "impression", "impression_rate(%)",
                           "click", "ctr(%)", "media_cost(USD)", "eCPM(USD)", "RPR"]
            st.dataframe(
                _by_pid[_cols_order].style.format({
                    "request": "{:,}",
                    "impression": "{:,}",
                    "media_cost(USD)": "{:,.4f}",
                    "eCPM(USD)": "{:,.4f}",
                }),
                use_container_width=True, hide_index=True
            )
            st.caption("※ 애드팝콘 원본 export 컬럼 · response/fill_rate/impression_rate/click/ctr/RPR은 우리 시스템에 미수집 (데이터 없음)")

            # 유저당 추가 참여 횟수 (최초 전면 vs 추가 RV)
            st.markdown("##### 유저당 평균 추가 참여 횟수")
            _pivot = _df_ad.pivot_table(index=["category", "os"], columns="phase",
                                         values="impression", aggfunc="sum", fill_value=0).reset_index()
            if "initial" in _pivot.columns and "repeat" in _pivot.columns:
                _pivot["유저당 추가 참여"] = _pivot.apply(
                    lambda r: round(r["repeat"] / r["initial"], 2) if r["initial"] > 0 else 0, axis=1)
                _pivot["category"] = _pivot["category"].map({"pick": "픽", "apply": "응모", "cheer": "응원"}).fillna(_pivot["category"])
                _pivot = _pivot.rename(columns={"category": "구분", "initial": "최초(유저)", "repeat": "추가(횟수)"})
                st.dataframe(_pivot[["구분", "os", "최초(유저)", "추가(횟수)", "유저당 추가 참여"]]
                             .rename(columns={"os": "OS"}),
                             use_container_width=True, hide_index=True)
                st.caption("최초 = 전면비디오 노출 (유저 1회) / 추가 = RV 노출 (반복) / 픽 최대 4회, 응모 제한 없음")

        st.divider()

        # ══════════════════════════════════════════════════════════
        # 광고 참여 퍼널 (서버 × Airbridge × 매출 매핑)
        # ══════════════════════════════════════════════════════════
        st.markdown(f"### 🎯 광고 참여 퍼널 ({start} ~ {end})")
        st.caption("서버 예측/응모 → c_ad_entry → pv_ad → pv_ad_reward_completed → 애드팝콘 매출 · **좌측 사이드바 기간 기준**")

        # 조회기간 필터된 데이터 사용
        _fn_start = start.isoformat()
        _fn_end = end.isoformat()

        # 서버 데이터 (예측/응모) — df_daily 이미 조회기간 필터됨
        # 사이드바 기간 필터된 df_daily
        _fn_mask = (df_daily["date"].dt.date >= start) & (df_daily["date"].dt.date <= end)
        _fn_daily = df_daily[_fn_mask]
        _sv_pick_cnt = int(_fn_daily["server_pred_cnt"].sum()) if "server_pred_cnt" in _fn_daily.columns else 0
        _sv_pick_user = int(_fn_daily["server_pred_user"].sum()) if "server_pred_user" in _fn_daily.columns else 0
        _sv_apply_cnt = int(_fn_daily["server_entry_cnt"].sum()) if "server_entry_cnt" in _fn_daily.columns else 0
        _sv_apply_user = int(_fn_daily["server_entry_user"].sum()) if "server_entry_user" in _fn_daily.columns else 0

        # Airbridge 광고 퍼널 이벤트
        _funnel = fetch_ad_funnel(_fn_start, _fn_end)

        # 광고 매출 (조회기간, 이미 계산된 _df_ad 재사용 — 현재 스코프)
        _ad_rev_pick = 0
        _ad_rev_apply = 0
        _ad_rev_cheer = 0
        if not _df_ad.empty:
            _ad_rev_pick = int((_df_ad[_df_ad["category"]=="pick"]["cost_usd"].sum() * _fx).round())
            _ad_rev_apply = int((_df_ad[_df_ad["category"]=="apply"]["cost_usd"].sum() * _fx).round())
            _ad_rev_cheer = int((_df_ad[_df_ad["category"]=="cheer"]["cost_usd"].sum() * _fx).round())

        # events 메트릭은 Airbridge에 없음 (0) — users 메트릭으로 판단
        if not _funnel or (_funnel["total"].get("entry_u", 0) == 0 and _funnel["total"].get("entry", 0) == 0):
            st.info("광고 퍼널 이벤트 데이터가 아직 없습니다. (이벤트 배포일 4/20 이후 자동 수집)")
            with st.expander("📋 매핑 구조 (데이터 들어오면 표시될 것)"):
                st.markdown("""
**퍼널 단계:**

| 단계 | 소스 | 지표 | 의미 |
|------|------|------|------|
| 1. 서비스 이용 | 서버 | 예측 수 / 응모 수 | 전체 이용 (비광고 포함) |
| 2. 광고 진입 | Airbridge `c_ad_entry` | 진입점 클릭 수 | "광고 볼게" 선택 |
| 3. 광고 노출 | Airbridge `pv_ad` | 노출 수 | 광고 실제 재생 시작 |
| 4. 광고 완료 | Airbridge `pv_ad_reward_completed` | 완료 수 | 끝까지 시청 |
| 5. 매출 | 애드팝콘 | 매출 | 완료 단위 매출 |

**Label 매핑:**
- `01` = 최초참여 (전면비디오)
- `02~05` = 추가참여 (리워드비디오)
- 픽 최대 5회 (01~05_up), 응모 제한 없음

**전환율 지표:**
- 광고 참여율 = c_ad_entry / 서버 예측(응모) 수
- 노출 성공률 = pv_ad / c_ad_entry
- 완료율 = pv_ad_reward_completed / pv_ad
""")
        else:
            _bal = _funnel.get("by_action_label", {})
            _tot = _funnel["total"]

            # 카테고리 브레이크다운 — by_action_label이 비면 total 값 나눌 수 없음 → 합계만 표시
            _has_action_split = bool(_bal)

            if not _has_action_split:
                # 전체 합계만 표시
                st.caption("ℹ️ action(pick/apply) × label 분리 데이터 없음 — 전체 합계 기준 표시 (Airbridge 속성 필터 미지원 시)")
                _e_total = _funnel["total"].get("entry_u", 0)
                _a_total = _funnel["total"].get("ad_u", 0)
                _r_total = _funnel["total"].get("reward_u", 0)
                _ep = round(_a_total / _e_total * 100, 1) if _e_total > 0 else 0
                _rp = round(_r_total / _a_total * 100, 1) if _a_total > 0 else 0
                _t_rev = _ad_rev_pick + _ad_rev_apply
                _t_srv_cnt = _sv_pick_cnt + _sv_apply_cnt
                _ent_part = round(_e_total / _t_srv_cnt * 100, 2) if _t_srv_cnt > 0 else 0

                fc1, fc2, fc3, fc4, fc5 = st.columns(5)
                fc1.metric("서버 예측+응모", f"{_t_srv_cnt:,}건", help="서버 예측 + 응모 합계")
                fc2.metric("광고 진입 (유저)", f"{_e_total:,}명",
                           f"참여율 {_ent_part}%" if _ent_part > 0 else None,
                           help="c_ad_entry 유니크 유저 — 광고 시청 선택")
                fc3.metric("광고 노출 (유저)", f"{_a_total:,}명",
                           f"노출률 {_ep}%" if _ep > 0 else None,
                           help="pv_ad 유니크 유저")
                fc4.metric("리워드 완료 (유저)", f"{_r_total:,}명",
                           f"완료율 {_rp}%" if _rp > 0 else None,
                           help="pv_ad_reward_completed 유니크 유저")
                fc5.metric("매출", f"{_t_rev:,}원",
                           help="애드팝콘 총 매출")
                st.caption("※ 유저 수 기준 (Airbridge는 custom event의 이벤트 수 메트릭 미지원, 유니크 유저만 집계)")

                # ── 일별 추이 (사이드바 조회 기간) ──
                _daily_fn = _funnel.get("daily", {})
                if _daily_fn:
                    st.markdown("##### 일별 퍼널 추이")

                    # 날짜별로 정렬, 광고 매출 매칭
                    _ad_rev_by_date = {}
                    if not _df_ad.empty:
                        for _dt, _sub in _df_ad.groupby(_df_ad["date"].dt.date):
                            _ad_rev_by_date[str(_dt)] = int((_sub["cost_usd"].sum() * _fx).round())

                    _daily_rows = []
                    for _d in sorted(_daily_fn.keys()):
                        v = _daily_fn[_d]
                        entry_u = v.get("entry_u", 0)
                        ad_u = v.get("ad_u", 0)
                        reward_u = v.get("reward_u", 0)
                        rev = _ad_rev_by_date.get(_d, 0)
                        _daily_rows.append({
                            "날짜": _d,
                            "광고 진입 (유저)": entry_u,
                            "광고 노출 (유저)": ad_u,
                            "리워드 완료 (유저)": reward_u,
                            "진입→노출": f"{round(ad_u/entry_u*100,1)}%" if entry_u > 0 else "—",
                            "노출→완료": f"{round(reward_u/ad_u*100,1)}%" if ad_u > 0 else "—",
                            "매출(원)": rev,
                            "유저당 매출": f"{int(rev/reward_u):,}원" if reward_u > 0 and rev > 0 else "—",
                        })

                    if _daily_rows:
                        _df_fn = pd.DataFrame(_daily_rows)

                        # 차트 (Plotly)
                        import plotly.graph_objects as _goFN
                        _fig_fn = _goFN.Figure()
                        _fig_fn.add_trace(_goFN.Bar(
                            x=_df_fn["날짜"], y=_df_fn["광고 진입 (유저)"],
                            name="광고 진입", marker_color="#3B82F6",
                            hovertemplate="%{x}<br>진입: %{y:,}명<extra></extra>",
                        ))
                        _fig_fn.add_trace(_goFN.Bar(
                            x=_df_fn["날짜"], y=_df_fn["광고 노출 (유저)"],
                            name="광고 노출", marker_color="#F59E0B",
                            hovertemplate="%{x}<br>노출: %{y:,}명<extra></extra>",
                        ))
                        _fig_fn.add_trace(_goFN.Bar(
                            x=_df_fn["날짜"], y=_df_fn["리워드 완료 (유저)"],
                            name="리워드 완료", marker_color="#10B981",
                            hovertemplate="%{x}<br>완료: %{y:,}명<extra></extra>",
                        ))
                        # 매출 라인 (y2 축)
                        _fig_fn.add_trace(_goFN.Scatter(
                            x=_df_fn["날짜"], y=_df_fn["매출(원)"],
                            name="매출(원)", yaxis="y2",
                            mode="lines+markers", line=dict(color="#EF4444", width=2.5),
                            marker=dict(size=8),
                            hovertemplate="%{x}<br>매출: %{y:,}원<extra></extra>",
                        ))
                        _fig_fn.update_layout(
                            height=360, hovermode="x unified",
                            barmode="group",
                            yaxis=dict(title="유저 수"),
                            yaxis2=dict(title="매출(원)", overlaying="y", side="right", tickformat=","),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                            margin=dict(l=10, r=10, t=30, b=10),
                        )
                        st.plotly_chart(_fig_fn, use_container_width=True, key="tab13_ad_funnel_daily")

                        # 표
                        st.dataframe(
                            _df_fn.style.format({
                                "광고 진입 (유저)": "{:,}",
                                "광고 노출 (유저)": "{:,}",
                                "리워드 완료 (유저)": "{:,}",
                                "매출(원)": "{:,}",
                            }),
                            use_container_width=True, hide_index=True
                        )
            else:
                _ft1, _ft2, _ft3 = st.tabs(["📊 픽 (승부예측)", "🎟️ 응모", "📣 응원 (커뮤니티)"])

                for _tab, _act, _srv_cnt, _srv_user, _cat_rev in [
                    (_ft1, "pick", _sv_pick_cnt, _sv_pick_user, _ad_rev_pick),
                    (_ft2, "apply", _sv_apply_cnt, _sv_apply_user, _ad_rev_apply),
                    (_ft3, "cheer", 0, 0, _ad_rev_cheer),
                ]:
                    with _tab:
                        # 유저 수 — action별 직접 쿼리 (유니크, 중복 없음)
                        _ba = _funnel.get("by_action", {}).get(_act, {})
                        _entry_u = _ba.get("entry_u", 0)
                        _ad_u = _ba.get("ad_u", 0)
                        _reward_u = _ba.get("reward_u", 0)
                        # 이벤트 수 — label별 합산 (중복 OK, 실제 발생 횟수)
                        _entry_e = sum(v.get("entry", 0) for (a, l), v in _bal.items() if a == _act)
                        _ad_e = sum(v.get("ad", 0) for (a, l), v in _bal.items() if a == _act)
                        _reward_e = sum(v.get("reward", 0) for (a, l), v in _bal.items() if a == _act)

                        _srv_entry_pct = round(_entry_u / _srv_cnt * 100, 2) if _srv_cnt > 0 else 0
                        _ad_pct = round(_ad_u / _entry_u * 100, 1) if _entry_u > 0 else 0
                        _reward_pct = round(_reward_u / _ad_u * 100, 1) if _ad_u > 0 else 0
                        _rev_per_reward = int(_cat_rev / _reward_u) if _reward_u > 0 else 0

                        _act_label_map = {"pick": "예측", "apply": "응모", "cheer": "응원"}
                        _act_label = _act_label_map.get(_act, _act)
                        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
                        fc1.metric(f"서버 {_act_label} 수",
                                   f"{_srv_cnt:,}건" if _srv_cnt > 0 else "—",
                                   f"유저 {_srv_user:,}명" if _srv_user > 0 else None,
                                   help="서버 데이터 미연동 (cheer)" if _act == "cheer" else None)
                        fc2.metric("광고 진입 (유저)",
                                   f"{_entry_u:,}명" if _entry_u > 0 else "—",
                                   f"참여율 {_srv_entry_pct}%" if _srv_entry_pct > 0 else None,
                                   help=f"📌 **c_ad_entry — 유니크 유저**\n\n"
                                        f"이벤트 수: {_entry_e:,}회 "
                                        f"(1인당 {round(_entry_e/_entry_u,2) if _entry_u else 0}회)\n\n"
                                        f"**유니크 유저 = action별 직접 쿼리 (중복 없음)**\n"
                                        f"이벤트 수 = 라벨별 합산 (실제 발생 횟수)")
                        fc3.metric("광고 노출 (유저)",
                                   f"{_ad_u:,}명" if _ad_u > 0 else "—",
                                   f"노출률 {_ad_pct}%" if _ad_pct > 0 else None,
                                   help=f"📌 **pv_ad — 유니크 유저**\n\n"
                                        f"이벤트 수: {_ad_e:,}회 "
                                        f"(1인당 {round(_ad_e/_ad_u,2) if _ad_u else 0}회)")
                        fc4.metric("리워드 완료 (유저)",
                                   f"{_reward_u:,}명" if _reward_u > 0 else "—",
                                   f"완료율 {_reward_pct}%" if _reward_pct > 0 else None,
                                   help=f"📌 **pv_ad_reward_completed — 유니크 유저**\n\n"
                                        f"이벤트 수: {_reward_e:,}회 "
                                        f"(1인당 {round(_reward_e/_reward_u,2) if _reward_u else 0}회)\n\n"
                                        f"대시보드 UI 'pv_ad_reward_completed' 숫자와 동일")
                        fc5.metric("매출",
                                   f"{_cat_rev:,}원" if _cat_rev > 0 else "—",
                                   f"유저당 {_rev_per_reward:,}원" if _rev_per_reward > 0 else None,
                                   help="애드팝콘 매출 (해당 카테고리)")

                        # ── 일별 퍼널 표 (좌측 조회기간 × 해당 카테고리) ──
                        _bda = _funnel.get("by_date_action", {})

                        _act_disp = {"pick": "픽", "apply": "응모", "cheer": "응원"}.get(_act, _act)
                        st.markdown(f"##### 일별 퍼널 — {_act_disp} ({start} ~ {end})")

                        # 서버 데이터 컬럼 선택
                        if _act == "pick":
                            _srv_cnt_col = "server_pred_cnt"
                            _srv_u_col = "server_pred_user"
                            _srv_label = "예측"
                        elif _act == "apply":
                            _srv_cnt_col = "server_entry_cnt"
                            _srv_u_col = "server_entry_user"
                            _srv_label = "응모"
                        else:  # cheer
                            _srv_cnt_col = None
                            _srv_u_col = None
                            _srv_label = "응원"

                        # 광고 매출 일별 (해당 카테고리) — 방어적
                        _rev_by_date = {}
                        if isinstance(_df_ad, pd.DataFrame) and not _df_ad.empty and "category" in _df_ad.columns:
                            _cat_df = _df_ad[_df_ad["category"] == _act]
                            if not _cat_df.empty:
                                for _dt, _sub in _cat_df.groupby(_cat_df["date"].dt.date):
                                    _rev_by_date[str(_dt)] = int((_sub["cost_usd"].sum() * _fx).round())

                        # 사이드바 기간 내 모든 날짜를 순회 (데이터 없어도 0으로 표시)
                        import datetime as _dtFN
                        _cur = start
                        _daily_funnel_rows = []
                        while _cur <= end:
                            _d = _cur.isoformat()
                            v = _bda.get((_d, _act), {})
                            e = v.get("entry_u", 0)
                            ad = v.get("ad_u", 0)
                            rw = v.get("reward_u", 0)

                            # 서버 서비스 이용 수 (해당 날짜)
                            _srv_row = df_daily[df_daily["date"].dt.date == _cur]
                            _srv_cnt_val = int(_srv_row[_srv_cnt_col].iloc[0]) if _srv_cnt_col and not _srv_row.empty and _srv_cnt_col in _srv_row.columns else 0
                            _srv_u_val = int(_srv_row[_srv_u_col].iloc[0]) if _srv_u_col and not _srv_row.empty and _srv_u_col in _srv_row.columns else 0
                            _rev_d = _rev_by_date.get(_d, 0)

                            _daily_funnel_rows.append({
                                "날짜": _d,
                                f"서버 {_srv_label}(건)": _srv_cnt_val,
                                f"서버 {_srv_label}(유저)": _srv_u_val,
                                "광고 진입": e,
                                "참여율": f"{round(e/_srv_cnt_val*100,2)}%" if _srv_cnt_val > 0 and e > 0 else "—",
                                "광고 노출": ad,
                                "노출률": f"{round(ad/e*100,1)}%" if e > 0 else "—",
                                "리워드 완료": rw,
                                "완료율": f"{round(rw/ad*100,1)}%" if ad > 0 else "—",
                                "매출(원)": _rev_d,
                                "유저당 매출": f"{int(_rev_d/rw):,}원" if rw > 0 and _rev_d > 0 else "—",
                            })
                            _cur += _dtFN.timedelta(days=1)

                        if _daily_funnel_rows:
                            _df_daily_fn = pd.DataFrame(_daily_funnel_rows)
                            st.dataframe(
                                _df_daily_fn.style.format({
                                    f"서버 {_srv_label}(건)": "{:,}",
                                    f"서버 {_srv_label}(유저)": "{:,}",
                                    "광고 진입": "{:,}",
                                    "광고 노출": "{:,}",
                                    "리워드 완료": "{:,}",
                                    "매출(원)": "{:,}",
                                }),
                                use_container_width=True, hide_index=True
                            )
                            if not any(r["광고 진입"] for r in _daily_funnel_rows):
                                st.caption("ℹ️ 광고 퍼널 이벤트(c_ad_entry 등)는 4/20부터 심어짐. 이전 날짜는 서버 데이터만 표시.")

                        st.markdown("##### Label별 분해")
                        _label_order = ["01", "02", "03", "04", "05_up"]
                        _rows = []
                        for lbl in _label_order:
                            v = _bal.get((_act, lbl))
                            if not v:
                                continue
                            _kind = "최초 (전면)" if lbl == "01" else f"추가 {int(lbl.replace('_up','')) if lbl != '05_up' else '5+'}"
                            _entry_val = v.get("entry_u", 0)
                            _ad_val = v.get("ad_u", 0)
                            _reward_val = v.get("reward_u", 0)
                            _rows.append({
                                "Label": lbl,
                                "단계": _kind,
                                "진입(유저)": _entry_val,
                                "노출(유저)": _ad_val,
                                "완료(유저)": _reward_val,
                                "진입→노출": f"{round(_ad_val/_entry_val*100,1)}%" if _entry_val > 0 else "—",
                                "노출→완료": f"{round(_reward_val/_ad_val*100,1)}%" if _ad_val > 0 else "—",
                            })
                        if _rows:
                            st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

        st.divider()

        # ══════════════════════════════════════════════════════════
        # BEP · LTV · ARPU — 기간 필터 적용
        # ══════════════════════════════════════════════════════════
        st.markdown("### 📉 BEP · LTV · ARPU")

        # 광고 시작일 = ad_revenue 최소 날짜
        import datetime as _dtBEP
        _ad_launch_date = _dtBEP.date(2026, 4, 14)  # 기본값
        if _ad_rev:
            try:
                _ad_launch_date = min(_dtBEP.date.fromisoformat(r["date"]) for r in _ad_rev)
            except Exception:
                pass

        # 기간 필터 radio
        _bep_filter_opts = ["광고 시작 이후", "좌측 조회기간", "전체 누적"]
        _bfc1, _bfc2 = st.columns([2, 3])
        _bep_filter = _bfc1.radio(
            "기간 필터", _bep_filter_opts, horizontal=False, key="bep_filter",
            help="BEP·LTV·ARPU 계산에 포함할 기간 선택"
        )

        if _bep_filter == "광고 시작 이후":
            _bep_start, _bep_end = _ad_launch_date, _dtBEP.date.today()
            _bep_desc = f"광고 시작일 {_ad_launch_date} 부터 현재까지 — BM 수익성 관점"
        elif _bep_filter == "좌측 조회기간":
            _bep_start, _bep_end = start, end
            _bep_desc = f"사이드바 기간 ({start} ~ {end})"
        else:
            _bep_start, _bep_end = _dtBEP.date(2020, 1, 1), _dtBEP.date.today()
            _bep_desc = "모든 기간 누적 (광고 시작 전 마케팅비 포함 → BEP 희석)"

        _bfc2.caption(f"**선택:** {_bep_desc}")

        # 비용 필터 (방어적)
        if isinstance(_costs_all, list) and len(_costs_all) > 0 and isinstance(_costs_all[0], dict):
            _df_cost_full = pd.DataFrame(_costs_all)
        else:
            _df_cost_full = pd.DataFrame()
        if not _df_cost_full.empty and "date" in _df_cost_full.columns:
            _df_cost_full["date"] = pd.to_datetime(_df_cost_full["date"])
            _df_cost_full = _df_cost_full[
                (_df_cost_full["date"].dt.date >= _bep_start) &
                (_df_cost_full["date"].dt.date <= _bep_end)
            ]

        # 광고 매출 필터 (방어적: list of dict 확인)
        if isinstance(_ad_rev, list) and len(_ad_rev) > 0 and isinstance(_ad_rev[0], dict):
            _df_ad_full = pd.DataFrame(_ad_rev)
        else:
            _df_ad_full = pd.DataFrame()
        if not _df_ad_full.empty and "date" in _df_ad_full.columns and "cost_usd" in _df_ad_full.columns:
            _df_ad_full["date"] = pd.to_datetime(_df_ad_full["date"])
            _df_ad_full["krw"] = (_df_ad_full["cost_usd"] * _fx).round().astype(int)
            _df_ad_full = _df_ad_full[
                (_df_ad_full["date"].dt.date >= _bep_start) &
                (_df_ad_full["date"].dt.date <= _bep_end)
            ]

        _total_cost_all = int(_df_cost_full["spend"].sum()) if (not _df_cost_full.empty and "spend" in _df_cost_full.columns) else 0
        _total_ad_all = int(_df_ad_full["krw"].sum()) if (not _df_ad_full.empty and "krw" in _df_ad_full.columns) else 0
        _bep_pct = round(_total_ad_all / _total_cost_all * 100, 1) if _total_cost_all > 0 else 0
        _profit_all = _total_ad_all - _total_cost_all

        # 가입자 수 — 같은 기간 필터 적용
        _mask_bep_daily = (df_daily["date"].dt.date >= _bep_start) & (df_daily["date"].dt.date <= _bep_end)
        _total_signup_all = int(df_daily[_mask_bep_daily]["server_signup"].sum()) if "server_signup" in df_daily.columns else 0
        _cac_all = int(_total_cost_all / _total_signup_all) if _total_signup_all > 0 and _total_cost_all > 0 else 0
        _arpu_all = int(_total_ad_all / _total_signup_all) if _total_signup_all > 0 and _total_ad_all > 0 else 0
        _ltv_cac = round(_arpu_all / _cac_all, 2) if _cac_all > 0 else 0

        # ARPDAU (앱 광고 매출 / 앱 DAU 합산) — 필터 기간과 일치 (cost/revenue와 동일 기준)
        _arpdau = 0
        _dau_app_sum = 0
        _mask_ad_period = _mask_bep_daily  # 필터 기간 그대로 사용
        if "dau_app" in df_daily.columns:
            _dau_app_sum = int(df_daily[_mask_ad_period]["dau_app"].sum())
            _arpdau = round(_total_ad_all / _dau_app_sum, 1) if _dau_app_sum > 0 and _total_ad_all > 0 else 0

        # BEP 카드 (1행)
        b1, b2, b3, b4 = st.columns(4)
        b1.metric(
            "마케팅 비용 합계",
            f"{_total_cost_all:,}원",
            help=f"📌 **선택 기간 동안 쓴 마케팅 비용**\n\n"
                 f"(기간: {_bep_start} ~ {_bep_end})\n\n"
                 f"광고비, 인플루언서 비용, CRM 발송비 등 모든 유입 비용 합산.\n\n"
                 f"**예시:** 인스타 광고, 네이버 블로거 비용, 알림톡 발송비…\n\n"
                 f"**왜 중요?** 매출이 이 금액을 넘어야 '본전' (BEP)"
        )
        b2.metric(
            "광고 매출 합계",
            f"{_total_ad_all:,}원",
            help="📌 **지금까지 번 광고 매출 전부**\n\n"
                 "앱 안에서 유저가 광고 보고 발생한 수익 (애드팝콘 누적).\n\n"
                 "**현재 폴리볼 BM:**\n"
                 "· 픽 적중보상확대 (전면/리워드)\n"
                 "· 응모 추가응모권 (전면/리워드)"
        )
        b3.metric(
            "BEP 진행률",
            f"{_bep_pct}%",
            help=f"📌 **본전(BEP)까지 얼마나 왔는가**\n\n"
                 f"= 광고매출 ÷ 총비용 × 100\n\n"
                 f"· **100% 도달** = BEP 달성 (쓴 돈 회수)\n"
                 f"· **100% 초과** = 흑자\n\n"
                 f"**현재 남은 금액:** {max(0, _total_cost_all - _total_ad_all):,}원\n\n"
                 f"**해석:** 스타트업 초기엔 낮은 게 정상. 꾸준히 오르는 추세가 중요."
        )
        b4.metric(
            "손익",
            f"{_profit_all:,}원",
            delta=f"{'달성' if _profit_all >= 0 else '미달'}",
            help="📌 **지금까지 번 돈 - 쓴 돈**\n\n"
                 "= 총 광고 매출 - 총 누적 비용\n\n"
                 "· **양수(+)** = 흑자\n"
                 "· **음수(-)** = 아직 투자 회수 중 (스타트업 초기엔 정상)\n\n"
                 "**해석:** 손익이 덜 마이너스로 좁혀지는 추세면 좋음"
        )

        # LTV 카드 (2행)
        l1, l2, l3, l4 = st.columns(4)
        l1.metric(
            "CAC (전체)",
            f"{_cac_all:,}원" if _cac_all > 0 else "—",
            help="📌 **고객 1명 데려오는 데 쓴 비용** (Customer Acquisition Cost)\n\n"
                 "= 총 마케팅 비용 ÷ 총 가입자 수\n\n"
                 "**예시:** CAC 5,000원 = 유저 1명 가입시키는 데 5,000원 썼다\n\n"
                 "**왜 중요?** CAC가 LTV보다 작아야 수익 구조가 건전. "
                 "CAC > LTV면 한 명 데려올 때마다 손해보는 셈.\n\n"
                 "**주의:** 여기 CAC는 '전체 마케팅비 ÷ 서버 가입자(웹 기준)'. "
                 "알림톡 CRM 비용 포함 등 단순 계산."
        )
        l2.metric(
            "ARPU (광고매출)",
            f"{_arpu_all:,}원" if _arpu_all > 0 else "—",
            help="📌 **유저 1명이 지금까지 만들어준 평균 광고 매출**\n\n"
                 "(Average Revenue Per User)\n\n"
                 "= 총 광고 매출 ÷ 총 가입자 수\n\n"
                 "**예시:** ARPU 10원 = 유저 1명이 평균 10원어치 광고 수익 기여\n\n"
                 "**한계:** 지금까지 누적값이지 '평생 가치(LTV)' 아님. "
                 "유저가 앞으로도 계속 앱 쓰면 LTV는 ARPU보다 훨씬 커짐.\n\n"
                 "**LTV와 차이:** LTV는 리텐션 곡선 반영 → 아래 '앱 리텐션' 섹션 참조"
        )
        l3.metric(
            "LTV / CAC",
            f"{_ltv_cac}x" if _ltv_cac > 0 else "—",
            delta="건전" if _ltv_cac >= 3 else ("위험" if _ltv_cac > 0 and _ltv_cac < 1 else ("성장 필요" if _ltv_cac > 0 else None)),
            help="📌 **유저 가치 vs 데려오기 비용 비율**\n\n"
                 "= ARPU ÷ CAC\n\n"
                 "**해석 기준 (업계 표준):**\n"
                 "· **3.0x 이상** = 건전 (1원 써서 3원 이상 회수)\n"
                 "· **1.0~3.0x** = 성장 필요\n"
                 "· **1.0x 미만** = 적자 구조 (쓰는 만큼 못 번다)\n\n"
                 "**예시:** 1.5x → 유저 1명 데려오는데 5,000원 쓰고 7,500원 버는 중\n\n"
                 "**주의:** 여기선 누적 ARPU 기반. 진짜 LTV/CAC는 아래 리텐션 섹션에서."
        )
        l4.metric(
            "ARPDAU (앱)",
            f"{_arpdau}원" if _arpdau > 0 else "—",
            help="📌 **앱 일방문자 1명당 광고 매출** (Avg Revenue Per DAU)\n\n"
                 "**표준 공식:**\n"
                 "= Σ(일별 광고 매출) ÷ Σ(일별 앱 DAU)\n"
                 "= 총 매출 ÷ 앱 DAU 합산\n\n"
                 "**참고 — '일별 (매출/DAU)의 평균'과 수학적 동일:**\n"
                 "DAU가 많은 날의 ARPDAU가 더 크게 반영되는 가중평균 방식 (업계 표준)\n\n"
                 "**왜 앱 DAU만?** 광고는 앱에서만 발생 → 웹 DAU 포함하면 ARPDAU가 희석되어 과소평가됨\n\n"
                 "**예시:** ARPDAU 50원 = 앱 켠 사람 1명당 평균 50원 매출 기여\n\n"
                 "**활용:** 앱 DAU × ARPDAU = 일별 예상 광고 매출\n"
                 "  (예: ARPDAU 50원 × 앱 DAU 2,000 = 하루 10만원)\n\n"
                 "**업계 참고:** 리워드 광고 기반 앱 ARPDAU ~10~100원 수준"
        )

        # 3행 — 필터 기간 DAU 컨텍스트 (ARPDAU 산정 기준과 동일)
        _ad_days_count = 0
        _avg_app_dau = 0
        _avg_total_dau = 0
        if "dau_app" in df_daily.columns:
            _period_df = df_daily[_mask_bep_daily]
            _ad_days_count = len(_period_df)
            _avg_app_dau = int(round(_period_df["dau_app"].mean())) if _ad_days_count > 0 else 0
            _avg_total_dau = int(round(_period_df["dau_total"].mean())) if _ad_days_count > 0 and "dau_total" in _period_df.columns else 0

        d1, d2, d3, d4 = st.columns(4)
        d1.metric(
            "앱 DAU (평균)",
            f"{_avg_app_dau:,}명" if _avg_app_dau > 0 else "—",
            help=f"📌 **선택 기간 앱 DAU 일평균**\n\n"
                 f"(기간: {_bep_start} ~ {_bep_end})\n\n"
                 f"= 선택 기간 동안 앱을 켠 유저 수의 일평균\n\n"
                 f"**ARPDAU 해석용 참고 값:**\n"
                 f"· ARPDAU × 앱 DAU 평균 = 하루 평균 매출\n"
                 f"· 앱 DAU가 늘어나면 총 매출 비례 증가 예상"
        )
        d2.metric(
            "앱 DAU (합산)",
            f"{_dau_app_sum:,}" if _dau_app_sum > 0 else "—",
            help="📌 **ARPDAU 계산의 분모**\n\n"
                 "= Σ(일별 앱 DAU)\n"
                 "선택 기간 내 일별 앱 DAU를 모두 더한 값.\n\n"
                 "같은 유저가 3일 오면 3번 카운트됨 (DAU 이벤트 기준)"
        )
        d3.metric(
            "선택 기간 일수",
            f"{_ad_days_count}일" if _ad_days_count > 0 else "—",
            help="📌 **선택된 기간의 일수**\n\n"
                 "ARPDAU · 비용 · 매출 · 가입자 등 모든 지표가 이 기간을 기준으로 계산됨."
        )
        # DAU 대비 앱 비중
        _app_ratio = round(_avg_app_dau / _avg_total_dau * 100, 1) if _avg_total_dau > 0 else 0
        d4.metric(
            "앱 DAU 비중",
            f"{_app_ratio}%" if _app_ratio > 0 else "—",
            help="📌 **전체 DAU(웹+앱) 중 앱 유저 비율**\n\n"
                 "= 앱 DAU 평균 ÷ 전체 DAU 평균 × 100\n\n"
                 "**해석:** 앱 비중이 커질수록 광고 매출 잠재력 ↑\n"
                 "(광고가 앱에서만 나오므로)"
        )

        # ══════════════════════════════════════════════════════════
        # 📱 앱 유저 행동 (리텐션) — 좌측 사이드바 기간 기준
        # ══════════════════════════════════════════════════════════
        st.markdown("### 📱 앱 유저 행동 (리텐션)")

        import datetime as _dtRT
        _today_ret = _dtRT.date.today()
        _yesterday_ret = _today_ret - _dtRT.timedelta(days=1)
        # 좌측 사이드바 기간을 install 조회 범위로 사용 (어제까지 clamp)
        _ret_start = start
        _ret_end = min(end, _yesterday_ret)

        st.caption(
            f"유저가 앱을 얼마나 오래 쓰는지 — **좌측 사이드바 기간({_ret_start} ~ {_ret_end})** install 코호트 기준. "
            f"사이드바 기간을 바꾸면 install 범위도 바뀜."
        )

        # 지연 로딩 — 버튼 클릭 시에만 API 호출 (첫 로드 5~10초 절약)
        _ret_load_key = "_ret_load_triggered"
        _ret_loaded = st.session_state.get(_ret_load_key, False)
        _ret_result = None

        if not _ret_loaded:
            _lb1, _lb2 = st.columns([1, 3])
            if _lb1.button("📊 리텐션 불러오기", key="ret_load_btn", type="primary"):
                st.session_state[_ret_load_key] = True
                st.rerun()
            _lb2.caption("⏱️ Airbridge Retention API는 응답에 5~10초 소요 · 1시간 캐시")
        else:
            _ret_result = fetch_airbridge_retention_report(_ret_start.isoformat(), _ret_end.isoformat())

        if _ret_result is None:
            pass  # 아직 불러오기 버튼 안 누름
        elif _ret_result.get("error"):
            st.error(f"❌ 리텐션 API 실패: {_ret_result.get('error')}")
            with st.expander("🔍 디버그"):
                if _ret_result.get("body"):
                    st.code(_ret_result["body"], language="text")
                st.json(_ret_result.get("payload", {}))
        else:
            _channels = parse_retention_v5(_ret_result.get("raw", {}))

            if not _channels:
                st.info("리텐션 데이터 없음")
            else:
                # ── Install 기간 세부 필터 (조회 범위 내에서 추가 필터) ──
                _ret_filter_opts = ["전체 (조회범위)", "광고 시작 이후", "최근 14일", "최근 7일"]
                _rfc1, _rfc2 = st.columns([2, 3])
                _ret_filter = _rfc1.radio(
                    "Install 기간 세부 필터",
                    _ret_filter_opts,
                    horizontal=False,
                    key="ret_install_filter",
                    help=f"API 조회 범위({_ret_start}~{_ret_end}) 내에서 추가 필터. "
                         f"D+N 관측은 각 코호트별 D+30까지 자동 유지."
                )

                if _ret_filter == "광고 시작 이후":
                    _install_start, _install_end = _ad_launch_date, _ret_end
                elif _ret_filter == "최근 7일":
                    _install_start, _install_end = _ret_end - _dtRT.timedelta(days=6), _ret_end
                elif _ret_filter == "최근 14일":
                    _install_start, _install_end = _ret_end - _dtRT.timedelta(days=13), _ret_end
                else:
                    _install_start, _install_end = _ret_start, _ret_end

                # 조회 범위로 자동 clamp
                _install_start = max(_install_start, _ret_start)
                _install_end = min(_install_end, _ret_end)

                _rfc2.caption(
                    f"**선택된 Install 기간:** {_install_start} ~ {_install_end} "
                    f"({(_install_end - _install_start).days + 1}일) · 각 코호트 D+0~D+30 관측"
                )

                # 채널 선택
                _ch_names = sorted(_channels.keys(), key=lambda k: -_channels[k]["total_size"])
                _ch_options = ["(전체 합계)"] + _ch_names
                _sel_ch = st.selectbox(
                    "채널 선택", _ch_options, key="ret_ch_sel",
                    help="Airbridge Retention — Install(App) 기준 D+N 리턴율"
                )

                # ── 필터 기간의 코호트들만 모아서 재계산 ──
                def _in_range(date_str):
                    try:
                        d = _dtRT.date.fromisoformat(date_str)
                        return _install_start <= d <= _install_end
                    except Exception:
                        return False

                # 채널별 코호트 필터 후 가중평균 재계산
                def _aggregate_cohorts(cohorts_dict):
                    """필터 기간 내 코호트만으로 D+N 가중평균."""
                    filtered = {d: c for d, c in cohorts_dict.items() if _in_range(d)}
                    if not filtered:
                        return 0, [], {}
                    total_size = sum(c["size"] for c in filtered.values())
                    # D+N별 count 합산
                    max_days = 31  # D+0 ~ D+30
                    values = []
                    for day in range(max_days):
                        count_sum = 0
                        valid_cohort_installs = 0  # 해당 D+N 관측 가능한 코호트의 install 합
                        for d_str, c in filtered.items():
                            if day < len(c["values"]):
                                v = c["values"][day]
                                if not v.get("incomplete"):
                                    count_sum += v["count"]
                                    valid_cohort_installs += c["size"]
                        rate = count_sum / valid_cohort_installs if valid_cohort_installs > 0 else 0
                        values.append({"day": day, "count": count_sum, "rate": rate,
                                       "valid_base": valid_cohort_installs})
                    return total_size, values, filtered

                if _sel_ch == "(전체 합계)":
                    # 모든 채널의 코호트 병합
                    merged_cohorts = {}
                    for ch_data in _channels.values():
                        for d_str, c in ch_data["cohorts"].items():
                            if d_str not in merged_cohorts:
                                merged_cohorts[d_str] = {"size": 0, "values": []}
                                for day in range(31):
                                    merged_cohorts[d_str]["values"].append({
                                        "day": day, "count": 0, "rate": 0, "incomplete": False
                                    })
                            merged_cohorts[d_str]["size"] += c["size"]
                            for day, v in enumerate(c["values"]):
                                if day < 31:
                                    merged_cohorts[d_str]["values"][day]["count"] += v["count"]
                                    # incomplete 하나라도 있으면 True 유지
                                    if v.get("incomplete"):
                                        merged_cohorts[d_str]["values"][day]["incomplete"] = True
                    # rate 재계산
                    for d_str, c in merged_cohorts.items():
                        for v in c["values"]:
                            v["rate"] = v["count"] / c["size"] if c["size"] > 0 else 0
                    _sel_size, _sel_values, _sel_cohorts = _aggregate_cohorts(merged_cohorts)
                else:
                    _sel_size, _sel_values, _sel_cohorts = _aggregate_cohorts(_channels[_sel_ch]["cohorts"])

                # 관측 완료된 코호트 개수 표시
                _n_cohorts = len(_sel_cohorts) if _sel_cohorts else 0
                if _sel_size == 0:
                    st.warning(f"선택한 기간({_install_start} ~ {_install_end})에 {_sel_ch} 코호트가 없습니다.")
                    _sel_values = [{"day": i, "count": 0, "rate": 0, "valid_base": 0} for i in range(31)]

                # 요약
                _d1 = _sel_values[1]["rate"] * 100 if len(_sel_values) > 1 else 0
                _d7 = _sel_values[7]["rate"] * 100 if len(_sel_values) > 7 else 0
                _d14 = _sel_values[14]["rate"] * 100 if len(_sel_values) > 14 else 0
                _d30 = _sel_values[30]["rate"] * 100 if len(_sel_values) > 30 else 0

                rm1, rm2, rm3, rm4, rm5 = st.columns(5)
                rm1.metric(
                    "Install 코호트", f"{_sel_size:,}명",
                    delta=f"{_n_cohorts}일치" if _n_cohorts > 0 else None,
                    help=f"📌 **분석 대상 앱 신규 설치 유저 수**\n\n"
                         f"선택한 Install 기간({_install_start}~{_install_end})에 "
                         f"앱을 처음 설치·실행한 유저 수.\n"
                         f"(선택한 채널 기준)\n\n"
                         f"**용어:** '코호트(cohort)' = 같은 시점에 가입한 유저 그룹\n\n"
                         f"**참고:** 각 D+N 지표는 해당 시점이 **지난 코호트만** 반영 "
                         f"(예: D+7은 7일 이상 된 코호트만 집계)"
                )
                rm2.metric(
                    "D+1 리텐션", f"{_d1:.1f}%" if _d1 > 0 else "—",
                    help="📌 **설치 다음날 앱 다시 연 유저 비율**\n\n"
                         "= Install 다음날 앱 활성 유저 ÷ 전체 Install × 100\n\n"
                         "**업계 평균 (게임/콘텐츠 앱):**\n"
                         "· 30%+ = 우수\n"
                         "· 20~30% = 평균\n"
                         "· 20% 미만 = 개선 필요\n\n"
                         "**해석:** 첫 경험이 좋았는지 보여주는 핵심 지표. "
                         "D+1이 낮으면 온보딩 문제일 확률 큼."
                )
                rm3.metric(
                    "D+7 리텐션", f"{_d7:.1f}%" if _d7 > 0 else "—",
                    help="📌 **설치 일주일 후에도 쓰는 유저 비율**\n\n"
                         "= Install 7일차 앱 활성 유저 ÷ 전체 Install × 100\n\n"
                         "**업계 평균 (게임/콘텐츠 앱):**\n"
                         "· 15%+ = 우수\n"
                         "· 10~15% = 평균\n"
                         "· 10% 미만 = 개선 필요\n\n"
                         "**해석:** '습관 형성' 여부. D+7이 유지되면 중장기 리텐션 기대 가능."
                )
                rm4.metric(
                    "D+14 리텐션", f"{_d14:.1f}%" if _d14 > 0 else "—",
                    help="📌 **설치 2주 후에도 쓰는 유저 비율**\n\n"
                         "= Install 14일차 앱 활성 유저 ÷ 전체 Install × 100\n\n"
                         "**해석:** 진성 유저 비율의 근사치. "
                         "이쯤 되면 앱에 정착한 유저들.\n\n"
                         "**주의:** '진행중' 표시는 14일 안 지나서 관측 미완료"
                )
                rm5.metric(
                    "D+30 리텐션", f"{_d30:.1f}%" if _d30 > 0 else "—",
                    help="📌 **설치 한달 후에도 쓰는 유저 비율**\n\n"
                         "= Install 30일차 앱 활성 유저 ÷ 전체 Install × 100\n\n"
                         "**업계 평균:**\n"
                         "· 10%+ = 아주 좋음 (게임/콘텐츠 앱 기준)\n"
                         "· 5~10% = 평균\n"
                         "· 5% 미만 = 심각 (앱 자체 매력도 재검토 필요)\n\n"
                         "**왜 중요?** LTV 계산의 기반. D+30 리텐션 × ARPDAU = 장기 유저가치"
                )

                # 평균 리텐션 곡선 (D+0 ~ D+30)
                import plotly.graph_objects as _goRet
                _xs = [v["day"] for v in _sel_values]
                _ys = [round(v["rate"] * 100, 1) for v in _sel_values]
                _fig_ret = _goRet.Figure()
                _fig_ret.add_trace(_goRet.Scatter(
                    x=_xs, y=_ys, mode="lines+markers",
                    line=dict(color="#3B82F6", width=2.5),
                    marker=dict(size=6),
                    fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
                    name=f"{_sel_ch} 리텐션 (%)",
                    hovertemplate="D+%{x}: %{y:.1f}%<extra></extra>",
                ))
                _fig_ret.update_layout(
                    height=300, hovermode="x unified",
                    xaxis=dict(title="일자 (D+N)", tickmode="linear", dtick=2),
                    yaxis=dict(title="리텐션 (%)", range=[0, max(_ys) * 1.15 if _ys else 100]),
                    margin=dict(l=10, r=10, t=20, b=10),
                    showlegend=False,
                )
                st.plotly_chart(_fig_ret, use_container_width=True, key="tab13_ret_curve")

                # 코호트별 매트릭스 (최근 20일)
                if _sel_cohorts:
                    st.markdown("##### Install 코호트별 매트릭스 (최근 20일)")
                    _offsets = [1, 3, 7, 14, 30]
                    _mat_rows = []
                    _cohort_dates = sorted(_sel_cohorts.keys(), reverse=True)[:20]
                    for cd in _cohort_dates:
                        c = _sel_cohorts[cd]
                        sz = c["size"]
                        if sz <= 0:
                            continue
                        row = {"Install 일": cd, "코호트": sz}
                        for off in _offsets:
                            if off < len(c["values"]):
                                v = c["values"][off]
                                if v.get("incomplete"):
                                    row[f"D+{off}"] = "진행중"
                                elif v["count"] > 0:
                                    row[f"D+{off}"] = f"{v['rate']*100:.1f}% ({v['count']})"
                                else:
                                    row[f"D+{off}"] = "—"
                            else:
                                row[f"D+{off}"] = "—"
                        _mat_rows.append(row)
                    if _mat_rows:
                        st.dataframe(pd.DataFrame(_mat_rows), use_container_width=True, hide_index=True)

                # ══════════════════════════════════════════════════════════
                # LTV 추정 — 분리된 기준:
                #   평균 생존일수: 전체 코호트 (Airbridge 조회 30일 전체) 기준 — 유저 행동 특성
                #   ARPDAU: 광고 집행 이후 (4/14~today) 고정 — 매출 단위의 정확도
                # ══════════════════════════════════════════════════════════

                # [1] 평균 생존일수 — install 필터 무시, 전체 코호트의 total_values 사용
                if _sel_ch == "(전체 합계)":
                    # 모든 채널의 total_values 가중합산
                    _ltv_total_size = sum(c["total_size"] for c in _channels.values())
                    _ltv_max_days = max(len(c["total_values"]) for c in _channels.values())
                    _ltv_total_values = []
                    for day in range(_ltv_max_days):
                        count_sum = sum(c["total_values"][day]["count"] for c in _channels.values()
                                        if day < len(c["total_values"]))
                        rate = count_sum / _ltv_total_size if _ltv_total_size > 0 else 0
                        _ltv_total_values.append({"day": day, "count": count_sum, "rate": rate})
                else:
                    _ltv_total_values = _channels[_sel_ch]["total_values"]
                    _ltv_total_size = _channels[_sel_ch]["total_size"]

                _auc = 0
                _valid_points = [(v["day"], v["rate"]) for v in _ltv_total_values if v["rate"] > 0 or v["day"] == 0]
                if len(_valid_points) >= 2:
                    for i in range(len(_valid_points) - 1):
                        d1, r1 = _valid_points[i]
                        d2, r2 = _valid_points[i + 1]
                        _auc += (r1 + r2) / 2 * (d2 - d1)
                _survival_days = round(_auc, 1)
                _survival_max_day = len(_ltv_total_values) - 1 if _ltv_total_values else 0

                # [2] ARPDAU (LTV 전용) — 광고 집행 이후 고정
                _ltv_ad_start = _ad_launch_date  # 광고 시작일 (앞서 정의됨)
                _ltv_ad_end = _dtRT.date.today()
                _ltv_mask_daily = (df_daily["date"].dt.date >= _ltv_ad_start) & (df_daily["date"].dt.date <= _ltv_ad_end)
                _ltv_dau_sum = int(df_daily[_ltv_mask_daily]["dau_app"].sum()) if "dau_app" in df_daily.columns else 0
                # 전체 ad_revenue 중 광고 시작 이후만 — USD 합산 후 한 번만 반올림 (누적 오차 방지)
                _ltv_rev_usd = 0.0
                if _ad_rev:
                    for _r in _ad_rev:
                        try:
                            _rd = _dtRT.date.fromisoformat(_r["date"])
                            if _ltv_ad_start <= _rd <= _ltv_ad_end:
                                _ltv_rev_usd += float(_r.get("cost_usd", 0) or 0)
                        except Exception:
                            pass
                _ltv_rev_krw = int(round(_ltv_rev_usd * _fx))
                _ltv_arpdau = round(_ltv_rev_krw / _ltv_dau_sum, 1) if _ltv_dau_sum > 0 and _ltv_rev_krw > 0 else 0

                _ltv_est = int(round(_ltv_arpdau * _survival_days)) if _ltv_arpdau > 0 and _survival_days > 0 else 0
                _payback = int(round(_cac_all / _ltv_arpdau)) if _ltv_arpdau > 0 and _cac_all > 0 else None
                _ltv_cac = round(_ltv_est / _cac_all, 2) if _cac_all > 0 and _ltv_est > 0 else 0

                # LTV 섹션은 리텐션 섹션 끝난 후 독립 섹션으로 이동 — 여기선 survival/arpdau만 준비
                _has_ltv_data = True
                _ltv_sel_ch = _sel_ch

        # ══════════════════════════════════════════════════════════
        # 💎 LTV 추정 — 독립 섹션
        #   생존일수 = 전체 코호트 (Airbridge 30일 조회 전체) — 유저 행동 특성
        #   ARPDAU = 광고 집행 이후 (4/14~today) 고정 — 매출 단위
        # ══════════════════════════════════════════════════════════
        if _ret_result is not None and not _ret_result.get("error") and locals().get("_has_ltv_data"):
            st.markdown("### 💎 LTV 추정 (유저 1명의 평생 가치)")
            st.caption(
                f"**계산 기준:** 평균 생존일수 = 전체 코호트 (최근 30일 Airbridge 조회) · "
                f"ARPDAU = 광고 집행 이후 ({_ad_launch_date}~{_dtRT.date.today()}) 고정 · "
                f"선택 채널: {_ltv_sel_ch}"
            )

            lv1, lv2, lv3, lv4 = st.columns(4)
            lv1.metric(
                "평균 생존 일수", f"{_survival_days}일",
                help=f"📌 **유저 1명이 평균 며칠 앱을 쓰는지**\n\n"
                     f"**계산 방식:**\n"
                     f"= 리텐션 곡선 아래 면적 (AUC, 사다리꼴 적분)\n"
                     f"= Σ (D+N 리텐션율 × 1일) for N=0~{_survival_max_day}\n\n"
                     f"**기준:**\n"
                     f"· Install 코호트 = **좌측 사이드바 기간 ({_ret_start} ~ {_ret_end})**\n"
                     f"· 각 코호트별 D+0 ~ D+{_survival_max_day} 리텐션 가중 평균\n"
                     f"· 선택 채널: {_ltv_sel_ch}\n"
                     f"· 광고 시작 여부와 무관 (유저 행동 특성)\n\n"
                     f"**예시 해석:**\n"
                     f"· 생존일수 5일 = 유저가 가입 후 평균 5일 동안 앱 활성\n"
                     f"· (= D+0 100% + D+1 50% + D+2 30% + ... 같은 면적)\n\n"
                     f"**한계:**\n"
                     f"· D+{_survival_max_day}까지만 관측 → 실제 장기 생존일수는 더 길 수 있음\n"
                     f"· 최근 install 코호트는 아직 관측 덜 돼서 수치 낮게 나옴\n"
                     f"· 사이드바 기간을 넓혀 오래된 코호트 포함하면 정확도 ↑"
            )
            lv2.metric(
                "ARPDAU (광고 기간)", f"{_ltv_arpdau:,}원" if _ltv_arpdau > 0 else "—",
                help=f"📌 **광고 집행 이후 앱 DAU 1인당 매출**\n\n"
                     f"= 광고 매출 ÷ 광고 기간 앱 DAU 합산\n\n"
                     f"**기준:** {_ad_launch_date}~{_dtRT.date.today()} (광고 집행 기간 고정)\n\n"
                     f"**왜 상단 ARPDAU와 다를 수 있음?**\n"
                     f"상단은 사용자 선택 필터 기준이라 다를 수 있음. "
                     f"여기는 LTV 계산 정확도를 위해 '광고가 발생한 기간' 고정.\n\n"
                     f"**LTV 공식:** 생존일수 × ARPDAU = LTV"
            )
            lv3.metric(
                f"LTV 추정 (~D+{_survival_max_day})",
                f"{_ltv_est:,}원" if _ltv_est > 0 else "—",
                help="📌 **유저 1명의 평생 예상 매출 가치**\n\n"
                     "= 평균 생존일수 × ARPDAU\n\n"
                     "**예시:** LTV 500원 = 유저 1명이 앱 쓰는 동안 총 500원 벌어다 줄 것으로 기대\n\n"
                     "**활용:** 이 값이 CAC(유입비용)보다 커야 돈 버는 구조\n\n"
                     "**한계:** D+30까지만 관측 → 장기 LTV는 이보다 높을 가능성"
            )
            if _payback:
                _pb_delta = "✅ 회수 가능" if _payback <= 30 else "⚠️ 30일 초과"
                lv4.metric("Payback 추정", f"{_payback:,}일", delta=_pb_delta,
                    help="📌 **유저 유입비용 회수까지 걸리는 일수**\n\n"
                         "= CAC ÷ ARPDAU\n\n"
                         "**예시:** Payback 100일 = 유저 1명 데려오는 5,000원을 100일 광고 매출로 회수\n\n"
                         "**해석 기준:**\n"
                         "· **30일 이내** = 훌륭\n"
                         "· **30~90일** = 평균\n"
                         "· **90일+** = CAC 낮추거나 ARPDAU 높이기 필요"
                )
            else:
                lv4.metric("Payback 추정", "—")

            if _ltv_cac > 0:
                _badge = "✅ 건전 (3x+)" if _ltv_cac >= 3 else ("⚠️ 적자 (<1x)" if _ltv_cac < 1 else "📈 성장 필요")
                st.caption(
                    f"**LTV / CAC = {_ltv_cac}x** — {_badge}  ·  "
                    f"유저 1명 획득 비용 대비 그 유저가 만드는 가치. "
                    f"**3x 이상 = 건전, 1x 미만 = 손해 구조**"
                )

            # ══════════════════════════════════════════════════════════
            # 🎯 광고 기여 유저 LTV (신규)
            #   - 기준 이벤트 토글: pv_ad / pv_ad_reward_completed
            #   - 기간 필터 (섹션 전용)
            #   - Airbridge Retention API에 returnEvents 커스텀 이벤트 주입 →
            #     install 코호트 중 해당 이벤트 발생 유저 리텐션 곡선
            #   - AUC = 광고 유저 평균 생존일수
            #   - 누적 도달률 곡선 → 광고 유저 되기까지 평균 일수
            # ══════════════════════════════════════════════════════════
            st.markdown("---")
            st.markdown("### 🎯 광고 기여 유저 LTV")
            st.caption(
                "**정의:** install 후 광고 이벤트 발생 유저의 리텐션·LTV 분리 측정  ·  "
                "**매출 귀속:** install 기준 (앱 광고 매출은 앱 유입 채널 기여)"
            )

            # ── 1행: 기준 이벤트 + 기간 필터
            _ad_ctrl1, _ad_ctrl2, _ad_ctrl3 = st.columns([2, 1, 1])
            with _ad_ctrl1:
                _ad_ltv_event = st.radio(
                    "기준 이벤트",
                    ["pv_ad_reward_completed (매출 기여)", "pv_ad (광고 노출)"],
                    horizontal=True,
                    key="ad_ltv_event_mode",
                    help="**pv_ad_reward_completed**: 광고를 끝까지 본 유저 = 실제 매출 발생 (정석)\n\n"
                         "**pv_ad**: 광고 유닛 노출된 유저 = 관심은 있었음 (표본 더 넓음)"
                )
            # Airbridge Retention API는 `app_custom_<event>` 포맷 (Actuals와 다름)
            _ad_evt_metric = "app_custom_pv_ad_reward_completed" if "reward" in _ad_ltv_event else "app_custom_pv_ad"
            _ad_ltv_key = "reward_u" if "reward" in _ad_ltv_event else "ad_u"

            # 기간 필터
            # - 최소 선택일: 2026-04-20 (광고 이벤트 SDK 배포일, 그 이전은 데이터 없음)
            # - 최대 선택일: 매출 마지막 날 (ARPDAU 정확도 위해 DAU와 기간 맞춤)
            _AD_EVENT_LAUNCH = _dtRT.date(2026, 4, 21)  # pv_ad/reward 이벤트 수집 안정화 시작일 (4/20은 배포일 일부만 집계)
            _ad_rev_max_date = None
            if _ad_rev:
                try:
                    # 실제 매출 발생한 마지막 날 (cost_usd > 0) — 0원 행은 제외
                    _rev_dates_with_amount = [
                        _dtRT.date.fromisoformat(r["date"])
                        for r in _ad_rev
                        if r.get("date") and (r.get("cost_usd", 0) or 0) > 0
                    ]
                    if _rev_dates_with_amount:
                        _ad_rev_max_date = max(_rev_dates_with_amount)
                except Exception:
                    pass
            _ad_default_end = _ad_rev_max_date if _ad_rev_max_date else _dtRT.date.today()
            if _ad_default_end < _AD_EVENT_LAUNCH:
                _ad_default_end = _AD_EVENT_LAUNCH
            _ad_default_start = max(_ad_launch_date, _AD_EVENT_LAUNCH)
            with _ad_ctrl2:
                _ad_range_start = st.date_input(
                    "시작일",
                    value=_ad_default_start,
                    min_value=_AD_EVENT_LAUNCH,
                    max_value=_ad_default_end,
                    key="ad_ltv_range_start",
                    help="⚠️ **2026-04-21 이전 날짜 선택 불가**\n\n"
                         "`pv_ad` / `pv_ad_reward_completed` 이벤트가 **4/20에 SDK 배포**되었으나 "
                         "배포일은 일부만 집계되어 **4/21부터 안정적 수집**됨."
                )
            with _ad_ctrl3:
                _ad_range_end = st.date_input(
                    "종료일 (매출 최신일 기준)",
                    value=_ad_default_end,
                    min_value=_ad_range_start,
                    max_value=_ad_default_end,
                    key="ad_ltv_range_end",
                    help=f"광고 매출은 {_ad_rev_max_date}까지 수집됨. "
                         f"DAU와 매출 기간을 맞춰야 ARPDAU가 정확함."
                )

            st.caption(
                "📅 **조회 가능 범위: 2026-04-21 ~** (pv_ad 이벤트 안정 수집 시작일)  ·  "
                "4/20은 SDK 배포일 일부만 집계되어 제외"
            )

            # ── Retention API 호출 (광고 유저 리텐션)
            # 리텐션 코호트는 최대한 넓게 (3/26 ~ 오늘) 잡아야 D+N 관측 범위 확보됨.
            # 선택한 기간은 ARPDAU/매출 계산용이고, 리텐션 코호트는 별개 처리.
            _ad_ret_cohort_start = _dtRT.date(2026, 3, 26)
            _ad_ret_cohort_end = _ad_range_end
            _ad_ret_result = None
            _ad_ret_error = None
            try:
                _ad_ret_result = fetch_airbridge_retention_report(
                    _ad_ret_cohort_start.isoformat(),
                    _ad_ret_cohort_end.isoformat(),
                    group_by="channel",
                    return_events=[_ad_evt_metric],
                )
                if _ad_ret_result.get("error"):
                    _ad_ret_error = _ad_ret_result.get("error")
            except Exception as _e:
                _ad_ret_error = str(_e)[:200]

            # ── 광고 퍼널 (DAU_ad, user-days)
            try:
                _ad_ltv_funnel = fetch_ad_funnel(
                    _ad_range_start.isoformat(),
                    _ad_range_end.isoformat(),
                )
            except Exception:
                _ad_ltv_funnel = None

            # ── 광고 매출 (기간)
            _ad_ltv_rev_usd = 0.0
            if _ad_rev:
                for _r in _ad_rev:
                    try:
                        _rd = _dtRT.date.fromisoformat(_r["date"])
                        if _ad_range_start <= _rd <= _ad_range_end:
                            _ad_ltv_rev_usd += float(_r.get("cost_usd", 0) or 0)
                    except Exception:
                        pass
            _ad_ltv_rev = int(round(_ad_ltv_rev_usd * _fx))

            # ── 앱 DAU 합산 (기간)
            _ad_mask_dau = (df_daily["date"].dt.date >= _ad_range_start) & (df_daily["date"].dt.date <= _ad_range_end)
            _ad_dau_sum = int(df_daily[_ad_mask_dau]["dau_app"].sum()) if "dau_app" in df_daily.columns else 0

            if not _ad_ltv_funnel or not _ad_ltv_funnel.get("daily"):
                st.info("광고 퍼널 데이터를 불러올 수 없음. (Airbridge API 토큰 또는 기간 확인)")
            else:
                _daily_u = _ad_ltv_funnel["daily"]
                _user_days = sum(v.get(_ad_ltv_key, 0) for v in _daily_u.values())
                # 이벤트 수 (유저당 평균 시청 횟수 계산용)
                _ad_evt_key = "reward" if "reward" in _ad_ltv_event else "ad"
                _event_total = sum(v.get(_ad_evt_key, 0) for v in _daily_u.values())
                _freq_per_user = round(_event_total / _user_days, 2) if _user_days > 0 else 0
                _period_days = max(1, (_ad_range_end - _ad_range_start).days + 1)
                _avg_daily_ad_u = round(_user_days / _period_days, 1)
                _arpdau_ad = round(_ad_ltv_rev / _user_days, 1) if _user_days > 0 else 0
                _ad_engage_rate = round(_user_days / _ad_dau_sum * 100, 1) if _ad_dau_sum > 0 else 0

                # ── 광고 유저 리텐션 곡선 파싱 → 생존일수 + 첫 도달
                _survival_ad = None
                _max_day_ad = 0
                _ad_total_values = []
                _ad_total_size = 0
                _first_reach_days = None   # 평균 첫 도달 일수
                _reach_d0 = _reach_d1 = _reach_d3 = _reach_d7 = None
                _ret_fallback = False

                if _ad_ret_error:
                    _ret_fallback = True
                elif _ad_ret_result and _ad_ret_result.get("raw"):
                    try:
                        _ad_channels = parse_retention_v5(_ad_ret_result["raw"])
                        # 전체 합계
                        _ad_total_size = sum(c["total_size"] for c in _ad_channels.values())
                        if _ad_total_size > 0:
                            _max_day_ad = max(len(c["total_values"]) for c in _ad_channels.values())
                            for day in range(_max_day_ad):
                                count_sum = sum(
                                    c["total_values"][day]["count"]
                                    for c in _ad_channels.values()
                                    if day < len(c["total_values"])
                                )
                                rate = count_sum / _ad_total_size if _ad_total_size > 0 else 0
                                _ad_total_values.append({"day": day, "count": count_sum, "rate": rate})

                            # AUC = 광고 유저 평균 생존일수
                            _auc_ad = 0
                            _pts = [(v["day"], v["rate"]) for v in _ad_total_values if v["rate"] > 0 or v["day"] == 0]
                            if len(_pts) >= 2:
                                for i in range(len(_pts) - 1):
                                    d1, r1 = _pts[i]; d2, r2 = _pts[i+1]
                                    _auc_ad += (r1 + r2) / 2 * (d2 - d1)
                            _survival_ad = round(_auc_ad, 1)
                            _max_day_ad = len(_ad_total_values) - 1 if _ad_total_values else 0

                            # 누적 도달률 (install한 유저 중 D+N까지 광고 이벤트 발생한 유니크 유저 비율)
                            # = D+0 ~ D+N 까지의 "신규 도달" 합산 근사.
                            # Airbridge 리턴 유형(return_on)은 D+N '당일' 발생이므로 누적 추정은 "max 리턴일"을 근사값으로 사용
                            # 간단화: 일자별 rate를 신규 도달 근사로 사용 (중복 있지만 상대 비교용)
                            if _ad_total_values:
                                _reach_cum = 0
                                _weighted_days = 0
                                _weight_sum = 0
                                _reach_lookup = {0: 0, 1: 0, 3: 0, 7: 0}
                                for v in _ad_total_values:
                                    # 신규 도달 근사: 해당 일 rate (중복 감안 과대평가 가능 — caveat 명시)
                                    _rate = v["rate"]
                                    _weighted_days += v["day"] * _rate
                                    _weight_sum += _rate
                                    _reach_cum += _rate
                                    for k in [0, 1, 3, 7]:
                                        if v["day"] <= k:
                                            _reach_lookup[k] = _reach_cum
                                _reach_d0 = round(_reach_lookup[0] * 100, 1)
                                _reach_d1 = round(_reach_lookup[1] * 100, 1)
                                _reach_d3 = round(_reach_lookup[3] * 100, 1)
                                _reach_d7 = round(_reach_lookup[7] * 100, 1)
                                _first_reach_days = round(_weighted_days / _weight_sum, 1) if _weight_sum > 0 else None
                        else:
                            _ret_fallback = True
                    except Exception as _e:
                        _ad_ret_error = f"parse error: {str(_e)[:150]}"
                        _ret_fallback = True
                else:
                    _ret_fallback = True

                # 생존일수 fallback — 광고 유저 전체 데이터 기준 (필터 무관)
                if _survival_ad is None:
                    _survival_ad = 0  # 데이터 부족 → 0 표시 (전체 유저 값으로 대체하지 않음, 광고 유저 기준 유지)
                    _ret_fallback = True

                # LTV 계산용 ARPDAU — 필터 무관, 광고 유저 전체 기간 기준
                # (LTV는 장기 가치라 특정 기간으로 쪼개면 왜곡됨)
                _ltv_full_start = _AD_EVENT_LAUNCH  # 4/21
                _ltv_full_end = _ad_rev_max_date or _dtRT.date.today()
                try:
                    _funnel_full = fetch_ad_funnel(_ltv_full_start.isoformat(), _ltv_full_end.isoformat())
                    _user_days_full = sum(v.get(_ad_ltv_key, 0) for v in _funnel_full.get("daily", {}).values()) if _funnel_full else 0
                except Exception:
                    _user_days_full = 0
                # 행별 반올림 시 누적 오차 발생 → USD 합산 후 한 번만 반올림
                _rev_full_usd = 0.0
                if _ad_rev:
                    for _r in _ad_rev:
                        try:
                            _rd = _dtRT.date.fromisoformat(_r["date"])
                            if _ltv_full_start <= _rd <= _ltv_full_end:
                                _rev_full_usd += float(_r.get("cost_usd", 0) or 0)
                        except Exception:
                            pass
                _rev_full = int(round(_rev_full_usd * _fx))
                _arpdau_ad_ltv = round(_rev_full / _user_days_full, 1) if _user_days_full > 0 else 0

                # LTV_ad (생존일수 × 전체 기간 ARPDAU — 둘 다 필터 무관)
                _ltv_ad = int(round(_arpdau_ad_ltv * _survival_ad)) if _arpdau_ad_ltv > 0 and _survival_ad > 0 else 0
                _payback_ad = int(round(_cac_all / _arpdau_ad_ltv)) if _arpdau_ad_ltv > 0 and _cac_all > 0 else None

                # ── 메트릭 카드 (1행)
                st.markdown("##### 📊 핵심 지표")
                al1, al2, al3, al4 = st.columns(4)
                al1.metric(
                    "평균 일별 광고 유저", f"{_avg_daily_ad_u:,}명",
                    help=f"= 기간 총 user-days({_user_days:,}) ÷ 일수({_period_days}일)"
                )
                al2.metric(
                    "앱 DAU 중 광고 기여율", f"{_ad_engage_rate}%",
                    help=f"= 광고 user-days({_user_days:,}) ÷ 앱 DAU 합산({_ad_dau_sum:,})\n\n"
                         "100%에 가까울수록 모든 유저가 광고 봄."
                )
                al3.metric(
                    "ARPDAU (광고 유저)", f"{_arpdau_ad:,}원",
                    help=f"= 광고 매출({_ad_ltv_rev:,}) ÷ user-days({_user_days:,})\n\n"
                         f"전체 ARPDAU({_ltv_arpdau}원)와 비교 — 광고 유저 분모이므로 보통 더 큼."
                )
                al4.metric(
                    "평균 생존일수 (광고 유저)",
                    f"{_survival_ad}일" + (" ⚠️" if _ret_fallback else ""),
                    help=(
                        f"📌 **광고 유저 전체 데이터 기준 (필터 무관)**\n\n"
                        f"= install 코호트({_ad_ret_cohort_start}~{_ad_ret_cohort_end}) 중 "
                        f"D+N일차 {_ad_ltv_event.split(' ')[0]} 발생 유저 비율의 AUC\n\n"
                        f"관측 범위: D+0 ~ D+{_max_day_ad}\n\n"
                        f"**왜 필터 무관?** 생존일수는 누적 관측 지표. "
                        f"기간 쪼개면 관측 길이가 짧아져 실제보다 낮게 나옴."
                    ) if not _ret_fallback else (
                        f"⚠️ **데이터 부족** — 이벤트 수집 시작({_AD_EVENT_LAUNCH}) 후 관측 기간 짧아 "
                        f"리텐션 곡선 형성 미흡.\n\n"
                        f"정확한 값은 2주 이상 데이터 쌓인 후 측정 가능.\n\n"
                        f"에러: {_ad_ret_error or '리텐션 곡선 비어있음'}"
                    )
                )

                # ── 메트릭 카드 (2행) — 이벤트수 / 1인당 횟수 / 광고유저 LTV / Payback
                al5, al6, al7, al8 = st.columns(4)
                al5.metric(
                    "총 이벤트 수",
                    f"{_event_total:,}회",
                    help=f"📌 **기간 중 {_ad_ltv_event.split(' ')[0]} 이벤트 총 발생 횟수**\n\n"
                         f"= 일별 이벤트 수 합산\n\n"
                         f"**유저 수 vs 이벤트 수:**\n"
                         f"· 유니크 유저 user-days({_user_days:,}) × {_freq_per_user}회 ≈ 이벤트 수({_event_total:,})\n\n"
                         f"대시보드 UI의 'pv_ad_reward_completed' 숫자와 동일한 값"
                )
                al6.metric(
                    "1인당 평균 시청 횟수",
                    f"{_freq_per_user}회/일",
                    help=f"📌 **광고 기여 유저 1명이 하루에 해당 이벤트 몇 번 발생시키나**\n\n"
                         f"= 이벤트 수({_event_total:,}) ÷ user-days({_user_days:,})\n\n"
                         f"**해석:**\n"
                         f"· 1.0회 = 유저가 하루 1번만 광고 봄\n"
                         f"· 2.0회+ = 헤비 유저 존재\n"
                         f"· 리워드 상한(일별/유저별)이 있다면 그 근처에서 수렴\n\n"
                         f"**활용:** 광고 UX 개선 전후 비교 지표 (유저당 노출 밀도)"
                )
                al7.metric(
                    f"광고 유저 LTV (~D+{_max_day_ad or _survival_max_day})",
                    f"{_ltv_ad:,}원" if _ltv_ad > 0 else "—",
                    help=f"📌 **광고 유저 전체 데이터 기준 (필터 무관)**\n\n"
                         f"= 생존일수({_survival_ad}일) × ARPDAU({_arpdau_ad_ltv}원)\n\n"
                         f"**ARPDAU 계산 기간:** {_ltv_full_start} ~ {_ltv_full_end} (광고 이벤트 전체 데이터)\n"
                         f"· 총 광고 매출: {_rev_full:,}원\n"
                         f"· 총 광고 user-days: {_user_days_full:,}\n\n"
                         f"**필터와 무관한 이유:** LTV는 장기 가치 추정. "
                         f"기간 쪼개면 분자-분모 비율은 같아도 생존일수 관측이 부정확해짐."
                )
                al8.metric(
                    "Payback (광고 유저)",
                    f"{_payback_ad:,}일" if _payback_ad else "—",
                    help=f"📌 **CAC ÷ 광고 유저 ARPDAU (필터 무관)**\n\n"
                         f"= {_cac_all:,}원 ÷ {_arpdau_ad_ltv}원/day"
                )

                # ── 메트릭 카드 (3행) — 첫 도달 관련
                al9, al10, al11, al12 = st.columns(4)
                al9.metric(
                    "평균 첫 광고 도달",
                    f"{_first_reach_days}일" if _first_reach_days is not None else "—",
                    help="📌 **install 후 광고 이벤트 첫 발생까지 평균 일수**\n\n"
                         "= 리텐션 곡선의 일자 가중평균\n\n"
                         "⚠️ 리텐션 API는 '당일 리턴' 기준이라 누적 신규 도달과 정확히 일치하진 않음 (근사치)"
                )
                al10.metric(
                    "D+0 광고 도달률",
                    f"{_reach_d0}%" if _reach_d0 is not None else "—",
                    help="설치 당일 광고 이벤트 발생 비율"
                )
                al11.metric(
                    "D+7 광고 도달률",
                    f"{_reach_d7}%" if _reach_d7 is not None else "—",
                    help="설치 후 7일 이내 누적 광고 도달 비율"
                )
                if _ad_total_values:
                    al12.metric(
                        "코호트 size (install)",
                        f"{_ad_total_size:,}명",
                        help="기간 내 install한 유저 총 수 (리텐션 코호트 분모)"
                    )
                else:
                    al12.metric("코호트 size", "—")

                # ── 광고 리텐션 응답이 전부 0인 경우 안내
                _all_zero = (_ad_total_values and all(v["count"] == 0 for v in _ad_total_values))
                if _all_zero:
                    st.warning(
                        f"⚠️ **광고 유저 리텐션 데이터 0** — Airbridge Retention API가 "
                        f"`app_installs` → `app_custom_events_{_ad_evt_metric.replace('app_custom_events_','')}` 조합에서 "
                        f"모든 채널 D+N=0 반환.\n\n"
                        f"- `any_event` 리턴: 정상 (전체 유저 리텐션은 나옴)\n"
                        f"- custom event 리턴: 0 ← **이벤트 이름 포맷 문제 추정**\n\n"
                        f"**조치:** Airbridge에 Retention API의 custom event returnEvents 포맷 문의 필요. "
                        f"(기존 signup 미추적 문의와 함께 전달 추천)"
                    )

                # ── 도달률 곡선 미니 테이블
                if _reach_d0 is not None and not _all_zero:
                    st.markdown("##### 📈 Install → 광고 도달률 (install 유저 중 D+N일차 누적 비율)")
                    try:
                        import pandas as _pd_reach
                        _reach_rows = [
                            {"기준": "D+0 (당일)", "도달률": f"{_reach_d0}%"},
                            {"기준": "D+1 이내", "도달률": f"{_reach_d1}%"},
                            {"기준": "D+3 이내", "도달률": f"{_reach_d3}%"},
                            {"기준": "D+7 이내", "도달률": f"{_reach_d7}%"},
                        ]
                        st.dataframe(_pd_reach.DataFrame(_reach_rows), hide_index=True, use_container_width=True)
                        st.caption(
                            "_※ 도달률은 리텐션 '당일 리턴' 누적 근사. 정확한 '최초 도달'은 "
                            "Raw Export API 또는 first_event_dimension 지원 시 정밀화 가능._"
                        )
                    except Exception:
                        pass

                # ── 리텐션 곡선 (광고 유저)
                if _ad_total_values:
                    st.markdown("##### 📉 광고 유저 리텐션 곡선 (install 시드 × 광고 이벤트 리턴)")
                    try:
                        import pandas as _pd_rc, plotly.graph_objects as _go_rc
                        _rc_df = _pd_rc.DataFrame([
                            {"D+N": v["day"], "리텐션율(%)": round(v["rate"]*100, 2), "유저수": v["count"]}
                            for v in _ad_total_values
                        ])
                        _fig_rc = _go_rc.Figure()
                        _fig_rc.add_trace(_go_rc.Scatter(
                            x=_rc_df["D+N"], y=_rc_df["리텐션율(%)"],
                            mode="lines+markers", name="광고 유저 리텐션",
                            line=dict(color="#f59e0b", width=2),
                        ))
                        # 전체 유저 리텐션도 겹쳐서 비교
                        try:
                            _fig_rc.add_trace(_go_rc.Scatter(
                                x=[v["day"] for v in _ltv_total_values],
                                y=[round(v["rate"]*100, 2) for v in _ltv_total_values],
                                mode="lines", name="전체 유저 리텐션 (참고)",
                                line=dict(color="#94a3b8", width=1, dash="dot"),
                            ))
                        except Exception:
                            pass
                        _fig_rc.update_layout(
                            height=320, margin=dict(l=30, r=10, t=30, b=30),
                            xaxis_title="D+N", yaxis_title="리턴율 (%)",
                        )
                        st.plotly_chart(_fig_rc, use_container_width=True)
                    except Exception:
                        pass

                # ── 전체 vs 광고 유저 비교표
                st.markdown("##### 📊 전체 유저 vs 광고 기여 유저")
                try:
                    import pandas as _pd_cmp
                    _cmp_rows = [
                        {
                            "지표": "ARPDAU",
                            "전체 유저": f"{_ltv_arpdau:,}원",
                            "광고 기여 유저": f"{_arpdau_ad:,}원",
                            "배수": f"{round(_arpdau_ad / _ltv_arpdau, 2)}x" if _ltv_arpdau > 0 else "—",
                        },
                        {
                            "지표": "평균 생존일수",
                            "전체 유저": f"{_survival_days}일",
                            "광고 기여 유저": f"{_survival_ad}일" + (" *" if _ret_fallback else ""),
                            "배수": f"{round(_survival_ad / _survival_days, 2)}x" if _survival_days > 0 and not _ret_fallback else ("1.0x (fallback)" if _ret_fallback else "—"),
                        },
                        {
                            "지표": "LTV",
                            "전체 유저": f"{_ltv_est:,}원" if _ltv_est > 0 else "—",
                            "광고 기여 유저": f"{_ltv_ad:,}원" if _ltv_ad > 0 else "—",
                            "배수": f"{round(_ltv_ad / _ltv_est, 2)}x" if _ltv_est > 0 and _ltv_ad > 0 else "—",
                        },
                    ]
                    st.dataframe(_pd_cmp.DataFrame(_cmp_rows), hide_index=True, use_container_width=True)
                    if _ret_fallback:
                        st.caption("_* 생존일수 fallback — Airbridge Retention API 이벤트 필터 응답 없음 (원인 확인 필요)_")
                except Exception:
                    pass

                # ── 인사이트
                if _arpdau_ad > 0 and _ltv_arpdau > 0:
                    _arpdau_ratio = _arpdau_ad / _ltv_arpdau
                    if _arpdau_ratio >= 2:
                        _ins = f"✅ 광고 유저 ARPDAU가 전체 대비 **{_arpdau_ratio:.1f}배** — 광고 UX 노출 확대 시 매출 성장 여력 큼"
                    elif _arpdau_ratio >= 1.2:
                        _ins = f"📈 광고 유저 ARPDAU 전체 대비 **{_arpdau_ratio:.1f}배** — 적정"
                    else:
                        _ins = f"⚠️ 광고 유저 ARPDAU와 전체 차이 작음 ({_arpdau_ratio:.1f}배) — 광고 기여율({_ad_engage_rate}%) 재확인"
                    st.info(_ins)

        st.divider()

        # BEP 곡선 — 일별 누적 비용 vs 누적 매출
        if not _df_cost_full.empty and not _df_ad_full.empty:
            st.markdown("##### BEP 곡선 (누적 비용 vs 누적 매출)")

            # 전체 날짜 범위
            _all_dates = pd.date_range(
                min(_df_cost_full["date"].min(), _df_ad_full["date"].min()),
                max(_df_cost_full["date"].max(), _df_ad_full["date"].max()),
                freq="D"
            )

            _cost_daily = _df_cost_full.groupby(_df_cost_full["date"].dt.date)["spend"].sum().reindex(
                [d.date() for d in _all_dates], fill_value=0)
            _rev_daily = _df_ad_full.groupby(_df_ad_full["date"].dt.date)["krw"].sum().reindex(
                [d.date() for d in _all_dates], fill_value=0)

            _cost_cum = _cost_daily.cumsum()
            _rev_cum = _rev_daily.cumsum()

            # BEP 도달 시점 찾기
            _bep_reached = None
            for _dt, _c, _r in zip(_all_dates, _cost_cum, _rev_cum):
                if _r >= _c and _c > 0:
                    _bep_reached = _dt.date()
                    break

            import plotly.graph_objects as _goBEP
            _fig_bep = _goBEP.Figure()
            _fig_bep.add_trace(_goBEP.Scatter(
                x=_all_dates, y=_cost_cum.values, name="누적 비용",
                mode="lines", line=dict(color="#EF4444", width=2.5),
                fill="tozeroy", fillcolor="rgba(239,68,68,0.08)",
            ))
            _fig_bep.add_trace(_goBEP.Scatter(
                x=_all_dates, y=_rev_cum.values, name="누적 광고 매출",
                mode="lines", line=dict(color="#10B981", width=2.5),
                fill="tozeroy", fillcolor="rgba(16,185,129,0.1)",
            ))
            _fig_bep.update_layout(
                height=360, hovermode="x unified",
                yaxis=dict(title="원", tickformat=","),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(_fig_bep, use_container_width=True, key="tab13_bep_curve")

            if _bep_reached:
                st.success(f"✅ BEP 도달: {_bep_reached.strftime('%Y-%m-%d')}")
            else:
                # BEP까지 남은 금액/일수 예측
                _remaining = _total_cost_all - _total_ad_all
                _ad_days = _df_ad_full["date"].nunique()
                _daily_avg_rev = int(_total_ad_all / _ad_days) if _ad_days > 0 else 0
                _days_to_bep = int(_remaining / _daily_avg_rev) if _daily_avg_rev > 0 else None
                if _days_to_bep is not None:
                    st.warning(f"⏳ BEP 미달 — 남은 금액 {_remaining:,}원. 현재 일평균 매출({_daily_avg_rev:,}원) 유지 시 약 **{_days_to_bep}일** 더 필요")
                else:
                    st.warning(f"⏳ BEP 미달 — 남은 금액 {_remaining:,}원. 광고 매출 증가 필요")

        # 카테고리별 ARPU (픽 vs 응모)
        if not _df_ad_full.empty:
            st.markdown("##### 카테고리별 유저당 매출 기여")
            _cat_rev = _df_ad_full.groupby("category")["krw"].sum().reset_index()
            _cat_rev["category"] = _cat_rev["category"].map({"pick": "픽(예측)", "apply": "응모", "cheer": "응원(커뮤니티)"}).fillna(_cat_rev["category"])
            if _total_signup_all > 0:
                _cat_rev["유저당 기여(원)"] = (_cat_rev["krw"] / _total_signup_all).round().astype(int)
                _cat_rev["전체대비"] = (_cat_rev["krw"] / _total_ad_all * 100).round(1).astype(str) + "%"
                _cat_rev = _cat_rev.rename(columns={"category": "구분", "krw": "매출(원)"}).sort_values("매출(원)", ascending=False)
                st.dataframe(_cat_rev, use_container_width=True, hide_index=True)
                st.caption(f"총 가입자 {_total_signup_all:,}명 기준")

        st.divider()

    if not _rev:
        # 광고 매출만 있고 placement_attribution 없음 — 간단히 안내 + 비용 요약만
        if not _ad_rev:
            st.info("광고 매출 데이터가 아직 없습니다. 애드팝콘 raw 데이터를 `dashboard/fetch_ad_revenue.py`로 적재하면 표시됩니다.")

        st.markdown("### 📊 마케팅 비용 (조회기간 합산)")
        _df_cost = pd.DataFrame(_costs_all) if _costs_all else pd.DataFrame()
        if not _df_cost.empty:
            _df_cost["date"] = pd.to_datetime(_df_cost["date"])
            _df_cost = _df_cost[(_df_cost["date"].dt.date >= start) & (_df_cost["date"].dt.date <= end)]

        if _df_cost.empty:
            st.caption("조회기간 내 비용 데이터 없음")
        else:
            _t_cost = int(_df_cost["spend"].sum())
            _cost_ch = _df_cost.groupby("channel")["spend"].sum().sort_values(ascending=False)

            _c1, _c2, _c3 = st.columns(3)
            _c1.metric("총 비용", f"{_t_cost:,}원")
            _c2.metric("활성 채널", f"{len(_cost_ch)}개")
            # 광고 매출과의 간이 ROI (방어적)
            if isinstance(_ad_rev, list) and len(_ad_rev) > 0 and isinstance(_ad_rev[0], dict):
                _ad_df_roi = pd.DataFrame(_ad_rev)
                if "date" in _ad_df_roi.columns and "cost_usd" in _ad_df_roi.columns:
                    _ad_df_roi["date"] = pd.to_datetime(_ad_df_roi["date"])
                    _ad_df_roi = _ad_df_roi[(_ad_df_roi["date"].dt.date >= start) & (_ad_df_roi["date"].dt.date <= end)]
                    _ad_krw = int(round(float(_ad_df_roi["cost_usd"].sum()) * _ad_meta.get("exchange_rate_usd_krw", 1480)))
                    _c3.metric("광고 매출 / 비용", f"{round(_ad_krw/_t_cost*100,1)}%" if _t_cost > 0 else "—",
                               help=f"광고 매출 {_ad_krw:,}원 ÷ 총 비용 {_t_cost:,}원 — 채널귀속 매출 붙기 전 참고용")
                else:
                    _c3.metric("매출", "—")
            else:
                _c3.metric("매출", "—")

            st.markdown("##### 채널별 누적 비용")
            _cost_tbl = _cost_ch.reset_index()
            _cost_tbl.columns = ["채널", "비용"]
            _cost_tbl["비중"] = (_cost_tbl["비용"] / _t_cost * 100).round(1).astype(str) + "%"
            _cost_tbl["비용"] = _cost_tbl["비용"].apply(lambda x: f"{int(x):,}원")
            st.dataframe(_cost_tbl, use_container_width=True, hide_index=True)

        with st.expander("📌 이 탭에서 추가로 보게 될 것 (채널 매출 귀속 붙은 후)", expanded=False):
            st.markdown("""
- **채널별 손익/ROI** — Ad Creative 단위까지 드릴다운
- **BEP 분석** — 비용 집행일 기준 누적 귀속 매출 곡선
- **ROAS & LTV** — 실측 D+14 + 예측 D+30~D+90
- **코호트 BEP 예측** — 오늘 가입한 유저 몇 일 뒤 투자금 회수?

**필요한 것:** c_ad_entry 이벤트 심은 후, 가입 채널 × placement 매핑 데이터
""")

    else:
        # 매출 데이터가 있을 때 — 전체 분석 표시
        df_rev = pd.DataFrame(_rev)
        df_rev["date"] = pd.to_datetime(df_rev["date"])
        df_costs_t = pd.DataFrame(_costs_all)
        if not df_costs_t.empty:
            df_costs_t["date"] = pd.to_datetime(df_costs_t["date"])

        total_rev = int(df_rev["amount"].sum())
        total_cost = int(df_costs_t["spend"].sum()) if not df_costs_t.empty else 0
        profit = total_rev - total_cost
        roi = round(total_rev / total_cost * 100) if total_cost > 0 else 0

        # 1. 전체 요약
        st.markdown("#### 전체 요약")
        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric("총 매출", f"{total_rev:,}원")
        rc2.metric("총 비용", f"{total_cost:,}원")
        rc3.metric("ROI", f"{roi}%")
        rc4.metric("활성 채널", f"{df_costs_t['channel'].nunique() if not df_costs_t.empty else 0}개")

        profit_color = "#10B981" if profit >= 0 else "#EF4444"
        profit_sign = "+" if profit >= 0 else ""
        st.markdown(
            f'<div style="background:linear-gradient(135deg,{profit_color}15,{profit_color}05);'
            f'border:2px solid {profit_color};border-radius:12px;padding:16px 24px;margin:8px 0;text-align:center">'
            f'<span style="font-size:14px;color:#64748B;font-weight:600">매출 - 비용</span><br>'
            f'<span style="font-size:32px;font-weight:800;color:{profit_color}">{profit_sign}{profit:,}원</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.info("매출 귀속 분석, BEP, LTV 상세는 placement_attribution 데이터 입력 후 활성화됩니다.")
