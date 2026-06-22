import logging
import time
from typing import Optional

import requests

from ._constants import MARS_BASE_URL

_logger = logging.getLogger(__name__)

_ACCESS_TOKEN: Optional[str] = None
_ACCESS_EXPIRES_AT: float = 0.0
_REFRESH_AHEAD_SECONDS = 60


def _get_icp(env):
    return env["ir.config_parameter"].sudo()  # sudo() required: ICP access needs admin rights


def get_access_token(env) -> Optional[str]:
    """Return a valid access token, refreshing or re-obtaining as needed."""
    global _ACCESS_TOKEN, _ACCESS_EXPIRES_AT

    now = time.time()
    if _ACCESS_TOKEN and now < _ACCESS_EXPIRES_AT - _REFRESH_AHEAD_SECONDS:
        return _ACCESS_TOKEN

    # Try refresh first
    icp = _get_icp(env)
    refresh_token = icp.get_param("ocr_techdata.refresh_token", "")
    if refresh_token:
        result = _refresh_access_token(env, refresh_token)
        if result:
            return result

    # Full re-authentication
    return _obtain_tokens(env)


def _refresh_access_token(env, refresh_token: str) -> Optional[str]:
    global _ACCESS_TOKEN, _ACCESS_EXPIRES_AT

    icp = _get_icp(env)
    try:
        resp = requests.post(
            f"{MARS_BASE_URL}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        _logger.warning("ocr_techdata: refresh token request failed (network error)")
        return None

    if resp.status_code == 401:
        # Token was rotated or mars restarted — clear stale token so next call re-auths directly
        _logger.warning("ocr_techdata: refresh token rejected (401) — clearing from ICP")
        icp.set_param("ocr_techdata.refresh_token", "")
        return None

    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        _logger.warning("ocr_techdata: refresh token request failed (status %s)", resp.status_code)
        return None

    _ACCESS_TOKEN = data.get("access_token")
    _ACCESS_EXPIRES_AT = time.time() + data.get("expires_in", 900)

    # mars rotates the refresh token on every /auth/refresh (the old jti is invalidated
    # server-side). We MUST persist the rotated token, otherwise the next refresh reuses
    # an already-invalidated token -> 401 -> forced full re-auth on every cycle.
    new_refresh = data.get("refresh_token")
    if new_refresh:
        icp.set_param("ocr_techdata.refresh_token", new_refresh)

    return _ACCESS_TOKEN


def _obtain_tokens(env) -> Optional[str]:
    global _ACCESS_TOKEN, _ACCESS_EXPIRES_AT

    icp = _get_icp(env)
    client_id = icp.get_param("ocr_techdata.client_id", "")
    client_secret = icp.get_param("ocr_techdata.client_secret", "")

    if not all([client_id, client_secret]):
        _logger.error("ocr_techdata: credentials not configured — run module installation to register")
        return None

    try:
        resp = requests.post(
            f"{MARS_BASE_URL}/auth/token",
            json={"client_id": client_id, "client_secret": client_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        _logger.error("ocr_techdata: failed to obtain tokens from mars")
        return None

    _ACCESS_TOKEN = data.get("access_token")
    _ACCESS_EXPIRES_AT = time.time() + data.get("expires_in", 900)

    new_refresh = data.get("refresh_token")
    if new_refresh:
        icp.set_param("ocr_techdata.refresh_token", new_refresh)

    return _ACCESS_TOKEN


def clear_token_cache() -> None:
    global _ACCESS_TOKEN, _ACCESS_EXPIRES_AT
    _ACCESS_TOKEN = None
    _ACCESS_EXPIRES_AT = 0.0
