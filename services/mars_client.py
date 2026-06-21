import base64
import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import jwt_auth
from ._constants import MARS_BASE_URL

_logger = logging.getLogger(__name__)

_TIMEOUT = 30
_MAX_RETRIES = 3
_RETRY_STATUSES = (502, 503, 504)
_ADMIN_KEY_PARAM = "ocr_techdata.mars_admin_key"


def _get_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=_MAX_RETRIES,
        backoff_factor=0.5,
        status_forcelist=_RETRY_STATUSES,
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def extract(
    env,
    document_b64: str,
    document_type: str,
    mime_type: str,
    language_hint: str = "fr",
) -> Optional[dict]:
    """Call /ocr/extract on mars and return the JSON result, or None on error."""
    token = jwt_auth.get_access_token(env)
    if not token:
        _logger.error("ocr_techdata: could not obtain access token")
        return None

    session = _get_session()
    try:
        resp = session.post(
            f"{MARS_BASE_URL}/ocr/extract",
            json={
                "document": document_b64,
                "document_type": document_type,
                "mime_type": mime_type,
                "language_hint": language_hint,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        _logger.error("ocr_techdata: request to mars timed out after %ds", _TIMEOUT)
        return None
    except requests.exceptions.ConnectionError:
        _logger.error("ocr_techdata: cannot connect to mars (%s)", mars_url)
        return None
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        if status == 402:
            _logger.warning("ocr_techdata: mars reported insufficient_credits")
            return {"status": "error", "error": "insufficient_credits"}
        _logger.error("ocr_techdata: mars returned HTTP %s", status)
        return None
    except Exception:
        _logger.exception("ocr_techdata: unexpected error calling mars")
        return None


def get_token_balance(env) -> int:
    """Return current token balance for this client_id from mars. Returns -1 on error."""
    token = jwt_auth.get_access_token(env)
    if not token:
        return -1
    try:
        session = _get_session()
        resp = session.get(
            f"{MARS_BASE_URL}/tokens/balance",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("balance", 0)
    except Exception:
        return -1


def get_packs(env) -> list:
    """Return available token packs from mars. Returns [] on error."""
    token = jwt_auth.get_access_token(env)
    if not token:
        return []
    try:
        session = _get_session()
        resp = session.get(
            f"{MARS_BASE_URL}/checkout/packs",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def create_checkout_session(env, pack_id: str, success_url: str, cancel_url: str) -> str | None:
    """Create a Stripe Checkout Session on mars and return the checkout URL."""
    token = jwt_auth.get_access_token(env)
    if not token:
        _logger.error("ocr_techdata: could not obtain access token")
        return None
    try:
        session = _get_session()
        resp = session.post(
            f"{MARS_BASE_URL}/checkout/create",
            json={"pack_id": pack_id, "success_url": success_url, "cancel_url": cancel_url},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("checkout_url")
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        _logger.error("ocr_techdata: checkout/create returned HTTP %s", status)
        return None
    except Exception:
        _logger.exception("ocr_techdata: unexpected error calling checkout/create")
        return None


def credit_on_mars(env, client_id: str, amount: int, note: str = "") -> bool:
    """Relay a token credit to the mars server after a confirmed Stripe payment."""
    icp = env["ir.config_parameter"].sudo()
    admin_key = icp.get_param(_ADMIN_KEY_PARAM, "")
    if not admin_key:
        _logger.error("ocr_techdata: mars_admin_key not configured — token credit NOT relayed")
        return False

    try:
        resp = requests.post(
            f"{MARS_BASE_URL}/tokens/credit",
            json={"client_id": client_id, "amount": amount, "note": note},
            headers={"X-Admin-Key": admin_key},
            timeout=10,
        )
        resp.raise_for_status()
        _logger.info("ocr_techdata: relayed %d credits to mars for client %s", amount, client_id)
        return True
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        _logger.error("ocr_techdata: mars /tokens/credit returned HTTP %s", status)
        return False
    except Exception:
        _logger.exception("ocr_techdata: unexpected error relaying credit to mars")
        return False
