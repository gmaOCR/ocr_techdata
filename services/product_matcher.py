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

    # Normalise the OCR ref: strip surrounding whitespace, match case-insensitively
    # (OCR often introduces trailing spaces / case noise). `=ilike` = exact, no wildcard.
    ref = (product_ref or "").strip()
    # Cross-partner matches (levels 2/3) require a ref long enough to be discriminating;
    # short codes ("1", "A1") collide across vendor catalogs → false positives.
    _MIN_CROSS_REF = 3

    # ── 1. product_ref match in supplierinfo for this partner ───────────────
    if ref:
        si = SupplierInfo.search([
            ("product_code", "=ilike", ref),
            ("partner_id", "=", partner_id),
        ], limit=1)
        if si and si.product_id:
            _logger.debug("product_matcher: hit level-1 (supplierinfo+partner) ref=%s", ref)
            return si.product_id

    # ── 2. product_ref match in supplierinfo (any partner) — guarded ────────
    if len(ref) >= _MIN_CROSS_REF:
        si = SupplierInfo.search([("product_code", "=ilike", ref)], limit=1)
        if si and si.product_id:
            _logger.debug("product_matcher: hit level-2 (supplierinfo any) ref=%s", ref)
            return si.product_id

    # ── 3. product_ref match on product.default_code — guarded ──────────────
    if len(ref) >= _MIN_CROSS_REF:
        product = Product.search([("default_code", "=ilike", ref)], limit=1)
        if product:
            _logger.debug("product_matcher: hit level-3 (default_code) ref=%s", ref)
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
        if _enough_words(keywords):  # avoid laxist single/short-word matches
            product = Product.search([("name", "ilike", keywords)], limit=1)
            if product:
                _logger.debug("product_matcher: hit level-6 (name ilike) keywords=%r", keywords)
                return product

    return None


def find_candidates(env, partner_id: int, description: str, product_ref: str | None = None, limit: int = 5):
    """Return up to `limit` plausible product.product candidates (relaxed, ranked),
    for the reconcile wizard to present to the user. Never auto-applies — just suggests."""
    Product = env["product.product"]
    SupplierInfo = env["product.supplierinfo"]
    ref = (product_ref or "").strip()

    ids: list[int] = []

    def _add(recs):
        for r in recs:
            pid = getattr(r, "product_id", r)
            pid = pid.id if hasattr(pid, "id") else pid
            if pid and pid not in ids:
                ids.append(pid)

    if ref:
        _add(SupplierInfo.search([("product_code", "=ilike", ref), ("partner_id", "=", partner_id)], limit=limit))
        _add(SupplierInfo.search([("product_code", "=ilike", ref)], limit=limit))
        _add(Product.search([("default_code", "=ilike", ref)], limit=limit))
    if description and partner_id:
        _add(SupplierInfo.search([("product_name", "ilike", description), ("partner_id", "=", partner_id)], limit=limit))
    keywords = _first_words(description, 3)
    if _enough_words(keywords):
        _add(Product.search([("name", "ilike", keywords)], limit=limit))
    return Product.browse(ids[:limit])


def _enough_words(text: str, minimum: int = 2) -> bool:
    """True when `text` has at least `minimum` real words (>=2 chars each).
    Guards level-6 ilike against single/short-token false positives."""
    words = [w for w in (text or "").split() if len(w) >= 2]
    return len(words) >= minimum
