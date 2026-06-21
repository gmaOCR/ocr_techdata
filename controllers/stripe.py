import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class OcrPurchaseController(http.Controller):

    @http.route(
        "/ocr_techdata/purchase/success",
        type="http",
        auth="user",
        methods=["GET"],
        website=False,
    )
    def purchase_success(self, session_id=None, **kwargs):
        """Landing page after successful Stripe payment — shows confirmation and redirects."""
        return request.render("ocr_techdata.purchase_success_page", {
            "session_id": session_id or "",
        })
