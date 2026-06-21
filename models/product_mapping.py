from odoo import fields, models


class OcrProductMapping(models.Model):
    _name = "ocr_techdata.product_mapping"
    _description = "OCR — Mémorisation produit par fournisseur"
    _order = "partner_id, last_validated desc"

    partner_id = fields.Many2one(
        "res.partner",
        string="Fournisseur",
        required=True,
        ondelete="cascade",
        index=True,
    )
    description_key = fields.Char(
        string="Description (normalisée)",
        required=True,
        help="Version normalisée (minuscules) de la description OCR de la ligne.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Produit",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    match_count = fields.Integer(
        string="Nb confirmations",
        default=1,
        help="Nombre de fois où ce mapping a été confirmé par validation d'une facture.",
    )
    last_validated = fields.Datetime(string="Dernière validation")

    _unique_partner_desc_company = models.Constraint(
        "UNIQUE(partner_id, description_key, company_id)",
        "Un seul mapping par fournisseur / description / société.",
    )
