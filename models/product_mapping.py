from odoo import fields, models


class OcrProductMapping(models.Model):
    _name = "ocr_techdata.product_mapping"
    _description = "OCR — Product memory by supplier"
    _order = "partner_id, last_validated desc"

    partner_id = fields.Many2one(
        "res.partner",
        string="Supplier",
        required=True,
        ondelete="cascade",
        index=True,
    )
    description_key = fields.Char(
        string="Description (normalized)",
        required=True,
        help="Normalized (lowercase) version of the OCR description for the line.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    match_count = fields.Integer(
        string="Confirmations",
        default=1,
        help="Number of times this mapping was confirmed by validating an invoice.",
    )
    last_validated = fields.Datetime(string="Last validated")

    _unique_partner_desc_company = models.Constraint(
        "UNIQUE(partner_id, description_key, company_id)",
        "Only one mapping per supplier / description / company.",
    )
