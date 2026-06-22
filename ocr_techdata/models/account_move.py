import logging

from odoo import Command, _, fields, models

from ..services import field_mapper, mars_client, product_matcher

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    # Snapshot des champs OCR utiles au wizard de rapprochement (product_ref, VAT, IBAN,
    # BIC… ne sont pas persistés par Odoo). Clé 'fields' + liste 'lines' (par ligne mars).
    ocr_extraction_data = fields.Json(string="OCR — extraction data", copy=False)
    ocr_has_unmatched = fields.Boolean(
        string="OCR — unmatched entities", compute="_compute_ocr_has_unmatched"
    )

    def _compute_ocr_has_unmatched(self):
        icp = self.env["ir.config_parameter"].sudo()
        is_paddle = icp.get_param("ocr_techdata.provider", "odoo_iap") == "paddlevl"
        for move in self:
            unmatched = False
            if is_paddle and move.extract_state in ("waiting_validation", "to_validate"):
                no_product = any(
                    not line.product_id
                    for line in move.invoice_line_ids.filtered("is_imported")
                )
                unmatched = (not move.partner_id) or no_product
            move.ocr_has_unmatched = unmatched

    def action_reload_ai_data(self):
        """Override: Reload AI Data button — reset fields then re-run our synchronous OCR."""
        icp = self.env["ir.config_parameter"].sudo()
        provider = icp.get_param("ocr_techdata.provider", "odoo_iap")
        if provider != "paddlevl":
            return super().action_reload_ai_data()

        self = self.with_context(skip_is_manually_modified=True, from_ocr=True)  # noqa: PLW0642
        with self._get_edi_creation() as move_form:
            move_form.partner_id = False
            move_form.invoice_date = False
            move_form.invoice_payment_term_id = False
            move_form.invoice_date_due = False
            if move_form.is_purchase_document(include_receipts=True):
                move_form.ref = False
            move_form.payment_reference = False
            move_form.currency_id = move_form.company_currency_id
            move_form.invoice_line_ids = [Command.clear()]

        self.extract_state = "no_extract_requested"
        self._upload_to_extract()

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
        mime_type = attachment.mimetype or "application/pdf"
        document_type = self._get_ocr_document_type()
        language_hint = self.env.user.lang[:2] if self.env.user.lang else "fr"

        mars_response = mars_client.extract(
            self.env,
            document_b64=document_b64,
            document_type=document_type,
            mime_type=mime_type,
            language_hint=language_hint,
        )

        if mars_response is None:
            self.extract_state = "error_status"
            self.extract_status = "error_no_connection"
            _logger.error("ocr_techdata: mars returned no response for account.move id=%d", self.id)
            self._ocr_bus_send_error()
            return False

        if mars_response.get("error") == "insufficient_credits":
            self.extract_state = "not_enough_credit"
            _logger.info("ocr_techdata: insufficient credits for account.move id=%d", self.id)
            return False

        ocr_results = field_mapper.mars_to_odoo(mars_response, document_type)
        if ocr_results is None:
            self.extract_state = "error_status"
            self.extract_status = "error_internal"
            self._ocr_bus_send_error()
            return False

        # Pré-remplir extract_partner_name pour la mémorisation de layout
        supplier_name = (mars_response.get("fields") or {}).get("vendor_name")
        if supplier_name and hasattr(self, "extract_partner_name"):
            self.extract_partner_name = supplier_name

        # Extraire les product_refs avant fill — Odoo ignore les clés inconnues dans invoice_lines
        line_product_refs = [
            il.get("product_ref") for il in ocr_results.get("invoice_lines", [])
        ]

        # Snapshot OCR pour le wizard de rapprochement (champs non persistés par Odoo)
        ocr_fields_snapshot = mars_response.get("fields") or {}
        self.ocr_extraction_data = {
            "fields": {
                k: ocr_fields_snapshot.get(k)
                for k in ("vendor_name", "vendor_vat", "email", "phone", "iban", "bic")
                if ocr_fields_snapshot.get(k)
            },
            "lines": [
                {
                    "description": il.get("description"),
                    "product_ref": il.get("product_ref"),
                    "unit_price": il.get("unit_price"),
                }
                for il in ocr_results.get("invoice_lines", [])
            ],
        }

        self.extract_state = "waiting_validation"
        self.extract_status = "success"
        self.with_company(self.company_id)._fill_document_with_results(ocr_results)
        self._track_set_author(self.env.ref("base.partner_root"))

        # Compléter le matching fournisseur si Odoo n'a pas trouvé de partenaire
        if not self.partner_id:
            ocr_fields = mars_response.get("fields") or {}
            suggested = self._suggest_partner(ocr_fields)
            if suggested:
                self.partner_id = suggested
                _logger.info(
                    "ocr_techdata: partner suggested by VAT/name match → %s (id=%d)",
                    suggested.name,
                    suggested.id,
                )

        # Appliquer le matching produit sur les lignes créées par _fill_document_with_results
        if self.partner_id and any(line_product_refs or [None]):
            self._apply_product_matching(line_product_refs)

        # Pré-remplir extract_prefill_data APRÈS _fill_document_with_results
        # (_fill_document_with_results lit les 'candidates' IAP et écrase le champ)
        if hasattr(self, "extract_prefill_data"):
            fields_data = mars_response.get("fields") or {}
            extra = {k: v for k in ("email", "phone", "website") if (v := fields_data.get(k))}
            if extra:
                self.extract_prefill_data = {**(self.extract_prefill_data or {}), **extra}

        raw_text = mars_response.get("full_text_annotation") or mars_response.get("raw_text")
        if raw_text:
            try:
                attachment.sudo().index_content = raw_text
            except Exception:
                pass

        self._ocr_bus_send_success()

        _logger.info(
            "ocr_techdata: extracted account.move id=%d confidence=%.2f partner_id=%s",
            self.id,
            mars_response.get("confidence", 0.0),
            self.partner_id.name if self.partner_id else "not found",
        )
        return True

    def _suggest_partner(self, ocr_fields: dict):
        """Return a res.partner match based on VAT (priority) or exact name, or None."""
        Partner = self.env["res.partner"]

        vendor_vat = (ocr_fields.get("vendor_vat") or "").strip()
        if vendor_vat:
            partner = Partner.search(
                [("vat", "=", vendor_vat), ("is_company", "=", True), ("active", "=", True)],
                limit=1,
            )
            if partner:
                return partner

        vendor_name = (ocr_fields.get("vendor_name") or "").strip()
        if vendor_name:
            partner = Partner.search(
                [("name", "=ilike", vendor_name), ("is_company", "=", True), ("active", "=", True)],
                limit=1,
            )
            if partner:
                return partner

        return None

    @staticmethod
    def _ocr_zip_guard(imported_lines, ocr_seq, context=""):
        """Positional pairing is only safe when counts match. When Odoo merged invoice
        lines (e.g. company option `extract_single_line_per_tax`), the count diverges
        from the OCR line count and positional zip would mismatch product↔line. In that
        case return [] (skip) and log, rather than assign the wrong products."""
        if len(imported_lines) != len(ocr_seq):
            _logger.warning(
                "ocr_techdata: line count mismatch (%d Odoo vs %d OCR)%s — skipping "
                "positional product matching to avoid wrong assignments",
                len(imported_lines), len(ocr_seq), f" [{context}]" if context else "",
            )
            return []
        return list(zip(imported_lines, ocr_seq))

    def _apply_product_matching(self, line_product_refs: list):
        """Try to assign product_id to OCR-imported lines using product_matcher."""
        imported_lines = self.invoice_line_ids.filtered("is_imported")
        for line, product_ref in self._ocr_zip_guard(imported_lines, line_product_refs, "matching"):
            if line.product_id:
                continue  # already assigned (account_predictive_bills or other)
            product = product_matcher.find_product(
                self.env,
                self.partner_id.id,
                line.name,
                product_ref,
            )
            if product:
                line.write({"product_id": product.id})
                _logger.info(
                    "ocr_techdata: matched line %r → product %r (id=%d)",
                    line.name,
                    product.name,
                    product.id,
                )

    def _post(self, soft=True):
        result = super()._post(soft=soft)
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("ocr_techdata.provider", "odoo_iap") == "paddlevl":
            self._learn_product_mappings()
        return result

    def _learn_product_mappings(self):
        """On validation, memorise (partner, OCR-description) → product_id.

        Uses the ORIGINAL OCR description from the extraction snapshot — NOT line.name,
        which Odoo rewrites when a product is set, so the learned key would otherwise
        diverge from the OCR description seen on the next scan (level-4 miss)."""
        for move in self:
            if not move.partner_id or move.extract_state not in ("waiting_validation", "done"):
                continue
            ocr_lines = (move.ocr_extraction_data or {}).get("lines", [])
            imported = move.invoice_line_ids.filtered("is_imported")
            desc_by_line_id = {
                line.id: (ol.get("description") or line.name)
                for line, ol in self._ocr_zip_guard(imported, ocr_lines, "learning")
            }
            move._learn_product_mappings_from_ocr(desc_by_line_id)

    def _learn_product_mappings_from_ocr(self, desc_by_line_id: dict):
        """Record mappings for imported lines that have a product, keyed by the OCR
        description provided in `desc_by_line_id` (falls back to line.name if absent)."""
        self.ensure_one()
        if not self.partner_id:
            return
        Mapping = self.env["ocr_techdata.product_mapping"]
        for line in self.invoice_line_ids.filtered(lambda l: l.is_imported and l.product_id):
            ocr_desc = desc_by_line_id.get(line.id) or line.name
            key = product_matcher._normalize_description(ocr_desc)
            if not key:
                continue
            existing = Mapping.search([
                ("partner_id", "=", self.partner_id.id),
                ("description_key", "=", key),
                ("company_id", "=", self.company_id.id),
            ], limit=1)
            if existing:
                existing.write({
                    "product_id": line.product_id.id,
                    "match_count": existing.match_count + 1,
                    "last_validated": fields.Datetime.now(),
                })
            else:
                Mapping.create({
                    "partner_id": self.partner_id.id,
                    "description_key": key,
                    "product_id": line.product_id.id,
                    "company_id": self.company_id.id,
                    "last_validated": fields.Datetime.now(),
                })

    def _ocr_partner_candidates(self, ocr_fields: dict, limit: int = 5):
        """Return up to `limit` res.partner candidates for the reconcile wizard."""
        Partner = self.env["res.partner"]
        ids: list[int] = []
        vat = (ocr_fields.get("vendor_vat") or "").strip()
        if vat:
            ids += Partner.search([("vat", "=", vat), ("active", "=", True)], limit=limit).ids
        name = (ocr_fields.get("vendor_name") or "").strip()
        if name:
            for p in Partner.search(
                [("name", "ilike", name), ("is_company", "=", True), ("active", "=", True)],
                limit=limit,
            ):
                if p.id not in ids:
                    ids.append(p.id)
        return Partner.browse(ids[:limit])

    def action_open_ocr_reconcile(self):
        """Open the reconcile wizard for the move's unmatched supplier / product lines."""
        self.ensure_one()
        data = self.ocr_extraction_data or {}
        ocr_fields = data.get("fields", {})
        ocr_lines = data.get("lines", [])

        partner_line_vals = []
        if not self.partner_id:
            candidates = self._ocr_partner_candidates(ocr_fields)
            partner_line_vals.append((0, 0, {
                "ocr_vendor_name": ocr_fields.get("vendor_name"),
                "ocr_vendor_vat": ocr_fields.get("vendor_vat"),
                "ocr_email": ocr_fields.get("email"),
                "ocr_phone": ocr_fields.get("phone"),
                "ocr_iban": ocr_fields.get("iban"),
                "ocr_bic": ocr_fields.get("bic"),
                "candidate_partner_ids": [Command.set(candidates.ids)],
                "action": "match" if candidates else "create",
                "partner_id": candidates[:1].id if candidates else False,
            }))

        product_line_vals = []
        imported = self.invoice_line_ids.filtered("is_imported")
        for line, ocr_line in self._ocr_zip_guard(imported, ocr_lines, "wizard"):
            if line.product_id:
                continue
            ref = ocr_line.get("product_ref") or None
            desc = ocr_line.get("description") or line.name
            cands = product_matcher.find_candidates(self.env, self.partner_id.id, desc, ref)
            product_line_vals.append((0, 0, {
                "move_line_id": line.id,
                "ocr_description": desc,
                "ocr_product_ref": ref,
                "ocr_unit_price": ocr_line.get("unit_price") or 0.0,
                "candidate_product_ids": [Command.set(cands.ids)],
                "action": "match" if cands else "create",
                "product_id": cands[:1].id if cands else False,
            }))

        wizard = self.env["ocr_techdata.reconcile.wizard"].create({
            "move_id": self.id,
            "partner_line_ids": partner_line_vals,
            "product_line_ids": product_line_vals,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("OCR Reconciliation"),
            "res_model": "ocr_techdata.reconcile.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def _get_ocr_document_type(self) -> str:
        if self.is_purchase_document(include_receipts=True):
            return "invoice"
        return "invoice"

    def _ocr_bus_send_success(self):
        try:
            self.env.user._bus_send("extract_mixin_new_document", {
                "status": self.extract_state,
                "error_message": "",
                "extract_document_uuid": self.extract_document_uuid or "",
            })
        except Exception:
            pass

    def _ocr_bus_send_error(self):
        try:
            self.env["bus.bus"]._sendone(
                f"extract.mixin.status#{self.id}",
                "state_change",
                {
                    "status": self.extract_state,
                    "error_message": self.extract_error_message or "OCR error",
                },
            )
        except Exception:
            pass
