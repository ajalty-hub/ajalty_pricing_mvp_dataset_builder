
import io
import re
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


st.set_page_config(page_title="Ajalty Pricing Engine — Dataset Builder", layout="wide")

# -----------------------------
# Configuration
# -----------------------------
CANONICAL_FIELDS = [
    "part_number",
    "brand",
    "description",
    "category",
    "market",
    "country",
    "currency",
    "price",
    "price_ex_vat",
    "price_inc_vat",
    "vat_rate",
    "vat_status",
    "quantity",
    "price_type",
    "supplier",
    "supplier_type",
    "authorized_status",
    "source",
    "source_type",
    "price_evidence_level",
    "observation_date",
    "source_url",
    "source_location",
    "shipping_adjustment_pct",
    "shipping_adjustment",
    "benchmark_price",
    "valid",
    "rejection_reason",
    "notes",
]

REQUIRED_FOR_BENCHMARK = [
    "part_number",
    "currency",
    "price",
    "market",
    "price_type",
    "source",
    "source_type",
]

SOURCE_PRESETS = {
    "KSA dealership genuine pricing": {
        "source_type": "DEALERSHIP",
        "market": "Saudi Arabia",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "price_type": "WHOLESALE",
        "price_evidence_level": 1,
        "supplier_type": "Authorized/Dealership",
        "authorized_status": "CONFIRMED",
        "source_location": "Saudi Arabia",
    },
    "KSA Mendoubak marketplace": {
        "source_type": "MARKETPLACE",
        "market": "Saudi Arabia",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "price_type": "MARKETPLACE",
        "price_evidence_level": 5,
        "supplier_type": "Marketplace seller",
        "authorized_status": "UNKNOWN",
        "source_location": "Saudi Arabia",
    },
    "Ajalty / Saudi client transaction": {
        "source_type": "ACTUAL_TRANSACTION",
        "market": "Saudi Arabia",
        "country": "Saudi Arabia",
        "currency": "AED",
        "price_type": "TRANSACTION",
        "price_evidence_level": 1,
        "supplier_type": "Ajalty",
        "authorized_status": "N/A",
        "source_location": "Jebel Ali, UAE",
    },
    "Other / custom": {
        "source_type": "OTHER",
        "market": "",
        "country": "",
        "currency": "",
        "price_type": "UNKNOWN",
        "price_evidence_level": 5,
        "supplier_type": "",
        "authorized_status": "UNKNOWN",
        "source_location": "",
    },
}

FIELD_ALIASES = {
    "part_number": ["part number", "part_number", "part no", "part no.", "item", "item number", "pn", "clean pn", "رقم الصنف"],
    "description": ["description", "desc", "الوصف"],
    "quantity": ["qty", "quantity", "الكمية"],
    "price": ["price", "new price aed", "new price", "target price", "target price ", "usd cost", " ر.س.-", "ر.س."],
    "currency": ["currency"],
    "brand": ["brand", "make", "manufacturer"],
    "category": ["category", "product category"],
    "date": ["date", "order date", "observation date"],
    "source_url": ["source url", "url", "link"],
    "vat_rate": ["vat", "vat rate", "tax", "tax rate"],
    "supplier": ["supplier", "seller", "customer"],
    "source": ["source", "source name"],
}

def clean_col(x):
    if x is None:
        return ""
    return str(x).strip().lower().replace("\n", " ")

def normalize_part_number(value):
    if pd.isna(value):
        return ""
    s = str(value).strip().upper()
    # Preserve original elsewhere; normalized form removes common separators only.
    return re.sub(r"[\s\-_./]+", "", s)

def parse_numeric(value):
    if pd.isna(value) or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    # Handles strings such as "SAR 431.86", "AED 20.69", "$123.00"
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None
    # Conservative parsing: if comma is decimal separator and no dot, convert it.
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None

def read_uploaded(uploaded):
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith(".csv"):
        # Try UTF-8 first, then common fallback.
        try:
            return {"Sheet1": pd.read_csv(io.BytesIO(data))}
        except UnicodeDecodeError:
            return {"Sheet1": pd.read_csv(io.BytesIO(data), encoding="latin1")}
    xls = pd.ExcelFile(io.BytesIO(data))
    return {sheet: pd.read_excel(io.BytesIO(data), sheet_name=sheet) for sheet in xls.sheet_names}

def suggest_mapping(columns):
    suggestions = {}
    normalized = {c: clean_col(c) for c in columns}
    for field, aliases in FIELD_ALIASES.items():
        best = None
        for col, c in normalized.items():
            if c in aliases:
                best = col
                break
        if best is None:
            for col, c in normalized.items():
                if any(a in c for a in aliases):
                    best = col
                    break
        suggestions[field] = best
    return suggestions

def build_rows(df, mapping, metadata, filename, sheet_name):
    out = pd.DataFrame(index=df.index)
    for field in CANONICAL_FIELDS:
        out[field] = ""

    def get(field):
        col = mapping.get(field)
        if col and col in df.columns:
            return df[col]
        return pd.Series([""] * len(df), index=df.index)

    raw_part = get("part_number")
    raw_price = get("price")
    raw_qty = get("quantity")
    raw_date = get("date")

    out["part_number"] = raw_part
    out["normalized_part_number"] = raw_part.map(normalize_part_number)
    out["description"] = get("description")
    out["brand"] = get("brand")
    out["category"] = get("category")
    out["quantity"] = raw_qty.map(parse_numeric)
    out["price"] = raw_price.map(parse_numeric)

    # Metadata overrides / supplements
    for key in [
        "market", "country", "currency", "price_type", "supplier",
        "supplier_type", "authorized_status", "source", "source_type",
        "price_evidence_level", "source_url", "source_location",
        "shipping_adjustment_pct", "vat_rate", "vat_status", "notes"
    ]:
        if key in metadata:
            out[key] = metadata[key]

    out["observation_date"] = metadata.get("observation_date", str(date.today()))
    if mapping.get("date"):
        parsed_dates = pd.to_datetime(raw_date, errors="coerce")
        out["observation_date"] = parsed_dates.dt.strftime("%Y-%m-%d").fillna(metadata.get("observation_date", str(date.today())))

    # VAT calculations only when explicitly supplied.
    vat_rate = pd.to_numeric(out["vat_rate"], errors="coerce")
    out["vat_rate"] = vat_rate
    out["vat_status"] = out["vat_status"].replace("", "VAT_UNKNOWN")

    out["price_ex_vat"] = None
    out["price_inc_vat"] = None
    for i, row in out.iterrows():
        p = row["price"]
        vr = row["vat_rate"]
        status = str(row["vat_status"]).upper()
        if pd.isna(p):
            continue
        if status == "VAT_INCLUDED" and pd.notna(vr):
            out.at[i, "price_ex_vat"] = p / (1 + vr / 100)
            out.at[i, "price_inc_vat"] = p
        elif status == "VAT_EXCLUDED":
            out.at[i, "price_ex_vat"] = p
            if pd.notna(vr):
                out.at[i, "price_inc_vat"] = p * (1 + vr / 100)
        else:
            out.at[i, "price_ex_vat"] = p

    # Benchmark adjustment is intentionally explicit and reversible.
    pct = pd.to_numeric(out["shipping_adjustment_pct"], errors="coerce").fillna(0)
    out["shipping_adjustment"] = pd.to_numeric(out["price"], errors="coerce") * pct / 100
    out["benchmark_price"] = pd.to_numeric(out["price"], errors="coerce") + out["shipping_adjustment"]

    out["valid"] = True
    out["rejection_reason"] = ""

    # Deterministic quality checks; do not silently remove rows.
    for i, row in out.iterrows():
        reasons = []
        if not str(row["part_number"]).strip():
            reasons.append("missing_part_number")
        if pd.isna(row["price"]):
            reasons.append("missing_or_unreadable_price")
        if not str(row["currency"]).strip():
            reasons.append("missing_currency")
        if not str(row["market"]).strip():
            reasons.append("missing_market")
        if not str(row["price_type"]).strip() or str(row["price_type"]).upper() == "UNKNOWN":
            reasons.append("price_type_unknown")
        if not str(row["source"]).strip():
            reasons.append("missing_source")
        if reasons:
            out.at[i, "valid"] = False
            out.at[i, "rejection_reason"] = "; ".join(reasons)

    out["source_file"] = filename
    out["source_sheet"] = sheet_name
    return out

# -----------------------------
# UI
# -----------------------------
st.title("Ajalty Intelligent Pricing Engine — MVP Dataset Builder")
st.caption("G1-01 / G1 Dataset Preparation • Upload → Map → Classify → Validate → Export")

if "file_configs" not in st.session_state:
    st.session_state.file_configs = {}
if "mapped_outputs" not in st.session_state:
    st.session_state.mapped_outputs = {}

st.info(
    "This tool prepares the benchmark dataset. It does not predict prices yet. "
    "Observed prices remain separate from benchmark adjustments and later model predictions."
)

uploads = st.file_uploader(
    "Add one or more Excel/CSV files",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    key="uploads",
)

if uploads:
    for uploaded in uploads:
        file_key = uploaded.name
        if file_key not in st.session_state.file_configs:
            try:
                sheets = read_uploaded(uploaded)
                st.session_state.file_configs[file_key] = {
                    "sheets": sheets,
                    "active_sheet": list(sheets.keys())[0],
                }
            except Exception as e:
                st.error(f"Could not read {file_key}: {e}")

    st.success(f"{len(uploads)} file(s) loaded. Configure each file below.")

    for idx, uploaded in enumerate(uploads, start=1):
        file_key = uploaded.name
        cfg = st.session_state.file_configs[file_key]
        sheets = cfg["sheets"]

        with st.expander(f"{idx}. {file_key}", expanded=(idx == 1)):
            st.write(f"**Sheets:** {', '.join(sheets.keys())}")

            active_sheet = st.selectbox(
                "Sheet to import",
                list(sheets.keys()),
                index=list(sheets.keys()).index(cfg["active_sheet"]),
                key=f"sheet_{file_key}",
            )
            cfg["active_sheet"] = active_sheet
            df = sheets[active_sheet].copy()

            st.write("**File preview — first 8 rows**")
            st.dataframe(df.head(8), use_container_width=True, height=240)

            preset_name = st.selectbox(
                "What type of source is this file?",
                list(SOURCE_PRESETS.keys()),
                key=f"preset_{file_key}",
            )
            preset = SOURCE_PRESETS[preset_name].copy()

            st.markdown("#### Source / market information")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                market = st.text_input("Target market", value=preset["market"], key=f"market_{file_key}")
                country = st.text_input("Country", value=preset["country"], key=f"country_{file_key}")
            with c2:
                currency = st.text_input("Currency", value=preset["currency"], key=f"currency_{file_key}")
                source_type = st.selectbox(
                    "Source type",
                    ["DEALERSHIP", "MARKETPLACE", "ACTUAL_TRANSACTION", "SUPPLIER", "OEM", "OTHER"],
                    index=["DEALERSHIP", "MARKETPLACE", "ACTUAL_TRANSACTION", "SUPPLIER", "OEM", "OTHER"].index(preset["source_type"])
                    if preset["source_type"] in ["DEALERSHIP", "MARKETPLACE", "ACTUAL_TRANSACTION", "SUPPLIER", "OEM", "OTHER"] else 5,
                    key=f"source_type_{file_key}",
                )
            with c3:
                price_type = st.selectbox(
                    "Price type",
                    ["WHOLESALE", "TRADE", "DISTRIBUTOR", "DEALER", "RETAIL", "MSRP", "PUBLIC_OEM", "MARKETPLACE", "QUOTE", "TRANSACTION", "UNKNOWN"],
                    index=["WHOLESALE", "TRADE", "DISTRIBUTOR", "DEALER", "RETAIL", "MSRP", "PUBLIC_OEM", "MARKETPLACE", "QUOTE", "TRANSACTION", "UNKNOWN"].index(preset["price_type"]),
                    key=f"price_type_{file_key}",
                )
                evidence = st.selectbox(
                    "Evidence level",
                    [1, 2, 3, 4, 5],
                    index=int(preset["price_evidence_level"]) - 1,
                    key=f"evidence_{file_key}",
                    help="1 = strongest evidence, 5 = general web/marketplace evidence.",
                )
            with c4:
                source_location = st.text_input("Price/source location", value=preset["source_location"], key=f"location_{file_key}")
                obs_date = st.date_input("Observation date", value=date.today(), key=f"date_{file_key}")

            source_name = st.text_input(
                "Source name / reference",
                value=preset_name if preset_name != "Other / custom" else file_key,
                key=f"source_{file_key}",
            )

            st.markdown("#### Optional benchmark adjustments")
            a1, a2, a3 = st.columns(3)
            with a1:
                shipping_pct = st.number_input(
                    "Shipping / location adjustment (%)",
                    min_value=0.0, max_value=100.0, value=0.0, step=0.5,
                    key=f"shipping_{file_key}",
                    help="Keep this as an explicit assumption. It is not applied to the observed price itself.",
                )
            with a2:
                vat_status = st.selectbox(
                    "VAT status",
                    ["VAT_UNKNOWN", "VAT_INCLUDED", "VAT_EXCLUDED"],
                    key=f"vat_status_{file_key}",
                )
            with a3:
                supplier = st.text_input("Supplier / seller", value=preset["supplier_type"], key=f"supplier_{file_key}")

            st.markdown("#### Column mapping")
            st.caption("The app suggests mappings from the file headers. Review them before importing.")

            suggestions = suggest_mapping(df.columns.tolist())
            mapping = {}
            map_cols = st.columns(3)
            for n, field in enumerate(["part_number", "description", "quantity", "price", "brand", "category", "date", "source_url"]):
                with map_cols[n % 3]:
                    options = ["— Not mapped —"] + list(df.columns)
                    suggested = suggestions.get(field)
                    default_index = options.index(suggested) if suggested in options else 0
                    selected = st.selectbox(
                        field,
                        options,
                        index=default_index,
                        key=f"map_{file_key}_{field}",
                    )
                    if selected != "— Not mapped —":
                        mapping[field] = selected

            metadata = {
                "market": market,
                "country": country,
                "currency": currency.strip().upper(),
                "price_type": price_type,
                "source": source_name,
                "source_type": source_type,
                "price_evidence_level": evidence,
                "source_location": source_location,
                "observation_date": str(obs_date),
                "shipping_adjustment_pct": shipping_pct,
                "vat_status": vat_status,
                "supplier": supplier,
                "supplier_type": preset["supplier_type"],
                "authorized_status": preset["authorized_status"],
            }

            if st.button(f"Process this file", key=f"process_{file_key}", type="primary"):
                result = build_rows(df, mapping, metadata, file_key, active_sheet)
                st.session_state.mapped_outputs[file_key] = result
                st.success(f"Processed {len(result):,} rows from {file_key}.")

            if file_key in st.session_state.mapped_outputs:
                result = st.session_state.mapped_outputs[file_key]
                valid_count = int(result["valid"].sum())
                invalid_count = len(result) - valid_count
                m1, m2, m3 = st.columns(3)
                m1.metric("Rows imported", len(result))
                m2.metric("Valid", valid_count)
                m3.metric("Needs review", invalid_count)

                st.dataframe(
                    result[
                        ["part_number", "normalized_part_number", "description", "quantity",
                         "currency", "price", "benchmark_price", "price_type", "source_type",
                         "valid", "rejection_reason"]
                    ].head(20),
                    use_container_width=True,
                    height=300,
                )

    if st.session_state.mapped_outputs:
        st.divider()
        st.header("Combined benchmark review")

        combined = pd.concat(st.session_state.mapped_outputs.values(), ignore_index=True)

        # Ensure canonical ordering and include generated normalized field.
        display_cols = [
            c for c in [
                "source_file", "source_sheet", "part_number", "normalized_part_number",
                "brand", "description", "category", "market", "country", "currency",
                "price", "price_ex_vat", "price_inc_vat", "vat_rate", "vat_status",
                "quantity", "price_type", "supplier", "supplier_type", "authorized_status",
                "source", "source_type", "price_evidence_level", "observation_date",
                "source_url", "source_location", "shipping_adjustment_pct",
                "shipping_adjustment", "benchmark_price", "valid", "rejection_reason", "notes"
            ] if c in combined.columns
        ]

        c1, c2, c3 = st.columns(3)
        c1.metric("Total observations", len(combined))
        c2.metric("Valid observations", int(combined["valid"].sum()))
        c3.metric("Needs review", int((~combined["valid"]).sum()))

        st.dataframe(combined[display_cols].head(100), use_container_width=True, height=450)

        st.markdown("### Data quality summary")
        summary = pd.DataFrame({
            "metric": [
                "Total observations",
                "Valid observations",
                "Rows needing review",
                "Unique normalized parts",
                "KSA observations",
                "UAE observations",
            ],
            "value": [
                len(combined),
                int(combined["valid"].sum()),
                int((~combined["valid"]).sum()),
                combined["normalized_part_number"].replace("", pd.NA).nunique(),
                int((combined["market"].astype(str).str.contains("Saudi", case=False, na=False)).sum()),
                int((combined["market"].astype(str).str.contains("UAE", case=False, na=False)).sum()),
            ],
        })
        st.dataframe(summary, hide_index=True, use_container_width=True)

        st.warning(
            "Before using this as ground truth, review rows marked invalid/needs review and confirm "
            "price type, genuine status, source evidence, quantity, currency and VAT assumptions."
        )

        csv_bytes = combined[display_cols].to_csv(index=False).encode("utf-8-sig")
        xlsx_buffer = io.BytesIO()
        with pd.ExcelWriter(xlsx_buffer, engine="openpyxl") as writer:
            combined[display_cols].to_excel(writer, index=False, sheet_name="benchmark_v01")
            summary.to_excel(writer, index=False, sheet_name="data_quality")

        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "Download benchmark_v01.csv",
                data=csv_bytes,
                file_name="benchmark_v01.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with d2:
            st.download_button(
                "Download benchmark_v01.xlsx",
                data=xlsx_buffer.getvalue(),
                file_name="benchmark_v01.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

else:
    st.markdown(
        """
        ### Start here

        Upload the first file. After it is loaded you can:
        1. Preview its first rows.
        2. Designate what the file represents.
        3. Review automatic column suggestions.
        4. Correct the mapping.
        5. Process the file.
        6. Add another file without losing the previous mapping.
        7. Review the combined benchmark.
        8. Download CSV or Excel.
        """
    )
