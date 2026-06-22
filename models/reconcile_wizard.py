"""Wizard de rapprochement OCR — pour chaque entité (fournisseur, produits) non
résolue automatiquement après extraction, l'utilisateur choisit : rapprocher un
enregistrement existant, en créer un nouveau (pré-rempli depuis l'OCR), ou ignorer.

Chaque création produit crée aussi le `product.supplierinfo` correspondant, ce qui
alimente les niveaux 1-2 du product_matcher pour les prochains scans. Les lignes
résolues nourrissent l'apprentissage `ocr_techdata.product_mapping` avec la
description OCR d'origine (et non `line.name` muté par Odoo)."""
import logging

from odoo import Command, _, api, fields, models

from ..services import product_matcher

_logger = logging.getLogger(__name__)

_ACTIONS = [
    ("match", "Rapprocher"),
    ("create", "Créer"),
    ("skip", "Ignorer"),
]


class OcrReconcileWizard(models.TransientModel):
    _name = "ocr_techdata.reconcile.wizard"
    _description = "OCR — Rapprochement fournisseur & produits"

    move_id = fields.Many2one("account.move", required=True, ondelete="cascade")
    partner_line_ids = fields.One2many(
        "ocr_techdata.reconcile.partner.line", "wizard_id", string="Fournisseur"
    )
    product_line_ids = fields.One2many(
        "ocr_techdata.reconcile.product.line", "wizard_id", string="Produits"
    )
    has_partner_line = fields.Boolean(compute="_compute_flags")
    has_product_lines = fields.Boolean(compute="_compute_flags")

    @api.depends("partner_line_ids", "product_line_ids")
    def _compute_flags(self):
        for wiz in self:
            wiz.has_partner_line = bool(wiz.partner_line_ids)
            wiz.has_product_lines = bool(wiz.product_line_ids)

    def action_apply(self):
        """Apply user choices: match / create / skip for partner and each product line."""
        self.ensure_one()
        move = self.move_id

        for pline in self.partner_line_ids:
            if pline.action == "match" and pline.partner_id:
                move.partner_id = pline.partner_id
            elif pline.action == "create":
                move.partner_id = pline._create_partner()

        for line in self.product_line_ids:
            if not line.move_line_id:
                continue
            if line.action == "match" and line.product_id:
                line.move_line_id.product_id = line.product_id
            elif line.action == "create":
                line.move_line_id.product_id = line._create_product(move.partner_id)

        # Learning uses the ORIGINAL OCR description, not the (possibly mutated) line name.
        move._learn_product_mappings_from_ocr({
            line.move_line_id.id: line.ocr_description
            for line in self.product_line_ids
            if line.move_line_id and line.move_line_id.product_id
        })
        return {"type": "ir.actions.act_window_close"}


class OcrReconcilePartnerLine(models.TransientModel):
    _name = "ocr_techdata.reconcile.partner.line"
    _description = "OCR — Ligne de rapprochement fournisseur"

    wizard_id = fields.Many2one("ocr_techdata.reconcile.wizard", required=True, ondelete="cascade")
    ocr_vendor_name = fields.Char("Nom OCR", readonly=True)
    ocr_vendor_vat = fields.Char("TVA OCR", readonly=True)
    ocr_email = fields.Char("Email OCR", readonly=True)
    ocr_phone = fields.Char("Téléphone OCR", readonly=True)
    ocr_iban = fields.Char("IBAN OCR", readonly=True)
    ocr_bic = fields.Char("BIC OCR", readonly=True)
    candidate_partner_ids = fields.Many2many("res.partner", string="Candidats")
    action = fields.Selection(_ACTIONS, required=True, default="create")
    partner_id = fields.Many2one("res.partner", string="Fournisseur existant")

    def _create_partner(self):
        """Create a supplier prefilled from OCR data (+ bank account from IBAN/BIC)."""
        self.ensure_one()
        vals = {
            "name": self.ocr_vendor_name or _("Fournisseur OCR"),
            "is_company": True,
            "supplier_rank": 1,
        }
        if self.ocr_vendor_vat:
            vals["vat"] = self.ocr_vendor_vat
        if self.ocr_email:
            vals["email"] = self.ocr_email
        if self.ocr_phone:
            vals["phone"] = self.ocr_phone
        partner = self.env["res.partner"].create(vals)
        if self.ocr_iban:
            self.env["res.partner.bank"].create({
                "acc_number": self.ocr_iban,
                "partner_id": partner.id,
            })
        _logger.info("ocr_techdata: created supplier %r (id=%d) from wizard", partner.name, partner.id)
        return partner


class OcrReconcileProductLine(models.TransientModel):
    _name = "ocr_techdata.reconcile.product.line"
    _description = "OCR — Ligne de rapprochement produit"

    wizard_id = fields.Many2one("ocr_techdata.reconcile.wizard", required=True, ondelete="cascade")
    move_line_id = fields.Many2one("account.move.line", required=True, ondelete="cascade")
    ocr_description = fields.Char("Description OCR", readonly=True)
    ocr_product_ref = fields.Char("Réf. OCR", readonly=True)
    ocr_unit_price = fields.Float("Prix unitaire OCR", readonly=True)
    candidate_product_ids = fields.Many2many("product.product", string="Candidats")
    action = fields.Selection(_ACTIONS, required=True, default="skip")
    product_id = fields.Many2one("product.product", string="Produit existant")

    def _create_product(self, partner):
        """Create a product prefilled from OCR data + a supplierinfo for `partner`,
        so future scans match it at level 1/2 of the matcher."""
        self.ensure_one()
        product = self.env["product.product"].create({
            "name": self.ocr_description or _("Produit OCR"),
            "default_code": self.ocr_product_ref or False,
            "type": "consu",
            "purchase_ok": True,
        })
        if partner:
            self.env["product.supplierinfo"].create({
                "partner_id": partner.id,
                "product_tmpl_id": product.product_tmpl_id.id,
                "product_code": self.ocr_product_ref or False,
                "product_name": self.ocr_description or False,
                "price": self.ocr_unit_price or 0.0,
            })
        _logger.info("ocr_techdata: created product %r (id=%d) from wizard", product.name, product.id)
        return product
