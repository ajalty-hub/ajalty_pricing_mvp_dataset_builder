import io
import re
import math
import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Ajalty Pricing Engine MVP — R03", layout="wide")

# ============================================================
# R03
# Deterministic dataset builder only.
# Core principles:
# - raw observations remain immutable
# - observed price != benchmark price
# - explicit currency in raw price wins over file-level currency
# - file-level brand is applied when no brand column is mapped
# - parsing status is explicit; no silent conversion
# ============================================================

MVP_ALLOWED_BRANDS = ["TOYOTA"]

CANONICAL_COLUMNS = [
    "source_file", "source_sheet",
    "part_number", "normalized_part_number",
    "brand", "brand_source",
    "description", "category",
    "market", "country", "currency",
    "raw_price", "price", "price_parse_status",
    "price_ex_vat", "price_inc_vat", "vat_rate", "vat_status",
    "quantity", "price_type",
    "supplier", "supplier_type", "authorized_status",
    "source", "source_type", "price_evidence_level",
    "observation_date", "source_url",
    "source_location",
    "shipping_adjustment_pct", "shipping_adjustment", "benchmark_price",
    "valid", "rejection_reason", "notes"
]

CURRENCY_CODES = [
    "SAR", "AED", "USD", "EUR", "GBP", "JPY", "CNY", "THB", "MYR",
    "KWD", "BHD", "QAR", "OMR", "INR", "RUB", "TRY", "SGD", "AUD",
    "CAD", "HKD"
]

PRICE_STATUS = ["EXTRACTED", "CONTEXT", "MISSING", "AMBIGUOUS"]

def normalize_part_number(value):
    if pd.isna(value):
        return ""
    s = str(value).strip().upper()
    # Preserve alphanumeric identity while normalizing common separators.
    s = re.sub(r"[\s\-_./]+", "", s)
    return s

def clean_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()

def detect_currency(raw):
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None
    s = str(raw).upper()
    # Longest / most explicit tokens first.
    for code in sorted(CURRENCY_CODES, key=len, reverse=True):
        if re.search(rf"(?<![A-Z]){re.escape(code)}(?![A-Z])", s):
            return code
    # Common symbols
    if "﷼" in s:
        return "SAR"
    if "د.إ" in s or "AED" in s:
        return "AED"
    if "$" in s:
        return "USD"
    if "€" in s:
        return "EUR"
    if "£" in s:
        return "GBP"
    if "¥" in s:
        return "JPY"
    return None

def parse_price(raw_value, fallback_currency=None):
    """
    Returns:
      numeric price,
      detected currency or fallback currency,
      parse status,
      reason
    Examples:
      100 SAR      -> 100, SAR, EXTRACTED
      SAR 100      -> 100, SAR, EXTRACTED
      1,250 SAR    -> 1250, SAR, EXTRACTED
      100           -> 100, fallback, CONTEXT
      100 USD       -> 100, USD, EXTRACTED
    """
    if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
        return None, fallback_currency, "MISSING", "missing_price"

    raw = str(raw_value).strip()
    if not raw:
        return None, fallback_currency, "MISSING", "missing_price"

    currency = detect_currency(raw)

    # Remove currency words/symbols and common commercial suffixes.
    cleaned = raw.upper()
    for code in CURRENCY_CODES:
        cleaned = re.sub(rf"(?<![A-Z]){re.escape(code)}(?![A-Z)", " ", cleaned)
    cleaned = cleaned.replace("﷼", " ").replace("د.إ", " ")
    cleaned = cleaned.replace("$", " ").replace("€", " ").replace("£", " ").replace("¥", " ")
    cleaned = re.sub(r"\b(INC\.?|EX\.?)\s*VAT\b", " ", cleaned)
    cleaned = re.sub(r"\b\+?\s*VAT\b", " ", cleaned)
    cleaned = re.sub(r"\bPER\s+(SET|PCS?|UNIT|PAIR|KIT)\b", " ", cleaned)
    cleaned = re.sub(r"/\s*(SET|PCS?|PCS|UNIT|PAIR|KIT)\b", " ", cleaned)
    cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Numeric extraction. Do not invent a value if multiple unrelated
    # numbers are present.
    matches = re.findall(r"(?<![A-Z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)", cleaned)

    if not matches:
        return None, currency or fallback_currency, "MISSING", "no_numeric_value"

    if len(matches) > 1:
        # If the raw field has multiple numbers, mark ambiguous rather
        # than guessing which one is the price.
        return None, currency or fallback_currency, "AMBIGUOUS", "multiple_numeric_values"

    try:
        value = float(matches[0])
    except ValueError:
        return None, currency or fallback_currency, "MISSING", "numeric_parse_failed"

    if not math.isfinite(value) or value < 0:
        return None, currency or fallback_currency, "MISSING", "invalid_numeric_value"

    if currency:
        return value, currency, "EXTRACTED", ""
    if fallback_currency:
        return value, str(fallback_currency).upper().strip(), "CONTEXT", ""
    return value, None, "CONTEXT", "currency_not_found"

def read_uploaded_file(uploaded):
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith(".csv"):
        # Try common encodings.
        for enc in ["utf-8-sig", "utf-8", "cp1256", "latin1"]:
            try:
                return {"Sheet1": pd.read_csv(io.BytesIO(data), encoding=enc)}
            except Exception:
                pass
        raise ValueError("Could not read CSV.")
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(data), sheet_name=None)
    raise ValueError("Unsupported file type. Use CSV or Excel.")

def guess_column(columns, keywords):
    upper = {str(c).upper(): c for c in columns}
    for key in keywords:
        for uc, original in upper.items():
            if key in uc:
                return original
    return None

def make_mapping(df):
    cols = list(df.columns)
    return {
        "part_number": guess_column(cols, ["PART NUMBER", "PART_NO", "PARTNO", "PART", "OEM"]),
        "price": guess_column(cols, ["PRICE", "COST", "AMOUNT", "VALUE"]),
        "brand": guess_column(cols, ["BRAND", "MAKE"]),
        "description": guess_column(cols, ["DESCRIPTION", "DESC", "NAME"]),
        "category": guess_column(cols, ["CATEGORY", "GROUP"]),
        "quantity": guess_column(cols, ["QUANTITY", "QTY"]),
        "currency": guess_column(cols, ["CURRENCY", "CUR"]),
        "vat_rate": guess_column(cols, ["VAT RATE", "VAT"]),
        "supplier": guess_column(cols, ["SUPPLIER", "VENDOR"]),
        "source_url": guess_column(cols, ["SOURCE URL", "URL", "LINK"]),
        "observation_date": guess_column(cols, ["DATE", "UPDATED", "OBSERVATION"]),
    }

def build_rows(df, mapping, meta):
    out = pd.DataFrame(index=df.index)

    def mapped(name):
        col = mapping.get(name)
        return df[col] if col and col in df.columns else pd.Series([""] * len(df), index=df.index)

    # Raw source values are retained.
    raw_part = mapped("part_number")
    raw_price = mapped("price")

    out["source_file"] = meta["source_file"]
    out["source_sheet"] = meta["source_sheet"]
    out["part_number"] = raw_part.map(clean_text)
    out["normalized_part_number"] = raw_part.map(normalize_part_number)

    # FIX: actually apply file-level brand to output.
    brand_col = mapping.get("brand")
    if brand_col and brand_col in df.columns:
        out["brand"] = df[brand_col].map(clean_text).str.upper()
        out["brand_source"] = "SOURCE_COLUMN"
    else:
        out["brand"] = str(meta.get("brand") or "").strip().upper()
        out["brand_source"] = "USER_ASSIGNED_FILE_LEVEL"

    out["description"] = mapped("description").map(clean_text)
    out["category"] = mapped("category").map(clean_text)
    out["market"] = meta.get("target_market", "")
    out["country"] = meta.get("source_country", "")
    out["currency"] = ""
    out["raw_price"] = raw_price.map(lambda x: "" if pd.isna(x) else str(x))

    parsed = [parse_price(x, meta.get("currency")) for x in raw_price]
    out["price"] = [x[0] for x in parsed]
    out["currency"] = [x[1] for x in parsed]
    out["price_parse_status"] = [x[2] for x in parsed]
    parse_reasons = [x[3] for x in parsed]

    # Price basis fields start empty; they must be supplied/verified later.
    out["price_ex_vat"] = ""
    out["price_inc_vat"] = ""
    out["vat_rate"] = ""
    out["vat_status"] = meta.get("vat_status", "VAT_UNKNOWN")

    out["quantity"] = mapped("quantity").map(clean_text)
    out["price_type"] = meta.get("price_type", "UNKNOWN")
    out["supplier"] = mapped("supplier").map(clean_text)
    out["supplier_type"] = meta.get("supplier_type", "")
    out["authorized_status"] = meta.get("authorized_status", "")
    out["source"] = meta.get("source_name", "")
    out["source_type"] = meta.get("source_type", "")
    out["price_evidence_level"] = meta.get("evidence_level", "")
    out["observation_date"] = mapped("observation_date").map(clean_text)
    out["source_url"] = mapped("source_url").map(clean_text)
    out["source_location"] = meta.get("source_location", "")
    out["shipping_adjustment_pct"] = pd.to_numeric(
        pd.Series([meta.get("shipping_adjustment_pct", 0)] * len(out)), errors="coerce"
    ).fillna(0.0)
    out["shipping_adjustment"] = out["price"] * out["shipping_adjustment_pct"] / 100.0
    out["benchmark_price"] = out["price"] + out["shipping_adjustment"]

    reasons = []
    valid = []
    for i, row in out.iterrows():
        r = []
        if not row["normalized_part_number"]:
            r.append("missing_part_number")
        if row["price_parse_status"] in ["MISSING", "AMBIGUOUS"]:
            r.append("missing_or_unreadable_price")
        if not row["brand"]:
            r.append("missing_brand")
        if not row["currency"]:
            r.append("missing_currency")
        if parse_reasons[i]:
            r.append(parse_reasons[i])
        reasons.append("; ".join(dict.fromkeys(r)))
        valid.append(len(r) == 0)

    out["valid"] = valid
    out["rejection_reason"] = reasons
    out["notes"] = meta.get("notes", "")

    return out[CANONICAL_COLUMNS]

def file_fingerprint(uploaded):
    return hashlib.sha256(uploaded.getvalue()).hexdigest()[:12]

# ============================================================
# UI
# ============================================================
st.title("Ajalty Intelligent Pricing Engine — MVP Dataset Builder R03")
st.caption("Deterministic ingestion/normalization layer. R03 fixes file-level brand assignment and robust commercial price parsing.")

if "files" not in st.session_state:
    st.session_state.files = []

st.subheader("1. Add source file")
uploaded = st.file_uploader(
    "Upload CSV or Excel",
    type=["csv", "xlsx", "xlsm", "xls"],
    key="uploader"
)

if uploaded:
    fp = file_fingerprint(uploaded)
    existing = [x["fingerprint"] for x in st.session_state.files]
    if fp not in existing:
        try:
            sheets = read_uploaded_file(uploaded)
            first_sheet = next(iter(sheets))
            df = sheets[first_sheet]
            st.session_state.current = {
                "fingerprint": fp,
                "filename": uploaded.name,
                "sheets": sheets,
                "sheet": first_sheet,
                "df": df,
                "mapping": make_mapping(df)
            }
        except Exception as e:
            st.error(str(e))

if "current" in st.session_state:
    cur = st.session_state.current
    sheets = cur["sheets"]

    cur["sheet"] = st.selectbox("Sheet", list(sheets.keys()), index=list(sheets.keys()).index(cur["sheet"]))
    cur["df"] = sheets[cur["sheet"]]
    df = cur["df"]

    st.write(f"Preview: **{len(df):,} rows × {len(df.columns)} columns**")
    st.dataframe(df.head(10), use_container_width=True)

    st.subheader("2. Map source columns")
    cols = ["(none)"] + list(df.columns)
    auto = cur["mapping"]

    def select_map(label, key, required=False):
        default = auto.get(key)
        idx = cols.index(default) if default in cols else 0
        return st.selectbox(label, cols, index=idx, key=f"map_{key}")

    mapping = {}
    mapping["part_number"] = select_map("Part number *", "part_number")
    mapping["price"] = select_map("Price *", "price")
    mapping["brand"] = select_map("Brand (optional)", "brand")
    mapping["description"] = select_map("Description", "description")
    mapping["category"] = select_map("Category", "category")
    mapping["quantity"] = select_map("Quantity", "quantity")
    mapping["currency"] = select_map("Currency column (optional)", "currency")
    mapping["vat_rate"] = select_map("VAT rate column (optional)", "vat_rate")
    mapping["supplier"] = select_map("Supplier", "supplier")
    mapping["source_url"] = select_map("Source URL", "source_url")
    mapping["observation_date"] = select_map("Observation date", "observation_date")

    # Remove sentinel.
    mapping = {k: (None if v == "(none)" else v) for k, v in mapping.items()}

    st.subheader("3. Source classification")
    c1, c2, c3 = st.columns(3)
    with c1:
        brand = st.text_input(
            "File-level brand",
            value="" if mapping.get("brand") else "TOYOTA",
            help="Used only when no brand column is mapped."
        )
        source_name = st.text_input("Source / supplier name")
        source_type = st.selectbox(
            "Source type",
            ["OEM_DEALERSHIP", "AUTHORIZED_DISTRIBUTOR", "WHOLESALER",
             "MARKETPLACE", "CUSTOMER_TRANSACTION", "PUBLIC_OEM", "OTHER"]
        )
    with c2:
        source_country = st.text_input("Source country", value="Saudi Arabia")
        source_location = st.text_input("Source location", value="Saudi Arabia")
        target_market = st.text_input("Target market", value="Saudi Arabia")
    with c3:
        currency = st.selectbox("Default currency", [""] + CURRENCY_CODES, index=1 if "SAR" in CURRENCY_CODES else 0)
        price_type = st.selectbox(
            "Price type",
            ["WHOLESALE", "TRADE", "DISTRIBUTOR", "DEALER", "RETAIL",
             "MSRP", "PUBLIC_OEM", "MARKETPLACE", "QUOTE", "TRANSACTION", "UNKNOWN"]
        )
        vat_status = st.selectbox("VAT status", ["VAT_UNKNOWN", "VAT_INCLUDED", "VAT_EXCLUDED"])

    c4, c5, c6 = st.columns(3)
    with c4:
        supplier_type = st.selectbox(
            "Supplier type",
            ["DEALERSHIP", "AUTHORIZED_DISTRIBUTOR", "WHOLESALER",
             "MARKETPLACE_SELLER", "CUSTOMER", "OTHER", ""]
        )
    with c5:
        authorized_status = st.selectbox(
            "Authorized status",
            ["VERIFIED", "NOT_VERIFIED", "UNKNOWN"]
        )
    with c6:
        evidence_level = st.selectbox("Evidence level", ["S1", "S2", "S3", "S4", "S5", "UNKNOWN"])

    shipping_pct = st.number_input(
        "Shipping / location adjustment % (explicit assumption; does NOT alter observed price)",
        value=0.0, step=0.5, format="%.2f"
    )
    notes = st.text_area("Notes / verification comments")

    st.subheader("4. Validate this file")
    if st.button("Process & Add File", type="primary"):
        # If a currency column is mapped, use its first non-empty value as
        # a fallback only. Explicit currency embedded in price still wins.
        fallback_currency = currency
        if mapping.get("currency"):
            vals = df[mapping["currency"]].dropna().astype(str).str.strip()
            if len(vals):
                detected = detect_currency(vals.iloc[0])
                if detected:
                    fallback_currency = detected

        meta = {
            "source_file": uploaded.name,
            "source_sheet": cur["sheet"],
            "brand": brand,
            "target_market": target_market,
            "source_country": source_country,
            "currency": fallback_currency,
            "vat_status": vat_status,
            "price_type": price_type,
            "supplier_type": supplier_type,
            "authorized_status": authorized_status,
            "source_name": source_name,
            "source_type": source_type,
            "evidence_level": evidence_level,
            "source_location": source_location,
            "shipping_adjustment_pct": shipping_pct,
            "notes": notes,
        }

        result = build_rows(df, mapping, meta)

        # Apply mapped currency column where the price itself has no currency.
        if mapping.get("currency"):
            for i, v in df[mapping["currency"]].items():
                if result.at[i, "price_parse_status"] == "CONTEXT":
                    cc = detect_currency(v)
                    if cc:
                        result.at[i, "currency"] = cc
                    elif clean_text(v):
                        result.at[i, "currency"] = clean_text(v).upper()

        # Re-evaluate currency validity.
        result["valid"] = result["valid"] & result["currency"].fillna("").astype(str).str.strip().ne("")
        result.loc[result["currency"].fillna("").astype(str).str.strip().eq(""), "rejection_reason"] = (
            result.loc[result["currency"].fillna("").astype(str).str.strip().eq(""), "rejection_reason"]
            .astype(str).str.replace(r"(?<!missing_currency)(^|; )missing_currency", "missing_currency", regex=True)
        )

        st.session_state.files.append({
            "fingerprint": fp,
            "name": uploaded.name,
            "data": result
        })
        del st.session_state.current
        st.rerun()

if st.session_state.files:
    st.divider()
    st.subheader("5. Files added")

    for idx, item in enumerate(st.session_state.files):
        data = item["data"]
        valid_n = int(data["valid"].sum())
        st.write(f"**{idx+1}. {item['name']}** — {len(data):,} rows | valid: {valid_n:,} | rejected: {len(data)-valid_n:,}")
        if st.button(f"Remove {idx+1}", key=f"remove_{idx}"):
            st.session_state.files.pop(idx)
            st.rerun()

    combined = pd.concat([x["data"] for x in st.session_state.files], ignore_index=True)

    st.subheader("6. Combined preview")
    st.dataframe(combined.head(50), use_container_width=True)

    st.write("### Validation summary")
    summary = (
        combined.groupby(["valid"], dropna=False)
        .size()
        .rename("rows")
        .reset_index()
    )
    st.dataframe(summary, use_container_width=True)

    rejection = (
        combined.loc[~combined["valid"], "rejection_reason"]
        .fillna("")
        .replace("", "unknown")
        .value_counts()
        .rename_axis("rejection_reason")
        .reset_index(name="rows")
    )
    st.dataframe(rejection.head(30), use_container_width=True)

    csv_bytes = combined.to_csv(index=False).encode("utf-8-sig")
    xlsx_buf = io.BytesIO()
    with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
        combined.to_excel(writer, index=False, sheet_name="benchmark")
        rejection.to_excel(writer, index=False, sheet_name="rejections")
    xlsx_buf.seek(0)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "Download combined CSV",
            csv_bytes,
            file_name="benchmark_R03.csv",
            mime="text/csv"
        )
    with c2:
        st.download_button(
            "Download combined Excel",
            xlsx_buf.getvalue(),
            file_name="benchmark_R03.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    st.info(
        "R03 intentionally does not calculate an inferred wholesale price. "
        "Observed price and benchmark_price remain separate. "
        "The next MVP stage is deterministic validation/normalization and the ratio baseline."
    )
