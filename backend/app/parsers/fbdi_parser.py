"""Robust Oracle FBDI template parser supporting two formats:

1. Oracle FBDI transposed format: column A has row-labels (Name/Description/Data Type),
   fields span columns B onwards.
2. Standard tabular format: row 1 has field names as column headers from col A onwards,
   prefix "* " marks required fields.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from openpyxl import load_workbook

_DATA_TYPE_RE = re.compile(r"^\s*([A-Za-z]+)\s*(?:\(\s*(\d+)(?:\s*,\s*\d+)?\s*\))?", re.IGNORECASE)


def _parse_data_type(raw):
    if not raw:
        return ("Character", None)
    m = _DATA_TYPE_RE.match(str(raw))
    if not m:
        return (str(raw).strip(), None)
    dtype = m.group(1).strip().capitalize()
    length = int(m.group(2)) if m.group(2) else None
    return (dtype, length)


def _looks_like_module_label(value):
    if not value:
        return False
    s = str(value).strip()
    keywords = ("management","planning","order promising","operations","supply","common","interface","loader")
    return any(k in s.lower() for k in keywords) and len(s) < 80


def parse_fbdi_template(file_path):
    file_path = Path(file_path)
    wb = load_workbook(filename=file_path, data_only=True, keep_vba=False, read_only=False)

    business_object = None
    description = None

    for sname in wb.sheetnames:
        if "instruction" in sname.lower():
            ws = wb[sname]
            for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
                for cell in row:
                    if cell and isinstance(cell, str) and ":" in cell and len(cell) < 200:
                        if any(k in cell.lower() for k in ("upload:","import:","interface:")):
                            business_object = cell.split(":", 1)[1].strip()
                            break
                if business_object:
                    break
            break

    sheets_out = []
    fields_out = []
    seq = 0

    for sname in wb.sheetnames:
        if "instruction" in sname.lower():
            continue
        ws = wb[sname]
        max_row = ws.max_row or 0
        max_col = ws.max_column or 0
        if max_row < 1 or max_col < 1:
            continue

        col_a_labels = []
        for r in range(1, min(15, max_row + 1)):
            v = ws.cell(r, 1).value
            if v:
                col_a_labels.append((r, str(v).strip()))

        is_oracle_fbdi = any(lbl.lower() == "name" for _, lbl in col_a_labels)
        sheet_field_count = 0

        if is_oracle_fbdi:
            name_row = next((r for r, lbl in col_a_labels if lbl.lower() == "name"), 1)
            desc_row = next((r for r, lbl in col_a_labels if "description" in lbl.lower()), 2)
            type_row = next((r for r, lbl in col_a_labels if "data type" in lbl.lower() or lbl.lower() == "type"), 3)
            module_rows = [(r, lbl) for r, lbl in col_a_labels if r > type_row and _looks_like_module_label(lbl)]

            for col in range(2, max_col + 1):
                raw_name = ws.cell(name_row, col).value
                if not raw_name:
                    continue
                name = str(raw_name).strip()
                if not name:
                    continue
                is_req = name.startswith("*")
                field_name = name.lstrip("*").strip()
                desc_val = ws.cell(desc_row, col).value
                desc_text = str(desc_val).strip() if desc_val else None
                dtype_raw = ws.cell(type_row, col).value
                data_type, max_length = _parse_data_type(dtype_raw)
                req_modules = []
                for r, lbl in module_rows:
                    cv = ws.cell(r, col).value
                    if cv and "required" in str(cv).strip().lower():
                        req_modules.append(lbl)
                required = is_req or bool(req_modules)
                seq += 1
                sheet_field_count += 1
                fields_out.append({
                    "field_name": field_name,
                    "display_name": field_name,
                    "description": desc_text,
                    "required": required,
                    "data_type": data_type,
                    "max_length": max_length,
                    "format_mask": "YYYYMMDD" if data_type.lower() == "date" else None,
                    "sample_value": None,
                    "lookup_type": None,
                    "validation_notes": None,
                    "sequence": seq,
                    "sheet_name": sname,
                    "required_modules": req_modules,
                })

        else:
            for col in range(1, max_col + 1):
                raw_name = ws.cell(1, col).value
                if not raw_name:
                    continue
                name = str(raw_name).strip()
                if not name:
                    continue
                is_req = name.startswith("*")
                field_name = name.lstrip("* ").strip()
                if not field_name:
                    continue
                sample_val = ws.cell(2, col).value
                sample_text = str(sample_val).strip() if sample_val is not None else None
                if isinstance(sample_val, (int, float)):
                    data_type, max_length = "Number", None
                else:
                    data_type, max_length = "Character", None
                seq += 1
                sheet_field_count += 1
                fields_out.append({
                    "field_name": field_name,
                    "display_name": field_name,
                    "description": None,
                    "required": is_req,
                    "data_type": data_type,
                    "max_length": max_length,
                    "format_mask": None,
                    "sample_value": sample_text,
                    "lookup_type": None,
                    "validation_notes": None,
                    "sequence": seq,
                    "sheet_name": sname,
                    "required_modules": [],
                })

        sheets_out.append({"sheet_name": sname, "sequence": len(sheets_out), "field_count": sheet_field_count})

    return {"business_object": business_object, "description": description, "sheets": sheets_out, "fields": fields_out}
