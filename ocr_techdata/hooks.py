import logging

import requests

from odoo.tools import config

from .services._constants import MARS_BASE_URL

_logger = logging.getLogger(__name__)

_REGISTER_TIMEOUT = 10


def register_instance(env):
    """Register this Odoo instance with the OCR server and store the returned
    credentials in ir.config_parameter.

    Shared by post_init_hook (automatic, on real installs) and the manual
    "Register" button in settings (action_register_instance). Never raises —
    returns (ok: bool, message: str) so callers can react without try/except.
    """
    icp = env["ir.config_parameter"].sudo()

    # Idempotence: credentials already present → nothing to do.
    if icp.get_param("ocr_techdata.client_id", ""):
        return True, "Instance already registered."

    database_uuid = icp.get_param("database.uuid", "")
    if not database_uuid:
        _logger.warning("ocr_techdata: registration skipped — database.uuid not found")
        return False, "Cannot register: database UUID not found."

    try:
        resp = requests.post(
            f"{MARS_BASE_URL}/auth/register",
            json={"database_uuid": database_uuid},
            timeout=_REGISTER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.ConnectionError:
        _logger.warning("ocr_techdata: registration skipped — cannot reach %s", MARS_BASE_URL)
        return False, "Cannot reach the OCR server. Try again later."
    except requests.exceptions.Timeout:
        _logger.warning("ocr_techdata: registration skipped — request timed out")
        return False, "The OCR server timed out. Try again later."
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        _logger.warning("ocr_techdata: registration rejected by server (HTTP %s)", status)
        return False, f"The OCR server rejected the request (HTTP {status}). Try again later."
    except Exception:
        _logger.exception("ocr_techdata: unexpected error during registration")
        return False, "Unexpected error during registration. See server logs."

    client_id = data.get("client_id", "")
    client_secret = data.get("client_secret", "")
    credits_granted = data.get("credits_granted", 0)
    is_new = data.get("is_new", False)

    if client_id:
        icp.set_param("ocr_techdata.client_id", client_id)
    if client_secret:
        icp.set_param("ocr_techdata.client_secret", client_secret)

    if is_new:
        _logger.info(
            "ocr_techdata: instance registered — client_id=%s credits_granted=%d",
            client_id,
            credits_granted,
        )
        return True, f"Instance registered. {credits_granted} token(s) granted."

    _logger.info("ocr_techdata: instance already registered on server (client_id=%s)", client_id)
    return True, "Instance already registered on the server."


def post_init_hook(env):
    icp = env["ir.config_parameter"].sudo()

    # Installing this module means the user wants to use Techdata OCR → make it
    # the default provider, unless a choice was already persisted (reinstall).
    if not icp.get_param("ocr_techdata.provider", ""):
        icp.set_param("ocr_techdata.provider", "paddlevl")

    # odoo.sh dev/CI builds recreate a fresh DB on every push and install with
    # --test-enable. We do NOT auto-register there: the server would rate-limit
    # (429) and pollute the build log. Admins can register on demand from
    # Settings (action_register_instance). Real production installs (no
    # test_enable) register automatically below.
    if config.get("test_enable"):
        _logger.info("ocr_techdata: post_init_hook — test build detected, skipping auto-registration")
        return

    ok, message = register_instance(env)
    _logger.info("ocr_techdata: post_init_hook — %s", message)
