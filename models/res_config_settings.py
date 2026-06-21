import logging

from odoo import _, fields, models

from ..services import mars_client
from ..services._constants import MARS_BASE_URL

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ocr_provider = fields.Selection(
        [("odoo_iap", "Odoo IAP (natif)"), ("paddlevl", "Techdata OCR")],
        string="Fournisseur OCR",
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
        string="Seuil confiance élevée",
        default=0.90,
        config_parameter="ocr_techdata.confidence_high",
    )
    ocr_confidence_low = fields.Float(
        string="Seuil confiance minimale",
        default=0.70,
        config_parameter="ocr_techdata.confidence_low",
    )

    def action_test_mars_connection(self):
        import requests
        try:
            resp = requests.get(f"{MARS_BASE_URL}/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                msg = f"Connexion OK — modèle chargé : {data.get('model_loaded')}, version : {data.get('version')}"
                return self._notify(msg, "success")
            return self._notify(f"mars a répondu HTTP {resp.status_code}", "warning")
        except Exception as exc:
            return self._notify(f"Connexion impossible : {exc}", "warning")

    def action_check_balance(self):
        balance = mars_client.get_token_balance(self.env)
        if balance < 0:
            return self._notify("Impossible de récupérer le solde — vérifiez la connexion mars", "warning")
        return self._notify(f"Solde OCR : {balance} token(s)", "success")

    def action_buy_tokens(self):
        """Open the token purchase wizard."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Acheter des tokens OCR"),
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
