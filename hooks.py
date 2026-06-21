import logging

import requests

from .services._constants import MARS_BASE_URL

_logger = logging.getLogger(__name__)

_REGISTER_TIMEOUT = 10


def post_init_hook(env):
    icp = env["ir.config_parameter"].sudo()

    # Idempotence : si les credentials existent déjà, ne rien faire
    if icp.get_param("ocr_techdata.client_id", ""):
        _logger.info("ocr_techdata: post_init_hook — credentials already present, skipping registration")
        return

    database_uuid = icp.get_param("database.uuid", "")
    if not database_uuid:
        _logger.warning("ocr_techdata: post_init_hook — database.uuid not found, skipping auto-registration")
        return

    try:
        resp = requests.post(
            f"{MARS_BASE_URL}/auth/register",
            json={"database_uuid": database_uuid},
            timeout=_REGISTER_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.ConnectionError:
        _logger.warning(
            "ocr_techdata: post_init_hook — cannot reach %s, registration skipped. "
            "Run action_register_instance from settings when the server is available.",
            MARS_BASE_URL,
        )
        return
    except requests.exceptions.Timeout:
        _logger.warning("ocr_techdata: post_init_hook — registration request timed out, skipping")
        return
    except Exception:
        _logger.exception("ocr_techdata: post_init_hook — unexpected error during registration")
        return

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
    else:
        _logger.info("ocr_techdata: instance already registered on mars (client_id=%s)", client_id)
