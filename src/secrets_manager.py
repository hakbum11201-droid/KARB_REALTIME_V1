"""
secrets_manager.py
===================
환경변수 또는 .env 파일에서 API 키를 읽는다.
- config.yaml에는 절대 키를 넣지 않는다.
- 키 값은 절대 print/log 하지 않는다.
- paper 모드에서는 키 없어도 실행 가능.
- tiny_live / live 모드에서 키가 없으면 즉시 중단.
"""

import os

# python-dotenv가 설치된 경우에만 .env 파일을 자동 로드한다.
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)  # 이미 설정된 환경변수는 덮어쓰지 않는다
except ImportError:
    pass  # python-dotenv 없어도 os.environ에서 직접 읽으면 동작한다


def _get_env(key: str) -> str | None:
    """환경변수를 읽되, 값 자체는 절대 출력하지 않는다."""
    return os.environ.get(key)


def _require_env(key: str) -> str:
    """환경변수가 없으면 RuntimeError를 발생시킨다. 값은 절대 노출하지 않는다."""
    val = _get_env(key)
    if not val or val.startswith("YOUR_"):
        raise RuntimeError(
            f"[SecretsManager] 환경변수 '{key}'가 설정되지 않았습니다. "
            f".env 파일 또는 시스템 환경변수를 확인하세요."
        )
    return val


def get_upbit_credentials() -> tuple[str, str]:
    """
    Upbit API 자격증명을 반환한다.
    tiny_live / live 모드에서만 호출해야 한다.
    """
    access_key = _require_env("UPBIT_ACCESS_KEY")
    secret_key = _require_env("UPBIT_SECRET_KEY")
    return access_key, secret_key


def get_binance_credentials() -> tuple[str, str]:
    """
    Binance API 자격증명을 반환한다.
    tiny_live / live 모드에서만 호출해야 한다.
    """
    api_key = _require_env("BINANCE_API_KEY")
    api_secret = _require_env("BINANCE_API_SECRET")
    return api_key, api_secret


def get_fx_api_key() -> str | None:
    """외부 FX API 키를 반환한다. 없으면 None (크로스레이트 폴백 사용)."""
    return _get_env("FX_API_KEY")


def assert_live_credentials_available(mode: str) -> None:
    """
    tiny_live / live 모드 진입 전 반드시 호출한다.
    키가 없으면 RuntimeError를 발생시켜 즉시 중단한다.
    paper 모드에서는 아무것도 하지 않는다.
    """
    if mode == "paper":
        return
    if mode in ("tiny_live", "live"):
        get_upbit_credentials()
        get_binance_credentials()
    else:
        raise ValueError(f"[SecretsManager] 알 수 없는 모드: {mode}")
