"""
Tests for product_matcher service.

Uses unittest.mock to simulate the Odoo ORM — no running instance needed.
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ── Bootstrap: create stub odoo modules so we can import product_matcher ──────

def _make_odoo_stub():
    odoo = types.ModuleType("odoo")
    sys.modules.setdefault("odoo", odoo)


_make_odoo_stub()

# Now import the service under test
import importlib
import os

_HERE = os.path.dirname(__file__)
_SERVICE_PATH = os.path.join(_HERE, "..", "services", "product_matcher.py")

spec = importlib.util.spec_from_file_location("product_matcher", _SERVICE_PATH)
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env(supplierinfo_results=None, product_results=None, mapping_results=None):
    """Build a minimal mock Odoo env."""
    env = MagicMock()

    si_model = MagicMock()
    si_model.search.return_value = supplierinfo_results or MagicMock(product_id=None, __bool__=lambda s: False)

    prod_model = MagicMock()
    prod_model.search.return_value = product_results or MagicMock(__bool__=lambda s: False)

    map_model = MagicMock()
    map_model.search.return_value = mapping_results or MagicMock(__bool__=lambda s: False)

    _registry = {
        "product.supplierinfo": si_model,
        "product.product": prod_model,
        "ocr_techdata.product_mapping": map_model,
    }

    env.__getitem__ = MagicMock(side_effect=lambda key: _registry.get(key, MagicMock()))
    env.company.id = 1
    return env


def _si(product):
    """Return a supplierinfo mock that points to a product."""
    si = MagicMock()
    si.__bool__ = lambda s: True
    si.product_id = product
    return si


def _product(name="Product A", pid=42):
    p = MagicMock()
    p.__bool__ = lambda s: True
    p.name = name
    p.id = pid
    return p


def _mapping(product):
    m = MagicMock()
    m.__bool__ = lambda s: True
    m.product_id = product
    return m


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNormalizeDescription(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(pm._normalize_description("PAPIER A4"), "papier a4")

    def test_strips_edges(self):
        self.assertEqual(pm._normalize_description("  Stylos  "), "stylos")

    def test_collapses_spaces(self):
        self.assertEqual(pm._normalize_description("a   b"), "a b")

    def test_empty(self):
        self.assertEqual(pm._normalize_description(""), "")

    def test_none(self):
        self.assertEqual(pm._normalize_description(None), "")

    def test_digits_preserved(self):
        # digits are NOT stripped (too aggressive)
        self.assertEqual(pm._normalize_description("Papier A4 500 feuilles"), "papier a4 500 feuilles")


class TestFindProductLevel1(unittest.TestCase):
    """Level 1: supplierinfo.product_code + partner match."""

    def test_match_returns_product(self):
        prod = _product()
        si = _si(prod)
        env = _env(supplierinfo_results=si)
        result = pm.find_product(env, partner_id=1, description="Anything", product_ref="REF-001")
        self.assertEqual(result, prod)

    def test_no_match_falls_through(self):
        empty_si = MagicMock(__bool__=lambda s: False)
        env = _env(supplierinfo_results=empty_si, product_results=MagicMock(__bool__=lambda s: False))
        # Should not raise, returns None at the end
        result = pm.find_product(env, partner_id=1, description="x", product_ref="UNKNOWN")
        self.assertIsNone(result)


class TestFindProductLevel2(unittest.TestCase):
    """Level 2: supplierinfo.product_code match on any partner."""

    def test_any_partner_match(self):
        prod = _product()
        si = _si(prod)
        # First search (level 1 — specific partner) returns nothing, second (level 2 — any) returns si
        si_model = MagicMock()
        si_model.search.side_effect = [
            MagicMock(__bool__=lambda s: False),  # level 1: partner specific → miss
            si,                                    # level 2: any partner → hit
        ]
        prod_model = MagicMock()
        map_model = MagicMock()
        _registry = {
            "product.supplierinfo": si_model,
            "product.product": prod_model,
            "ocr_techdata.product_mapping": map_model,
        }
        env = MagicMock()
        env.company.id = 1
        env.__getitem__ = MagicMock(side_effect=lambda key: _registry.get(key, MagicMock()))
        result = pm.find_product(env, partner_id=1, description="Stylos bille", product_ref="REF-X")
        self.assertEqual(result, prod)


class TestFindProductLevel3(unittest.TestCase):
    """Level 3: product.default_code exact match."""

    def test_default_code_match(self):
        prod = _product()
        # supplierinfo returns nothing, product search returns prod
        empty_si = MagicMock(__bool__=lambda s: False)
        env = _env(supplierinfo_results=empty_si, product_results=prod)
        result = pm.find_product(env, partner_id=1, description="Desc", product_ref="SKU-99")
        self.assertEqual(result, prod)


class TestFindProductLevel4(unittest.TestCase):
    """Level 4: learning mapping lookup."""

    def test_learning_match(self):
        prod = _product()
        empty_si = MagicMock(__bool__=lambda s: False)
        empty_prod = MagicMock(__bool__=lambda s: False)
        mapping = _mapping(prod)
        env = _env(
            supplierinfo_results=empty_si,
            product_results=empty_prod,
            mapping_results=mapping,
        )
        result = pm.find_product(env, partner_id=1, description="Papier A4 500 feuilles")
        self.assertEqual(result, prod)

    def test_no_learning_no_description(self):
        """Empty description → learning key is empty → skip level 4."""
        env = _env()
        result = pm.find_product(env, partner_id=1, description="")
        self.assertIsNone(result)


class TestFindProductLevel5(unittest.TestCase):
    """Level 5: supplierinfo.product_name ilike + partner."""

    def test_supplierinfo_name_ilike(self):
        prod = _product()
        si = _si(prod)
        si_model = MagicMock()
        # levels 1 & 2 miss (no product_ref), level 5 hit
        si_model.search.return_value = si
        prod_model = MagicMock()
        prod_model.search.return_value = MagicMock(__bool__=lambda s: False)
        map_model = MagicMock()
        map_model.search.return_value = MagicMock(__bool__=lambda s: False)
        _registry = {
            "product.supplierinfo": si_model,
            "product.product": prod_model,
            "ocr_techdata.product_mapping": map_model,
        }
        env = MagicMock()
        env.company.id = 1
        env.__getitem__ = MagicMock(side_effect=lambda key: _registry.get(key, MagicMock()))
        result = pm.find_product(env, partner_id=7, description="Papier multifonction A4", product_ref=None)
        self.assertEqual(result, prod)

    def test_no_match_without_description(self):
        env = _env()
        result = pm.find_product(env, partner_id=7, description="", product_ref=None)
        self.assertIsNone(result)


class TestFindProductLevel6(unittest.TestCase):
    """Level 6: product.name ilike first 3 words."""

    def test_ilike_match(self):
        prod = _product()
        empty_si = MagicMock(__bool__=lambda s: False)
        empty_mapping = MagicMock(__bool__=lambda s: False)
        # product_ref=None → levels 1-3 skipped entirely, first product.search call is level 6
        prod_model = MagicMock()
        prod_model.search.return_value = prod

        si_model = MagicMock()
        si_model.search.return_value = empty_si

        map_model = MagicMock()
        map_model.search.return_value = empty_mapping

        _registry = {
            "product.supplierinfo": si_model,
            "product.product": prod_model,
            "ocr_techdata.product_mapping": map_model,
        }

        env = MagicMock()
        env.company.id = 1
        env.__getitem__ = MagicMock(side_effect=lambda key: _registry.get(key, MagicMock()))

        result = pm.find_product(env, partner_id=1, description="Papier A4 Standard", product_ref=None)
        self.assertEqual(result, prod)

    def test_short_keyword_skipped(self):
        """Descriptions shorter than 3 chars after first 3 words should not trigger ilike."""
        env = _env()
        result = pm.find_product(env, partner_id=1, description="AB", product_ref=None)
        self.assertIsNone(result)


class TestEnoughWords(unittest.TestCase):
    """Level-6 guard: require >= 2 real words (>=2 chars) to avoid laxist ilike."""

    def test_two_real_words_ok(self):
        self.assertTrue(pm._enough_words("Papier A4"))

    def test_single_word_rejected(self):
        self.assertFalse(pm._enough_words("Photocopieur"))

    def test_short_tokens_rejected(self):
        self.assertFalse(pm._enough_words("a b"))  # both single-char

    def test_empty(self):
        self.assertFalse(pm._enough_words(""))


class TestCrossPartnerRefGuard(unittest.TestCase):
    """Levels 2/3 (cross-partner) must be skipped for short refs (<3) — collision guard.
    Level 1 (partner-scoped) stays permissive."""

    def _env_two_si_calls(self):
        si = _si(_product())
        si_model = MagicMock()
        # level 1 (partner) misses; level 2 (any) would hit IF reached
        si_model.search.side_effect = [MagicMock(__bool__=lambda s: False), si]
        prod_model = MagicMock()
        prod_model.search.return_value = MagicMock(__bool__=lambda s: False)
        map_model = MagicMock()
        map_model.search.return_value = MagicMock(__bool__=lambda s: False)
        registry = {
            "product.supplierinfo": si_model,
            "product.product": prod_model,
            "ocr_techdata.product_mapping": map_model,
        }
        env = MagicMock()
        env.company.id = 1
        env.__getitem__ = MagicMock(side_effect=lambda k: registry.get(k, MagicMock()))
        return env, si_model, si

    def test_short_ref_skips_levels_2_3(self):
        env, si_model, _ = self._env_two_si_calls()
        result = pm.find_product(env, partner_id=1, description="", product_ref="A1")
        self.assertIsNone(result)
        self.assertEqual(si_model.search.call_count, 1)  # only level-1 ran

    def test_long_ref_allows_level_2(self):
        env, si_model, si = self._env_two_si_calls()
        result = pm.find_product(env, partner_id=1, description="", product_ref="ABC")
        self.assertEqual(result, si.product_id)
        self.assertEqual(si_model.search.call_count, 2)  # level-1 miss + level-2 hit

    def test_ref_is_stripped(self):
        """Trailing OCR whitespace must not prevent a level-1 match."""
        env, si_model, _ = self._env_two_si_calls()
        pm.find_product(env, partner_id=1, description="", product_ref="  AB  ")
        # "  AB  " → stripped "AB" (len 2) → cross-partner guarded → only level-1 search
        self.assertEqual(si_model.search.call_count, 1)


class TestFindProductNoMatch(unittest.TestCase):
    def test_returns_none_when_nothing_matches(self):
        env = _env()
        result = pm.find_product(env, partner_id=1, description="Totally unknown item XYZ")
        self.assertIsNone(result)

    def test_no_product_ref_skips_levels_1_2_3(self):
        """When product_ref is None, levels 1-3 must be skipped entirely."""
        env = _env()
        # Verify supplierinfo is never called with product_code domain
        pm.find_product(env, partner_id=1, description="Some desc", product_ref=None)
        si_model = env["product.supplierinfo"]
        for call in si_model.search.call_args_list:
            domain = call[0][0] if call[0] else call[1].get("domain", [])
            for clause in domain:
                if isinstance(clause, (list, tuple)) and len(clause) == 3:
                    self.assertNotEqual(clause[0], "product_code")


if __name__ == "__main__":
    unittest.main()
