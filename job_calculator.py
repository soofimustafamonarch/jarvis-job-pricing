import re
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from tally_importer import _stock_items_with_rates, load_all, optional_float, optional_text


SOLD_ITEM_COLUMNS = ["description", "quantity", "unit"]
COMPONENT_COLUMNS = [
    "component_type",
    "description",
    "quantity",
    "unit",
    "unit_rate",
    "yield_per_purchase_unit",
    "wastage_percent",
    "line_cost",
    "rate_source",
    "tally_item_guid",
    "notes",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def blank_sold_items():
    return pd.DataFrame(columns=SOLD_ITEM_COLUMNS)


def blank_components():
    return pd.DataFrame(columns=COMPONENT_COLUMNS)


def _number(value, default=0.0):
    value = optional_float(value)
    return default if value is None else value


def _clean_dataframe(dataframe, columns):
    frame = dataframe.copy() if isinstance(dataframe, pd.DataFrame) else pd.DataFrame()
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    return frame[columns]


def analyse_sold_items(requirement):
    text = (requirement or "").strip()
    if not text:
        return blank_sold_items()

    parts = [
        part.strip(" -\t")
        for part in re.split(r"\s*(?:\+|;|\r?\n)\s*", text)
        if part.strip(" -\t")
    ]
    rows = []
    unit_aliases = {
        "pcs": "pcs",
        "pc": "pcs",
        "nos": "pcs",
        "no": "pcs",
        "pieces": "pcs",
        "sqm": "sqm",
        "m2": "sqm",
        "roll": "roll",
        "rolls": "roll",
        "sheet": "sheet",
        "sheets": "sheet",
        "job": "job",
        "jobs": "job",
    }
    quantity_pattern = re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(pcs|pc|nos|no|pieces|sqm|m2|rolls?|sheets?|jobs?)\b",
        flags=re.IGNORECASE,
    )
    for part in parts:
        match = quantity_pattern.search(part)
        if match:
            quantity = float(match.group(1))
            unit = unit_aliases.get(match.group(2).lower(), "job")
            description = f"{part[:match.start()]} {part[match.end():]}".strip()
            description = re.sub(r"\s+", " ", description)
        else:
            leading_quantity = re.match(
                r"^\s*(\d+(?:\.\d+)?)\s+(?=[A-Za-z])", part
            )
            quantity = float(leading_quantity.group(1)) if leading_quantity else 1.0
            unit = "pcs" if leading_quantity else "job"
            description = (
                part[leading_quantity.end():].strip() if leading_quantity else part
            )
        rows.append({"description": description, "quantity": quantity, "unit": unit})
    return pd.DataFrame(rows, columns=SOLD_ITEM_COLUMNS)


def calculate_component_costs(dataframe):
    frame = _clean_dataframe(dataframe, COMPONENT_COLUMNS)
    costs = []
    for _, row in frame.iterrows():
        quantity = _number(row.get("quantity"))
        unit_rate = _number(row.get("unit_rate"))
        item_yield = _number(row.get("yield_per_purchase_unit"), 1.0)
        wastage = _number(row.get("wastage_percent"))
        if item_yield <= 0:
            item_yield = 1.0
        cost = (quantity / item_yield) * unit_rate * (1 + wastage / 100)
        costs.append(round(cost, 6))
    frame["line_cost"] = costs
    return frame


def calculate_prices(total_cost, target_margin, minimum_margin, chosen_quote, vat_percent):
    if not 0 <= target_margin < 100:
        raise ValueError("Target margin must be between 0% and 99.99%.")
    if not 0 <= minimum_margin < 100:
        raise ValueError("Minimum margin must be between 0% and 99.99%.")
    recommended = total_cost / (1 - target_margin / 100) if total_cost else 0.0
    minimum = total_cost / (1 - minimum_margin / 100) if total_cost else 0.0
    quote_before_vat = recommended if chosen_quote is None else chosen_quote
    vat_amount = quote_before_vat * vat_percent / 100
    total_with_vat = quote_before_vat + vat_amount
    profit = quote_before_vat - total_cost
    actual_margin = (profit / quote_before_vat * 100) if quote_before_vat else 0.0
    return {
        "recommended": recommended,
        "minimum": minimum,
        "quote_before_vat": quote_before_vat,
        "vat_amount": vat_amount,
        "total_with_vat": total_with_vat,
        "profit": profit,
        "actual_margin": actual_margin,
    }


def build_client_message(
    customer,
    sold_items,
    quote_before_vat,
    vat_percent,
    vat_amount,
    total_with_vat,
    delivery_time,
    validity_days,
    payment_terms,
):
    greeting = f"Dear {customer.strip()}," if (customer or "").strip() else "Dear Sir/Madam,"
    lines = [greeting, "", "Thank you for your enquiry. Please find our quotation below:", ""]
    for _, row in sold_items.iterrows():
        description = optional_text(row.get("description"))
        if not description:
            continue
        quantity = _number(row.get("quantity"), 1.0)
        unit = optional_text(row.get("unit")) or "job"
        quantity_text = f"{quantity:g}"
        lines.append(f"• {quantity_text} {unit} – {description}")
    lines.extend(
        [
            "",
            f"Price before VAT: OMR {quote_before_vat:,.3f}",
            f"VAT ({vat_percent:g}%): OMR {vat_amount:,.3f}",
            f"Total: OMR {total_with_vat:,.3f}",
        ]
    )
    if (delivery_time or "").strip():
        lines.append(f"Delivery: {delivery_time.strip()}")
    if validity_days:
        lines.append(f"Quotation validity: {int(validity_days)} days")
    if (payment_terms or "").strip():
        lines.append(f"Payment terms: {payment_terms.strip()}")
    lines.extend(["", "Please let us know if you would like us to proceed.", "", "Regards,", "Ocean Prints"])
    return "\n".join(lines)


def render_price_summary(total_cost, prices):
    cards = [
        ("Internal cost", total_cost),
        ("Recommended", prices["recommended"]),
        ("Minimum", prices["minimum"]),
        ("Total with VAT", prices["total_with_vat"]),
    ]
    card_html = "".join(
        (
            '<div class="jarvis-price-card">'
            f'<div class="jarvis-price-label">{label}</div>'
            f'<div class="jarvis-price-value">OMR {value:,.3f}</div>'
            "</div>"
        )
        for label, value in cards
    )
    st.markdown(
        f"""
        <style>
        .jarvis-price-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.6rem;
            margin: 0.4rem 0 0.8rem 0;
        }}
        .jarvis-price-card {{
            border: 1px solid rgba(128, 128, 128, 0.28);
            border-radius: 0.55rem;
            padding: 0.65rem 0.75rem;
            min-width: 0;
        }}
        .jarvis-price-label {{
            font-size: 0.78rem;
            opacity: 0.75;
            margin-bottom: 0.2rem;
        }}
        .jarvis-price-value {{
            font-size: 1.15rem;
            font-weight: 650;
            line-height: 1.25;
            white-space: nowrap;
        }}
        @media (max-width: 480px) {{
            .jarvis-price-value {{ font-size: 1rem; }}
        }}
        </style>
        <div class="jarvis-price-grid">{card_html}</div>
        """,
        unsafe_allow_html=True,
    )


def load_stock_rate_library(supabase):
    stock_items = pd.DataFrame(load_all(supabase, "tally_stock_items", "item_name"))
    vouchers = pd.DataFrame(load_all(supabase, "tally_vouchers", "voucher_date", desc=True))
    voucher_lines = pd.DataFrame(load_all(supabase, "tally_voucher_lines", "id", desc=True))
    movements = pd.DataFrame(load_all(supabase, "tally_stock_movement", "id", desc=True))
    if stock_items.empty:
        return stock_items
    if vouchers.empty or voucher_lines.empty:
        joined_lines = pd.DataFrame()
    else:
        voucher_columns = [
            "voucher_key",
            "report_type",
            "voucher_date",
            "party_name",
            "is_active",
        ]
        joined_lines = voucher_lines.merge(
            vouchers[[column for column in voucher_columns if column in vouchers.columns]],
            on="voucher_key",
            how="left",
        )
    return _stock_items_with_rates(stock_items, joined_lines, movements)


def _initial_state():
    defaults = {
        "calculator_customer": "",
        "calculator_requirement": "",
        "calculator_sold_items": blank_sold_items(),
        "calculator_components": blank_components(),
        "calculator_target_margin": 30.0,
        "calculator_minimum_margin": 20.0,
        "calculator_vat": 5.0,
        "calculator_final_quote": "",
        "calculator_delivery": "",
        "calculator_validity": 14,
        "calculator_payment_terms": "",
        "calculator_notes": "",
        "calculator_client_message": "",
        "calculator_id": None,
        "calculator_editor_version": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def clear_calculator():
    keys = [key for key in st.session_state if key.startswith("calculator_")]
    for key in keys:
        st.session_state.pop(key, None)
    _initial_state()


def load_saved_calculations(supabase):
    response = (
        supabase.table("jarvis_calculations")
        .select("*")
        .order("id", desc=True)
        .limit(100)
        .execute()
    )
    return response.data or []


def load_calculation_record(supabase, calculation_id):
    response = (
        supabase.table("jarvis_calculations")
        .select("*")
        .eq("id", calculation_id)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def load_calculation_children(supabase, calculation_id):
    item_response = (
        supabase.table("jarvis_calculation_items")
        .select("description,quantity,unit")
        .eq("calculation_id", calculation_id)
        .order("line_number")
        .execute()
    )
    component_response = (
        supabase.table("jarvis_calculation_components")
        .select(
            "component_type,description,quantity,unit,unit_rate,yield_per_purchase_unit,"
            "wastage_percent,line_cost,rate_source,tally_item_guid,notes"
        )
        .eq("calculation_id", calculation_id)
        .order("line_number")
        .execute()
    )
    return (
        pd.DataFrame(item_response.data or [], columns=SOLD_ITEM_COLUMNS),
        pd.DataFrame(component_response.data or [], columns=COMPONENT_COLUMNS),
    )


def save_calculation(supabase, calculation_id, header, sold_items, components):
    if calculation_id is None:
        response = supabase.table("jarvis_calculations").insert(header).execute()
        calculation_id = response.data[0]["id"]
    else:
        header["updated_at"] = now_iso()
        supabase.table("jarvis_calculations").update(header).eq("id", calculation_id).execute()
        supabase.table("jarvis_calculation_items").delete().eq(
            "calculation_id", calculation_id
        ).execute()
        supabase.table("jarvis_calculation_components").delete().eq(
            "calculation_id", calculation_id
        ).execute()

    item_rows = []
    for _, row in sold_items.iterrows():
        description = optional_text(row.get("description"))
        if not description:
            continue
        item_rows.append(
            {
                "calculation_id": calculation_id,
                "line_number": len(item_rows) + 1,
                "description": description,
                "quantity": optional_float(row.get("quantity")),
                "unit": optional_text(row.get("unit")),
            }
        )
    if item_rows:
        supabase.table("jarvis_calculation_items").insert(item_rows).execute()

    component_rows = []
    for _, row in components.iterrows():
        description = optional_text(row.get("description"))
        if not description:
            continue
        component_rows.append(
            {
                "calculation_id": calculation_id,
                "line_number": len(component_rows) + 1,
                "component_type": optional_text(row.get("component_type")) or "Other",
                "description": description,
                "quantity": optional_float(row.get("quantity")),
                "unit": optional_text(row.get("unit")),
                "unit_rate": optional_float(row.get("unit_rate")),
                "yield_per_purchase_unit": optional_float(
                    row.get("yield_per_purchase_unit")
                ),
                "wastage_percent": optional_float(row.get("wastage_percent")),
                "line_cost": optional_float(row.get("line_cost")),
                "rate_source": optional_text(row.get("rate_source")),
                "tally_item_guid": optional_text(row.get("tally_item_guid")),
                "notes": optional_text(row.get("notes")),
            }
        )
    if component_rows:
        supabase.table("jarvis_calculation_components").insert(component_rows).execute()
    return calculation_id


def load_into_calculator(supabase, record):
    sold_items, components = load_calculation_children(supabase, record["id"])
    st.session_state["calculator_id"] = record["id"]
    st.session_state["calculator_customer"] = record.get("customer") or ""
    st.session_state["calculator_requirement"] = record.get("requirement") or ""
    st.session_state["calculator_sold_items"] = sold_items
    st.session_state["calculator_components"] = components
    st.session_state["calculator_target_margin"] = _number(
        record.get("target_margin_percent"), 30.0
    )
    st.session_state["calculator_minimum_margin"] = _number(
        record.get("minimum_margin_percent"), 20.0
    )
    st.session_state["calculator_vat"] = _number(record.get("vat_percent"), 5.0)
    st.session_state["calculator_final_quote"] = (
        "" if record.get("quote_before_vat") is None else str(record["quote_before_vat"])
    )
    st.session_state["calculator_delivery"] = record.get("delivery_time") or ""
    st.session_state["calculator_validity"] = int(record.get("validity_days") or 14)
    st.session_state["calculator_payment_terms"] = record.get("payment_terms") or ""
    st.session_state["calculator_notes"] = record.get("notes") or ""
    st.session_state["calculator_client_message"] = record.get("client_message") or ""
    st.session_state["calculator_editor_version"] += 1


def render_job_calculator_page(supabase):
    _initial_state()
    pending_load_id = st.session_state.pop("calculator_pending_load_id", None)
    if pending_load_id is not None:
        pending_record = load_calculation_record(supabase, pending_load_id)
        if pending_record:
            load_into_calculator(supabase, pending_record)
    st.subheader("Job Calculator")
    st.caption("Build the internal cost first, then create a customer-safe quotation message.")

    new_tab, saved_tab = st.tabs(["Calculate job", "Saved calculations"])

    with new_tab:
        calculation_id = st.session_state.get("calculator_id")
        if calculation_id:
            st.info(f"Editing saved calculation #{calculation_id}")
        col_clear, _ = st.columns([1, 3])
        with col_clear:
            if st.button("Start new calculation"):
                clear_calculator()
                st.rerun()

        customer = st.text_input("Customer", key="calculator_customer")
        requirement = st.text_area(
            "Requirement",
            height=110,
            placeholder=(
                "Example: 100pcs Mactac stickers 10x10cm + "
                "2 ACP signboards 120x60cm with sticker"
            ),
            key="calculator_requirement",
        )
        if st.button("Detect customer-facing items", type="primary"):
            detected = analyse_sold_items(requirement)
            if detected.empty:
                st.warning("Enter a requirement first.")
            else:
                st.session_state["calculator_sold_items"] = detected
                st.session_state["calculator_editor_version"] += 1
                st.rerun()

        st.markdown("#### What the customer is buying")
        sold_items = st.data_editor(
            _clean_dataframe(st.session_state["calculator_sold_items"], SOLD_ITEM_COLUMNS),
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            column_config={
                "description": st.column_config.TextColumn("Sold item", required=True),
                "quantity": st.column_config.NumberColumn(
                    "Quantity", min_value=0.0, format="%.3f"
                ),
                "unit": st.column_config.SelectboxColumn(
                    "Selling unit",
                    options=["pcs", "sqm", "roll", "sheet", "running metre", "job"],
                ),
            },
            key=f"calculator_sold_editor_{st.session_state['calculator_editor_version']}",
        )
        st.session_state["calculator_sold_items"] = sold_items

        st.markdown("#### Internal cost components")
        st.caption(
            "Add each real cost used for the job—for example material, printing, labour, "
            "outsourcing, or transport."
        )
        components = calculate_component_costs(st.session_state["calculator_components"])
        components["description"] = components["description"].fillna("")
        edited_components = st.data_editor(
            components,
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            height=440,
            row_height=44,
            disabled=["line_cost", "rate_source", "tally_item_guid"],
            column_config={
                "component_type": st.column_config.SelectboxColumn(
                    "Type",
                    options=["Material", "Outsource", "Manpower", "Transport", "Other"],
                    width="small",
                ),
                "description": st.column_config.TextColumn(
                    "Component", required=True, width="medium"
                ),
                "quantity": st.column_config.NumberColumn(
                    "Quantity", min_value=0.0, format="%.3f", width="small"
                ),
                "unit": None,
                "unit_rate": st.column_config.NumberColumn(
                    "Rate (OMR)", min_value=0.0, format="%.3f", width="small"
                ),
                "yield_per_purchase_unit": None,
                "wastage_percent": None,
                "line_cost": st.column_config.NumberColumn(
                    "Total (OMR)", format="%.3f", width="small"
                ),
                "rate_source": None,
                "tally_item_guid": None,
                "notes": None,
            },
            key=f"calculator_component_editor_{st.session_state['calculator_editor_version']}",
        )
        edited_components = calculate_component_costs(edited_components)
        st.session_state["calculator_components"] = edited_components

        add_col1, add_col2 = st.columns(2)
        with add_col1:
            if st.button("+ Add cost"):
                new_row = pd.DataFrame(
                    [
                        {
                            "component_type": "Material",
                            "description": "",
                            "quantity": 1.0,
                            "unit": "pcs",
                            "unit_rate": 0.0,
                            "yield_per_purchase_unit": 1.0,
                            "wastage_percent": 0.0,
                            "line_cost": 0.0,
                            "rate_source": "Manual",
                            "tally_item_guid": None,
                            "notes": "",
                        }
                    ]
                )
                st.session_state["calculator_components"] = pd.concat(
                    [edited_components, new_row], ignore_index=True
                )
                st.session_state["calculator_editor_version"] += 1
                st.rerun()

        try:
            stock_rates = load_stock_rate_library(supabase)
        except Exception:
            stock_rates = pd.DataFrame()

        with st.expander("Use a Tally material rate (optional)"):
            if stock_rates.empty:
                st.info("No Tally stock items are available.")
            else:
                rate_options = []
                rate_rows = {}
                for _, row in stock_rates.iterrows():
                    rate = optional_float(row.get("reference_cost_rate"))
                    rate_text = f"OMR {rate:.3f}" if rate is not None else "no rate"
                    label = f"{row['item_name']} — {rate_text}"
                    rate_options.append(label)
                    rate_rows[label] = row
                selected_rate_item = st.selectbox("Tally stock item", rate_options)
                selected_tally_row = rate_rows[selected_rate_item]
                selected_rate = optional_float(
                    selected_tally_row.get("reference_cost_rate")
                )
                tally_usage_quantity = st.number_input(
                    "Quantity needed", min_value=0.0, value=1.0, step=1.0
                )
                different_units = st.checkbox(
                    "Convert a roll/sheet into sqm or pieces",
                    help="Use this only when the purchase unit and usage unit are different.",
                )
                if different_units:
                    tally_usage_unit = st.text_input(
                        "Usage unit", value="sqm", placeholder="sqm or pcs"
                    )
                    tally_yield = st.number_input(
                        "Usable quantity from one roll/sheet",
                        min_value=0.000001,
                        value=1.0,
                        step=1.0,
                    )
                    tally_wastage = st.number_input(
                        "Wastage %", min_value=0.0, value=0.0, step=1.0
                    )
                else:
                    tally_usage_unit = (
                        optional_text(selected_tally_row.get("base_unit")) or "unit"
                    )
                    tally_yield = 1.0
                    tally_wastage = 0.0

                preview_rate = selected_rate or 0.0
                preview_cost = (
                    tally_usage_quantity / tally_yield * preview_rate
                    * (1 + tally_wastage / 100)
                )
                if selected_rate is None:
                    st.warning("No rate is available for this item. Add it, then enter the rate manually.")
                else:
                    st.info(f"Estimated material cost: OMR {preview_cost:,.3f}")
                if st.button("Add selected Tally material"):
                    row = selected_tally_row
                    rate = selected_rate or 0.0
                    new_row = pd.DataFrame(
                        [
                            {
                                "component_type": "Material",
                                "description": row.get("item_name"),
                                "quantity": tally_usage_quantity,
                                "unit": tally_usage_unit,
                                "unit_rate": rate,
                                "yield_per_purchase_unit": tally_yield,
                                "wastage_percent": tally_wastage,
                                "line_cost": 0.0,
                                "rate_source": row.get("rate_source") or "Tally (no rate)",
                                "tally_item_guid": row.get("tally_guid"),
                                "notes": "",
                            }
                        ]
                    )
                    st.session_state["calculator_components"] = calculate_component_costs(
                        pd.concat([edited_components, new_row], ignore_index=True)
                    )
                    st.session_state["calculator_editor_version"] += 1
                    st.rerun()

        final_components = calculate_component_costs(
            st.session_state["calculator_components"]
        )
        total_cost = float(pd.to_numeric(final_components["line_cost"], errors="coerce").fillna(0).sum())

        st.markdown("#### Pricing")
        price_col1, price_col2, price_col3 = st.columns(3)
        with price_col1:
            target_margin = st.number_input(
                "Target margin %", min_value=0.0, max_value=99.99, key="calculator_target_margin"
            )
        with price_col2:
            minimum_margin = st.number_input(
                "Minimum margin %", min_value=0.0, max_value=99.99, key="calculator_minimum_margin"
            )
        with price_col3:
            vat_percent = st.number_input(
                "VAT %", min_value=0.0, max_value=100.0, key="calculator_vat"
            )

        chosen_quote_text = st.text_input(
            "Final quote before VAT (leave blank to use recommended)",
            key="calculator_final_quote",
        )
        try:
            chosen_quote = optional_float(chosen_quote_text)
            prices = calculate_prices(
                total_cost,
                target_margin,
                minimum_margin,
                chosen_quote,
                vat_percent,
            )
        except ValueError as error:
            st.error(str(error))
            return

        render_price_summary(total_cost, prices)
        sold_units = {
            optional_text(value)
            for value in sold_items.get("unit", pd.Series(dtype=object)).tolist()
            if optional_text(value)
        }
        sold_quantity_total = pd.to_numeric(
            sold_items.get("quantity", pd.Series(dtype=float)), errors="coerce"
        ).fillna(0).sum()
        price_per_unit = None
        price_unit = None
        if len(sold_units) == 1 and sold_quantity_total > 0:
            price_unit = next(iter(sold_units))
            price_per_unit = prices["quote_before_vat"] / sold_quantity_total
            st.info(f"Quote per {price_unit}: OMR {price_per_unit:,.3f} before VAT")
        st.caption(
            f"Profit: OMR {prices['profit']:,.3f} | "
            f"Actual margin: {prices['actual_margin']:.2f}% | "
            f"VAT: OMR {prices['vat_amount']:,.3f}"
        )
        if prices["quote_before_vat"] < prices["minimum"]:
            st.error("The final quote is below the calculated minimum price.")

        st.markdown("#### Client Message")
        message_col1, message_col2 = st.columns(2)
        with message_col1:
            delivery_time = st.text_input(
                "Delivery time", placeholder="Example: 7–10 working days", key="calculator_delivery"
            )
            validity_days = st.number_input(
                "Validity days", min_value=0, step=1, key="calculator_validity"
            )
        with message_col2:
            payment_terms = st.text_input(
                "Payment terms", placeholder="Example: 50% advance", key="calculator_payment_terms"
            )
            notes = st.text_input("Internal notes", key="calculator_notes")

        if st.button("Generate client message"):
            st.session_state["calculator_client_message"] = build_client_message(
                customer,
                sold_items,
                prices["quote_before_vat"],
                vat_percent,
                prices["vat_amount"],
                prices["total_with_vat"],
                delivery_time,
                validity_days,
                payment_terms,
            )

        client_message = st.text_area(
            "Edit and copy this message",
            height=300,
            key="calculator_client_message",
        )
        if client_message:
            st.caption("Copy-ready version")
            st.code(client_message, language=None)

        if st.button("Save calculation", type="primary"):
            if sold_items.empty:
                st.error("Add at least one customer-facing item.")
            elif final_components.empty:
                st.error("Add at least one internal cost component.")
            else:
                header = {
                    "customer": optional_text(customer),
                    "requirement": optional_text(requirement),
                    "target_margin_percent": target_margin,
                    "minimum_margin_percent": minimum_margin,
                    "vat_percent": vat_percent,
                    "total_cost": total_cost,
                    "recommended_price": prices["recommended"],
                    "minimum_price": prices["minimum"],
                    "quote_before_vat": prices["quote_before_vat"],
                    "vat_amount": prices["vat_amount"],
                    "total_with_vat": prices["total_with_vat"],
                    "profit": prices["profit"],
                    "actual_margin_percent": prices["actual_margin"],
                    "price_per_unit": price_per_unit,
                    "price_unit": price_unit,
                    "delivery_time": optional_text(delivery_time),
                    "validity_days": int(validity_days),
                    "payment_terms": optional_text(payment_terms),
                    "client_message": optional_text(client_message),
                    "notes": optional_text(notes),
                    "status": "Draft",
                }
                try:
                    saved_id = save_calculation(
                        supabase,
                        calculation_id,
                        header,
                        sold_items,
                        final_components,
                    )
                    st.session_state["calculator_id"] = saved_id
                    st.success(f"Calculation #{saved_id} saved.")
                except Exception as error:
                    st.error("Could not save. Run the Jarvis v0.6 Supabase migration first.")
                    st.exception(error)

    with saved_tab:
        st.markdown("#### Saved calculations")
        try:
            records = load_saved_calculations(supabase)
            if not records:
                st.info("No calculations saved yet.")
            else:
                summary_columns = [
                    "id",
                    "created_at",
                    "customer",
                    "requirement",
                    "total_cost",
                    "quote_before_vat",
                    "total_with_vat",
                    "status",
                ]
                st.dataframe(
                    pd.DataFrame(records)[summary_columns],
                    use_container_width=True,
                    hide_index=True,
                )
                options = [
                    f"{record['id']} - {record.get('customer') or 'No customer'} - "
                    f"{(record.get('requirement') or 'Calculation')[:60]}"
                    for record in records
                ]
                selected = st.selectbox("Select calculation", options)
                selected_id = int(selected.split(" - ", 1)[0])
                record = next(row for row in records if row["id"] == selected_id)
                if st.button("Open selected calculation"):
                    st.session_state["calculator_pending_load_id"] = record["id"]
                    st.rerun()
        except Exception as error:
            st.info("Run the Jarvis v0.6 Supabase migration to save calculations.")
