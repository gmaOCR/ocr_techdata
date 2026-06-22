"""Tests for AccountMove._suggest_partner (VAT + name matching)."""
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase


class TestSuggestPartner(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Partner = self.env["res.partner"]
        self.Move = self.env["account.move"]

        self.vendor_with_vat = self.Partner.create({
            "name": "Acme SARL",
            "is_company": True,
            "vat": "FR12345678901",
        })
        self.vendor_exact_name = self.Partner.create({
            "name": "Bureau Dupont",
            "is_company": True,
        })
        # Minimal move to get a model instance with _suggest_partner method
        self.move = self.Move.new({})

    def test_vat_match_returns_partner(self):
        result = self.move._suggest_partner({"vendor_vat": "FR12345678901", "vendor_name": "Other"})
        self.assertEqual(result.id, self.vendor_with_vat.id)

    def test_name_match_used_when_no_vat(self):
        result = self.move._suggest_partner({"vendor_name": "Bureau Dupont"})
        self.assertEqual(result.id, self.vendor_exact_name.id)

    def test_vat_takes_priority_over_name(self):
        # VAT points to vendor_with_vat, name would match vendor_exact_name
        result = self.move._suggest_partner({
            "vendor_vat": "FR12345678901",
            "vendor_name": "Bureau Dupont",
        })
        self.assertEqual(result.id, self.vendor_with_vat.id)

    def test_returns_none_when_no_match(self):
        result = self.move._suggest_partner({
            "vendor_vat": "FR99999999999",
            "vendor_name": "Unknown Company XYZ",
        })
        self.assertFalse(result)

    def test_returns_none_with_empty_fields(self):
        result = self.move._suggest_partner({})
        self.assertFalse(result)

    def test_partial_name_does_not_match(self):
        # "Acme" alone should not match "Acme SARL" (=ilike requires exact phrase)
        result = self.move._suggest_partner({"vendor_name": "Acme"})
        self.assertFalse(result)

    def test_name_match_is_case_insensitive(self):
        result = self.move._suggest_partner({"vendor_name": "bureau dupont"})
        self.assertEqual(result.id, self.vendor_exact_name.id)
