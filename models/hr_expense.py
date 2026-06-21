import logging

from odoo import models

from ..services import field_mapper, mars_client

_logger = logging.getLogger(__name__)


class HrExpense(models.Model):
    _inherit = "hr.expense"

    def _upload_to_extract(self):
        """Override: redirect to mars when Techdata OCR provider is selected."""
        icp = self.env["ir.config_parameter"].sudo()
        provider = icp.get_param("ocr_techdata.provider", "odoo_iap")

        if provider != "paddlevl":
            return super()._upload_to_extract()

        self.ensure_one()

        if not self._get_ocr_option_can_extract():
            return False

        attachment = self.message_main_attachment_id
        if not attachment or self.extract_state not in (
            "no_extract_requested", "not_enough_credit", "error_status"
        ):
            return False

        document_b64 = attachment.datas.decode("utf-8")
        mime_type = attachment.mimetype or "image/jpeg"
        language_hint = self.env.user.lang[:2] if self.env.user.lang else "fr"

        mars_response = mars_client.extract(
            self.env,
            document_b64=document_b64,
            document_type="expense",
            mime_type=mime_type,
            language_hint=language_hint,
        )

        if mars_response is None:
            self.extract_state = "error_status"
            self.extract_status = "error_no_connection"
            _logger.error("ocr_techdata: mars returned no response for hr.expense id=%d", self.id)
            return False

        if mars_response.get("error") == "insufficient_credits":
            self.extract_state = "not_enough_credit"
            _logger.info("ocr_techdata: insufficient credits for hr.expense id=%d", self.id)
            return False

        ocr_results = field_mapper.mars_to_odoo(mars_response, "expense")
        if ocr_results is None:
            self.extract_state = "error_status"
            self.extract_status = "error_internal"
            return False

        self.extract_state = "waiting_validation"
        self.extract_status = "success"
        self.with_company(self.company_id)._fill_document_with_results(ocr_results)
        self._track_set_author(self.env.ref("base.partner_root"))

        raw_text = mars_response.get("full_text_annotation") or mars_response.get("raw_text")
        if raw_text:
            try:
                attachment.sudo().index_content = raw_text
            except Exception:
                pass

        try:
            self.env.user._bus_send("extract_mixin_new_document", {
                "status": self.extract_state,
                "error_message": "",
                "extract_document_uuid": self.extract_document_uuid or "",
            })
        except Exception:
            pass

        _logger.info(
            "ocr_techdata: extracted hr.expense id=%d confidence=%.2f",
            self.id,
            mars_response.get("confidence", 0.0),
        )
        return True
