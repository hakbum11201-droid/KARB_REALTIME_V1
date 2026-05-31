"""
secrets_manager.py
==================
환경변수 또는 .env 파일에서 API 키를 읽는다.
- config.yaml에는 절대 키를 넣지 않는다.
- 키 값은 절대 print/log 하지 않는다.
- paper 모드: 키 없어도 실행 가능.
- tiny_live / live 모드: 키 없으면 즉시 중단.
- UI용: 키 설정 여부(Set/Missing)만 반환한다.
- .env.local 또는 .env 파일에 저장한다. .env.local이 우선이다.
"""

import os

# python-dotenv가 설치된 경우에만 .env 파일을 자동 로드한다.
try:
    from dotenv import load_dotenv, set_key
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _ENV_LOCAL = os.path.join(_BASE, '.env.local')
    _ENV       = os.path.join(_BASE, '.env')
    # .env.local 우선 로드
    if os.path.exists(_ENV_LOCAL):
        load_dotenv(_ENV_LOCAL, override=False)
    elif os.path.exists(_ENV):
        load_dotenv(_ENV, override=False)
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False
    _ENV_LOCAL = ''
    _ENV = ''


def _get_env(key: str) -> str | None:
    """환경변수를 읽되, 값 자체는 절대 출력하지 않는다."""
    return os.environ.get(key)


def _require_env(key: str) -> str:
    """환경변수가 없으면 RuntimeError. 값은 절대 노출하지 않는다."""
    val = _get_env(key)
    if not val or val.startswith("YOUR_"):
        raise RuntimeError(
            f"[SecretsManager] 환경변수 '{key}'가 설정되지 않았습니다. "
            f".env.local 또는 .env 파일을 확인하세요."
        )
    return val


def get_upbit_credentials() -> tuple[str, str]:
    """Upbit API 자격증명 반환. tiny_live / live 모드에서만 호출."""
    access_key = _require_env("UPBIT_ACCESS_KEY")
    secret_key = _require_env("UPBIT_SECRET_KEY")
    return access_key, secret_key


def get_binance_credentials() -> tuple[str, str]:
    """Binance API 자격증명 반환. tiny_live / live 모드에서만 호출."""
    api_key    = _require_env("BINANCE_API_KEY")
    api_secret = _require_env("BINANCE_API_SECRET")
    return api_key, api_secret


def get_fx_api_key() -> str | None:
    """외부 FX API 키. 없으면 None (크로스레이트 폴백 사용)."""
    return _get_env("FX_API_KEY")


def assert_live_credentials_available(mode: str) -> None:
    """
    tiny_live / live 진입 전 반드시 호출.
    키가 없으면 RuntimeError → sys.exit(1).
    paper 모드에서는 아무것도 하지 않는다.
    """
    if mode == "paper":
        return
    if mode in ("tiny_live", "live"):
        get_upbit_credentials()
        get_binance_credentials()
    else:
        raise ValueError(f"[SecretsManager] 알 수 없는 모드: {mode}")


# ──────────────────────────────────────────────────────────────────────────
# UI 전용: 키 설정 여부 조회 / 저장
# 키 값은 절대 반환하지 않는다.
# ──────────────────────────────────────────────────────────────────────────

def get_key_status() -> dict:
    """
    각 키의 Set/Missing 상태만 반환.
    키 값 자체는 절대 포함하지 않는다.
    """
    keys = ["UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY", "BINANCE_API_KEY", "BINANCE_API_SECRET"]
    return {
        k: ("Set" if (_get_env(k) and not _get_env(k).startswith("YOUR_")) else "Missing")
        for k in keys
    }


def save_keys(upbit_access: str, upbit_secret: str,
              binance_api: str, binance_secret: str) -> dict:
    """
    키를 .env.local에 저장한다. .env.local은 Git 제외.
    키 값은 절대 print/log하지 않는다.
    반환: { 'ok': bool, 'message': str }
    """
    if not _DOTENV_AVAILABLE:
        return {'ok': False, 'message': 'python-dotenv가 설치되지 않았습니다.'}

    target = _ENV_LOCAL if _ENV_LOCAL else _ENV
    try:
        from dotenv import set_key as _set_key
        pairs = [
            ("UPBIT_ACCESS_KEY",    upbit_access.strip()),
            ("UPBIT_SECRET_KEY",    upbit_secret.strip()),
            ("BINANCE_API_KEY",     binance_api.strip()),
            ("BINANCE_API_SECRET",  binance_secret.strip()),
        ]
        for k, v in pairs:
            if v:  # 비어있으면 덮어쓰지 않는다
                _set_key(target, k, v)
                os.environ[k] = v   # 현재 프로세스에도 반영

        return {'ok': True, 'message': '.env.local에 저장됨. 엔진 재시작 시 적용됩니다.'}
    except Exception as e:
        return {'ok': False, 'message': f'저장 실패: {type(e).__name__}'}
