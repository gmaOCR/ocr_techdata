"""
Convert the JSON response from mars into the format expected by Odoo's
_fill_document_with_results() / _save_form() methods.

Odoo reads scalar fields with:
    _get_ocr_selected_value(ocr_results, 'supplier', "")
    → ocr_results.get('supplier', {}).get('selected_value', {}).get('content', "")

So every scalar field must be wrapped as:
    { "selected_value": { "content": <value> } }

EXCEPTION — invoice_lines: Odoo reads it as a plain list:
    ocr_results.get('invoice_lines', [])
Each element: { "description", "unit_price", "quantity", "taxes": [rate, ...], "subtotal", "total" }

EXCEPTION — SWIFT_code: Odoo json.loads() the content:
    json.loads(_get_ocr_selected_value(ocr_results, 'SWIFT_code', "{}"))
So it must be a JSON string inside the content field.
"""

import json
import logging
from typing import Optional

_logger = logging.getLogger(__name__)


def _wrap(value) -> dict:
    return {"selected_value": {"content": value}}


def mars_to_odoo(mars_response: dict, document_type: str) -> Optional[dict]:
    """
    Map a mars /ocr/extract response to the Odoo IAP OCR result format.
    Returns None if the mars response indicates an error.
    """
    if mars_response.get("status") != "success":
        _logger.warning(
            "ocr_techdata: mars returned status=%s error=%s",
            mars_response.get("status"),
            mars_response.get("error"),
        )
        return None

    fields = mars_response.get("fields") or {}
    result: dict = {}
    doc_type = fields.get("document_type")  # resolved early — used for credit_note logic below

    # ── Scalar fields (wrapped) ───────────────────────────────────────────────

    vendor_name = fields.get("vendor_name")
    if vendor_name:
        result["supplier"] = _wrap(vendor_name)

    vendor_vat = fields.get("vendor_vat")
    if vendor_vat:
        result["VAT_Number"] = _wrap(vendor_vat)

    inv_num = fields.get("invoice_number")
    if inv_num:
        result["invoice_id"] = _wrap(inv_num)

    date = fields.get("date")
    if date:
        result["date"] = _wrap(date)

    date_due = fields.get("date_due")
    if date_due:
        result["due_date"] = _wrap(date_due)
    elif doc_type == "credit_note" and date:
        # Credit notes have no payment due date — use issue date to avoid Odoo defaulting to today
        result["due_date"] = _wrap(date)

    amount_total = fields.get("amount_total")
    if amount_total is not None:
        result["total"] = _wrap(float(amount_total))

    amount_untaxed = fields.get("amount_untaxed")
    if amount_untaxed is not None:
        result["subtotal"] = _wrap(float(amount_untaxed))

    amount_tax = fields.get("amount_tax")
    if amount_tax is not None:
        result["total_tax_amount"] = _wrap(float(amount_tax))

    currency = fields.get("currency")
    if currency:
        result["currency"] = _wrap(currency)

    # payment_ref: use explicit payment_reference, fall back to invoice_number
    payment_ref = fields.get("payment_reference") or fields.get("invoice_number")
    if payment_ref:
        result["payment_ref"] = _wrap(payment_ref)

    # ── Banking fields ────────────────────────────────────────────────────────

    iban = fields.get("iban")
    if iban:
        result["iban"] = _wrap(iban)

    bic = fields.get("bic")
    if bic:
        # SWIFT_code content is a JSON string (Odoo does json.loads on it)
        swift_data = {"bic": bic, "verified_bic": False, "name": "", "city": "", "country_code": ""}
        result["SWIFT_code"] = _wrap(json.dumps(swift_data))

    # ── Contact / partner prefill fields ─────────────────────────────────────

    email = fields.get("email")
    if email:
        result["email"] = _wrap(email)

    phone = fields.get("phone")
    if phone:
        result["phone"] = _wrap(phone)

    website = fields.get("website")
    if website:
        result["website"] = _wrap(website)

    # ── Document type (refund detection) ─────────────────────────────────────
    # Odoo reads ocr_results.get('type') directly (NOT via _get_ocr_selected_value)
    # and compares to plain strings "refund" or "receipt" — do NOT wrap this field.
    if doc_type == "credit_note":
        result["type"] = "refund"  # triggers action_switch_move_type() → in_refund

    # ── Expense-specific fields ───────────────────────────────────────────────

    description = fields.get("description")
    if description and document_type == "expense":
        result["description"] = _wrap(description)

    # ── Line items — MUST be a plain list (not wrapped) ───────────────────────
    # Odoo reads: ocr_results.get('invoice_lines', [])
    line_items = fields.get("line_items") or []
    if line_items:
        lines = []
        for item in line_items:
            pu = item.get("unit_price") or 0.0
            tax_rate = item.get("tax_rate")
            total = item.get("total") or 0.0
            raw_qty = item.get("quantity")
            # Deduce qty from total/unit_price when Ollama couldn't extract it (OCR layout issue)
            if raw_qty is None and float(pu) > 0 and float(total) > 0:
                raw_qty = round(float(total) / float(pu), 4)
            qty = raw_qty if raw_qty is not None else 1.0
            line: dict = {
                "description": item.get("description") or "/",
                "quantity": float(qty),
                "unit_price": float(pu),
                "subtotal": float(qty) * float(pu),
                "total": float(total),
                "taxes": [float(tax_rate)] if tax_rate is not None else [],
                "product_ref": item.get("product_ref") or None,  # passed through for product_matcher, ignored by Odoo
            }
            lines.append(line)
        result["invoice_lines"] = lines  # plain list — critical, NOT wrapped

    return result


def get_confidence_for_field(mars_response: dict, odoo_field: str) -> float:
    """Return the confidence score for a given Odoo field name."""
    mars_field_map = {
        "supplier": "vendor_name",
        "VAT_Number": "vendor_vat",
        "invoice_id": "invoice_number",
        "date": "date",
        "due_date": "date_due",
        "total": "amount_total",
        "subtotal": "amount_untaxed",
        "currency": "currency",
        "iban": "iban",
        "email": "email",
        "phone": "phone",
    }
    mars_key = mars_field_map.get(odoo_field)
    if not mars_key:
        return 0.0
    return mars_response.get("confidence_scores", {}).get(mars_key, 0.0)
