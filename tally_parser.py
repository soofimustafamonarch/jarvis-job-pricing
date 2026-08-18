import hashlib
import html
import io
import json
import math
import re
from datetime import date, datetime

import pandas as pd


def normalize_name(value):
    text = str(value or "").strip().upper()
    return re.sub(r"\s+", " ", text)


def clean_text(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)).strip()
    return text or None


def clean_unit(value):
    text = clean_text(value)
    if not text or "NOT APPLICABLE" in text.upper():
        return None
    return text


def clean_number(value):
    try:
        if value is None or pd.isna(value):
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except Exception:
        return None


def json_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def row_as_json(row_index, row):
    cells = {
        str(column): json_value(value)
        for column, value in enumerate(row.tolist())
        if json_value(value) is not None
    }
    return {"source_row": int(row_index) + 1, "cells": cells}


def stable_hash(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_hash(raw_bytes):
    return hashlib.sha256(raw_bytes).hexdigest()


def parse_date_value(value):
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.date().isoformat() if hasattr(value, "date") else value.isoformat()
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def parse_period(frame):
    pattern = re.compile(
        r"(\d{1,2}-[A-Za-z]{3}-\d{2,4})\s+to\s+(\d{1,2}-[A-Za-z]{3}-\d{2,4})",
        flags=re.IGNORECASE,
    )
    for value in frame.iloc[:12].fillna("").astype(str).to_numpy().flatten():
        match = pattern.search(value)
        if match:
            return parse_date_value(match.group(1)), parse_date_value(match.group(2))
    return None, None


def decode_tally_xml(raw_bytes):
    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw_bytes.decode("utf-16", errors="replace")
    return raw_bytes.decode("utf-8-sig", errors="replace")


def extract_xml_tag(block, tag):
    match = re.search(rf"<{tag}>(.*?)</{tag}>", block, flags=re.DOTALL)
    return html.unescape(match.group(1).strip()) if match else ""


def parse_inventory_master_xml(raw_bytes):
    xml = decode_tally_xml(raw_bytes)
    blocks = re.findall(
        r'<STOCKITEM NAME="([^"]*)"[^>]*>(.*?)</STOCKITEM>',
        xml,
        flags=re.DOTALL,
    )
    items = []
    for raw_name, block in blocks:
        item_name = clean_text(html.unescape(raw_name))
        guid = clean_text(extract_xml_tag(block, "GUID"))
        if not item_name or not guid:
            continue
        alter_id = clean_number(extract_xml_tag(block, "ALTERID"))
        items.append(
            {
                "tally_guid": guid,
                "item_name": item_name,
                "normalized_name": normalize_name(item_name),
                "base_unit": clean_unit(extract_xml_tag(block, "BASEUNITS")),
                "additional_unit": clean_unit(extract_xml_tag(block, "ADDITIONALUNITS")),
                "alter_id": int(alter_id) if alter_id is not None else None,
                "is_deleted": extract_xml_tag(block, "ISDELETED").strip().lower() == "yes",
            }
        )
    return {
        "report_type": "Inventory Master",
        "period_start": None,
        "period_end": None,
        "stock_items": items,
        "record_count": len(items),
    }


def read_excel_report(raw_bytes, file_name):
    suffix = file_name.lower().rsplit(".", 1)[-1]
    if suffix == "csv":
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return pd.read_csv(io.BytesIO(raw_bytes), header=None, encoding=encoding)
            except UnicodeDecodeError as error:
                last_error = error
        raise ValueError("Could not read the CSV encoding") from last_error
    return pd.read_excel(io.BytesIO(raw_bytes), header=None)


def detect_excel_report_type(frame):
    heading = " ".join(
        frame.iloc[:12].fillna("").astype(str).to_numpy().flatten().tolist()
    ).upper()
    if "PURCHASE REGISTER" in heading:
        return "Purchase"
    if "TAX INVOICE REGISTER" in heading or "SALES REGISTER" in heading:
        return "Sales"
    if "MOVEMENT ANALYSIS" in heading:
        return "Stock Movement"
    raise ValueError("This Excel report is not a supported Tally Purchase, Sales, or Movement report")


def stock_item_lookup(stock_items):
    return {item["normalized_name"]: item for item in stock_items or []}


def numeric_at(row, column):
    if column >= len(row):
        return None
    return clean_number(row.iloc[column])


def text_at(row, column):
    if column >= len(row):
        return None
    return clean_text(row.iloc[column])


def voucher_header_indices(frame):
    indices = []
    for index, value in frame.iloc[:, 0].items():
        if isinstance(value, (datetime, date, pd.Timestamp)):
            indices.append(index)
    return indices


def parse_voucher_report(frame, report_type, stock_items=None):
    lookup = stock_item_lookup(stock_items)
    period_start, period_end = parse_period(frame)
    header_indices = voucher_header_indices(frame)
    vouchers = []
    all_lines = []

    reserved_exact = {"NEW REF", "AGST REF", "SALES-INCOME", "PURCHASES"}

    for header_position, start_index in enumerate(header_indices):
        end_index = (
            header_indices[header_position + 1]
            if header_position + 1 < len(header_indices)
            else len(frame) - (1 if clean_text(frame.iloc[-1, 0]) == "Total:" else 0)
        )
        block = frame.iloc[start_index:end_index]
        header = frame.iloc[start_index]
        voucher_date = parse_date_value(header.iloc[0])
        party_name = text_at(header, 1)
        voucher_type = text_at(header, 6) or report_type
        voucher_number = text_at(header, 7)
        if not voucher_date or not voucher_number:
            continue

        total_amount = numeric_at(header, 8)
        if total_amount is None:
            total_amount = numeric_at(header, 9)

        references = []
        tax_amount = 0.0
        lines = []
        current_line = None

        for row_index, row in block.iloc[1:].iterrows():
            name = text_at(row, 1)
            if not name:
                continue
            normalized = normalize_name(name)
            if normalized in {"NEW REF", "AGST REF"}:
                reference = text_at(row, 2)
                if reference and reference not in references:
                    references.append(reference)
                current_line = None
                continue
            if "VAT" in normalized:
                tax_value = numeric_at(row, 8)
                if tax_value is None:
                    tax_value = numeric_at(row, 9)
                if tax_value is not None:
                    tax_amount += tax_value
                current_line = None
                continue
            if normalized in reserved_exact:
                current_line = None
                continue

            amount = numeric_at(row, 4)
            if amount is not None:
                master = lookup.get(normalized)
                quantity = numeric_at(row, 2)
                rate = numeric_at(row, 3)
                review_reasons = []
                if not master:
                    review_reasons.append("Item not matched to Inventory Master")
                if quantity is None:
                    review_reasons.append("Quantity missing")
                if not master or not master.get("base_unit"):
                    review_reasons.append("Unit missing")

                current_line = {
                    "line_number": len(lines) + 1,
                    "item_name": name,
                    "item_guid": master.get("tally_guid") if master else None,
                    "unit": master.get("base_unit") if master else None,
                    "quantity": quantity,
                    "rate": rate,
                    "amount": amount,
                    "description": None,
                    "review_status": "Needs review" if review_reasons else "Ready",
                    "review_note": "; ".join(review_reasons) or None,
                    "raw_row": row_as_json(row_index, row),
                }
                current_line["source_hash"] = stable_hash(current_line)
                lines.append(current_line)
                continue

            if current_line is not None and numeric_at(row, 8) is None and numeric_at(row, 9) is None:
                existing = current_line.get("description")
                current_line["description"] = f"{existing} {name}".strip() if existing else name
                current_line["source_hash"] = stable_hash(current_line)

        voucher_year = str(voucher_date)[:4] if voucher_date else "UNKNOWN-YEAR"
        voucher_key = (
            f"{report_type.upper()}|{normalize_name(voucher_type)}|"
            f"{voucher_year}|{normalize_name(voucher_number)}"
        )
        header_payload = {
            "voucher_key": voucher_key,
            "report_type": report_type,
            "voucher_date": voucher_date,
            "voucher_type": voucher_type,
            "voucher_number": voucher_number,
            "party_name": party_name,
            "reference_number": "; ".join(references) or None,
            "total_amount": total_amount,
            "tax_amount": tax_amount or None,
            "is_active": True,
            "raw_header": row_as_json(start_index, header),
        }
        header_payload["source_hash"] = stable_hash(header_payload)
        vouchers.append(header_payload)
        for line in lines:
            line["voucher_key"] = voucher_key
            all_lines.append(line)

    return {
        "report_type": report_type,
        "period_start": period_start,
        "period_end": period_end,
        "vouchers": vouchers,
        "voucher_lines": all_lines,
        "record_count": len(vouchers),
    }


def parse_stock_movement(frame, stock_items=None):
    lookup = stock_item_lookup(stock_items)
    period_start, period_end = parse_period(frame)
    movements = []
    for row_index, row in frame.iloc[9:].iterrows():
        item_name = text_at(row, 0)
        if not item_name or normalize_name(item_name) in {"GRAND TOTAL", "TOTAL:"}:
            continue
        normalized = normalize_name(item_name)
        master = lookup.get(normalized)
        numbers = [numeric_at(row, column) for column in range(1, 7)]
        if not any(value is not None for value in numbers):
            continue
        review_reasons = []
        if not master:
            review_reasons.append("Item not matched to Inventory Master")
        if not master or not master.get("base_unit"):
            review_reasons.append("Unit missing")
        if any(value is not None and value < 0 for value in numbers):
            review_reasons.append("Negative movement requires review")
        movement_key = f"{period_start}|{period_end}|{normalized}"
        movement = {
            "movement_key": movement_key,
            "period_start": period_start,
            "period_end": period_end,
            "item_name": item_name,
            "item_guid": master.get("tally_guid") if master else None,
            "unit": master.get("base_unit") if master else None,
            "inward_quantity": numbers[0],
            "inward_rate": numbers[1],
            "inward_value": numbers[2],
            "outward_quantity": numbers[3],
            "outward_rate": numbers[4],
            "outward_value": numbers[5],
            "review_status": "Needs review" if review_reasons else "Ready",
            "raw_row": row_as_json(row_index, row),
        }
        movement["source_hash"] = stable_hash(movement)
        movements.append(movement)
    return {
        "report_type": "Stock Movement",
        "period_start": period_start,
        "period_end": period_end,
        "stock_movements": movements,
        "record_count": len(movements),
    }


def parse_tally_upload(file_name, raw_bytes, stock_items=None):
    suffix = file_name.lower().rsplit(".", 1)[-1]
    if suffix == "xml":
        parsed = parse_inventory_master_xml(raw_bytes)
    else:
        frame = read_excel_report(raw_bytes, file_name)
        report_type = detect_excel_report_type(frame)
        if report_type in {"Sales", "Purchase"}:
            parsed = parse_voucher_report(frame, report_type, stock_items)
        else:
            parsed = parse_stock_movement(frame, stock_items)
    parsed["file_name"] = file_name
    parsed["file_hash"] = file_hash(raw_bytes)
    return parsed
