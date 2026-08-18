from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from tally_parser import parse_tally_upload


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def optional_value(value):
    """Convert Pandas blank/NaN values into JSON-safe None."""
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
    return float(value)


def chunked(rows, size=200):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def load_all(supabase, table_name, order_column=None, desc=False):
    query = supabase.table(table_name).select("*")
    if order_column:
        query = query.order(order_column, desc=desc)
    response = query.execute()
    return response.data or []


def load_stock_items(supabase):
    return load_all(supabase, "tally_stock_items", "item_name")


def existing_import(supabase, digest):
    response = (
        supabase.table("tally_imports")
        .select("id,file_name,report_type,imported_at,status")
        .eq("file_hash", digest)
        .limit(1)
        .execute()
    )
    return response.data[0] if response.data else None


def create_import(supabase, parsed):
    payload = {
        "file_name": parsed["file_name"],
        "file_hash": parsed["file_hash"],
        "report_type": parsed["report_type"],
        "period_start": parsed.get("period_start"),
        "period_end": parsed.get("period_end"),
        "record_count": parsed.get("record_count", 0),
        "status": "processing",
    }
    response = supabase.table("tally_imports").insert(payload).execute()
    return response.data[0]["id"]


def sync_inventory_master(supabase, parsed, import_id):
    timestamp = now_iso()
    rows = []
    for item in parsed["stock_items"]:
        row = dict(item)
        row["last_synced_at"] = timestamp
        rows.append(row)
    for group in chunked(rows):
        (
            supabase.table("tally_stock_items")
            .upsert(group, on_conflict="tally_guid")
            .execute()
        )
    return {"stock_items": len(rows), "import_id": import_id}


def sync_voucher_report(supabase, parsed, import_id, complete_period=False):
    timestamp = now_iso()
    voucher_rows = []
    for voucher in parsed["vouchers"]:
        row = dict(voucher)
        row["last_import_id"] = import_id
        row["last_synced_at"] = timestamp
        voucher_rows.append(row)

    for group in chunked(voucher_rows):
        (
            supabase.table("tally_vouchers")
            .upsert(group, on_conflict="voucher_key")
            .execute()
        )

    inactive_count = 0
    if complete_period and parsed.get("period_start") and parsed.get("period_end"):
        existing_response = (
            supabase.table("tally_vouchers")
            .select("voucher_key")
            .eq("report_type", parsed["report_type"])
            .gte("voucher_date", parsed["period_start"])
            .lte("voucher_date", parsed["period_end"])
            .execute()
        )
        current_keys = {voucher["voucher_key"] for voucher in voucher_rows}
        missing_keys = [
            row["voucher_key"]
            for row in (existing_response.data or [])
            if row["voucher_key"] not in current_keys
        ]
        for voucher_key in missing_keys:
            (
                supabase.table("tally_vouchers")
                .update({"is_active": False, "last_synced_at": timestamp})
                .eq("voucher_key", voucher_key)
                .execute()
            )
            (
                supabase.table("tally_voucher_lines")
                .update({"review_status": "Inactive"})
                .eq("voucher_key", voucher_key)
                .execute()
            )
        inactive_count = len(missing_keys)

    lines_by_voucher = {}
    for line in parsed["voucher_lines"]:
        lines_by_voucher.setdefault(line["voucher_key"], []).append(dict(line))

    for voucher in voucher_rows:
        voucher_key = voucher["voucher_key"]
        (
            supabase.table("tally_voucher_lines")
            .delete()
            .eq("voucher_key", voucher_key)
            .execute()
        )
        lines = lines_by_voucher.get(voucher_key, [])
        if lines:
            for group in chunked(lines):
                supabase.table("tally_voucher_lines").insert(group).execute()

    return {
        "vouchers": len(voucher_rows),
        "voucher_lines": len(parsed["voucher_lines"]),
        "vouchers_marked_inactive": inactive_count,
        "import_id": import_id,
    }


def sync_stock_movement(supabase, parsed, import_id, complete_period=False):
    timestamp = now_iso()
    rows = []
    for movement in parsed["stock_movements"]:
        row = dict(movement)
        row["last_import_id"] = import_id
        row["last_synced_at"] = timestamp
        row["is_active"] = True
        rows.append(row)
    for group in chunked(rows):
        (
            supabase.table("tally_stock_movement")
            .upsert(group, on_conflict="movement_key")
            .execute()
        )

    inactive_count = 0
    if complete_period and parsed.get("period_start") and parsed.get("period_end"):
        existing_response = (
            supabase.table("tally_stock_movement")
            .select("movement_key")
            .eq("period_start", parsed["period_start"])
            .eq("period_end", parsed["period_end"])
            .execute()
        )
        current_keys = {movement["movement_key"] for movement in rows}
        missing_keys = [
            row["movement_key"]
            for row in (existing_response.data or [])
            if row["movement_key"] not in current_keys
        ]
        for movement_key in missing_keys:
            (
                supabase.table("tally_stock_movement")
                .update(
                    {
                        "is_active": False,
                        "review_status": "Inactive",
                        "last_synced_at": timestamp,
                    }
                )
                .eq("movement_key", movement_key)
                .execute()
            )
        inactive_count = len(missing_keys)

    return {
        "stock_movements": len(rows),
        "stock_movements_marked_inactive": inactive_count,
        "import_id": import_id,
    }


def synchronize_tally_file(supabase, parsed, complete_period=False):
    previous = existing_import(supabase, parsed["file_hash"])
    if previous and previous.get("status") == "completed":
        return {"duplicate": True, "existing": previous}

    if previous:
        import_id = previous["id"]
        (
            supabase.table("tally_imports")
            .update({"status": "processing", "imported_at": now_iso()})
            .eq("id", import_id)
            .execute()
        )
    else:
        import_id = create_import(supabase, parsed)
    try:
        if parsed["report_type"] == "Inventory Master":
            result = sync_inventory_master(supabase, parsed, import_id)
        elif parsed["report_type"] in {"Sales", "Purchase"}:
            result = sync_voucher_report(
                supabase,
                parsed,
                import_id,
                complete_period=complete_period,
            )
        else:
            result = sync_stock_movement(
                supabase,
                parsed,
                import_id,
                complete_period=complete_period,
            )
        (
            supabase.table("tally_imports")
            .update({"status": "completed"})
            .eq("id", import_id)
            .execute()
        )
        result["duplicate"] = False
        return result
    except Exception:
        (
            supabase.table("tally_imports")
            .update({"status": "failed"})
            .eq("id", import_id)
            .execute()
        )
        raise


def preview_dataframe(parsed):
    if parsed["report_type"] == "Inventory Master":
        return pd.DataFrame(parsed["stock_items"])
    if parsed["report_type"] in {"Sales", "Purchase"}:
        vouchers = {
            voucher["voucher_key"]: voucher for voucher in parsed["vouchers"]
        }
        rows = []
        for line in parsed["voucher_lines"]:
            voucher = vouchers.get(line["voucher_key"], {})
            rows.append(
                {
                    "date": voucher.get("voucher_date"),
                    "voucher_no": voucher.get("voucher_number"),
                    "party": voucher.get("party_name"),
                    "item": line.get("item_name"),
                    "description": line.get("description"),
                    "quantity": line.get("quantity"),
                    "unit": line.get("unit"),
                    "rate": line.get("rate"),
                    "amount": line.get("amount"),
                    "review_status": line.get("review_status"),
                    "review_note": line.get("review_note"),
                }
            )
        return pd.DataFrame(rows)
    return pd.DataFrame(parsed["stock_movements"])


def review_count(parsed):
    if "voucher_lines" in parsed:
        return sum(
            row.get("review_status") == "Needs review"
            for row in parsed["voucher_lines"]
        )
    if "stock_movements" in parsed:
        return sum(
            row.get("review_status") == "Needs review"
            for row in parsed["stock_movements"]
        )
    return 0


def render_import_history(supabase):
    st.markdown("#### Import history")
    try:
        history = load_all(supabase, "tally_imports", "id", desc=True)
        if not history:
            st.caption("No Tally files synchronized yet.")
            return
        columns = [
            "id",
            "imported_at",
            "file_name",
            "report_type",
            "period_start",
            "period_end",
            "record_count",
            "status",
        ]
        st.dataframe(
            pd.DataFrame(history)[columns],
            use_container_width=True,
            hide_index=True,
        )
    except Exception as error:
        st.error("Could not load import history. Run the v0.4 Supabase migration first.")
        st.exception(error)


def render_tally_import_page(supabase):
    st.subheader("Tally import and synchronization")
    st.caption(
        "Import Inventory Master XML first, then Purchase, Sales, and Stock Movement files. "
        "An identical file is ignored; a changed export updates its existing vouchers."
    )

    uploaded = st.file_uploader(
        "Choose one Tally file",
        type=["xml", "xls", "xlsx", "csv"],
        key="tally_sync_upload",
    )

    if uploaded is not None:
        raw_bytes = uploaded.getvalue()
        try:
            stock_items = [] if uploaded.name.lower().endswith(".xml") else load_stock_items(supabase)
            parsed = parse_tally_upload(uploaded.name, raw_bytes, stock_items)
            previous = existing_import(supabase, parsed["file_hash"])
            duplicate = previous if previous and previous.get("status") == "completed" else None
            issues = review_count(parsed)

            col1, col2, col3 = st.columns(3)
            col1.metric("Type", parsed["report_type"])
            col2.metric("Records", parsed["record_count"])
            col3.metric("Needs review", issues)

            if parsed.get("period_start"):
                st.write(
                    f"Period: **{parsed['period_start']} to {parsed['period_end']}**"
                )
            if duplicate:
                st.info(
                    "This exact file was already synchronized. Jarvis will not create duplicates."
                )
            elif parsed["report_type"] != "Inventory Master" and not stock_items:
                st.warning("Inventory Master has not been synchronized yet. Import Master.xml first.")

            preview = preview_dataframe(parsed)
            st.dataframe(preview.head(200), use_container_width=True, hide_index=True)

            complete_period = False
            if parsed["report_type"] in {"Sales", "Purchase", "Stock Movement"}:
                complete_period = st.checkbox(
                    "This is the complete report for the displayed period",
                    help=(
                        "If a previously imported voucher is missing from this newer complete export, "
                        "Jarvis marks the old voucher or movement row inactive instead of keeping "
                        "duplicate/outdated data."
                    ),
                )

            confirmed = st.checkbox(
                "I checked the preview and want to synchronize this file",
                disabled=bool(duplicate),
            )
            if st.button(
                "Synchronize Tally file",
                type="primary",
                disabled=bool(duplicate) or not confirmed,
            ):
                with st.spinner("Synchronizing without duplicates..."):
                    result = synchronize_tally_file(
                        supabase,
                        parsed,
                        complete_period=complete_period,
                    )
                if result.get("duplicate"):
                    st.info("The exact same file was already synchronized.")
                else:
                    st.success("Tally data synchronized successfully.")
                    st.json({key: value for key, value in result.items() if key != "duplicate"})
        except Exception as error:
            st.error("Peter could not process this Tally file.")
            st.exception(error)

    st.divider()
    render_import_history(supabase)


def _search_frame(dataframe, search_text):
    if dataframe.empty or not search_text.strip():
        return dataframe
    needle = search_text.strip().lower()
    return dataframe[
        dataframe.apply(
            lambda row: needle in " ".join(
                "" if optional_value(value) is None else str(value).lower()
                for value in row.values
            ),
            axis=1,
        )
    ]


def _show_tally_table(dataframe, columns, search_text, file_name, key):
    available = [column for column in columns if column in dataframe.columns]
    visible = _search_frame(dataframe[available].copy(), search_text)
    st.caption(f"Showing {len(visible)} record(s)")
    st.dataframe(visible, use_container_width=True, hide_index=True)
    st.download_button(
        "Download this view as CSV",
        data=visible.to_csv(index=False).encode("utf-8"),
        file_name=file_name,
        mime="text/csv",
        key=key,
    )


def _stock_items_with_rates(stock_items, joined_lines, movements):
    stock_view = stock_items.copy()
    if stock_view.empty:
        return stock_view

    stock_view["latest_purchase_rate"] = None
    stock_view["weighted_avg_purchase_rate"] = None
    stock_view["last_supplier"] = None
    stock_view["last_purchase_date"] = None
    stock_view["latest_inward_rate"] = None

    if not joined_lines.empty and "report_type" in joined_lines.columns:
        purchases = joined_lines[joined_lines["report_type"] == "Purchase"].copy()
        if not purchases.empty and "item_guid" in purchases.columns:
            purchases["rate_number"] = pd.to_numeric(
                purchases.get("rate"), errors="coerce"
            )
            purchases["quantity_number"] = pd.to_numeric(
                purchases.get("quantity"), errors="coerce"
            )
            purchases["amount_number"] = pd.to_numeric(
                purchases.get("amount"), errors="coerce"
            )
            purchases["date_sort"] = pd.to_datetime(
                purchases.get("voucher_date"), errors="coerce"
            )

            latest = (
                purchases.dropna(subset=["item_guid", "rate_number"])
                .sort_values(["date_sort", "id"], na_position="first")
                .groupby("item_guid", as_index=False)
                .tail(1)
            )
            if not latest.empty:
                latest = latest[
                    ["item_guid", "rate_number", "party_name", "voucher_date"]
                ].rename(
                    columns={
                        "rate_number": "latest_purchase_rate_new",
                        "party_name": "last_supplier_new",
                        "voucher_date": "last_purchase_date_new",
                    }
                )
                stock_view = stock_view.merge(
                    latest,
                    left_on="tally_guid",
                    right_on="item_guid",
                    how="left",
                )
                stock_view["latest_purchase_rate"] = stock_view.pop(
                    "latest_purchase_rate_new"
                )
                stock_view["last_supplier"] = stock_view.pop("last_supplier_new")
                stock_view["last_purchase_date"] = stock_view.pop(
                    "last_purchase_date_new"
                )
                stock_view = stock_view.drop(columns=["item_guid"], errors="ignore")

            averages = []
            for item_guid, group in purchases.dropna(subset=["item_guid"]).groupby(
                "item_guid"
            ):
                valid = group[
                    group["quantity_number"].notna()
                    & group["amount_number"].notna()
                    & (group["quantity_number"] != 0)
                ]
                quantity_total = valid["quantity_number"].abs().sum()
                if quantity_total:
                    averages.append(
                        {
                            "tally_guid": item_guid,
                            "weighted_avg_purchase_rate_new": (
                                valid["amount_number"].abs().sum() / quantity_total
                            ),
                        }
                    )
            if averages:
                stock_view = stock_view.merge(
                    pd.DataFrame(averages), on="tally_guid", how="left"
                )
                stock_view["weighted_avg_purchase_rate"] = stock_view.pop(
                    "weighted_avg_purchase_rate_new"
                )

    if not movements.empty and "item_guid" in movements.columns:
        inward = movements.copy()
        inward["inward_rate_number"] = pd.to_numeric(
            inward.get("inward_rate"), errors="coerce"
        )
        inward["period_sort"] = pd.to_datetime(
            inward.get("period_end"), errors="coerce"
        )
        latest_inward = (
            inward.dropna(subset=["item_guid", "inward_rate_number"])
            .sort_values(["period_sort", "id"], na_position="first")
            .groupby("item_guid", as_index=False)
            .tail(1)[["item_guid", "inward_rate_number"]]
            .rename(columns={"inward_rate_number": "latest_inward_rate_new"})
        )
        if not latest_inward.empty:
            stock_view = stock_view.merge(
                latest_inward,
                left_on="tally_guid",
                right_on="item_guid",
                how="left",
            )
            stock_view["latest_inward_rate"] = stock_view.pop(
                "latest_inward_rate_new"
            )
            stock_view = stock_view.drop(columns=["item_guid"], errors="ignore")

    purchase_rates = pd.to_numeric(
        stock_view["latest_purchase_rate"], errors="coerce"
    )
    inward_rates = pd.to_numeric(
        stock_view["latest_inward_rate"], errors="coerce"
    )
    stock_view["latest_purchase_rate"] = purchase_rates
    stock_view["latest_inward_rate"] = inward_rates
    stock_view["weighted_avg_purchase_rate"] = pd.to_numeric(
        stock_view["weighted_avg_purchase_rate"], errors="coerce"
    )
    stock_view["reference_cost_rate"] = purchase_rates.combine_first(inward_rates)
    stock_view["rate_source"] = ""
    stock_view.loc[stock_view["latest_purchase_rate"].notna(), "rate_source"] = (
        "Latest purchase"
    )
    stock_view.loc[
        stock_view["latest_purchase_rate"].isna()
        & stock_view["latest_inward_rate"].notna(),
        "rate_source",
    ] = "Latest inward"
    return stock_view


def render_tally_data_page(supabase):
    st.subheader("Tally data library")
    st.caption(
        "Browse and search everything synchronized from Tally. "
        "Use Review Inbox to correct uncertain rows."
    )

    search_text = st.text_input(
        "Search Tally data",
        placeholder="Item, supplier, customer, voucher number, description...",
    )

    try:
        stock_items = pd.DataFrame(load_all(supabase, "tally_stock_items", "item_name"))
        vouchers = pd.DataFrame(load_all(supabase, "tally_vouchers", "voucher_date", desc=True))
        voucher_lines = pd.DataFrame(load_all(supabase, "tally_voucher_lines", "id", desc=True))
        movements = pd.DataFrame(load_all(supabase, "tally_stock_movement", "id", desc=True))
        imports = pd.DataFrame(load_all(supabase, "tally_imports", "id", desc=True))

        if vouchers.empty or voucher_lines.empty:
            joined_lines = pd.DataFrame()
        else:
            voucher_columns = [
                "voucher_key",
                "report_type",
                "voucher_date",
                "voucher_type",
                "voucher_number",
                "party_name",
                "reference_number",
                "total_amount",
                "tax_amount",
                "is_active",
            ]
            available_voucher_columns = [
                column for column in voucher_columns if column in vouchers.columns
            ]
            joined_lines = voucher_lines.merge(
                vouchers[available_voucher_columns],
                on="voucher_key",
                how="left",
            )

        stock_view = _stock_items_with_rates(stock_items, joined_lines, movements)

        tab_items, tab_purchase, tab_sales, tab_movement, tab_imports = st.tabs(
            ["Stock items", "Purchases", "Sales", "Stock movement", "Import history"]
        )

        with tab_items:
            if stock_view.empty:
                st.info("No stock items imported yet.")
            else:
                _show_tally_table(
                    stock_view,
                    [
                        "item_name",
                        "base_unit",
                        "additional_unit",
                        "reference_cost_rate",
                        "rate_source",
                        "latest_purchase_rate",
                        "weighted_avg_purchase_rate",
                        "latest_inward_rate",
                        "last_supplier",
                        "last_purchase_date",
                        "is_deleted",
                        "last_synced_at",
                    ],
                    search_text,
                    "jarvis_tally_stock_items.csv",
                    "download_tally_items",
                )

        voucher_view_columns = [
            "voucher_date",
            "voucher_number",
            "party_name",
            "item_name",
            "quantity",
            "unit",
            "rate",
            "amount",
            "description",
            "review_status",
            "review_note",
            "is_active",
        ]
        with tab_purchase:
            purchase = (
                joined_lines[joined_lines["report_type"] == "Purchase"]
                if not joined_lines.empty and "report_type" in joined_lines.columns
                else pd.DataFrame()
            )
            if purchase.empty:
                st.info("No purchase records imported yet.")
            else:
                _show_tally_table(
                    purchase,
                    voucher_view_columns,
                    search_text,
                    "jarvis_tally_purchases.csv",
                    "download_tally_purchases",
                )

        with tab_sales:
            sales = (
                joined_lines[joined_lines["report_type"] == "Sales"]
                if not joined_lines.empty and "report_type" in joined_lines.columns
                else pd.DataFrame()
            )
            if sales.empty:
                st.info("No sales records imported yet.")
            else:
                _show_tally_table(
                    sales,
                    voucher_view_columns,
                    search_text,
                    "jarvis_tally_sales.csv",
                    "download_tally_sales",
                )

        with tab_movement:
            if movements.empty:
                st.info("No stock movement records imported yet.")
            else:
                _show_tally_table(
                    movements,
                    [
                        "period_start",
                        "period_end",
                        "item_name",
                        "unit",
                        "inward_quantity",
                        "inward_rate",
                        "inward_value",
                        "outward_quantity",
                        "outward_rate",
                        "outward_value",
                        "review_status",
                        "is_active",
                    ],
                    search_text,
                    "jarvis_tally_stock_movement.csv",
                    "download_tally_movement",
                )

        with tab_imports:
            if imports.empty:
                st.info("No Tally imports yet.")
            else:
                _show_tally_table(
                    imports,
                    [
                        "id",
                        "imported_at",
                        "file_name",
                        "report_type",
                        "period_start",
                        "period_end",
                        "record_count",
                        "status",
                    ],
                    search_text,
                    "jarvis_tally_import_history.csv",
                    "download_tally_imports",
                )
    except Exception as error:
        st.error("Could not load the Tally data library.")
        st.exception(error)


def _master_maps(stock_items):
    by_guid = {item["tally_guid"]: item for item in stock_items}
    by_name = {item["item_name"]: item for item in stock_items}
    return by_guid, by_name


def render_review_inbox_page(supabase):
    st.subheader("Review Inbox")
    st.caption("Only uncertain Tally rows appear here. Correct them, then mark them Ready.")

    try:
        stock_items = load_stock_items(supabase)
        by_guid, by_name = _master_maps(stock_items)
        item_options = sorted(by_name)

        line_response = (
            supabase.table("tally_voucher_lines")
            .select("id,voucher_key,line_number,item_name,item_guid,unit,quantity,rate,amount,description,review_status,review_note")
            .eq("review_status", "Needs review")
            .order("id")
            .execute()
        )
        lines = pd.DataFrame(line_response.data or [])

        st.markdown("#### Purchase and sales lines")
        if lines.empty:
            st.success("No purchase or sales lines need review.")
        else:
            lines["matched_item"] = lines.apply(
                lambda row: by_guid.get(row.get("item_guid"), {}).get("item_name")
                or row.get("item_name"),
                axis=1,
            )
            editor_columns = [
                "id",
                "voucher_key",
                "item_name",
                "matched_item",
                "quantity",
                "unit",
                "rate",
                "amount",
                "description",
                "review_note",
                "review_status",
            ]
            edited = st.data_editor(
                lines[editor_columns],
                use_container_width=True,
                hide_index=True,
                disabled=["id", "voucher_key", "item_name"],
                column_config={
                    "matched_item": st.column_config.SelectboxColumn(
                        "Match to stock item", options=item_options, required=True
                    ),
                    "review_status": st.column_config.SelectboxColumn(
                        "Status",
                        options=["Needs review", "Ready", "Ignored"],
                        required=True,
                    ),
                },
                key="tally_lines_review_editor",
            )
            if st.button("Save reviewed purchase/sales lines", type="primary"):
                for _, row in edited.iterrows():
                    master = by_name.get(optional_text(row.get("matched_item")))
                    selected_status = row.get("review_status")
                    if selected_status == "Ready":
                        review_note = None
                    elif selected_status == "Ignored":
                        review_note = "Ignored as a non-stock or miscellaneous expense"
                    else:
                        review_note = optional_text(row.get("review_note"))

                    payload = {
                        "item_guid": master.get("tally_guid") if master else None,
                        "unit": optional_text(row.get("unit"))
                        or (master.get("base_unit") if master else None),
                        "quantity": optional_float(row.get("quantity")),
                        "rate": optional_float(row.get("rate")),
                        "amount": optional_float(row.get("amount")),
                        "description": optional_text(row.get("description")),
                        "review_status": selected_status,
                        "review_note": review_note,
                    }
                    (
                        supabase.table("tally_voucher_lines")
                        .update(payload)
                        .eq("id", int(row["id"]))
                        .execute()
                    )
                st.success("Review changes saved.")
                st.rerun()

        movement_response = (
            supabase.table("tally_stock_movement")
            .select("id,item_name,item_guid,unit,inward_quantity,inward_rate,inward_value,outward_quantity,outward_rate,outward_value,review_status")
            .eq("review_status", "Needs review")
            .eq("is_active", True)
            .order("id")
            .execute()
        )
        movements = pd.DataFrame(movement_response.data or [])
        st.markdown("#### Stock movement rows")
        if movements.empty:
            st.success("No stock movement rows need review.")
        else:
            movements["matched_item"] = movements.apply(
                lambda row: by_guid.get(row.get("item_guid"), {}).get("item_name")
                or row.get("item_name"),
                axis=1,
            )
            columns = [
                "id",
                "item_name",
                "matched_item",
                "unit",
                "inward_quantity",
                "inward_rate",
                "inward_value",
                "outward_quantity",
                "outward_rate",
                "outward_value",
                "review_status",
            ]
            edited_movements = st.data_editor(
                movements[columns],
                use_container_width=True,
                hide_index=True,
                disabled=["id", "item_name"],
                column_config={
                    "matched_item": st.column_config.SelectboxColumn(
                        "Match to stock item", options=item_options, required=True
                    ),
                    "review_status": st.column_config.SelectboxColumn(
                        "Status",
                        options=["Needs review", "Ready", "Ignored"],
                        required=True,
                    ),
                },
                key="tally_movement_review_editor",
            )
            if st.button("Save reviewed stock movements", type="primary"):
                numeric_columns = [
                    "inward_quantity",
                    "inward_rate",
                    "inward_value",
                    "outward_quantity",
                    "outward_rate",
                    "outward_value",
                ]
                for _, row in edited_movements.iterrows():
                    master = by_name.get(optional_text(row.get("matched_item")))
                    payload = {
                        "item_guid": master.get("tally_guid") if master else None,
                        "unit": optional_text(row.get("unit"))
                        or (master.get("base_unit") if master else None),
                        "review_status": row.get("review_status"),
                    }
                    for column in numeric_columns:
                        payload[column] = optional_float(row.get(column))
                    (
                        supabase.table("tally_stock_movement")
                        .update(payload)
                        .eq("id", int(row["id"]))
                        .execute()
                    )
                st.success("Stock movement reviews saved.")
                st.rerun()
    except Exception as error:
        st.error("Could not load Review Inbox. Run the v0.4 Supabase migration first.")
        st.exception(error)
