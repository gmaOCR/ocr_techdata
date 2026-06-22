from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger
from odoo.addons.ocr_techdata.services.field_mapper import mars_to_odoo, get_confidence_for_field

_MAPPER_LOGGER = "odoo.addons.ocr_techdata.services.field_mapper"

SAMPLE_MARS_RESPONSE = {
    "status": "success",
    "pages_processed": 1,
    "confidence": 0.94,
    "fields": {
        "vendor_name": "Acme SARL",
        "vendor_vat": "FR12345678901",
        "invoice_number": "FAC-2024-001",
        "date": "2024-01-15",
        "date_due": "2024-02-15",
        "amount_untaxed": 1000.00,
        "amount_tax": 200.00,
        "amount_total": 1200.00,
        "currency": "EUR",
        "payment_reference": "FAC-2024-001",
        "line_items": [
            {
                "description": "Prestation",
                "quantity": 1.0,
                "unit_price": 1000.00,
                "tax_rate": 20.0,
                "total": 1200.00,
            }
        ],
    },
    "confidence_scores": {
        "vendor_name": 0.99,
        "vendor_vat": 0.95,
        "invoice_number": 0.98,
        "date": 0.97,
        "amount_total": 0.99,
    },
    "error": None,
}


class TestFieldMapper(TransactionCase):
    def test_full_invoice_mapping(self):
        result = mars_to_odoo(SAMPLE_MARS_RESPONSE, "invoice")
        self.assertIsNotNone(result)
        self.assertEqual(result["supplier"]["selected_value"]["content"], "Acme SARL")
        self.assertEqual(result["VAT_Number"]["selected_value"]["content"], "FR12345678901")
        self.assertEqual(result["invoice_id"]["selected_value"]["content"], "FAC-2024-001")
        self.assertEqual(result["date"]["selected_value"]["content"], "2024-01-15")
        self.assertEqual(result["due_date"]["selected_value"]["content"], "2024-02-15")
        self.assertAlmostEqual(result["total"]["selected_value"]["content"], 1200.00)
        self.assertAlmostEqual(result["subtotal"]["selected_value"]["content"], 1000.00)
        self.assertAlmostEqual(result["total_tax_amount"]["selected_value"]["content"], 200.00)
        self.assertEqual(result["currency"]["selected_value"]["content"], "EUR")
        self.assertEqual(result["payment_ref"]["selected_value"]["content"], "FAC-2024-001")

    def test_line_items_mapped(self):
        result = mars_to_odoo(SAMPLE_MARS_RESPONSE, "invoice")
        # invoice_lines must be a plain list (not wrapped) — Odoo reads it directly
        lines = result["invoice_lines"]
        self.assertIsInstance(lines, list)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["description"], "Prestation")
        self.assertAlmostEqual(line["quantity"], 1.0)
        self.assertEqual(line["taxes"], [20.0])
        self.assertNotIn("taxes_type", line)

    @mute_logger(_MAPPER_LOGGER)
    def test_error_status_returns_none(self):
        bad = {"status": "error", "error": "unreadable_document"}
        result = mars_to_odoo(bad, "invoice")
        self.assertIsNone(result)

    def test_missing_optional_fields_omitted(self):
        minimal = {
            "status": "success",
            "fields": {"amount_total": 100.0, "currency": "EUR"},
            "confidence_scores": {},
        }
        result = mars_to_odoo(minimal, "invoice")
        self.assertIsNotNone(result)
        self.assertNotIn("supplier", result)
        self.assertNotIn("VAT_Number", result)
        self.assertNotIn("invoice_lines", result)

    def test_empty_line_items_not_mapped(self):
        no_lines = dict(SAMPLE_MARS_RESPONSE)
        no_lines["fields"] = dict(SAMPLE_MARS_RESPONSE["fields"])
        no_lines["fields"]["line_items"] = []
        result = mars_to_odoo(no_lines, "invoice")
        self.assertNotIn("invoice_lines", result)

    def test_confidence_for_known_field(self):
        score = get_confidence_for_field(SAMPLE_MARS_RESPONSE, "supplier")
        self.assertAlmostEqual(score, 0.99)

    def test_confidence_for_unknown_field_returns_zero(self):
        score = get_confidence_for_field(SAMPLE_MARS_RESPONSE, "unknown_field")
        self.assertEqual(score, 0.0)

    def test_amounts_coerced_to_float(self):
        resp = dict(SAMPLE_MARS_RESPONSE)
        resp["fields"] = dict(SAMPLE_MARS_RESPONSE["fields"])
        resp["fields"]["amount_total"] = "1200"
        result = mars_to_odoo(resp, "invoice")
        self.assertIsInstance(result["total"]["selected_value"]["content"], float)

    def test_expense_type_mapping(self):
        expense_resp = {
            "status": "success",
            "fields": {
                "vendor_name": "Shell",
                "date": "2024-03-01",
                "amount_total": 80.00,
                "currency": "EUR",
                "line_items": [],
            },
            "confidence_scores": {"amount_total": 0.91},
        }
        result = mars_to_odoo(expense_resp, "expense")
        self.assertEqual(result["supplier"]["selected_value"]["content"], "Shell")
        self.assertAlmostEqual(result["total"]["selected_value"]["content"], 80.00)

    # ── Régression #8 : signe des avoirs (montants positifs côté Odoo refund) ──
    def test_credit_note_amounts_are_positive(self):
        resp = {
            "status": "success",
            "fields": {
                "document_type": "credit_note",
                "amount_total": -3025.0,
                "amount_untaxed": -2500.0,
                "amount_tax": -525.0,
                "line_items": [{
                    "description": "Annulation",
                    "quantity": -1.0,
                    "unit_price": 2500.0,
                    "total": -3025.0,
                    "tax_rate": 21.0,
                }],
            },
            "confidence_scores": {},
        }
        result = mars_to_odoo(resp, "invoice")
        self.assertEqual(result["type"], "refund")
        self.assertAlmostEqual(result["total"]["selected_value"]["content"], 3025.0)
        self.assertAlmostEqual(result["subtotal"]["selected_value"]["content"], 2500.0)
        self.assertAlmostEqual(result["total_tax_amount"]["selected_value"]["content"], 525.0)
        self.assertAlmostEqual(result["invoice_lines"][0]["quantity"], 1.0)
        self.assertAlmostEqual(result["invoice_lines"][0]["total"], 3025.0)

    def test_regular_invoice_amounts_unchanged(self):
        """Non-credit-note amounts must NOT be abs-ed (negative stays negative if ever)."""
        resp = {"status": "success", "fields": {"document_type": "invoice", "amount_total": 1200.0}}
        result = mars_to_odoo(resp, "invoice")
        self.assertNotIn("type", result)
        self.assertAlmostEqual(result["total"]["selected_value"]["content"], 1200.0)

    # ── Régression #6 : email/phone/website lus par Odoo via .candidates ──────
    def test_contact_fields_use_candidates_format(self):
        resp = {
            "status": "success",
            "fields": {
                "vendor_name": "Acme",
                "email": "billing@acme.test",
                "phone": "+33123456789",
                "website": "https://acme.test",
            },
            "confidence_scores": {},
        }
        result = mars_to_odoo(resp, "invoice")
        self.assertEqual(result["email"], {"candidates": [{"content": "billing@acme.test"}]})
        self.assertEqual(result["phone"], {"candidates": [{"content": "+33123456789"}]})
        self.assertEqual(result["website"], {"candidates": [{"content": "https://acme.test"}]})

    def test_vat_number_has_both_formats(self):
        """VAT_Number must carry selected_value (partner matching) AND candidates (vat prefill)."""
        resp = {"status": "success", "fields": {"vendor_vat": "FR12345678901"}}
        result = mars_to_odoo(resp, "invoice")
        self.assertEqual(result["VAT_Number"]["selected_value"]["content"], "FR12345678901")
        self.assertEqual(result["VAT_Number"]["candidates"], [{"content": "FR12345678901"}])
