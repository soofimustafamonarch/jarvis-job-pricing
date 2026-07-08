import re
import math
from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import create_client


# -----------------------------
# Page setup
# -----------------------------
st.set_page_config(
    page_title="Jarvis",
    page_icon="🧠",
    layout="centered",
)


# -----------------------------
# Password protection
# -----------------------------
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    st.title("🧠 Jarvis Login")
    password = st.text_input("Enter password", type="password")

    if st.button("Login"):
        if password == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("Wrong password")

    return False


if not check_password():
    st.stop()


# -----------------------------
# Supabase connection
# -----------------------------
@st.cache_resource
def get_supabase_client():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


supabase = get_supabase_client()


# -----------------------------
# Helper functions
# -----------------------------
def to_float(value, default=0.0):
    """
    Safely converts values from Supabase/Pandas into float.
    Handles None and NaN.
    """
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def clean_number(value):
    """
    Converts 0 or blank numbers into None before saving.
    """
    if value is None:
        return None

    try:
        value = float(value)
    except Exception:
        return None

    if math.isnan(value) or math.isinf(value):
        return None

    if value == 0:
        return None

    return value


def safe_text(value):
    """
    Converts empty text into None.
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    value = str(value).strip()
    return value if value else None


def sanitize_value(value):
    """
    Supabase/JSON cannot accept NaN or Infinity.
    This converts empty/invalid numeric values into None.
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None

    return value


def sanitize_dict(data):
    """
    Cleans a full dictionary before sending it to Supabase.
    """
    return {key: sanitize_value(value) for key, value in data.items()}


def parse_measurement(value, output_unit="cm", default_unit="cm"):
    """
    Converts typed measurements like:
    10cm, 100mm, 1m, 1mtr, 1.22m

    output_unit can be:
    - "cm"
    - "m"
    - "mm"

    If user types only a number, default_unit is used.
    """
    if value is None:
        return 0.0

    text = str(value).strip().lower().replace(",", ".")

    if not text:
        return 0.0

    match = re.match(
        r"^\s*(\d+(?:\.\d+)?)\s*(mm|millimeter|millimetre|cm|centimeter|centimetre|m|meter|metre|mtr)?\s*$",
        text,
    )

    if not match:
        return 0.0

    number = float(match.group(1))
    unit = match.group(2) or default_unit

    # Convert input into meters first
    if unit in ["mm", "millimeter", "millimetre"]:
        meters = number / 1000
    elif unit in ["cm", "centimeter", "centimetre"]:
        meters = number / 100
    elif unit in ["m", "meter", "metre", "mtr"]:
        meters = number
    else:
        meters = number

    # Convert meters into requested output
    if output_unit == "m":
        return meters
    elif output_unit == "cm":
        return meters * 100
    elif output_unit == "mm":
        return meters * 1000

    return meters


def format_measurement(value, unit):
    """
    Makes saved numeric values show nicely in edit screens.
    Example: 14 + cm = 14cm
    """
    try:
        if value is None or pd.isna(value):
            return ""

        value = float(value)

        if value == 0:
            return ""

        return f"{value:g}{unit}"
    except Exception:
        return ""


def calculate_profit(cost_price, selling_price):
    profit = None
    margin_percent = None
    markup_percent = None

    if selling_price > 0 and cost_price > 0:
        profit = selling_price - cost_price
        margin_percent = (profit / selling_price) * 100
        markup_percent = (profit / cost_price) * 100

    return profit, margin_percent, markup_percent


def calculate_area_sqm(width_cm, height_cm, quantity):
    if width_cm > 0 and height_cm > 0 and quantity > 0:
        single_area = (width_cm / 100) * (height_cm / 100)
        return single_area * quantity
    return None


def calculate_sticker_roll_cost(
    roll_width_m,
    roll_length_m,
    roll_cost_omr,
    vat_percent,
    extra_cost_omr,
    wastage_percent,
):
    landed_roll_cost = roll_cost_omr + (roll_cost_omr * vat_percent / 100) + extra_cost_omr
    total_area = roll_width_m * roll_length_m
    usable_area = total_area * (1 - wastage_percent / 100)

    if usable_area <= 0:
        cost_per_sqm = 0
    else:
        cost_per_sqm = landed_roll_cost / usable_area

    return landed_roll_cost, total_area, usable_area, cost_per_sqm


def parse_requirement(text):
    """
    Basic reader for requirement text.

    Examples:
    - 100pcs mactac sticker 10x10cm latex print
    - 100pcs mactac sticker 10cm x 10cm latex print
    - 100pcs mactac sticker 100mm x 100mm latex print
    - engraved plates 4000nos 140x80mm in 3mm aluminium plates
    """
    result = {
        "quantity": 0.0,
        "width_cm": 0.0,
        "height_cm": 0.0,
        "thickness_mm": 0.0,
        "material": "",
        "process": "",
        "production_method": "Outsourced",
    }

    if not text:
        return result

    lower = text.lower()

    # Quantity examples:
    # 4000nos, 4000 nos, 4000pcs, 4000 pcs, qty 4000
    qty_patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:nos|no|pcs|pc|pieces|qty)",
        r"(?:qty|quantity)\s*[:\-]?\s*(\d+(?:\.\d+)?)",
    ]

    for pattern in qty_patterns:
        match = re.search(pattern, lower)
        if match:
            result["quantity"] = float(match.group(1))
            break

    # Size examples:
    # 140x80mm
    # 10cm x 10cm
    # 100mm x 100mm
    # 1m x 1m
    size_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(mm|cm|m|mtr|meter|metre)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|mtr|meter|metre)?",
        lower,
    )

    if size_match:
        width_number = size_match.group(1)
        width_unit = size_match.group(2)

        height_number = size_match.group(3)
        height_unit = size_match.group(4)

        # If unit only written at the end, apply it to both.
        final_width_unit = width_unit or height_unit or "cm"
        final_height_unit = height_unit or width_unit or "cm"

        result["width_cm"] = parse_measurement(
            f"{width_number}{final_width_unit}",
            output_unit="cm",
            default_unit="cm",
        )

        result["height_cm"] = parse_measurement(
            f"{height_number}{final_height_unit}",
            output_unit="cm",
            default_unit="cm",
        )

    # Thickness examples:
    # 3mm, 3 mm, 6mm thickness
    thickness_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:thick|thickness)?", lower)
    if thickness_match:
        result["thickness_mm"] = float(thickness_match.group(1))

    # Material detection
    materials = [
        "business card",
        "letterhead",
        "acrylic",
        "aluminium",
        "aluminum",
        "sticker",
        "vinyl",
        "mactac",
        "matac",
        "3m",
        "acp",
        "foam",
        "pvc",
        "banner",
        "flex",
        "paper",
    ]

    for material in materials:
        if material in lower:
            if material == "aluminum":
                result["material"] = "Aluminium"
            elif material in ["matac", "mactac"]:
                result["material"] = "Mactac Sticker"
            elif material == "3m":
                result["material"] = "3M"
            else:
                result["material"] = material.title()
            break

    # Process detection
    processes = []

    if "latex" in lower:
        processes.append("Latex Printing")

    if "uv" in lower:
        processes.append("UV Printing")

    if "engrave" in lower or "engraved" in lower or "engraving" in lower:
        processes.append("Engraving")

    if "cut" in lower or "cutting" in lower:
        processes.append("Cutting")

    if "business card" in lower or "business cards" in lower:
        processes.append("Business Cards")

    if "letterhead" in lower:
        processes.append("Letterhead")

    if "install" in lower or "fixing" in lower:
        processes.append("Installation")

    if "lamination" in lower or "laminate" in lower:
        processes.append("Lamination")

    result["process"] = ", ".join(processes)

    # Current business rule:
    # In-house: Latex printing and UV printing
    # Everything else: outsourced by default
    if processes:
        in_house_parts = {"Latex Printing", "UV Printing"}
        process_set = set(processes)

        if process_set.issubset(in_house_parts):
            result["production_method"] = "In-house"
        elif process_set.intersection(in_house_parts):
            result["production_method"] = "Mixed"
        else:
            result["production_method"] = "Outsourced"

    return result


# -----------------------------
# Supabase functions - jobs
# -----------------------------
def save_job(data):
    clean_data = sanitize_dict(data)
    return supabase.table("jobs").insert(clean_data).execute()


def load_jobs():
    response = supabase.table("jobs").select("*").order("id", desc=True).execute()

    if not response.data:
        return pd.DataFrame()

    return pd.DataFrame(response.data)


def update_job(job_id, data):
    clean_data = sanitize_dict(data)
    return supabase.table("jobs").update(clean_data).eq("id", job_id).execute()


def delete_job(job_id):
    return supabase.table("jobs").delete().eq("id", job_id).execute()


# -----------------------------
# Supabase functions - sticker items
# -----------------------------
def save_sticker_item(data):
    clean_data = sanitize_dict(data)
    return supabase.table("sticker_items").insert(clean_data).execute()


def load_sticker_items():
    response = (
        supabase.table("sticker_items")
        .select("*")
        .order("id", desc=True)
        .execute()
    )

    if not response.data:
        return pd.DataFrame()

    return pd.DataFrame(response.data)


def update_sticker_item(item_id, data):
    clean_data = sanitize_dict(data)
    return supabase.table("sticker_items").update(clean_data).eq("id", item_id).execute()


def delete_sticker_item(item_id):
    return supabase.table("sticker_items").delete().eq("id", item_id).execute()


# -----------------------------
# App UI
# -----------------------------
st.title("🧠 Jarvis")
st.caption("Job Pricing Data Collector Cloud v0.2.1")

menu = st.sidebar.radio(
    "Menu",
    [
        "Add Job",
        "View Jobs",
        "Edit/Delete Jobs",
        "Sticker Item Master",
        "Edit/Delete Sticker Items",
        "Search Jobs",
        "Export Data",
    ],
)


# -----------------------------
# Add Job
# -----------------------------
if menu == "Add Job":
    st.subheader("Add New Job")

    requirement = st.text_area(
        "Job requirement",
        placeholder="Example: 100pcs mactac sticker 10cm x 10cm latex print",
        height=100,
    )

    parsed = parse_requirement(requirement)

    with st.expander("Auto-detected details", expanded=True):
        st.write(f"Quantity: **{parsed['quantity'] or 'Not detected'}**")
        st.write(
            f"Size: **{parsed['width_cm'] or 'Not detected'} x {parsed['height_cm'] or 'Not detected'} cm**"
        )
        st.write(f"Thickness: **{parsed['thickness_mm'] or 'Not detected'} mm**")
        st.write(f"Material: **{parsed['material'] or 'Not detected'}**")
        st.write(f"Process: **{parsed['process'] or 'Not detected'}**")
        st.write(f"Suggested production: **{parsed['production_method']}**")

    sticker_df = load_sticker_items()

    selected_item_id = None
    selected_item_name = None
    calculated_material_cost = None
    recommended_selling_price = None
    minimum_selling_price = None

    with st.form("job_form"):
        customer = st.text_input("Customer name optional")

        quantity = st.number_input(
            "Quantity",
            min_value=0.0,
            value=float(parsed["quantity"]),
            step=1.0,
        )

        col1, col2 = st.columns(2)

        with col1:
            width_input = st.text_input(
                "Width",
                value=format_measurement(parsed["width_cm"], "cm"),
                placeholder="Example: 10cm, 100mm, 1mtr",
            )

        with col2:
            height_input = st.text_input(
                "Height",
                value=format_measurement(parsed["height_cm"], "cm"),
                placeholder="Example: 10cm, 100mm, 1mtr",
            )

        thickness_input = st.text_input(
            "Thickness",
            value=format_measurement(parsed["thickness_mm"], "mm"),
            placeholder="Example: 3mm",
        )

        width_cm = parse_measurement(width_input, output_unit="cm", default_unit="cm")
        height_cm = parse_measurement(height_input, output_unit="cm", default_unit="cm")
        thickness_mm = parse_measurement(thickness_input, output_unit="mm", default_unit="mm")

        material = st.text_input("Material", value=parsed["material"])
        process = st.text_input("Process", value=parsed["process"])

        production_options = ["In-house", "Outsourced", "Mixed", "Unknown"]
        production_method = st.selectbox(
            "Production method",
            production_options,
            index=production_options.index(parsed["production_method"])
            if parsed["production_method"] in production_options
            else 1,
        )

        vendor = st.text_input("Vendor / supplier optional")

        st.markdown("---")
        st.markdown("### Sticker calculation optional")

        if sticker_df.empty:
            st.info("No sticker items added yet. Add one from Sticker Item Master.")
            sticker_item_choice = "None"
        else:
            sticker_options = ["None"] + [
                f"{int(row['id'])} - {row['item_name']}"
                for _, row in sticker_df.iterrows()
            ]

            sticker_item_choice = st.selectbox(
                "Select sticker item",
                sticker_options,
            )

        job_wastage_percent = st.number_input(
            "Extra job wastage %",
            min_value=0.0,
            value=0.0,
            step=1.0,
        )

        if sticker_item_choice != "None" and not sticker_df.empty:
            selected_item_id = int(sticker_item_choice.split(" - ")[0])
            item_row = sticker_df[sticker_df["id"] == selected_item_id].iloc[0]

            selected_item_name = item_row["item_name"]

            raw_area_sqm = calculate_area_sqm(width_cm, height_cm, quantity) or 0
            chargeable_area_sqm = raw_area_sqm * (1 + job_wastage_percent / 100)

            item_cost_per_sqm = to_float(item_row.get("cost_per_sqm"))
            item_sell_per_sqm = to_float(item_row.get("default_selling_per_sqm"))
            item_min_sell_per_sqm = to_float(item_row.get("minimum_selling_per_sqm"))

            calculated_material_cost = chargeable_area_sqm * item_cost_per_sqm
            recommended_selling_price = chargeable_area_sqm * item_sell_per_sqm
            minimum_selling_price = chargeable_area_sqm * item_min_sell_per_sqm

            st.info(
                f"Sticker item: {selected_item_name}\n\n"
                f"Raw area: {raw_area_sqm:.3f} sqm\n\n"
                f"Chargeable area after wastage: {chargeable_area_sqm:.3f} sqm\n\n"
                f"Material cost estimate: {calculated_material_cost:.3f} OMR\n\n"
                f"Recommended selling: {recommended_selling_price:.3f} OMR\n\n"
                f"Minimum selling: {minimum_selling_price:.3f} OMR"
            )

        st.markdown("---")

        default_cost = calculated_material_cost if calculated_material_cost else 0.0
        default_selling = recommended_selling_price if recommended_selling_price else 0.0

        col3, col4 = st.columns(2)

        with col3:
            cost_price = st.number_input(
                "Final cost price OMR",
                min_value=0.0,
                value=float(default_cost),
                step=0.100,
                format="%.3f",
            )

        with col4:
            selling_price = st.number_input(
                "Final selling price OMR",
                min_value=0.0,
                value=float(default_selling),
                step=0.100,
                format="%.3f",
            )

        notes = st.text_area(
            "Notes optional",
            placeholder="Example: difficult customer, urgent job, customer negotiated, supplier delay, etc.",
        )

        submitted = st.form_submit_button("Save Job")

    if submitted:
        if not requirement.strip():
            st.error("Job requirement is required.")
        else:
            profit, margin_percent, markup_percent = calculate_profit(
                cost_price, selling_price
            )

            area_sqm = calculate_area_sqm(width_cm, height_cm, quantity)

            job_data = {
                "created_at": datetime.now().isoformat(),
                "customer": safe_text(customer),
                "requirement": requirement.strip(),
                "quantity": clean_number(quantity),
                "width_cm": clean_number(width_cm),
                "height_cm": clean_number(height_cm),
                "thickness_mm": clean_number(thickness_mm),
                "material": safe_text(material),
                "process": safe_text(process),
                "production_method": production_method,
                "vendor": safe_text(vendor),
                "cost_price": clean_number(cost_price),
                "selling_price": clean_number(selling_price),
                "profit": profit,
                "margin_percent": margin_percent,
                "markup_percent": markup_percent,
                "area_sqm": area_sqm,
                "notes": safe_text(notes),
                "item_id": selected_item_id,
                "item_name": selected_item_name,
                "calculated_material_cost": calculated_material_cost,
                "recommended_selling_price": recommended_selling_price,
                "minimum_selling_price": minimum_selling_price,
            }

            try:
                save_job(job_data)
                st.success("Job saved successfully.")

                if profit is not None:
                    st.info(
                        f"Profit: {profit:.3f} OMR | "
                        f"Margin: {margin_percent:.2f}% | "
                        f"Markup: {markup_percent:.2f}%"
                    )

                if area_sqm is not None:
                    st.info(f"Total area: {area_sqm:.3f} sqm")

            except Exception as e:
                st.error("Could not save job.")
                st.exception(e)


# -----------------------------
# View Jobs
# -----------------------------
elif menu == "View Jobs":
    st.subheader("Saved Jobs")

    try:
        df = load_jobs()

        if df.empty:
            st.warning("No jobs saved yet.")
        else:
            display_cols = [
                "id",
                "created_at",
                "customer",
                "requirement",
                "quantity",
                "width_cm",
                "height_cm",
                "material",
                "process",
                "item_name",
                "cost_price",
                "selling_price",
                "profit",
                "margin_percent",
                "notes",
            ]

            available_cols = [col for col in display_cols if col in df.columns]
            st.dataframe(df[available_cols], use_container_width=True)

    except Exception as e:
        st.error("Could not load jobs.")
        st.exception(e)


# -----------------------------
# Edit/Delete Jobs
# -----------------------------
elif menu == "Edit/Delete Jobs":
    st.subheader("Edit or Delete Job")

    try:
        df = load_jobs()

        if df.empty:
            st.warning("No jobs saved yet.")
        else:
            job_options = [
                f"{int(row['id'])} - {row.get('customer') or 'No customer'} - {row.get('requirement')}"
                for _, row in df.iterrows()
            ]

            selected_job = st.selectbox("Select job", job_options)
            selected_job_id = int(selected_job.split(" - ")[0])
            job = df[df["id"] == selected_job_id].iloc[0]

            with st.form("edit_job_form"):
                customer = st.text_input("Customer", value=job.get("customer") or "")
                requirement = st.text_area("Requirement", value=job.get("requirement") or "", height=100)

                quantity = st.number_input(
                    "Quantity",
                    min_value=0.0,
                    value=to_float(job.get("quantity")),
                    step=1.0,
                )

                col1, col2 = st.columns(2)

                with col1:
                    width_input = st.text_input(
                        "Width",
                        value=format_measurement(job.get("width_cm"), "cm"),
                        placeholder="Example: 10cm, 100mm, 1mtr",
                    )

                with col2:
                    height_input = st.text_input(
                        "Height",
                        value=format_measurement(job.get("height_cm"), "cm"),
                        placeholder="Example: 10cm, 100mm, 1mtr",
                    )

                thickness_input = st.text_input(
                    "Thickness",
                    value=format_measurement(job.get("thickness_mm"), "mm"),
                    placeholder="Example: 3mm",
                )

                width_cm = parse_measurement(width_input, output_unit="cm", default_unit="cm")
                height_cm = parse_measurement(height_input, output_unit="cm", default_unit="cm")
                thickness_mm = parse_measurement(thickness_input, output_unit="mm", default_unit="mm")

                material = st.text_input("Material", value=job.get("material") or "")
                process = st.text_input("Process", value=job.get("process") or "")

                production_options = ["In-house", "Outsourced", "Mixed", "Unknown"]
                current_production = job.get("production_method") or "Unknown"

                production_method = st.selectbox(
                    "Production method",
                    production_options,
                    index=production_options.index(current_production)
                    if current_production in production_options
                    else 3,
                )

                vendor = st.text_input("Vendor / supplier", value=job.get("vendor") or "")

                col3, col4 = st.columns(2)

                with col3:
                    cost_price = st.number_input(
                        "Cost price OMR",
                        min_value=0.0,
                        value=to_float(job.get("cost_price")),
                        step=0.100,
                        format="%.3f",
                    )

                with col4:
                    selling_price = st.number_input(
                        "Selling price OMR",
                        min_value=0.0,
                        value=to_float(job.get("selling_price")),
                        step=0.100,
                        format="%.3f",
                    )

                notes = st.text_area("Notes", value=job.get("notes") or "")

                confirm_delete = st.checkbox("I understand: delete this job permanently")

                col_update, col_delete = st.columns(2)

                with col_update:
                    update_button = st.form_submit_button("Update Job")

                with col_delete:
                    delete_button = st.form_submit_button("Delete Job")

            if update_button:
                profit, margin_percent, markup_percent = calculate_profit(
                    cost_price, selling_price
                )
                area_sqm = calculate_area_sqm(width_cm, height_cm, quantity)

                updated_data = {
                    "customer": safe_text(customer),
                    "requirement": requirement.strip(),
                    "quantity": clean_number(quantity),
                    "width_cm": clean_number(width_cm),
                    "height_cm": clean_number(height_cm),
                    "thickness_mm": clean_number(thickness_mm),
                    "material": safe_text(material),
                    "process": safe_text(process),
                    "production_method": production_method,
                    "vendor": safe_text(vendor),
                    "cost_price": clean_number(cost_price),
                    "selling_price": clean_number(selling_price),
                    "profit": profit,
                    "margin_percent": margin_percent,
                    "markup_percent": markup_percent,
                    "area_sqm": area_sqm,
                    "notes": safe_text(notes),
                }

                update_job(selected_job_id, updated_data)
                st.success("Job updated.")
                st.rerun()

            if delete_button:
                if confirm_delete:
                    delete_job(selected_job_id)
                    st.success("Job deleted.")
                    st.rerun()
                else:
                    st.error("Tick the delete confirmation checkbox first.")

    except Exception as e:
        st.error("Could not edit/delete job.")
        st.exception(e)


# -----------------------------
# Sticker Item Master
# -----------------------------
elif menu == "Sticker Item Master":
    st.subheader("Add Sticker Item")

    st.write("Use this for sticker rolls. Jarvis will convert roll cost into cost per sqm.")

    with st.form("sticker_item_form"):
        item_name = st.text_input("Item name", placeholder="Example: Mactac White Sticker")
        brand = st.text_input("Brand", placeholder="Example: Mactac, 3M, Avery")
        material_type = st.text_input("Material type", placeholder="Example: White sticker, Transparent, Reflective")

        col1, col2 = st.columns(2)

        with col1:
            roll_width_input = st.text_input(
                "Roll width",
                value="1.22m",
                placeholder="Example: 1.22m, 122cm, 1220mm",
            )

        with col2:
            roll_length_input = st.text_input(
                "Roll length",
                value="50m",
                placeholder="Example: 50m, 5000cm",
            )

        roll_width_m = parse_measurement(roll_width_input, output_unit="m", default_unit="m")
        roll_length_m = parse_measurement(roll_length_input, output_unit="m", default_unit="m")

        col3, col4 = st.columns(2)

        with col3:
            roll_cost_omr = st.number_input(
                "Roll cost OMR before VAT",
                min_value=0.0,
                value=0.0,
                step=0.100,
                format="%.3f",
            )

        with col4:
            vat_percent = st.number_input(
                "VAT %",
                min_value=0.0,
                value=5.0,
                step=0.5,
            )

        col5, col6 = st.columns(2)

        with col5:
            extra_cost_omr = st.number_input(
                "Extra cost OMR transport etc.",
                min_value=0.0,
                value=0.0,
                step=0.100,
                format="%.3f",
            )

        with col6:
            wastage_percent = st.number_input(
                "Roll wastage %",
                min_value=0.0,
                max_value=90.0,
                value=10.0,
                step=1.0,
            )

        landed, total_area, usable_area, cost_per_sqm = calculate_sticker_roll_cost(
            roll_width_m,
            roll_length_m,
            roll_cost_omr,
            vat_percent,
            extra_cost_omr,
            wastage_percent,
        )

        st.info(
            f"Landed roll cost: {landed:.3f} OMR\n\n"
            f"Total roll area: {total_area:.3f} sqm\n\n"
            f"Usable area after wastage: {usable_area:.3f} sqm\n\n"
            f"Cost per usable sqm: {cost_per_sqm:.3f} OMR"
        )

        col7, col8 = st.columns(2)

        with col7:
            default_selling_per_sqm = st.number_input(
                "Default selling per sqm OMR",
                min_value=0.0,
                value=0.0,
                step=0.100,
                format="%.3f",
            )

        with col8:
            minimum_selling_per_sqm = st.number_input(
                "Minimum selling per sqm OMR",
                min_value=0.0,
                value=0.0,
                step=0.100,
                format="%.3f",
            )

        machine = st.selectbox("Machine", ["Latex", "UV", "Other"])
        production_method = st.selectbox("Production method", ["In-house", "Outsourced", "Mixed", "Unknown"])
        notes = st.text_area("Notes optional")

        submitted = st.form_submit_button("Save Sticker Item")

    if submitted:
        if not item_name.strip():
            st.error("Item name is required.")
        elif roll_width_m <= 0 or roll_length_m <= 0 or roll_cost_omr <= 0:
            st.error("Roll width, roll length, and roll cost must be more than 0.")
        else:
            item_data = {
                "created_at": datetime.now().isoformat(),
                "item_name": item_name.strip(),
                "category": "Sticker",
                "brand": safe_text(brand),
                "material_type": safe_text(material_type),
                "purchase_unit": "Roll",
                "roll_width_m": roll_width_m,
                "roll_length_m": roll_length_m,
                "roll_cost_omr": roll_cost_omr,
                "vat_percent": vat_percent,
                "extra_cost_omr": extra_cost_omr,
                "wastage_percent": wastage_percent,
                "landed_roll_cost_omr": landed,
                "total_roll_area_sqm": total_area,
                "usable_area_sqm": usable_area,
                "cost_per_sqm": cost_per_sqm,
                "default_selling_per_sqm": clean_number(default_selling_per_sqm),
                "minimum_selling_per_sqm": clean_number(minimum_selling_per_sqm),
                "machine": machine,
                "production_method": production_method,
                "notes": safe_text(notes),
            }

            try:
                save_sticker_item(item_data)
                st.success("Sticker item saved.")
            except Exception as e:
                st.error("Could not save sticker item.")
                st.exception(e)


# -----------------------------
# Edit/Delete Sticker Items
# -----------------------------
elif menu == "Edit/Delete Sticker Items":
    st.subheader("Edit or Delete Sticker Item")

    try:
        df = load_sticker_items()

        if df.empty:
            st.warning("No sticker items saved yet.")
        else:
            item_options = [
                f"{int(row['id'])} - {row['item_name']}"
                for _, row in df.iterrows()
            ]

            selected_item = st.selectbox("Select sticker item", item_options)
            selected_item_id = int(selected_item.split(" - ")[0])
            item = df[df["id"] == selected_item_id].iloc[0]

            with st.form("edit_sticker_item_form"):
                item_name = st.text_input("Item name", value=item.get("item_name") or "")
                brand = st.text_input("Brand", value=item.get("brand") or "")
                material_type = st.text_input("Material type", value=item.get("material_type") or "")

                col1, col2 = st.columns(2)

                with col1:
                    roll_width_input = st.text_input(
                        "Roll width",
                        value=format_measurement(item.get("roll_width_m"), "m"),
                        placeholder="Example: 1.22m, 122cm, 1220mm",
                    )

                with col2:
                    roll_length_input = st.text_input(
                        "Roll length",
                        value=format_measurement(item.get("roll_length_m"), "m"),
                        placeholder="Example: 50m, 5000cm",
                    )

                roll_width_m = parse_measurement(roll_width_input, output_unit="m", default_unit="m")
                roll_length_m = parse_measurement(roll_length_input, output_unit="m", default_unit="m")

                col3, col4 = st.columns(2)

                with col3:
                    roll_cost_omr = st.number_input(
                        "Roll cost OMR before VAT",
                        min_value=0.0,
                        value=to_float(item.get("roll_cost_omr")),
                        step=0.100,
                        format="%.3f",
                    )

                with col4:
                    vat_percent = st.number_input(
                        "VAT %",
                        min_value=0.0,
                        value=to_float(item.get("vat_percent"), 5.0),
                        step=0.5,
                    )

                col5, col6 = st.columns(2)

                with col5:
                    extra_cost_omr = st.number_input(
                        "Extra cost OMR",
                        min_value=0.0,
                        value=to_float(item.get("extra_cost_omr")),
                        step=0.100,
                        format="%.3f",
                    )

                with col6:
                    wastage_percent = st.number_input(
                        "Roll wastage %",
                        min_value=0.0,
                        max_value=90.0,
                        value=to_float(item.get("wastage_percent"), 10.0),
                        step=1.0,
                    )

                landed, total_area, usable_area, cost_per_sqm = calculate_sticker_roll_cost(
                    roll_width_m,
                    roll_length_m,
                    roll_cost_omr,
                    vat_percent,
                    extra_cost_omr,
                    wastage_percent,
                )

                st.info(
                    f"Updated landed roll cost: {landed:.3f} OMR\n\n"
                    f"Updated total area: {total_area:.3f} sqm\n\n"
                    f"Updated usable area: {usable_area:.3f} sqm\n\n"
                    f"Updated cost per usable sqm: {cost_per_sqm:.3f} OMR"
                )

                col7, col8 = st.columns(2)

                with col7:
                    default_selling_per_sqm = st.number_input(
                        "Default selling per sqm OMR",
                        min_value=0.0,
                        value=to_float(item.get("default_selling_per_sqm")),
                        step=0.100,
                        format="%.3f",
                    )

                with col8:
                    minimum_selling_per_sqm = st.number_input(
                        "Minimum selling per sqm OMR",
                        min_value=0.0,
                        value=to_float(item.get("minimum_selling_per_sqm")),
                        step=0.100,
                        format="%.3f",
                    )

                machine_options = ["Latex", "UV", "Other"]
                current_machine = item.get("machine") or "Latex"

                machine = st.selectbox(
                    "Machine",
                    machine_options,
                    index=machine_options.index(current_machine)
                    if current_machine in machine_options
                    else 0,
                )

                production_options = ["In-house", "Outsourced", "Mixed", "Unknown"]
                current_production = item.get("production_method") or "In-house"

                production_method = st.selectbox(
                    "Production method",
                    production_options,
                    index=production_options.index(current_production)
                    if current_production in production_options
                    else 0,
                )

                notes = st.text_area("Notes", value=item.get("notes") or "")

                confirm_delete = st.checkbox("I understand: delete this sticker item permanently")

                col_update, col_delete = st.columns(2)

                with col_update:
                    update_button = st.form_submit_button("Update Sticker Item")

                with col_delete:
                    delete_button = st.form_submit_button("Delete Sticker Item")

            if update_button:
                updated_data = {
                    "item_name": item_name.strip(),
                    "brand": safe_text(brand),
                    "material_type": safe_text(material_type),
                    "roll_width_m": roll_width_m,
                    "roll_length_m": roll_length_m,
                    "roll_cost_omr": roll_cost_omr,
                    "vat_percent": vat_percent,
                    "extra_cost_omr": extra_cost_omr,
                    "wastage_percent": wastage_percent,
                    "landed_roll_cost_omr": landed,
                    "total_roll_area_sqm": total_area,
                    "usable_area_sqm": usable_area,
                    "cost_per_sqm": cost_per_sqm,
                    "default_selling_per_sqm": clean_number(default_selling_per_sqm),
                    "minimum_selling_per_sqm": clean_number(minimum_selling_per_sqm),
                    "machine": machine,
                    "production_method": production_method,
                    "notes": safe_text(notes),
                }

                update_sticker_item(selected_item_id, updated_data)
                st.success("Sticker item updated.")
                st.rerun()

            if delete_button:
                if confirm_delete:
                    delete_sticker_item(selected_item_id)
                    st.success("Sticker item deleted.")
                    st.rerun()
                else:
                    st.error("Tick the delete confirmation checkbox first.")

    except Exception as e:
        st.error("Could not edit/delete sticker item.")
        st.exception(e)


# -----------------------------
# Search Jobs
# -----------------------------
elif menu == "Search Jobs":
    st.subheader("Search Jobs")

    search = st.text_input("Search by customer, requirement, material, process, vendor, item, or notes")

    try:
        df = load_jobs()

        if df.empty:
            st.warning("No jobs saved yet.")
        elif search.strip():
            search_lower = search.lower()

            filtered = df[
                df.apply(
                    lambda row: search_lower
                    in " ".join([str(value).lower() for value in row.values]),
                    axis=1,
                )
            ]

            st.write(f"Found {len(filtered)} result(s).")
            st.dataframe(filtered, use_container_width=True)
        else:
            st.info("Type something to search.")

    except Exception as e:
        st.error("Could not search jobs.")
        st.exception(e)


# -----------------------------
# Export Data
# -----------------------------
elif menu == "Export Data":
    st.subheader("Export Data")

    try:
        jobs_df = load_jobs()
        sticker_df = load_sticker_items()

        if jobs_df.empty:
            st.warning("No jobs saved yet.")
        else:
            jobs_csv = jobs_df.to_csv(index=False).encode("utf-8")

            st.download_button(
                label="Download Jobs CSV",
                data=jobs_csv,
                file_name="jarvis_jobs_export.csv",
                mime="text/csv",
            )

            st.write("Jobs preview:")
            st.dataframe(jobs_df, use_container_width=True)

        st.markdown("---")

        if sticker_df.empty:
            st.warning("No sticker items saved yet.")
        else:
            sticker_csv = sticker_df.to_csv(index=False).encode("utf-8")

            st.download_button(
                label="Download Sticker Items CSV",
                data=sticker_csv,
                file_name="jarvis_sticker_items_export.csv",
                mime="text/csv",
            )

            st.write("Sticker items preview:")
            st.dataframe(sticker_df, use_container_width=True)

    except Exception as e:
        st.error("Could not export data.")
        st.exception(e)