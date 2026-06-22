import logging

from odoo import _, fields, models

from ..services import mars_client
from ..services._constants import MARS_BASE_URL

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ocr_provider = fields.Selection(
        [("odoo_iap", "Odoo IAP (native)"), ("paddlevl", "Techdata OCR")],
        string="OCR Provider",
        default="odoo_iap",
        config_parameter="ocr_techdata.provider",
    )
    ocr_client_id = fields.Char(
        string="Client ID",
        config_parameter="ocr_techdata.client_id",
        readonly=True,
    )
    ocr_client_secret = fields.Char(
        string="Client Secret",
        config_parameter="ocr_techdata.client_secret",
        groups="base.group_system",
        readonly=True,
    )
    ocr_confidence_high = fields.Float(
        string="High confidence threshold",
        default=0.90,
        config_parameter="ocr_techdata.confidence_high",
    )
    ocr_confidence_low = fields.Float(
        string="Minimum confidence threshold",
        default=0.70,
        config_parameter="ocr_techdata.confidence_low",
    )

    def action_register_instance(self):
        """Manually register this instance with the OCR server and fetch credentials.

        Fallback for instances where automatic registration did not run (odoo.sh
        dev/CI builds) or failed (server unreachable / rate-limited at install time).
        """
        from ..hooks import register_instance
        ok, message = register_instance(self.env)
        notification = self._notify(message, "success" if ok else "warning")
        # On success, reload the settings so the freshly stored Client ID shows up.
        if ok:
            notification["params"]["next"] = {"type": "ir.actions.client", "tag": "reload"}
        return notification

    def action_test_mars_connection(self):
        import requests
        try:
            resp = requests.get(f"{MARS_BASE_URL}/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                msg = f"Connection OK — model loaded: {data.get('model_loaded')}, version: {data.get('version')}"
                return self._notify(msg, "success")
            return self._notify(f"Server responded HTTP {resp.status_code}", "warning")
        except Exception as exc:
            return self._notify(f"Connection failed: {exc}", "warning")

    def action_check_balance(self):
        balance = mars_client.get_token_balance(self.env)
        if balance < 0:
            return self._notify("Cannot retrieve balance — check the OCR server connection", "warning")
        return self._notify(f"OCR balance: {balance} token(s)", "success")

    def action_buy_tokens(self):
        """Open the token purchase wizard."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Buy OCR tokens"),
            "res_model": "ocr_techdata.purchase.wizard",
            "view_mode": "form",
            "target": "new",
        }

    def _notify(self, message: str, level: str = "info") -> dict:
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": message, "type": level, "sticky": False},
        }
