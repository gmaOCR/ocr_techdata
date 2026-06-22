import logging

from odoo import _, api, fields, models

from ..services import mars_client

_logger = logging.getLogger(__name__)


class OcrPurchaseWizard(models.TransientModel):
    _name = "ocr_techdata.purchase.wizard"
    _description = "OCR token purchase"

    balance = fields.Integer("Current balance", readonly=True)
    pack_id = fields.Selection(selection="_get_pack_selection", string="Pack", required=True)
    pack_info = fields.Char("Pack info", compute="_compute_pack_info")

    @api.model
    def _get_pack_selection(self):
        packs = mars_client.get_packs(self.env)
        return [(p["id"], f"{p['label']} ({p['credits']} scans)") for p in packs]

    @api.depends("pack_id")
    def _compute_pack_info(self):
        packs = mars_client.get_packs(self.env)
        pack_map = {p["id"]: p for p in packs}
        for rec in self:
            p = pack_map.get(rec.pack_id or "")
            rec.pack_info = f"{p['credits']} scans" if p else ""

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        balance = mars_client.get_token_balance(self.env)
        vals["balance"] = balance if balance >= 0 else 0
        packs = mars_client.get_packs(self.env)
        if packs and "pack_id" in fields_list:
            vals.setdefault("pack_id", packs[0]["id"])
        return vals

    def action_checkout(self):
        """Redirect user to Stripe Checkout for the selected pack."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("web.base.url", "")
        # {CHECKOUT_SESSION_ID} is a Stripe template variable — must remain literal
        success_url = f"{base_url}/ocr_techdata/purchase/success?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{base_url}/odoo/settings"

        checkout_url = mars_client.create_checkout_session(
            self.env,
            pack_id=self.pack_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        if not checkout_url:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "message": _("Could not create the payment session. Please try again."),
                    "type": "danger",
                    "sticky": False,
                },
            }
        return {
            "type": "ir.actions.act_url",
            "url": checkout_url,
            "target": "self",
        }
