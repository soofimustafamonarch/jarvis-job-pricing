import re
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
# Supabase functions
# -----------------------------
def save_job(data):
    response = supabase.table("jobs").insert(data).execute()
    return response


def load_jobs():
    response = (
        supabase.table("jobs")
        .select("*")
        .order("id", desc=True)
        .execute()
    )

    if not response.data:
        return pd.DataFrame()

    return pd.DataFrame(response.data)


# -----------------------------
# Simple requirement parser
# -----------------------------
def parse_requirement(text):
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
    # 140x80mm, 140 x 80 mm, 60x40cm, 1x1mtr
    size_match = re.search(
        r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|mtr|meter|metre)?",
        lower,
    )

    if size_match:
        width = float(size_match.group(1))
        height = float(size_match.group(2))
        unit = size_match.group(3) or "cm"

        if unit == "mm":
            width_cm = width / 10
            height_cm = height / 10
        elif unit in ["m", "mtr", "meter", "metre"]:
            width_cm = width * 100
            height_cm = height * 100
        else:
            width_cm = width
            height_cm = height

        result["width_cm"] = width_cm
        result["height_cm"] = height_cm

    # Thickness examples:
    # 3mm, 3 mm, 6mm thickness
    thickness_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:thick|thickness)?", lower)
    if thickness_match:
        result["thickness_mm"] = float(thickness_match.group(1))

    # Material detection
    materials = [
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
        "business card",
        "letterhead",
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

    # Current known business rule:
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


def clean_number(value):
    if value == 0 or value == 0.0:
        return None
    return value


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


# -----------------------------
# App UI
# -----------------------------
st.title("🧠 Jarvis")
st.caption("Job Pricing Data Collector Cloud v0.1")

menu = st.sidebar.radio(
    "Menu",
    ["Add Job", "View Jobs", "Search Jobs", "Export Data"],
)


# -----------------------------
# Add Job
# -----------------------------
if menu == "Add Job":
    st.subheader("Add New Job")

    requirement = st.text_area(
        "Job requirement",
        placeholder="Example: engraved plates 4000nos 140x80mm in 3mm aluminium plates",
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
            width_cm = st.number_input(
                "Width cm",
                min_value=0.0,
                value=float(parsed["width_cm"]),
                step=0.1,
            )

        with col2:
            height_cm = st.number_input(
                "Height cm",
                min_value=0.0,
                value=float(parsed["height_cm"]),
                step=0.1,
            )

        thickness_mm = st.number_input(
            "Thickness mm",
            min_value=0.0,
            value=float(parsed["thickness_mm"]),
            step=0.1,
        )

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

        col3, col4 = st.columns(2)

        with col3:
            cost_price = st.number_input(
                "Cost price OMR",
                min_value=0.0,
                value=0.0,
                step=0.100,
                format="%.3f",
            )

        with col4:
            selling_price = st.number_input(
                "Selling price OMR",
                min_value=0.0,
                value=0.0,
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
                "customer": customer.strip() or None,
                "requirement": requirement.strip(),
                "quantity": clean_number(quantity),
                "width_cm": clean_number(width_cm),
                "height_cm": clean_number(height_cm),
                "thickness_mm": clean_number(thickness_mm),
                "material": material.strip() or None,
                "process": process.strip() or None,
                "production_method": production_method,
                "vendor": vendor.strip() or None,
                "cost_price": clean_number(cost_price),
                "selling_price": clean_number(selling_price),
                "profit": profit,
                "margin_percent": margin_percent,
                "markup_percent": markup_percent,
                "area_sqm": area_sqm,
                "notes": notes.strip() or None,
            }

            try:
                save_job(job_data)
                st.success("Job saved successfully to Supabase.")

                if profit is not None:
                    st.info(
                        f"Profit: {profit:.3f} OMR | "
                        f"Margin: {margin_percent:.2f}% | "
                        f"Markup: {markup_percent:.2f}%"
                    )

                if area_sqm is not None:
                    st.info(f"Total area: {area_sqm:.3f} sqm")

            except Exception as e:
                st.error("Could not save job to Supabase.")
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
            st.dataframe(df, use_container_width=True)

    except Exception as e:
        st.error("Could not load jobs from Supabase.")
        st.exception(e)


# -----------------------------
# Search Jobs
# -----------------------------
elif menu == "Search Jobs":
    st.subheader("Search Jobs")

    search = st.text_input("Search by customer, requirement, material, process, vendor, or notes")

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
        df = load_jobs()

        if df.empty:
            st.warning("No jobs saved yet.")
        else:
            csv_data = df.to_csv(index=False).encode("utf-8")

            st.download_button(
                label="Download CSV",
                data=csv_data,
                file_name="jarvis_jobs_export.csv",
                mime="text/csv",
            )

            st.write("Preview:")
            st.dataframe(df, use_container_width=True)

    except Exception as e:
        st.error("Could not export jobs.")
        st.exception(e)