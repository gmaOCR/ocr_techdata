from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestReconcileWizard(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wizard = cls.env["ocr_techdata.reconcile.wizard"]
        cls.PartnerLine = cls.env["ocr_techdata.reconcile.partner.line"]
        cls.ProductLine = cls.env["ocr_techdata.reconcile.product.line"]
        cls.expense_account = cls.company_data["default_account_expense"]

    def _make_imported_bill(self, with_partner=False):
        move = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.partner_a.id if with_partner else False,
            "invoice_line_ids": [(0, 0, {
                "name": "Prestation OCR",
                "quantity": 1,
                "price_unit": 100.0,
                "account_id": self.expense_account.id,
            })],
        })
        move.invoice_line_ids.write({"is_imported": True})
        return move

    def test_create_partner_from_ocr(self):
        """action=create builds a supplier prefilled from OCR + a bank account."""
        move = self._make_imported_bill()
        wizard = self.Wizard.create({"move_id": move.id})
        line = self.PartnerLine.create({
            "wizard_id": wizard.id,
            "ocr_vendor_name": "ACME Test SARL",
            "ocr_vendor_vat": "FR12345678901",
            "ocr_email": "billing@acme.test",
            "ocr_iban": "FR7630006000011234567890189",
            "action": "create",
        })
        partner = line._create_partner()
        self.assertEqual(partner.name, "ACME Test SARL")
        self.assertEqual(partner.vat, "FR12345678901")
        self.assertEqual(partner.email, "billing@acme.test")
        self.assertTrue(partner.is_company)
        self.assertTrue(partner.supplier_rank >= 1)
        self.assertTrue(partner.bank_ids, "an IBAN was provided → a bank account must exist")

    def test_create_product_creates_supplierinfo(self):
        """action=create builds a product AND its supplierinfo (feeds matcher L1/L2)."""
        move = self._make_imported_bill(with_partner=True)
        wizard = self.Wizard.create({"move_id": move.id})
        line = self.ProductLine.create({
            "wizard_id": wizard.id,
            "move_line_id": move.invoice_line_ids[0].id,
            "ocr_description": "Cartouche encre X500",
            "ocr_product_ref": "ENC-X500",
            "ocr_unit_price": 42.0,
            "action": "create",
        })
        product = line._create_product(move.partner_id)
        self.assertEqual(product.default_code, "ENC-X500")
        si = self.env["product.supplierinfo"].search([
            ("partner_id", "=", move.partner_id.id),
            ("product_code", "=", "ENC-X500"),
        ])
        self.assertTrue(si)
        self.assertEqual(si.price, 42.0)

    def test_action_apply_assigns_matches(self):
        """match writes partner on the move and product on the line."""
        move = self._make_imported_bill()
        product = self.env["product.product"].create({"name": "Produit existant"})
        wizard = self.Wizard.create({
            "move_id": move.id,
            "partner_line_ids": [(0, 0, {
                "ocr_vendor_name": "X", "action": "match", "partner_id": self.partner_a.id,
            })],
            "product_line_ids": [(0, 0, {
                "move_line_id": move.invoice_line_ids[0].id,
                "ocr_description": "ligne", "action": "match", "product_id": product.id,
            })],
        })
        wizard.action_apply()
        self.assertEqual(move.partner_id, self.partner_a)
        self.assertEqual(move.invoice_line_ids[0].product_id, product)

    def test_action_apply_skip_leaves_empty(self):
        move = self._make_imported_bill()
        wizard = self.Wizard.create({
            "move_id": move.id,
            "product_line_ids": [(0, 0, {
                "move_line_id": move.invoice_line_ids[0].id,
                "ocr_description": "ligne", "action": "skip",
            })],
        })
        wizard.action_apply()
        self.assertFalse(move.invoice_line_ids[0].product_id)

    def test_learn_uses_ocr_description_not_mutated_name(self):
        """Regression: learning key must be the ORIGINAL OCR description, not line.name
        (which Odoo rewrites when a product is set)."""
        move = self._make_imported_bill(with_partner=True)
        product = self.env["product.product"].create({"name": "P"})
        line = move.invoice_line_ids[0]
        line.product_id = product
        line.name = "NAME MUTATED BY ODOO"
        move._learn_product_mappings_from_ocr({line.id: "Description OCR Originale"})
        mapping = self.env["ocr_techdata.product_mapping"].search([
            ("partner_id", "=", move.partner_id.id),
            ("product_id", "=", product.id),
        ])
        self.assertEqual(len(mapping), 1)
        self.assertEqual(mapping.description_key, "description ocr originale")

    def test_open_wizard_builds_lines_from_snapshot(self):
        move = self._make_imported_bill()  # no partner, line without product
        move.ocr_extraction_data = {
            "fields": {"vendor_name": "NoMatch Vendor", "vendor_vat": "FR99999999999"},
            "lines": [{"description": "Item A", "product_ref": "REF-A", "unit_price": 10.0}],
        }
        action = move.action_open_ocr_reconcile()
        wizard = self.Wizard.browse(action["res_id"])
        self.assertEqual(len(wizard.partner_line_ids), 1)
        self.assertEqual(wizard.partner_line_ids.ocr_vendor_name, "NoMatch Vendor")
        self.assertEqual(len(wizard.product_line_ids), 1)
        self.assertEqual(wizard.product_line_ids.ocr_product_ref, "REF-A")
        self.assertEqual(wizard.product_line_ids.ocr_unit_price, 10.0)

    @mute_logger("odoo.addons.ocr_techdata.models.account_move")
    def test_count_mismatch_skips_product_lines(self):
        """Regression #5: when Odoo merged lines (count != OCR lines), positional pairing
        is unsafe → no product lines are built (rather than mismatching products)."""
        move = self._make_imported_bill()  # 1 imported line
        move.ocr_extraction_data = {
            "fields": {},
            "lines": [
                {"description": "A", "product_ref": "RA", "unit_price": 1.0},
                {"description": "B", "product_ref": "RB", "unit_price": 2.0},
            ],  # 2 OCR lines vs 1 Odoo line → mismatch
        }
        action = move.action_open_ocr_reconcile()
        wizard = self.Wizard.browse(action["res_id"])
        self.assertFalse(wizard.product_line_ids)
