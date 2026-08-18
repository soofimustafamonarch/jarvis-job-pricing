import hashlib
import io
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


BUCKET_NAME = "jarvis-documents"
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


def optional_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def optional_text(value):
    value = optional_value(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_float(value):
    value = optional_value(value)
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    return float(text)


def safe_filename(file_name):
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(file_name).stem).strip("_")
    suffix = Path(file_name).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    return f"{stem or 'document'}{suffix}"


def file_digest(raw_bytes):
    return hashlib.sha256(raw_bytes).hexdigest()


def locate_tesseract():
    command = shutil.which("tesseract")
    if command:
        return command

    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def prepare_image(raw_bytes):
    image = Image.open(io.BytesIO(raw_bytes))
    image = ImageOps.exif_transpose(image).convert("RGB")

    if max(image.size) < 1800:
        scale = 1800 / max(image.size)
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.Resampling.LANCZOS,
        )

    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.35)
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray


def run_ocr(raw_bytes):
    try:
        import pytesseract
    except ImportError as error:
        raise RuntimeError(
            "pytesseract is not installed. Run: py -m pip install -r requirements.txt"
        ) from error

    command = locate_tesseract()
    if not command:
        raise RuntimeError(
            "Tesseract OCR is not installed on this computer. Install it, then restart Jarvis."
        )
    pytesseract.pytesseract.tesseract_cmd = command

    available = set(pytesseract.get_languages(config=""))
    languages = [language for language in ["eng", "ara"] if language in available]
    language_setting = "+".join(languages) or "eng"

    image = prepare_image(raw_bytes)
    primary_text = pytesseract.image_to_string(
        image,
        lang=language_setting,
        config="--oem 3 --psm 3",
    )
    sparse_text = pytesseract.image_to_string(
        image,
        lang=language_setting,
        config="--oem 3 --psm 11",
    )
    text = f"{primary_text.strip()}\n\n--- SPARSE OCR ---\n{sparse_text.strip()}"
    return text.strip(), f"Tesseract ({language_setting}, PSM 3+11)"


def cleaned_lines(text):
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def numbers_in(text):
    matches = re.findall(r"(?<![A-Za-z])[-+]?\d[\d,.]*", text)
    values = []
    for match in matches:
        try:
            values.append(parse_ocr_number(match))
        except ValueError:
            pass
    return values


def parse_ocr_number(token):
    text = str(token).strip().rstrip(".,")
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    if not text:
        raise ValueError("Empty number")

    separators = [index for index, character in enumerate(text) if character in ".,"]
    if not separators:
        return sign * float(text)

    groups = re.split(r"[.,]", text)
    groups = [group for group in groups if group != ""]
    if not groups or any(not group.isdigit() for group in groups):
        raise ValueError(f"Invalid OCR number: {token}")

    if len(groups) >= 3 and all(len(group) == 3 for group in groups[1:]):
        normalized = "".join(groups[:-1]) + "." + groups[-1]
        return sign * float(normalized)

    if len(groups) == 2:
        left, right = groups
        if len(right) == 3:
            return sign * float(f"{left}.{right}")
        return sign * float(left + right)

    normalized = "".join(groups[:-1]) + "." + groups[-1]
    return sign * float(normalized)


def money_candidates(raw_value):
    candidates = []
    for divisor in [1, 10, 100, 1000]:
        candidate = raw_value / divisor
        if candidate >= 0 and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def reconcile_rate_and_amount(quantity, raw_rate, raw_amount):
    choices = []
    for rate in money_candidates(raw_rate):
        for amount in money_candidates(raw_amount):
            expected = quantity * rate
            error = abs(expected - amount)
            relative_error = error / max(abs(amount), 0.001)
            plausibility_penalty = 0
            if rate > 10000:
                plausibility_penalty += 0.1
            choices.append((relative_error + plausibility_penalty, rate, amount))
    _, rate, amount = min(choices, key=lambda choice: choice[0])
    return rate, amount


def amount_for_labels(lines, labels, excluded_labels=()):
    for line in reversed(lines):
        lower = line.lower()
        if any(excluded in lower for excluded in excluded_labels):
            continue
        if any(label in lower for label in labels):
            values = numbers_in(line)
            if values:
                return values[-1]
    return None


def detect_vat_amount(lines, currency):
    candidates = []
    for index, line in enumerate(lines):
        lower = line.lower()
        if "vat" not in lower and "tax" not in lower:
            continue
        if any(excluded in lower for excluded in ["tax invoice", "vat no", "vatin", "tax registration"]):
            continue
        values = numbers_in(line)
        if not values:
            continue
        score = 0
        if len(values) >= 2:
            score += 5
        if currency.lower() in lower:
            score += 3
        if any(word in lower for word in ["output vat", "input vat", "tax amount"]):
            score += 2
        candidates.append((score, -index, values[-1]))
    if not candidates:
        return None
    return max(candidates)[2]


def detect_document_number(lines):
    ignored = {
        "dated",
        "date",
        "invoice",
        "invoice no",
        "invoice no.",
        "delivery",
        "delivery note",
        "mode/terms of payment",
    }
    patterns = [
        r"\b(?:invoice|inv|bill)\s*(?:no\.?|number|#|num)\s*[:.-]?\s*([A-Za-z0-9][A-Za-z0-9/_-]+)",
        r"\b(?:voucher|reference|ref)\s*(?:no\.?|number|#)\s*[:.-]?\s*([A-Za-z0-9][A-Za-z0-9/_-]+)",
    ]
    for line in lines:
        for pattern in patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                candidate = match.group(1).strip(" .:|-")
                if candidate.lower() not in ignored:
                    return candidate

    label_pattern = re.compile(r"\b(?:invoice|inv|bill)\s*(?:no\.?|number|#|num)\b", re.IGNORECASE)
    for index, line in enumerate(lines):
        if not label_pattern.search(line):
            continue
        for candidate in lines[index + 1 : index + 8]:
            cleaned = candidate.strip(" .:|-")
            lower = cleaned.lower()
            if lower in ignored or not cleaned:
                continue
            if re.search(r"\b(?:p\.?o\.?\s*box|tel|email|e-mail|trn|llc|l\.l\.c)\b", lower):
                continue
            if re.search(r"\d{1,2}[-/]\w{1,9}[-/]\d{2,4}", cleaned):
                continue
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]{0,24}", cleaned):
                return cleaned
    return None


def normalized_date(year, month, day):
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def detect_document_date(lines):
    for line in lines:
        if (
            "date" not in line.lower()
            and not re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", line)
            and not re.search(r"\d{1,2}[- ][A-Za-z]{3,9}[- ]\d{2,4}", line)
        ):
            continue

        match = re.search(r"\b(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\b", line)
        if match:
            candidate = normalized_date(match.group(1), match.group(2), match.group(3))
            if candidate:
                return candidate

        match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", line)
        if match:
            year = match.group(3)
            if len(year) == 2:
                year = f"20{year}"
            candidate = normalized_date(year, match.group(2), match.group(1))
            if candidate:
                return candidate

        month_match = re.search(
            r"\b(\d{1,2})[- ]([A-Za-z]{3,9})[- ](\d{2,4})\b",
            line,
        )
        if month_match:
            month_names = {
                "jan": 1,
                "feb": 2,
                "mar": 3,
                "apr": 4,
                "may": 5,
                "jun": 6,
                "jul": 7,
                "aug": 8,
                "sep": 9,
                "oct": 10,
                "nov": 11,
                "dec": 12,
            }
            month = month_names.get(month_match.group(2)[:3].lower())
            year = month_match.group(3)
            if len(year) == 2:
                year = f"20{year}"
            if month:
                candidate = normalized_date(year, month, month_match.group(1))
                if candidate:
                    return candidate
    return None


def detect_party(lines, document_type):
    labels = (
        ["supplier", "vendor"]
        if document_type == "Purchase bill"
        else ["customer", "bill to", "buyer", "party"]
    )
    for line in lines:
        lower = line.lower().strip()
        if "supplier's ref" in lower or "supplier ref" in lower:
            continue
        for label in labels:
            match = re.match(
                rf"^{re.escape(label)}\s*[:.-]\s*(.+)$",
                line,
                flags=re.IGNORECASE,
            )
            if match and match.group(1).strip():
                return match.group(1).strip()

    company_pattern = re.compile(
        r"\b(?:L\.?\s*L\.?\s*C\.?|LLC|LTD\.?|LIMITED|TRDG\.?|ENTERPRISES|SPC|TRADING)\b",
        re.IGNORECASE,
    )
    candidates = []
    for index, line in enumerate(lines):
        company_matches = list(company_pattern.finditer(line))
        if not company_matches:
            continue
        company_match = company_matches[-1]
        company_prefix = line[: company_match.end()]
        company = re.split(
            r"\b(?:invoice\s*(?:no\.?|number)?|dated|delivery note|buyer|bill to)\b",
            company_prefix,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" .:|-")
        if company:
            candidates.append((index, company))

    if not candidates:
        return None

    buyer_indexes = [
        index
        for index, line in enumerate(lines)
        if re.search(r"\b(?:buyer|bill to|customer|party)\b", line, re.IGNORECASE)
    ]
    buyer_index = buyer_indexes[0] if buyer_indexes else None

    if document_type == "Purchase bill":
        before_buyer = [company for index, company in candidates if buyer_index is None or index < buyer_index]
        return before_buyer[-1] if before_buyer else candidates[0][1]

    after_buyer = [company for index, company in candidates if buyer_index is not None and index > buyer_index]
    return after_buyer[0] if after_buyer else candidates[-1][1]


def detect_currency(lines):
    currency_aliases = {
        "AED": ["AED", "UAE DIRHAM", "UAE DIRHAMS"],
        "OMR": ["OMR", "R.O", "RO ", "RIAL OMANI"],
        "USD": ["USD", "US DOLLAR"],
        "EUR": ["EUR", "EURO"],
        "INR": ["INR", "INDIAN RUPEE"],
    }
    upper_text = " ".join(lines).upper()
    for currency, aliases in currency_aliases.items():
        if any(alias in upper_text for alias in aliases):
            return currency
    return "OMR"


def detect_total(lines, currency):
    candidates = []
    for index, line in enumerate(lines):
        lower = line.lower()
        if "total" not in lower and "amount due" not in lower:
            continue
        if any(word in lower for word in ["subtotal", "sub total", "total carton", "total qty", "total quantity"]):
            continue
        values = numbers_in(line)
        if not values:
            continue
        score = 0
        if any(label in lower for label in ["grand total", "net total", "invoice total", "amount due"]):
            score += 6
        if currency.lower() in lower:
            score += 5
        if re.search(r"\b(?:aed|omr|usd|eur|inr)\b", lower):
            score += 3
        if len(values) > 1:
            score += 1
        candidates.append((score, index, values[-1]))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], -item[1]))[2]


def likely_item_lines(lines):
    ignored_words = {
        "invoice",
        "subtotal",
        "sub total",
        "vat",
        "tax",
        "grand total",
        "total amount",
        "balance",
        "phone",
        "email",
        "date",
        "description qty",
        "description quantity",
        "p.o.box",
        "p.o. box",
        "tel:",
        "telephone",
        "mobile",
        "mob:",
        "trn",
        "supplier's ref",
        "buyer",
        "customer",
        "delivery note",
        "despatch",
        "terms of delivery",
        "country",
        "h.s.code",
        "total carton",
        "made in",
        "amount chargeable",
        "declaration",
        "party:",
        "uv flatbed printing",
        "large format printing",
        "digital printing",
        "vehicle branding",
        "specialized in",
    }
    units = {"pc", "pcs", "nos", "no", "sqm", "mtr", "m", "roll", "sheet", "kg", "ltr", "box", "set"}
    rows = []

    for original_line in lines:
        line = re.sub(r"\b[sS][oO](?=nos\b)", "50 ", original_line, flags=re.IGNORECASE)
        line = re.sub(r"\b[rlI][oO](?=nos\b)", "10 ", line, flags=re.IGNORECASE)
        lower = line.lower()
        if any(word in lower for word in ignored_words):
            continue
        if not re.search(r"[A-Za-z]", line):
            continue

        token_matches = list(re.finditer(r"[-+]?\d[\d,.]*", line))
        if len(token_matches) < 3:
            continue

        try:
            quantity = parse_ocr_number(token_matches[-3].group())
            raw_rate = parse_ocr_number(token_matches[-2].group())
            raw_amount = parse_ocr_number(token_matches[-1].group())
            rate, amount = reconcile_rate_and_amount(quantity, raw_rate, raw_amount)
        except ValueError:
            continue

        if quantity <= 0 or rate < 0 or amount < 0:
            continue
        expected_amount = quantity * rate
        tolerance = max(0.05, abs(amount) * 0.03)
        if abs(expected_amount - amount) > tolerance:
            continue

        description = line[: token_matches[-3].start()].strip(" -|:#")
        description = re.sub(
            r"^(?:\d+|[SIl])\s*[\])|.,:-]*\s*",
            "",
            description,
            flags=re.IGNORECASE,
        ).strip()
        if len(description) < 2:
            continue

        unit = None
        words = re.findall(r"[A-Za-z]+", line[token_matches[-3].end() : token_matches[-2].start()].lower())
        for word in words:
            if word in units:
                unit = word
                break

        if unit is None and rate < 1 and amount < 1:
            continue

        rows.append(
            {
                "description": description,
                "quantity": quantity,
                "unit": unit,
                "rate": rate,
                "amount": amount,
                "review_status": "Needs review",
            }
        )

    return rows[:100]


def extract_draft(ocr_text, document_type):
    lines = cleaned_lines(ocr_text)
    currency = detect_currency(lines)
    subtotal = amount_for_labels(lines, ["subtotal", "sub total", "before vat", "before tax"])
    vat = detect_vat_amount(lines, currency)
    total = detect_total(lines, currency)
    item_lines = likely_item_lines(lines)
    item_total = sum(row["amount"] for row in item_lines if row.get("amount") is not None)

    if item_total > 0 and (subtotal is None or abs(subtotal - item_total) > max(0.05, item_total * 0.03)):
        subtotal = round(item_total, 3)
    if subtotal is None and total is not None and vat is not None and total >= vat:
        subtotal = round(total - vat, 3)
    if total is not None and subtotal is not None and total >= subtotal:
        calculated_vat = round(total - subtotal, 3)
        if vat is None or abs(vat - calculated_vat) > max(0.05, calculated_vat * 0.03):
            vat = calculated_vat
    return {
        "party_name": detect_party(lines, document_type),
        "document_number": detect_document_number(lines),
        "document_date": detect_document_date(lines),
        "subtotal": subtotal,
        "vat_amount": vat,
        "total_amount": total,
        "currency": currency,
        "lines": item_lines,
    }


def parse_date_input(value):
    text = optional_text(value)
    if text is None:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as error:
        raise ValueError("Document date must use YYYY-MM-DD, for example 2026-07-21.") from error


def existing_document(supabase, digest):
    response = (
        supabase.table("jarvis_documents")
        .select("id,file_name,document_type,created_at")
        .eq("file_hash", digest)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def upload_document_image(supabase, storage_path, raw_bytes, mime_type):
    return (
        supabase.storage.from_(BUCKET_NAME)
        .upload(
            path=storage_path,
            file=raw_bytes,
            file_options={
                "content-type": mime_type,
                "cache-control": "3600",
                "upsert": "false",
            },
        )
    )


def save_document(supabase, draft, edited_lines):
    digest = draft["file_hash"]
    duplicate = existing_document(supabase, digest)
    if duplicate:
        raise ValueError(f"This exact image is already saved as document #{duplicate['id']}.")

    folder = datetime.now().strftime("%Y/%m")
    storage_path = f"{folder}/{uuid.uuid4().hex}_{safe_filename(draft['file_name'])}"
    document_id = None
    uploaded = False

    try:
        upload_document_image(
            supabase,
            storage_path,
            draft["raw_bytes"],
            draft["mime_type"],
        )
        uploaded = True

        payload = {
            "document_type": draft["document_type"],
            "file_name": draft["file_name"],
            "file_hash": digest,
            "storage_path": storage_path,
            "mime_type": draft["mime_type"],
            "file_size": len(draft["raw_bytes"]),
            "party_name": optional_text(draft.get("party_name")),
            "document_number": optional_text(draft.get("document_number")),
            "document_date": parse_date_input(draft.get("document_date")),
            "subtotal": optional_float(draft.get("subtotal")),
            "vat_amount": optional_float(draft.get("vat_amount")),
            "total_amount": optional_float(draft.get("total_amount")),
            "currency": draft.get("currency") or "OMR",
            "ocr_text": optional_text(draft.get("ocr_text")),
            "ocr_engine": optional_text(draft.get("ocr_engine")),
            "review_status": draft.get("review_status", "Needs review"),
            "notes": optional_text(draft.get("notes")),
            "raw_metadata": {"confirmed_by_user": True},
        }
        response = supabase.table("jarvis_documents").insert(payload).execute()
        document_id = response.data[0]["id"]

        rows = []
        for _, row in edited_lines.iterrows():
            description = optional_text(row.get("description"))
            quantity = optional_float(row.get("quantity"))
            rate = optional_float(row.get("rate"))
            amount = optional_float(row.get("amount"))
            unit = optional_text(row.get("unit"))
            if not any([description, quantity is not None, rate is not None, amount is not None]):
                continue
            rows.append(
                {
                    "document_id": document_id,
                    "line_number": len(rows) + 1,
                    "description": description,
                    "quantity": quantity,
                    "unit": unit,
                    "rate": rate,
                    "amount": amount,
                    "review_status": row.get("review_status") or "Needs review",
                }
            )
        if rows:
            supabase.table("jarvis_document_lines").insert(rows).execute()
        return document_id
    except Exception:
        if document_id is not None:
            supabase.table("jarvis_documents").delete().eq("id", document_id).execute()
        if uploaded:
            supabase.storage.from_(BUCKET_NAME).remove([storage_path])
        raise


def recent_documents(supabase):
    response = (
        supabase.table("jarvis_documents")
        .select("id,created_at,document_type,party_name,document_number,document_date,total_amount,review_status,file_name,storage_path")
        .order("id", desc=True)
        .limit(50)
        .execute()
    )
    return response.data or []


def signed_url(supabase, storage_path):
    response = (
        supabase.storage.from_(BUCKET_NAME)
        .create_signed_url(storage_path, 300)
    )
    if isinstance(response, dict):
        return response.get("signedURL") or response.get("signedUrl") or response.get("signed_url")
    return None


def clear_draft():
    st.session_state.pop("document_ocr_draft", None)


def render_document_capture_page(supabase):
    st.subheader("Purchase bill / sales invoice OCR")
    st.caption("Peter extracts a draft only. Check every value before saving.")

    document_type = st.selectbox("Document type", ["Purchase bill", "Sales invoice"])
    source = st.radio("Image source", ["Upload image", "Take photo"], horizontal=True)

    if source == "Upload image":
        uploaded = st.file_uploader(
            "Choose a JPG, PNG, or WEBP image",
            type=["jpg", "jpeg", "png", "webp"],
            key="document_image_upload",
        )
    else:
        uploaded = st.camera_input("Photograph the full document", key="document_camera")

    if uploaded is not None:
        raw_bytes = uploaded.getvalue()
        mime_type = uploaded.type or "image/jpeg"
        digest = file_digest(raw_bytes)

        if len(raw_bytes) > MAX_FILE_SIZE:
            st.error("The image is larger than 10 MB.")
            return
        if mime_type not in ALLOWED_TYPES:
            st.error("Use a JPG, PNG, or WEBP image.")
            return

        st.image(raw_bytes, caption=uploaded.name, use_container_width=True)

        try:
            duplicate = existing_document(supabase, digest)
        except Exception as error:
            st.error("Run the v0.5 Supabase migration before using Document OCR.")
            st.exception(error)
            return

        if duplicate:
            st.info(f"This exact image is already saved as document #{duplicate['id']}.")
            return

        current_draft = st.session_state.get("document_ocr_draft")
        if current_draft and current_draft.get("file_hash") != digest:
            clear_draft()
            current_draft = None

        if st.button("Read image with Peter", type="primary"):
            try:
                with st.spinner("Reading the image..."):
                    ocr_text, ocr_engine = run_ocr(raw_bytes)
                    extracted = extract_draft(ocr_text, document_type)
                st.session_state["document_ocr_draft"] = {
                    **extracted,
                    "document_type": document_type,
                    "file_name": uploaded.name,
                    "file_hash": digest,
                    "mime_type": mime_type,
                    "raw_bytes": raw_bytes,
                    "ocr_text": ocr_text,
                    "ocr_engine": ocr_engine,
                }
                st.rerun()
            except Exception as error:
                st.error("Peter could not read this image.")
                st.exception(error)

    draft = st.session_state.get("document_ocr_draft")
    if not draft:
        return

    st.divider()
    st.markdown("#### Check Peter's draft")

    col1, col2 = st.columns(2)
    with col1:
        party_name = st.text_input(
            "Supplier" if draft["document_type"] == "Purchase bill" else "Customer",
            value=draft.get("party_name") or "",
        )
        document_number = st.text_input("Invoice / bill number", value=draft.get("document_number") or "")
    with col2:
        document_date = st.text_input(
            "Document date (YYYY-MM-DD)",
            value=draft.get("document_date") or "",
        )
        review_status = st.selectbox("Review status", ["Needs review", "Ready"])

    currency_options = ["OMR", "AED", "USD", "EUR", "INR"]
    detected_currency = draft.get("currency") or "OMR"
    currency = st.selectbox(
        "Currency",
        currency_options,
        index=currency_options.index(detected_currency) if detected_currency in currency_options else 0,
    )

    col3, col4, col5 = st.columns(3)
    with col3:
        subtotal = st.text_input("Subtotal", value="" if draft.get("subtotal") is None else str(draft["subtotal"]))
    with col4:
        vat_amount = st.text_input("VAT", value="" if draft.get("vat_amount") is None else str(draft["vat_amount"]))
    with col5:
        total_amount = st.text_input("Total", value="" if draft.get("total_amount") is None else str(draft["total_amount"]))

    line_columns = ["description", "quantity", "unit", "rate", "amount", "review_status"]
    initial_lines = pd.DataFrame(draft.get("lines") or [], columns=line_columns)
    edited_lines = st.data_editor(
        initial_lines,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "quantity": st.column_config.NumberColumn("Quantity", format="%.3f"),
            "rate": st.column_config.NumberColumn("Rate OMR", format="%.3f"),
            "amount": st.column_config.NumberColumn("Amount OMR", format="%.3f"),
            "review_status": st.column_config.SelectboxColumn(
                "Status", options=["Needs review", "Ready", "Ignored"]
            ),
        },
        key=f"document_lines_{draft['file_hash']}",
    )

    notes = st.text_area("Notes", placeholder="Optional correction/context")
    with st.expander("Raw OCR text"):
        ocr_text = st.text_area("OCR text", value=draft.get("ocr_text") or "", height=260)

    if st.button("Save confirmed document", type="primary"):
        try:
            final_draft = {
                **draft,
                "party_name": party_name,
                "document_number": document_number,
                "document_date": document_date,
                "subtotal": subtotal,
                "vat_amount": vat_amount,
                "total_amount": total_amount,
                "currency": currency,
                "review_status": review_status,
                "notes": notes,
                "ocr_text": ocr_text,
            }
            document_id = save_document(supabase, final_draft, edited_lines)
            clear_draft()
            st.success(f"Document #{document_id} and its private image were saved.")
        except Exception as error:
            st.error("Could not save this document.")
            st.exception(error)


def render_saved_documents_page(supabase):
    st.subheader("Saved purchase bills and sales invoices")
    try:
        rows = recent_documents(supabase)
        if not rows:
            st.info("No document photos saved yet.")
            return

        dataframe = pd.DataFrame(rows)
        visible = [
            "id",
            "created_at",
            "document_type",
            "party_name",
            "document_number",
            "document_date",
            "total_amount",
            "review_status",
            "file_name",
        ]
        st.dataframe(dataframe[visible], use_container_width=True, hide_index=True)

        options = [
            f"{row['id']} - {row['document_type']} - {row.get('file_name') or 'image'}"
            for row in rows
        ]
        selected = st.selectbox("View original image", options)
        selected_id = int(selected.split(" - ", 1)[0])
        record = next(row for row in rows if row["id"] == selected_id)
        url = signed_url(supabase, record["storage_path"])
        if url:
            st.link_button("Open private image (link valid for 5 minutes)", url)
        else:
            st.warning("Could not create the temporary image link.")
    except Exception as error:
        st.error("Could not load saved documents. Run the v0.5 Supabase migration first.")
        st.exception(error)
