"""
Product matching service for OCR-extracted invoice lines.

Priority order (first match wins):
  1. product.supplierinfo.product_code == product_ref  AND  partner
  2. product.supplierinfo.product_code == product_ref  (any partner)
  3. product.product.default_code == product_ref
  4. ocr_techdata.product_mapping (partner + normalized description)  [LEARNING]
  5. product.supplierinfo.product_name ilike description  AND  partner
  6. product.product.name ilike first 3 words of description
"""

import logging
import re

_logger = logging.getLogger(__name__)


def _normalize_description(desc: str) -> str:
    """Lowercase + strip + collapse whitespace. No digit removal (too aggressive)."""
    if not desc:
        return ""
    return re.sub(r"\s+", " ", desc.lower().strip())


def _first_words(desc: str, n: int = 3) -> str:
    """Return the first n words of a description for broad ilike matching."""
    words = desc.split()
    return " ".join(words[:n]) if words else ""


def find_product(env, partner_id: int, description: str, product_ref: str | None = None):
    """
    Return a product.product record matching the given OCR line, or None.

    :param env: Odoo environment
    :param partner_id: res.partner id of the invoice vendor
    :param description: line description from OCR
    :param product_ref: optional vendor product code extracted from the document
    """
    Product = env["product.product"]
    SupplierInfo = env["product.supplierinfo"]

    # ── 1. Exact product_ref match in supplierinfo for this partner ──────────
    if product_ref:
        si = SupplierInfo.search([
            ("product_code", "=", product_ref),
            ("partner_id", "=", partner_id),
        ], limit=1)
        if si and si.product_id:
            _logger.debug("product_matcher: hit level-1 (supplierinfo+partner) ref=%s", product_ref)
            return si.product_id

    # ── 2. Exact product_ref match in supplierinfo (any partner) ────────────
    if product_ref:
        si = SupplierInfo.search([("product_code", "=", product_ref)], limit=1)
        if si and si.product_id:
            _logger.debug("product_matcher: hit level-2 (supplierinfo any) ref=%s", product_ref)
            return si.product_id

    # ── 3. Exact product_ref match on product.default_code ──────────────────
    if product_ref:
        product = Product.search([("default_code", "=", product_ref)], limit=1)
        if product:
            _logger.debug("product_matcher: hit level-3 (default_code) ref=%s", product_ref)
            return product

    # ── 4. Learning mapping (partner + normalized description) ───────────────
    if description and partner_id:
        key = _normalize_description(description)
        if key:
            mapping = env["ocr_techdata.product_mapping"].search([
                ("partner_id", "=", partner_id),
                ("description_key", "=", key),
                ("company_id", "=", env.company.id),
            ], limit=1)
            if mapping:
                _logger.debug("product_matcher: hit level-4 (learning) key=%s", key)
                return mapping.product_id

    # ── 5. SupplierInfo product_name ilike for this partner ─────────────────
    if description and partner_id:
        si = SupplierInfo.search([
            ("product_name", "ilike", description),
            ("partner_id", "=", partner_id),
        ], limit=1)
        if si and si.product_id:
            _logger.debug("product_matcher: hit level-5 (supplierinfo name ilike)")
            return si.product_id

    # ── 6. product.name ilike first 3 words ─────────────────────────────────
    if description:
        keywords = _first_words(description, 3)
        if keywords and len(keywords) >= 3:  # avoid single-letter matches
            product = Product.search([("name", "ilike", keywords)], limit=1)
            if product:
                _logger.debug("product_matcher: hit level-6 (name ilike) keywords=%r", keywords)
                return product

    return None
