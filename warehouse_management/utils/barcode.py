import base64
import io

import frappe

FALLBACK_SYMBOLOGY = "code128"


def get_barcode_image(value, barcode_type=None, module_width=0.22, module_height=8.0):
	"""Jinja helper: `value` drawn as a barcode PNG data URI.
	Falls back to Code 128 when `barcode_type` is empty or cannot encode
	`value`, and returns "" when even that fails.
	"""
	value = frappe.utils.cstr(value).strip()
	if not value:
		return ""

	options = {
		"module_width": module_width,
		"module_height": module_height,
		"quiet_zone": 1.0,
		"write_text": False,
		"dpi": 300,
	}

	symbology = _symbology(barcode_type)
	png = _render(value, symbology, options)
	if not png and symbology != FALLBACK_SYMBOLOGY:
		png = _render(value, FALLBACK_SYMBOLOGY, options)

	return f"data:image/png;base64,{base64.b64encode(png).decode()}" if png else ""


def _symbology(barcode_type):
	"""Item Barcode's type ("EAN-13", "UPC-A") as a python-barcode name."""
	from barcode import PROVIDED_BARCODES

	name = frappe.utils.cstr(barcode_type).lower().replace("-", "").replace(" ", "")
	return name if name in PROVIDED_BARCODES else FALLBACK_SYMBOLOGY


def _render(value, symbology, options):
	"""PNG bytes, or None when the value doesn't fit the symbology's rules."""
	from barcode import get_barcode_class
	from barcode.writer import ImageWriter

	try:
		stream = io.BytesIO()
		get_barcode_class(symbology)(value, writer=ImageWriter()).write(stream, options)
		return stream.getvalue()
	except Exception:
		return None
