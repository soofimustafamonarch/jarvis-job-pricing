import io
import math
import re
from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import create_client


# =========================================================
# Page and login
# =========================================================

st.set_page_config(
    page_title="Jarvis",
    page_icon="J",
    layout="centered",
)


def check_password():
    if st.session_state.get("password_correct"):
        return True

    st.title("Jarvis Login")

    password = st.text_input(
        "Enter password",
        type="password",
    )

    if st.button("Login", type="primary"):
        if password == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("Wrong password")

    return False


if not check_password():
    st.stop()


# =========================================================
# Supabase
# =========================================================

@st.cache_resource
def get_supabase_client():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"],
    )


supabase = get_supabase_client()


# =========================================================
# General helper functions
# =========================================================

def safe_text(value):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    text = str(value).strip()
    return text or None


def safe_number(value):
    try:
        if value is None or pd.isna(value):
            return None

        number = float(value)

        if math.isnan(number) or math.isinf(number):
            return None

        return number

    except Exception:
        return None


def display_text(value):
    return safe_text(value) or ""


def display_number(value):
    return safe_number(value) or 0.0


def parse_measurement(number, unit, output_unit="cm"):
    """
    Convert mm, cm or metres into the requested output unit.
    """

    number = float(number)
    unit = (unit or "cm").lower()

    if unit == "mm":
        metres = number / 1000

    elif unit == "cm":
        metres = number / 100

    else:
        metres = number

    if output_unit == "mm":
        return metres * 1000

    if output_unit == "cm":
        return metres * 100

    return metres


# =========================================================
# Requirement splitting
# =========================================================

def split_requirement(text):
    """
    Split one paragraph into possible separate jobs.

    Recognized separators:
    - New line
    - +
    - ;
    - &
    - 'and' when followed by another quantity
    """

    cleaned = re.sub(r"\r\n?", "\n", text.strip())

    parts = re.split(
        r"\s*(?:"
        r"\n+"
        r"|\+"
        r"|;"
        r"|\s&\s"
        r"|\band\b(?=\s*(?:qty\s*[:\-]?\s*)?\d+)"
        r")\s*",
        cleaned,
        flags=re.IGNORECASE,
    )

    return [
        part.strip(" ,.-")
        for part in parts
        if part.strip(" ,.-")
    ]


# =========================================================
# Requirement detection
# =========================================================

def detect_quantity_and_unit(text):
    """
    Detect quantity and how the finished product was sold.
    """

    patterns = [
        (
            r"(?:qty|quantity)\s*[:\-]?\s*"
            r"(\d+(?:\.\d+)?)\s*(pcs?|nos?|pieces?)?",
            "pcs",
        ),
        (
            r"(\d+(?:\.\d+)?)\s*(pcs?|nos?|pieces?)\b",
            "pcs",
        ),
        (
            r"(\d+(?:\.\d+)?)\s*(sqm|sq\.?\s*m|m2|m²)\b",
            "sqm",
        ),
        (
            r"(\d+(?:\.\d+)?)\s*(rolls?)\b",
            "roll",
        ),
        (
            r"(\d+(?:\.\d+)?)\s*(sheets?)\b",
            "sheet",
        ),
        (
            r"(\d+(?:\.\d+)?)\s*"
            r"(?:running\s*)?(?:metres?|meters?|mtrs?|rm)\b",
            "running metre",
        ),
    ]

    for pattern, normalized_unit in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return float(match.group(1)), normalized_unit

    return None, "job"


def detect_size(text):
    """
    Detect sizes such as:
    10x10cm
    100mm x 100mm
    1m x 2m
    1mtr x 2mtr
    """

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*"
        r"(mm|cm|m|mtr|metre|meter)?\s*"
        r"[x×]\s*"
        r"(\d+(?:\.\d+)?)\s*"
        r"(mm|cm|m|mtr|metre|meter)?",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None, None

    width_number = match.group(1)
    width_unit = match.group(2)

    height_number = match.group(3)
    height_unit = match.group(4)

    # If only one unit is written, use it for both measurements.
    width_unit = width_unit or height_unit or "cm"
    height_unit = height_unit or width_unit

    metre_units = {
        "m",
        "mtr",
        "metre",
        "meter",
    }

    if width_unit.lower() in metre_units:
        width_unit = "m"

    if height_unit.lower() in metre_units:
        height_unit = "m"

    width_cm = parse_measurement(
        width_number,
        width_unit,
        output_unit="cm",
    )

    height_cm = parse_measurement(
        height_number,
        height_unit,
        output_unit="cm",
    )

    return width_cm, height_cm


def detect_thickness(text):
    """
    Detect thickness such as 3mm or thickness 3mm.
    """

    match = re.search(
        r"(?:thickness\s*[:\-]?\s*)?"
        r"(\d+(?:\.\d+)?)\s*mm\s*"
        r"(?:thick|thickness)?",
        text,
        flags=re.IGNORECASE,
    )

    if match:
        return float(match.group(1))

    return None


def detect_materials(text):
    """
    Detect one or more materials mentioned in a job.
    """

    aliases = [
        (
            "Mactac sticker",
            ["mactac", "matac"],
        ),
        (
            "Politape sticker",
            ["politape"],
        ),
        (
            "3M sticker",
            ["3m"],
        ),
        (
            "ACP",
            ["acp"],
        ),
        (
            "Aluminium",
            ["aluminium", "aluminum"],
        ),
        (
            "Acrylic",
            ["acrylic"],
        ),
        (
            "PVC",
            ["pvc"],
        ),
        (
            "Foam board",
            ["foam board", "foam"],
        ),
        (
            "Sticker",
            ["sticker", "vinyl"],
        ),
        (
            "Paper",
            ["paper"],
        ),
    ]

    lower = text.lower()
    found = []

    for name, words in aliases:
        matched = any(
            re.search(
                rf"\b{re.escape(word)}\b",
                lower,
            )
            for word in words
        )

        if not matched:
            continue

        # Avoid adding generic Sticker after a specific sticker.
        if name == "Sticker":
            specific_sticker_exists = any(
                "sticker" in item.lower()
                for item in found
            )

            if specific_sticker_exists:
                continue

        found.append(name)

    return ", ".join(found)


def detect_processes(text):
    """
    Detect processes mentioned in the requirement.
    """

    rules = [
        (
            "Latex printing",
            ["latex"],
        ),
        (
            "UV printing",
            ["uv print", "uv printing"],
        ),
        (
            "Engraving",
            ["engrave", "engraved", "engraving"],
        ),
        (
            "Cutting",
            ["cut", "cutting"],
        ),
        (
            "Lamination",
            ["laminate", "lamination"],
        ),
        (
            "Installation",
            ["install", "installation", "fixing"],
        ),
    ]

    lower = text.lower()
    found = []

    for name, words in rules:
        if any(word in lower for word in words):
            found.append(name)

    return ", ".join(found)


def detect_product(text):
    """
    Detect the finished/customer-facing product.

    Example:
    ACP + sticker + latex printing may still be sold as one signboard.
    """

    products = [
        (
            "Signboard",
            ["signboard", "sign board"],
        ),
        (
            "Sticker",
            ["sticker", "vinyl"],
        ),
        (
            "Engraved plate",
            ["engraved plate", "engraving plate"],
        ),
        (
            "Business card",
            ["business card"],
        ),
        (
            "Letterhead",
            ["letterhead"],
        ),
        (
            "Banner",
            ["banner", "flex"],
        ),
        (
            "Nameplate",
            ["nameplate", "name plate"],
        ),
    ]

    lower = text.lower()

    for product, words in products:
        if any(word in lower for word in words):
            return product

    # If unknown, keep part of the original description.
    return text[:80].strip()


def detect_components(
    text,
    product,
    materials,
    processes,
):
    """
    Suggest internal components separately from the sold product.
    """

    components = [
        part.strip()
        for part in materials.split(",")
        if part.strip()
    ]

    # For composite finished products, processes can also be internal work.
    if product == "Signboard":
        process_list = [
            part.strip()
            for part in processes.split(",")
            if part.strip()
        ]

        for process in process_list:
            if process not in components:
                components.append(process)

    return ", ".join(components)


def detect_production_method(process):
    """
    Current known business rule:
    Latex and UV printing are in-house.
    Other processes are outsourced by default.
    """

    if not process:
        return "Unknown"

    process_set = {
        part.strip()
        for part in process.split(",")
        if part.strip()
    }

    in_house_processes = {
        "Latex printing",
        "UV printing",
    }

    if process_set.issubset(in_house_processes):
        return "In-house"

    if process_set.intersection(in_house_processes):
        return "Mixed"

    return "Outsourced"


def parse_job_item(text):
    quantity, selling_unit = detect_quantity_and_unit(text)
    width_cm, height_cm = detect_size(text)

    material = detect_materials(text)
    process = detect_processes(text)
    product = detect_product(text)

    components = detect_components(
        text,
        product,
        material,
        process,
    )

    review_status = "Ready"

    if not quantity or not product:
        review_status = "Needs review"

    return {
        "description": text,
        "product_name": product,
        "quantity": quantity,
        "selling_unit": selling_unit,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "thickness_mm": detect_thickness(text),
        "material": material,
        "process": process,
        "components": components,
        "production_method": detect_production_method(process),
        "cost_price": None,
        "selling_price": None,
        "review_status": review_status,
    }


def analyse_requirement(text):
    parts = split_requirement(text)

    rows = [
        parse_job_item(part)
        for part in parts
    ]

    return pd.DataFrame(rows)


# =========================================================
# Supabase job functions
# =========================================================

def load_jobs():
    response = (
        supabase
        .table("jobs")
        .select("*")
        .order("id", desc=True)
        .execute()
    )

    return pd.DataFrame(response.data or [])


def insert_jobs(rows):
    return (
        supabase
        .table("jobs")
        .insert(rows)
        .execute()
    )


def update_job(job_id, data):
    return (
        supabase
        .table("jobs")
        .update(data)
        .eq("id", job_id)
        .execute()
    )


def delete_job(job_id):
    return (
        supabase
        .table("jobs")
        .delete()
        .eq("id", job_id)
        .execute()
    )


# =========================================================
# Tally file helper functions
# =========================================================

def read_uploaded_table(uploaded_file):
    """
    Read CSV or XLSX files.

    Excel files may contain multiple sheets.
    """

    raw = uploaded_file.getvalue()
    suffix = uploaded_file.name.lower().rsplit(".", 1)[-1]

    if suffix == "csv":
        encodings = [
            "utf-8-sig",
            "utf-8",
            "cp1252",
        ]

        last_error = None

        for encoding in encodings:
            try:
                dataframe = pd.read_csv(
                    io.BytesIO(raw),
                    encoding=encoding,
                )

                return {
                    "CSV": dataframe,
                }

            except UnicodeDecodeError as error:
                last_error = error

        raise ValueError(
            "Could not read the CSV encoding."
        ) from last_error

    workbook = pd.ExcelFile(
        io.BytesIO(raw)
    )

    sheets = {}

    for sheet_name in workbook.sheet_names:
        sheets[sheet_name] = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
        )

    return sheets


def guess_report_type(columns):
    """
    Preliminary report detection.

    This will be improved after seeing real Tally exports.
    """

    names = " ".join(
        str(column).lower()
        for column in columns
    )

    if any(
        word in names
        for word in [
            "supplier",
            "purchase",
            "purchase rate",
        ]
    ):
        return "Purchase"

    if any(
        word in names
        for word in [
            "customer",
            "sales",
            "selling rate",
        ]
    ):
        return "Sales"

    if any(
        word in names
        for word in [
            "inward",
            "outward",
            "closing quantity",
            "stock movement",
        ]
    ):
        return "Stock movement"

    return "Unknown — select manually later"


# =========================================================
# Main app
# =========================================================

st.title("Jarvis")
st.caption("Peter data collector v0.3.1")

menu = st.sidebar.radio(
    "Menu",
    [
        "Add job records",
        "Tally file preview",
        "View records",
        "Edit/Delete record",
        "Export data",
    ],
)


# =========================================================
# Add job records
# =========================================================

if menu == "Add job records":
    st.subheader("Add job records")

    st.write(
        "Enter one or several jobs. Peter will split them "
        "and let you review every row before saving."
    )

    requirement = st.text_area(
        "Requirement",
        height=130,
        placeholder=(
            "Example: 100pcs Mactac sticker 10x10cm latex print "
            "+ 2 ACP signboards 120x60cm with sticker"
        ),
    )

    if st.button(
        "Analyse requirement",
        type="primary",
    ):
        if requirement.strip():
            st.session_state.original_requirement = (
                requirement.strip()
            )

            st.session_state.detected_jobs = (
                analyse_requirement(requirement)
            )

        else:
            st.warning(
                "Enter a requirement first."
            )

    detected = st.session_state.get(
        "detected_jobs"
    )

    if (
        isinstance(detected, pd.DataFrame)
        and not detected.empty
    ):
        st.markdown(
            "#### Review detected jobs"
        )

        st.caption(
            "Correct any wrong values. Add or remove rows "
            "if Peter split the paragraph incorrectly."
        )

        edited = st.data_editor(
            detected,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "description": st.column_config.TextColumn(
                    "Detected description",
                    required=True,
                ),
                "product_name": st.column_config.TextColumn(
                    "Sold item",
                    required=True,
                ),
                "quantity": st.column_config.NumberColumn(
                    "Quantity",
                    min_value=0.0,
                ),
                "selling_unit": st.column_config.SelectboxColumn(
                    "Selling unit",
                    options=[
                        "pcs",
                        "sqm",
                        "roll",
                        "sheet",
                        "running metre",
                        "job",
                    ],
                    required=True,
                ),
                "production_method": (
                    st.column_config.SelectboxColumn(
                        "Production",
                        options=[
                            "In-house",
                            "Outsourced",
                            "Mixed",
                            "Unknown",
                        ],
                    )
                ),
                "review_status": (
                    st.column_config.SelectboxColumn(
                        "Review status",
                        options=[
                            "Ready",
                            "Needs review",
                        ],
                    )
                ),
            },
            key="job_review_editor",
        )

        customer = st.text_input(
            "Customer name (applies to all rows)"
        )

        common_notes = st.text_area(
            "Common notes optional"
        )

        if st.button(
            "Save reviewed records",
            type="primary",
        ):
            records = []

            original_requirement = (
                st.session_state.get(
                    "original_requirement",
                    requirement,
                )
            )

            for _, row in edited.iterrows():
                description = safe_text(
                    row.get("description")
                )

                product = safe_text(
                    row.get("product_name")
                )

                if not description or not product:
                    continue

                cost = safe_number(
                    row.get("cost_price")
                )

                selling = safe_number(
                    row.get("selling_price")
                )

                profit = None
                margin = None
                markup = None

                if (
                    cost is not None
                    and selling is not None
                ):
                    profit = selling - cost

                    if selling:
                        margin = (
                            profit / selling
                        ) * 100

                    if cost:
                        markup = (
                            profit / cost
                        ) * 100

                width = safe_number(
                    row.get("width_cm")
                )

                height = safe_number(
                    row.get("height_cm")
                )

                quantity = safe_number(
                    row.get("quantity")
                )

                area = None

                if width and height and quantity:
                    area = (
                        (width / 100)
                        * (height / 100)
                        * quantity
                    )

                record = {
                    "created_at": datetime.now().isoformat(),
                    "customer": safe_text(customer),
                    "requirement": description,
                    "quantity": quantity,
                    "width_cm": width,
                    "height_cm": height,
                    "thickness_mm": safe_number(
                        row.get("thickness_mm")
                    ),
                    "material": safe_text(
                        row.get("material")
                    ),
                    "process": safe_text(
                        row.get("process")
                    ),
                    "production_method": (
                        safe_text(
                            row.get("production_method")
                        )
                        or "Unknown"
                    ),
                    "vendor": None,
                    "cost_price": cost,
                    "selling_price": selling,
                    "profit": profit,
                    "margin_percent": margin,
                    "markup_percent": markup,
                    "area_sqm": area,
                    "notes": safe_text(common_notes),
                    "product_name": product,
                    "selling_unit": safe_text(
                        row.get("selling_unit")
                    ),
                    "components": safe_text(
                        row.get("components")
                    ),
                    "record_source": (
                        "Manual requirement"
                    ),
                    "parent_requirement": (
                        original_requirement
                    ),
                    "review_status": (
                        safe_text(
                            row.get("review_status")
                        )
                        or "Needs review"
                    ),
                }

                records.append(record)

            if not records:
                st.error(
                    "There are no complete rows to save."
                )

            else:
                try:
                    insert_jobs(records)

                    st.success(
                        f"Saved {len(records)} job record(s)."
                    )

                    st.session_state.pop(
                        "detected_jobs",
                        None,
                    )

                    st.session_state.pop(
                        "original_requirement",
                        None,
                    )

                except Exception as error:
                    st.error(
                        "Could not save the records. "
                        "Confirm that you ran the "
                        "v0.3 Supabase migration."
                    )

                    st.exception(error)


# =========================================================
# Tally file preview
# =========================================================

elif menu == "Tally file preview":
    st.subheader("Tally file preview")

    st.info(
        "Preview only: this version does not save Tally rows. "
        "We will map the real columns after you provide the "
        "one-month exports."
    )

    uploaded = st.file_uploader(
        "Upload a Tally CSV or Excel file",
        type=[
            "csv",
            "xlsx",
        ],
    )

    if uploaded:
        try:
            sheets = read_uploaded_table(
                uploaded
            )

            sheet_name = st.selectbox(
                "Sheet",
                list(sheets),
            )

            table = sheets[sheet_name]

            detected_report = guess_report_type(
                table.columns
            )

            st.write(
                f"Detected report type: "
                f"**{detected_report}**"
            )

            st.write(
                f"Rows: **{len(table):,}** | "
                f"Columns: **{len(table.columns)}**"
            )

            st.write(
                "Columns found:",
                [
                    str(column)
                    for column in table.columns
                ],
            )

            st.dataframe(
                table.head(100),
                use_container_width=True,
            )

            preview_csv = (
                table
                .to_csv(index=False)
                .encode("utf-8-sig")
            )

            st.download_button(
                "Download this preview as CSV",
                preview_csv,
                file_name=(
                    f"{sheet_name}_preview.csv"
                ),
                mime="text/csv",
            )

        except Exception as error:
            st.error(
                "Peter could not read this file."
            )

            st.exception(error)


# =========================================================
# View records
# =========================================================

elif menu == "View records":
    st.subheader("Saved records")

    try:
        jobs = load_jobs()

        if jobs.empty:
            st.info(
                "No records saved yet."
            )

        else:
            preferred_columns = [
                "id",
                "created_at",
                "customer",
                "product_name",
                "requirement",
                "quantity",
                "selling_unit",
                "width_cm",
                "height_cm",
                "material",
                "process",
                "components",
                "production_method",
                "cost_price",
                "selling_price",
                "record_source",
                "review_status",
            ]

            available_columns = [
                column
                for column in preferred_columns
                if column in jobs.columns
            ]

            st.dataframe(
                jobs[available_columns],
                use_container_width=True,
                hide_index=True,
            )

    except Exception as error:
        st.error(
            "Could not load records."
        )

        st.exception(error)


# =========================================================
# Edit or delete records
# =========================================================

elif menu == "Edit/Delete record":
    st.subheader("Edit or delete records")

    st.write(
        "Edit values directly inside the table. "
        "Tick Delete only for records you want to remove."
    )

    # Show a message after the page refreshes.
    edit_message = st.session_state.pop(
        "edit_records_message",
        None,
    )

    if edit_message:
        st.success(edit_message)

    try:
        jobs = load_jobs()

        if jobs.empty:
            st.info("No records saved yet.")

        else:
            editable_columns = [
                "id",
                "customer",
                "product_name",
                "requirement",
                "quantity",
                "selling_unit",
                "material",
                "process",
                "components",
                "cost_price",
                "selling_price",
                "notes",
                "review_status",
            ]

            # Add missing columns for older records.
            for column in editable_columns:
                if column not in jobs.columns:
                    jobs[column] = None

            editor_data = jobs[
                editable_columns
            ].copy()

            # Convert numeric columns properly.
            for column in [
                "quantity",
                "cost_price",
                "selling_price",
            ]:
                editor_data[column] = pd.to_numeric(
                    editor_data[column],
                    errors="coerce",
                )

            # Add deletion checkbox.
            editor_data["delete"] = False

            edited_records = st.data_editor(
                editor_data,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "id": st.column_config.NumberColumn(
                        "ID",
                        disabled=True,
                    ),
                    "customer": st.column_config.TextColumn(
                        "Customer",
                    ),
                    "product_name": st.column_config.TextColumn(
                        "Sold item",
                        required=True,
                    ),
                    "requirement": st.column_config.TextColumn(
                        "Description",
                        required=True,
                        width="large",
                    ),
                    "quantity": st.column_config.NumberColumn(
                        "Quantity",
                        min_value=0.0,
                    ),
                    "selling_unit": (
                        st.column_config.SelectboxColumn(
                            "Unit",
                            options=[
                                "pcs",
                                "sqm",
                                "roll",
                                "sheet",
                                "running metre",
                                "job",
                            ],
                        )
                    ),
                    "material": st.column_config.TextColumn(
                        "Material",
                    ),
                    "process": st.column_config.TextColumn(
                        "Process",
                    ),
                    "components": st.column_config.TextColumn(
                        "Internal components",
                        width="large",
                    ),
                    "cost_price": st.column_config.NumberColumn(
                        "Cost OMR",
                        min_value=0.0,
                        format="%.3f",
                    ),
                    "selling_price": st.column_config.NumberColumn(
                        "Selling OMR",
                        min_value=0.0,
                        format="%.3f",
                    ),
                    "notes": st.column_config.TextColumn(
                        "Notes",
                        width="large",
                    ),
                    "review_status": (
                        st.column_config.SelectboxColumn(
                            "Status",
                            options=[
                                "Ready",
                                "Needs review",
                            ],
                        )
                    ),
                    "delete": st.column_config.CheckboxColumn(
                        "Delete",
                        help=(
                            "Tick this only if you want "
                            "to permanently delete the record."
                        ),
                        default=False,
                    ),
                },
                key="records_table_editor",
            )

            delete_count = int(
                edited_records["delete"].sum()
            )

            if delete_count:
                st.warning(
                    f"{delete_count} record(s) marked "
                    f"for permanent deletion."
                )

                confirm_deletion = st.checkbox(
                    "I confirm the selected records "
                    "should be permanently deleted"
                )

            else:
                confirm_deletion = False

            if st.button(
                "Save table changes",
                type="primary",
            ):
                remaining_records = edited_records[
                    edited_records["delete"] == False
                ]

                invalid_records = remaining_records[
                    remaining_records["requirement"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .eq("")
                ]

                if not invalid_records.empty:
                    st.error(
                        "Every saved record must have "
                        "a description."
                    )

                elif (
                    delete_count > 0
                    and not confirm_deletion
                ):
                    st.error(
                        "Confirm deletion before saving."
                    )

                else:
                    updated_count = 0
                    deleted_count = 0

                    with st.spinner(
                        "Saving changes..."
                    ):
                        for _, row in (
                            edited_records.iterrows()
                        ):
                            record_id = int(row["id"])

                            if bool(row["delete"]):
                                delete_job(record_id)
                                deleted_count += 1
                                continue

                            cost_value = safe_number(
                                row.get("cost_price")
                            )

                            selling_value = safe_number(
                                row.get("selling_price")
                            )

                            profit = None
                            margin = None
                            markup = None

                            if (
                                cost_value is not None
                                and selling_value is not None
                            ):
                                profit = (
                                    selling_value
                                    - cost_value
                                )

                                if selling_value:
                                    margin = (
                                        profit
                                        / selling_value
                                        * 100
                                    )

                                if cost_value:
                                    markup = (
                                        profit
                                        / cost_value
                                        * 100
                                    )

                            update_data = {
                                "customer": safe_text(
                                    row.get("customer")
                                ),
                                "product_name": safe_text(
                                    row.get(
                                        "product_name"
                                    )
                                ),
                                "requirement": safe_text(
                                    row.get("requirement")
                                ),
                                "quantity": safe_number(
                                    row.get("quantity")
                                ),
                                "selling_unit": safe_text(
                                    row.get(
                                        "selling_unit"
                                    )
                                ),
                                "material": safe_text(
                                    row.get("material")
                                ),
                                "process": safe_text(
                                    row.get("process")
                                ),
                                "components": safe_text(
                                    row.get("components")
                                ),
                                "cost_price": cost_value,
                                "selling_price": (
                                    selling_value
                                ),
                                "profit": profit,
                                "margin_percent": margin,
                                "markup_percent": markup,
                                "notes": safe_text(
                                    row.get("notes")
                                ),
                                "review_status": (
                                    safe_text(
                                        row.get(
                                            "review_status"
                                        )
                                    )
                                    or "Needs review"
                                ),
                            }

                            update_job(
                                record_id,
                                update_data,
                            )

                            updated_count += 1

                    message = (
                        f"Updated {updated_count} "
                        f"record(s)."
                    )

                    if deleted_count:
                        message += (
                            f" Deleted {deleted_count} "
                            f"record(s)."
                        )

                    st.session_state[
                        "edit_records_message"
                    ] = message

                    st.rerun()

    except Exception as error:
        st.error(
            "Could not edit the records."
        )

        st.exception(error)

elif menu == "Export data":
    st.subheader("Export data")

    try:
        jobs = load_jobs()

        if jobs.empty:
            st.info(
                "No records available to export."
            )

        else:
            csv_data = (
                jobs
                .to_csv(index=False)
                .encode("utf-8-sig")
            )

            st.download_button(
                "Download all records as CSV",
                csv_data,
                file_name=(
                    "jarvis_job_records.csv"
                ),
                mime="text/csv",
            )

            st.dataframe(
                jobs,
                use_container_width=True,
                hide_index=True,
            )

    except Exception as error:
        st.error(
            "Could not export records."
        )

        st.exception(error)
