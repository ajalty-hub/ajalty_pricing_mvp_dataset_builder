import io
import re
import math
import hashlib

import pandas as pd
import streamlit as st

# ============================================================
# Ajalty Intelligent Pricing Engine — MVP
# Dataset Builder R04
#
# R04 is a controlled update of R03.1.
#
# Changes from R03.1:
#   1. Multiple-file upload workflow is preserved.
#   2. Each processed file remains in session state.
#   3. "Add another file" creates a fresh uploader.
#   4. Row-level validation details are displayed.
#   5. Rejection reasons can be filtered.
#   6. Dataset field definitions are displayed.
#
# Existing R03.1 functionality retained:
#   - File-level brand assignment
#   - Price parsing
#   - Currency detection
#   - Raw price preservation
#   - Part-number normalization
#   - Evidence levels
#   - Observed price / shipping adjustment separation
# ============================================================

st.set_page_config(
    page_title="Ajalty Pricing Engine MVP — R04",
    layout="wide"
)

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

MVP_ALLOWED_BRANDS = ["TOYOTA"]

CANONICAL_COLUMNS = [
    "source_file",
    "source_sheet",
    "part_number",
    "normalized_part_number",
    "brand",
    "brand_source",
    "description",
    "category",
    "market",
    "country",
    "currency",
    "raw_price",
    "price",
    "price_parse_status",
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

CURRENCY_CODES = [
    "SAR",
    "AED",
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CNY",
    "THB",
    "MYR",
    "KWD",
    "BHD",
    "QAR",
    "OMR",
    "INR",
    "RUB",
    "TRY",
    "SGD",
    "AUD",
    "CAD",
    "HKD",
]

EVIDENCE_LEVELS = {
    "S1": (
        "OEM / Authorized Distributor B2B",
        "OEM or verified authorized distributor with a genuine B2B/trade price."
    ),
    "S2": (
        "Genuine Specialist Wholesaler",
        "Verified genuine OEM-parts wholesaler/specialist offering trade or wholesale pricing."
    ),
    "S3": (
        "B2B Genuine Platform / Marketplace",
        "B2B platform or marketplace offering genuine parts, with seller/terms requiring more verification."
    ),
    "S4": (
        "Official OEM Public Pricing",
        "Official OEM/dealer public price or MSRP/list price. Useful as a reference but not automatically wholesale."
    ),
    "S5": (
        "General Web / Marketplace",
        "Public web or marketplace evidence where seller, genuineness, price basis, or commercial terms are less certain."
    ),
    "UNKNOWN": (
        "Not yet verified",
        "Evidence level has not been verified. Do not treat it as strong benchmark evidence."
    ),
}

FIELD_DEFINITIONS = {
    "source_file": "Original uploaded file.",
    "source_sheet": "Excel sheet from which the observation was read.",
    "part_number": "Original part number as supplied by the source.",
    "normalized_part_number": "Normalized part number used for deterministic matching.",
    "brand": "Brand assigned to the observation.",
    "brand_source": "Whether brand came from the source column or user file-level assignment.",
    "description": "Original product/part description.",
    "category": "Product category where available.",
    "market": "Target market for the comparison.",
    "country": "Country associated with the source observation.",
    "currency": "Currency of the observed price.",
    "raw_price": "Original price value before numeric parsing.",
    "price": "Numeric observed price after deterministic parsing.",
    "price_parse_status": "EXTRACTED, CONTEXT, MISSING, or AMBIGUOUS.",
    "price_ex_vat": "Price excluding VAT when explicitly established.",
    "price_inc_vat": "Price including VAT when explicitly established.",
    "vat_rate": "VAT percentage where available.",
    "vat_status": "VAT_INCLUDED, VAT_EXCLUDED, or VAT_UNKNOWN.",
    "quantity": "Quantity associated with the price.",
    "price_type": "WHOLESALE, TRADE, RETAIL, MARKETPLACE, TRANSACTION, etc.",
    "supplier": "Supplier/vendor name where available.",
    "supplier_type": "Dealership, wholesaler, marketplace seller, customer, etc.",
    "authorized_status": "Whether supplier authorization is verified.",
    "source": "Named source/supplier/platform.",
    "source_type": "Classification of the source.",
    "price_evidence_level": "S1-S5 evidence classification.",
    "observation_date": "Date associated with the price observation.",
    "source_url": "Source URL where available.",
    "source_location": "Geographic location of the source price.",
    "shipping_adjustment_pct": "Explicit location/shipping adjustment assumption.",
    "shipping_adjustment": "Calculated adjustment amount; does not overwrite observed price.",
    "benchmark_price": "Observed price plus explicit adjustment. Not the final predicted market price.",
    "valid": "Whether the observation passes the current deterministic validation checks.",
    "rejection_reason": "Reason a row failed validation.",
    "notes": "Manual verification/comments.",
}

# ------------------------------------------------------------
# Text / normalization helpers
# ------------------------------------------------------------

def clean_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_part_number(value):
    if pd.isna(value):
        return ""

    text = str(value).strip().upper()

    # Remove common separators while preserving the actual identity.
    return re.sub(r"[\s\-_./]+", "", text)


def header_key(value):
    """Normalize a column header for automatic mapping."""
    return re.sub(r"[^A-Z0-9]+", "", str(value).upper())


# ------------------------------------------------------------
# Currency / price parsing
# ------------------------------------------------------------

def detect_currency(raw):
    if raw is None:
        return None

    if isinstance(raw, float) and math.isnan(raw):
        return None

    text = str(raw).upper()

    # Currency codes.
    for code in sorted(CURRENCY_CODES, key=len, reverse=True):
        pattern = rf"(?<![A-Z]){re.escape(code)}(?![A-Z])"
        if re.search(pattern, text):
            return code

    # Common symbols.
    if "﷼" in text:
        return "SAR"

    if "د.إ" in text:
        return "AED"

    if "$" in text:
        return "USD"

    if "€" in text:
        return "EUR"

    if "£" in text:
        return "GBP"

    if "¥" in text:
        return "JPY"

    return None


def parse_price(raw_value, fallback_currency=None):
    """
    Deterministically extract a numeric price.

    Examples:
        100 SAR       -> 100, SAR, EXTRACTED
        SAR 100       -> 100, SAR, EXTRACTED
        1,250 SAR     -> 1250, SAR, EXTRACTED
        AED 45.50     -> 45.50, AED, EXTRACTED
        100            -> 100, fallback, CONTEXT

    Multiple numeric values are marked AMBIGUOUS rather than guessed.
    """

    if raw_value is None:
        return None, fallback_currency, "MISSING", "missing_price"

    if isinstance(raw_value, float) and math.isnan(raw_value):
        return None, fallback_currency, "MISSING", "missing_price"

    raw = str(raw_value).strip()

    if not raw:
        return None, fallback_currency, "MISSING", "missing_price"

    currency = detect_currency(raw)

    cleaned = raw.upper()

    # Correctly formed regex. This was the R03 crash source.
    for code in CURRENCY_CODES:
        pattern = rf"(?<![A-Z]){re.escape(code)}(?![A-Z])"
        cleaned = re.sub(pattern, " ", cleaned)

    cleaned = cleaned.replace("﷼", " ")
    cleaned = cleaned.replace("د.إ", " ")
    cleaned = cleaned.replace("$", " ")
    cleaned = cleaned.replace("€", " ")
    cleaned = cleaned.replace("£", " ")
    cleaned = cleaned.replace("¥", " ")

    cleaned = re.sub(
        r"\b(?:INC\.?|EX\.?)\s*VAT\b",
        " ",
        cleaned
    )

    cleaned = re.sub(
        r"\b\+?\s*VAT\b",
        " ",
        cleaned
    )

    cleaned = re.sub(
        r"\bPER\s+(?:SET|PCS?|UNIT|PAIR|KIT)\b",
        " ",
        cleaned
    )

    cleaned = re.sub(
        r"/\s*(?:SET|PCS?|UNIT|PAIR|KIT)\b",
        " ",
        cleaned
    )

    cleaned = cleaned.replace(",", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Do not guess if several unrelated numbers exist.
    matches = re.findall(
        r"(?<![A-Z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)",
        cleaned
    )

    if not matches:
        return (
            None,
            currency or fallback_currency,
            "MISSING",
            "no_numeric_value"
        )

    if len(matches) > 1:
        return (
            None,
            currency or fallback_currency,
            "AMBIGUOUS",
            "multiple_numeric_values"
        )

    try:
        value = float(matches[0])
    except ValueError:
        return (
            None,
            currency or fallback_currency,
            "MISSING",
            "numeric_parse_failed"
        )

    if not math.isfinite(value) or value < 0:
        return (
            None,
            currency or fallback_currency,
            "MISSING",
            "invalid_numeric_value"
        )

    if currency:
        return value, currency, "EXTRACTED", ""

    if fallback_currency:
        return (
            value,
            str(fallback_currency).upper().strip(),
            "CONTEXT",
            ""
        )

    return value, None, "CONTEXT", "currency_not_found"


# ------------------------------------------------------------
# File loading
# ------------------------------------------------------------

def read_uploaded_file(uploaded):
    name = uploaded.name.lower()
    data = uploaded.getvalue()

    if name.endswith(".csv"):
        for encoding in [
            "utf-8-sig",
            "utf-8",
            "cp1256",
            "latin1",
        ]:
            try:
                return {
                    "Sheet1": pd.read_csv(
                        io.BytesIO(data),
                        encoding=encoding
                    )
                }
            except Exception:
                continue

        raise ValueError("Could not read CSV file.")

    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(
            io.BytesIO(data),
            sheet_name=None
        )

    raise ValueError(
        "Unsupported file type. Please use CSV or Excel."
    )


# ------------------------------------------------------------
# Automatic column mapping
# ------------------------------------------------------------

def guess_column(columns, keyword_groups):
    """
    Return the best matching source column.

    keyword_groups:
        [
            (["EXACTHEADER", "OTHERHEADER"], 100),
            (["PART"], 70)
        ]
    """

    scored = []

    for column in columns:
        normalized = header_key(column)
        score = 0

        for group, weight in keyword_groups:
            for keyword in group:
                key = header_key(keyword)

                if normalized == key:
                    score = max(score, weight + 20)

                elif key in normalized:
                    score = max(score, weight)

        if score:
            scored.append((score, column))

    if not scored:
        return None

    return max(scored, key=lambda item: item[0])[1]


def make_mapping(df):
    columns = list(df.columns)

    return {
        "part_number": guess_column(
            columns,
            [
                (
                    [
                        "PART NUMBER",
                        "PARTNUMBER",
                        "PART NO",
                        "PARTNO",
                        "PART NUM",
                        "PARTNUM",
                        "OEM NUMBER",
                        "OEMNUMBER",
                        "OEM NO",
                        "OEMNO",
                    ],
                    100,
                ),
                (["PART"], 70),
            ],
        ),

        "price": guess_column(
            columns,
            [
                (
                    [
                        "PRICE",
                        "UNIT PRICE",
                        "UNITPRICE",
                        "SELLING PRICE",
                        "SELLINGPRICE",
                        "WHOLESALE PRICE",
                        "WHOLESALEPRICE",
                        "TRADE PRICE",
                        "TRADEPRICE",
                        "COST",
                    ],
                    100,
                ),
                (["AMOUNT", "VALUE"], 60),
            ],
        ),

        "brand": guess_column(
            columns,
            [
                (
                    ["BRAND", "MAKE", "MARQUE"],
                    100,
                )
            ],
        ),

        "description": guess_column(
            columns,
            [
                (
                    [
                        "DESCRIPTION",
                        "PART DESCRIPTION",
                        "PARTDESCRIPTION",
                        "DESC",
                        "NAME",
                    ],
                    80,
                )
            ],
        ),

        "category": guess_column(
            columns,
            [
                (
                    [
                        "CATEGORY",
                        "GROUP",
                        "PRODUCT GROUP",
                        "PRODUCTGROUP",
                    ],
                    80,
                )
            ],
        ),

        "quantity": guess_column(
            columns,
            [
                (
                    [
                        "QUANTITY",
                        "QTY",
                        "ORDER QTY",
                        "ORDERQTY",
                    ],
                    90,
                )
            ],
        ),

        "currency": guess_column(
            columns,
            [
                (
                    [
                        "CURRENCY",
                        "CUR",
                        "CURRENCY CODE",
                        "CURRENCYCODE",
                    ],
                    90,
                )
            ],
        ),

        "vat_rate": guess_column(
            columns,
            [
                (
                    [
                        "VAT RATE",
                        "VATRATE",
                        "VAT PERCENT",
                        "VATPERCENT",
                        "VAT",
                    ],
                    80,
                )
            ],
        ),

        "supplier": guess_column(
            columns,
            [
                (
                    ["SUPPLIER", "VENDOR", "SELLER"],
                    80,
                )
            ],
        ),

        "source_url": guess_column(
            columns,
            [
                (
                    [
                        "SOURCE URL",
                        "SOURCEURL",
                        "URL",
                        "LINK",
                        "WEB URL",
                        "WEBURL",
                    ],
                    80,
                )
            ],
        ),

        "observation_date": guess_column(
            columns,
            [
                (
                    [
                        "OBSERVATION DATE",
                        "OBSERVATIONDATE",
                        "UPDATED DATE",
                        "UPDATEDDATE",
                        "UPDATED",
                        "DATE",
                    ],
                    70,
                )
            ],
        ),
    }


# ------------------------------------------------------------
# Row construction
# ------------------------------------------------------------

def build_rows(df, mapping, meta):
    out = pd.DataFrame(index=df.index)

    def mapped(field_name):
        column = mapping.get(field_name)

        if column and column in df.columns:
            return df[column]

        return pd.Series(
            [""] * len(df),
            index=df.index
        )

    raw_part = mapped("part_number")
    raw_price = mapped("price")

    out["source_file"] = meta["source_file"]
    out["source_sheet"] = meta["source_sheet"]

    out["part_number"] = raw_part.map(clean_text)
    out["normalized_part_number"] = raw_part.map(
        normalize_part_number
    )

    # File-level brand is used only when no source brand column exists.
    brand_column = mapping.get("brand")

    if brand_column and brand_column in df.columns:
        out["brand"] = (
            df[brand_column]
            .map(clean_text)
            .str.upper()
        )
        out["brand_source"] = "SOURCE_COLUMN"

    else:
        out["brand"] = (
            str(meta.get("brand") or "")
            .strip()
            .upper()
        )
        out["brand_source"] = "USER_ASSIGNED_FILE_LEVEL"

    out["description"] = mapped(
        "description"
    ).map(clean_text)

    out["category"] = mapped(
        "category"
    ).map(clean_text)

    out["market"] = meta.get(
        "target_market",
        ""
    )

    out["country"] = meta.get(
        "source_country",
        ""
    )

    # Preserve exact source price.
    out["raw_price"] = raw_price.map(
        lambda value: (
            ""
            if pd.isna(value)
            else str(value)
        )
    )

    parsed = [
        parse_price(
            value,
            meta.get("currency")
        )
        for value in raw_price
    ]

    out["price"] = [
        item[0]
        for item in parsed
    ]

    out["currency"] = [
        item[1]
        for item in parsed
    ]

    out["price_parse_status"] = [
        item[2]
        for item in parsed
    ]

    parse_reasons = [
        item[3]
        for item in parsed
    ]

    # If a currency column exists, use it only where
    # the raw price itself did not contain a currency.
    currency_column = mapping.get("currency")

    if currency_column and currency_column in df.columns:
        for index, value in df[currency_column].items():

            if out.at[
                index,
                "price_parse_status"
            ] == "CONTEXT":

                detected = detect_currency(value)

                if detected:
                    out.at[
                        index,
                        "currency"
                    ] = detected

                elif clean_text(value):
                    out.at[
                        index,
                        "currency"
                    ] = clean_text(
                        value
                    ).upper()

    # These remain blank unless explicitly supplied/verified.
    out["price_ex_vat"] = ""
    out["price_inc_vat"] = ""
    out["vat_rate"] = ""

    out["vat_status"] = meta.get(
        "vat_status",
        "VAT_UNKNOWN"
    )

    out["quantity"] = mapped(
        "quantity"
    ).map(clean_text)

    out["price_type"] = meta.get(
        "price_type",
        "UNKNOWN"
    )

    out["supplier"] = mapped(
        "supplier"
    ).map(clean_text)

    out["supplier_type"] = meta.get(
        "supplier_type",
        ""
    )

    out["authorized_status"] = meta.get(
        "authorized_status",
        ""
    )

    out["source"] = meta.get(
        "source_name",
        ""
    )

    out["source_type"] = meta.get(
        "source_type",
        ""
    )

    out["price_evidence_level"] = meta.get(
        "evidence_level",
        "UNKNOWN"
    )

    out["observation_date"] = mapped(
        "observation_date"
    ).map(clean_text)

    out["source_url"] = mapped(
        "source_url"
    ).map(clean_text)

    out["source_location"] = meta.get(
        "source_location",
        ""
    )

    adjustment_pct = float(
        meta.get(
            "shipping_adjustment_pct",
            0
        ) or 0
    )

    out["shipping_adjustment_pct"] = adjustment_pct

    numeric_price = pd.to_numeric(
        out["price"],
        errors="coerce"
    )

    out["shipping_adjustment"] = (
        numeric_price
        * adjustment_pct
        / 100.0
    )

    out["benchmark_price"] = (
        numeric_price
        + out["shipping_adjustment"]
    )

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    reasons = []
    valid = []

    for index, row in out.iterrows():

        row_reasons = []

        if not row["normalized_part_number"]:
            row_reasons.append(
                "missing_part_number"
            )

        if row["price_parse_status"] in [
            "MISSING",
            "AMBIGUOUS",
        ]:
            row_reasons.append(
                "missing_or_unreadable_price"
            )

        if not row["brand"]:
            row_reasons.append(
                "missing_brand"
            )

        if not row["currency"]:
            row_reasons.append(
                "missing_currency"
            )

        if parse_reasons[index]:
            row_reasons.append(
                parse_reasons[index]
            )

        if (
            pd.notna(row["price"])
            and float(row["price"]) <= 0
        ):
            row_reasons.append(
                "non_positive_price"
            )

        # Remove duplicates while preserving order.
        row_reasons = list(
            dict.fromkeys(row_reasons)
        )

        reasons.append(
            "; ".join(row_reasons)
        )

        valid.append(
            len(row_reasons) == 0
        )

    out["valid"] = valid
    out["rejection_reason"] = reasons

    out["notes"] = meta.get(
        "notes",
        ""
    )

    return out[CANONICAL_COLUMNS]


def file_fingerprint(uploaded):
    return hashlib.sha256(
        uploaded.getvalue()
    ).hexdigest()[:12]


# ------------------------------------------------------------
# Streamlit session state
# ------------------------------------------------------------

if "files" not in st.session_state:
    st.session_state.files = []

if "upload_slot" not in st.session_state:
    st.session_state.upload_slot = 0

if "current" not in st.session_state:
    st.session_state.current = None


# ------------------------------------------------------------
# Header
# ------------------------------------------------------------

st.title(
    "Ajalty Intelligent Pricing Engine — MVP"
)

st.caption(
    "R04 — Dataset Builder / Evidence & Validation Layer"
)


# ------------------------------------------------------------
# Evidence definitions
# ------------------------------------------------------------

with st.expander(
    "Evidence Level Definitions",
    expanded=False
):

    evidence_table = pd.DataFrame(
        [
            {
                "Level": level,
                "Category": name,
                "Definition": definition,
            }
            for level, (
                name,
                definition
            ) in EVIDENCE_LEVELS.items()
        ]
    )

    st.dataframe(
        evidence_table,
        use_container_width=True,
        hide_index=True
    )


# ------------------------------------------------------------
# Dataset field definitions
# ------------------------------------------------------------

with st.expander(
    "Dataset Field Definitions",
    expanded=False
):

    definitions_table = pd.DataFrame(
        [
            {
                "Column": column,
                "Meaning": FIELD_DEFINITIONS.get(
                    column,
                    ""
                ),
            }
            for column in CANONICAL_COLUMNS
        ]
    )

    st.dataframe(
        definitions_table,
        use_container_width=True,
        hide_index=True
    )


# ------------------------------------------------------------
# Current uploaded file
# ------------------------------------------------------------

if st.session_state.current is None:

    st.subheader(
        "1. Add source file"
    )

    uploaded = st.file_uploader(
        "Upload CSV or Excel",
        type=[
            "csv",
            "xlsx",
            "xlsm",
            "xls",
        ],
        key=f"uploader_{st.session_state.upload_slot}",
    )

    if uploaded is not None:

        fingerprint = file_fingerprint(
            uploaded
        )

        already_added = [
            item["fingerprint"]
            for item in st.session_state.files
        ]

        if fingerprint in already_added:

            st.warning(
                "This exact file has already been added."
            )

        else:

            try:

                sheets = read_uploaded_file(
                    uploaded
                )

                first_sheet = next(
                    iter(sheets)
                )

                df = sheets[first_sheet]

                st.session_state.current = {
                    "fingerprint": fingerprint,
                    "filename": uploaded.name,
                    "sheets": sheets,
                    "sheet": first_sheet,
                    "df": df,
                    "mapping": make_mapping(df),
                }

                st.rerun()

            except Exception as error:

                st.error(
                    f"Could not read file: {error}"
                )


# ------------------------------------------------------------
# File mapping / processing
# ------------------------------------------------------------

if st.session_state.current is not None:

    current = st.session_state.current

    sheets = current["sheets"]

    st.subheader(
        f"2. Configure file: {current['filename']}"
    )

    selected_sheet = st.selectbox(
        "Excel sheet",
        list(sheets.keys()),
        index=list(
            sheets.keys()
        ).index(
            current["sheet"]
        ),
        key="current_sheet",
    )

    current["sheet"] = selected_sheet
    current["df"] = sheets[selected_sheet]

    df = current["df"]

    st.write(
        f"Preview: **{len(df):,} rows × "
        f"{len(df.columns):,} columns**"
    )

    st.dataframe(
        df.head(10),
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # Column mapping
    # --------------------------------------------------------

    st.subheader(
        "3. Column mapping"
    )

    columns = [
        "(none)"
    ] + list(df.columns)

    automatic_mapping = make_mapping(df)

    def mapping_selector(
        label,
        field_name
    ):

        automatic = automatic_mapping.get(
            field_name
        )

        default_index = (
            columns.index(automatic)
            if automatic in columns
            else 0
        )

        return st.selectbox(
            label,
            columns,
            index=default_index,
            key=f"mapping_{field_name}",
        )

    mapping = {}

    mapping["part_number"] = mapping_selector(
        "Part number *",
        "part_number"
    )

    mapping["price"] = mapping_selector(
        "Price *",
        "price"
    )

    mapping["brand"] = mapping_selector(
        "Brand (optional)",
        "brand"
    )

    mapping["description"] = mapping_selector(
        "Description",
        "description"
    )

    mapping["category"] = mapping_selector(
        "Category",
        "category"
    )

    mapping["quantity"] = mapping_selector(
        "Quantity",
        "quantity"
    )

    mapping["currency"] = mapping_selector(
        "Currency column (optional)",
        "currency"
    )

    mapping["vat_rate"] = mapping_selector(
        "VAT rate column (optional)",
        "vat_rate"
    )

    mapping["supplier"] = mapping_selector(
        "Supplier",
        "supplier"
    )

    mapping["source_url"] = mapping_selector(
        "Source URL",
        "source_url"
    )

    mapping["observation_date"] = mapping_selector(
        "Observation date",
        "observation_date"
    )

    mapping = {
        field: (
            None
            if value == "(none)"
            else value
        )
        for field, value in mapping.items()
    }

    # --------------------------------------------------------
    # Source metadata
    # --------------------------------------------------------

    st.subheader(
        "4. Source classification"
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        brand = st.text_input(
            "File-level brand",
            value=(
                ""
                if mapping.get("brand")
                else "TOYOTA"
            ),
            help=(
                "Used only when no brand "
                "column is mapped."
            ),
            key="file_brand",
        )

        source_name = st.text_input(
            "Source / supplier name",
            key="source_name",
        )

        source_type = st.selectbox(
            "Source type",
            [
                "OEM_DEALERSHIP",
                "AUTHORIZED_DISTRIBUTOR",
                "WHOLESALER",
                "MARKETPLACE",
                "CUSTOMER_TRANSACTION",
                "PUBLIC_OEM",
                "OTHER",
            ],
            key="source_type",
        )

    with col2:

        source_country = st.text_input(
            "Source country",
            value="Saudi Arabia",
            key="source_country",
        )

        source_location = st.text_input(
            "Source location",
            value="Saudi Arabia",
            key="source_location",
        )

        target_market = st.text_input(
            "Target market",
            value="Saudi Arabia",
            key="target_market",
        )

    with col3:

        currency = st.selectbox(
            "Default currency",
            [""] + CURRENCY_CODES,
            index=1,
            key="default_currency",
        )

        price_type = st.selectbox(
            "Price type",
            [
                "WHOLESALE",
                "TRADE",
                "DISTRIBUTOR",
                "DEALER",
                "RETAIL",
                "MSRP",
                "PUBLIC_OEM",
                "MARKETPLACE",
                "QUOTE",
                "TRANSACTION",
                "UNKNOWN",
            ],
            key="price_type",
        )

        vat_status = st.selectbox(
            "VAT status",
            [
                "VAT_UNKNOWN",
                "VAT_INCLUDED",
                "VAT_EXCLUDED",
            ],
            key="vat_status",
        )

    col4, col5, col6 = st.columns(3)

    with col4:

        supplier_type = st.selectbox(
            "Supplier type",
            [
                "DEALERSHIP",
                "AUTHORIZED_DISTRIBUTOR",
                "WHOLESALER",
                "MARKETPLACE_SELLER",
                "CUSTOMER",
                "OTHER",
                "",
            ],
            key="supplier_type",
        )

    with col5:

        authorized_status = st.selectbox(
            "Authorized status",
            [
                "VERIFIED",
                "NOT_VERIFIED",
                "UNKNOWN",
            ],
            key="authorized_status",
        )

    with col6:

        evidence_level = st.selectbox(
            "Evidence level",
            list(EVIDENCE_LEVELS.keys()),
            key="evidence_level",
        )

        st.caption(
            EVIDENCE_LEVELS[
                evidence_level
            ][0]
        )

    shipping_pct = st.number_input(
        "Shipping / location adjustment % "
        "(explicit assumption; does NOT alter observed price)",
        value=0.0,
        step=0.5,
        format="%.2f",
        key="shipping_pct",
    )

    notes = st.text_area(
        "Notes / verification comments",
        key="file_notes",
    )

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    st.subheader(
        "5. Process file"
    )

    process_file = st.button(
        "Process & Add File",
        type="primary",
        key="process_current_file",
    )

    if process_file:

        if not mapping.get("part_number"):

            st.error(
                "Part number column is required."
            )

        elif not mapping.get("price"):

            st.error(
                "Price column is required."
            )

        elif (
            not mapping.get("brand")
            and not brand
        ):

            st.error(
                "Enter a file-level brand "
                "when no brand column is mapped."
            )

        else:

            metadata = {
                "source_file": current[
                    "filename"
                ],
                "source_sheet": current[
                    "sheet"
                ],
                "brand": brand,
                "target_market": target_market,
                "source_country": source_country,
                "currency": currency,
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

            try:

                result = build_rows(
                    df,
                    mapping,
                    metadata
                )

                st.session_state.files.append(
                    {
                        "fingerprint": current[
                            "fingerprint"
                        ],
                        "name": current[
                            "filename"
                        ],
                        "data": result,
                    }
                )

                # Clear current file.
                st.session_state.current = None

                # IMPORTANT:
                # Change uploader key so Streamlit gives us
                # a fresh uploader for the next file.
                st.session_state.upload_slot += 1

                st.success(
                    f"Added {current['filename']} "
                    f"successfully."
                )

                st.rerun()

            except Exception as error:

                st.exception(error)


# ------------------------------------------------------------
# Added files
# ------------------------------------------------------------

if st.session_state.files:

    st.divider()

    st.subheader(
        "6. Files added"
    )

    file_summary = []

    for index, item in enumerate(
        st.session_state.files
    ):

        data = item["data"]

        valid_count = int(
            data["valid"].sum()
        )

        invalid_count = (
            len(data)
            - valid_count
        )

        file_summary.append(
            {
                "File": item["name"],
                "Rows": len(data),
                "Valid": valid_count,
                "Invalid": invalid_count,
            }
        )

    st.dataframe(
        pd.DataFrame(file_summary),
        use_container_width=True,
        hide_index=True
    )

    for index, item in enumerate(
        st.session_state.files
    ):

        data = item["data"]

        valid_count = int(
            data["valid"].sum()
        )

        invalid_count = (
            len(data)
            - valid_count
        )

        with st.expander(
            f"{index + 1}. {item['name']} "
            f"— {len(data):,} rows | "
            f"{valid_count:,} valid | "
            f"{invalid_count:,} invalid"
        ):

            st.dataframe(
                data.head(20),
                use_container_width=True,
                hide_index=True
            )

            if st.button(
                f"Remove file {index + 1}",
                key=f"remove_file_{index}",
            ):

                st.session_state.files.pop(
                    index
                )

                st.rerun()

    # --------------------------------------------------------
    # Add another file button
    # --------------------------------------------------------

    if st.button(
        "➕ Add Another File",
        key="add_another_file",
    ):

        st.session_state.current = None
        st.session_state.upload_slot += 1
        st.rerun()


# ------------------------------------------------------------
# Combined dataset
# ------------------------------------------------------------

if st.session_state.files:

    combined = pd.concat(
        [
            item["data"]
            for item in st.session_state.files
        ],
        ignore_index=True
    )

    st.divider()

    st.subheader(
        "7. Combined dataset"
    )

    st.write(
        f"**{len(combined):,} total observations** "
        f"from **{len(st.session_state.files)} file(s)**"
    )

    # --------------------------------------------------------
    # Combined preview
    # --------------------------------------------------------

    st.dataframe(
        combined.head(50),
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # Validation summary
    # --------------------------------------------------------

    st.subheader(
        "8. Validation summary"
    )

    valid_count = int(
        combined["valid"].sum()
    )

    invalid_count = (
        len(combined)
        - valid_count
    )

    summary = pd.DataFrame(
        [
            {
                "Status": "Valid",
                "Rows": valid_count,
            },
            {
                "Status": "Invalid",
                "Rows": invalid_count,
            },
        ]
    )

    st.dataframe(
        summary,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # Rejection summary
    # --------------------------------------------------------

    st.subheader(
        "9. Rejection summary"
    )

    invalid_rows = combined[
        ~combined["valid"]
    ]

    if len(invalid_rows) == 0:

        st.success(
            "No invalid rows."
        )

    else:

        rejection_summary = (
            invalid_rows[
                "rejection_reason"
            ]
            .fillna("unknown")
            .replace(
                "",
                "unknown"
            )
            .value_counts()
            .rename_axis(
                "rejection_reason"
            )
            .reset_index(
                name="rows"
            )
        )

        st.dataframe(
            rejection_summary,
            use_container_width=True,
            hide_index=True
        )

    # --------------------------------------------------------
    # Row-level validation details
    # --------------------------------------------------------

    st.subheader(
        "10. Validation details"
    )

    st.caption(
        "Use this section to see exactly which rows "
        "are invalid and why."
    )

    status_filter = st.selectbox(
        "Show rows",
        [
            "All",
            "Valid only",
            "Invalid only",
        ],
        key="validation_status_filter",
    )

    validation_view = combined.copy()

    if status_filter == "Valid only":

        validation_view = validation_view[
            validation_view["valid"]
        ]

    elif status_filter == "Invalid only":

        validation_view = validation_view[
            ~validation_view["valid"]
        ]

    available_reasons = sorted(
        [
            reason
            for reason in (
                combined.loc[
                    ~combined["valid"],
                    "rejection_reason"
                ]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )
            if reason
        ]
    )

    reason_options = [
        "All"
    ] + available_reasons

    selected_reason = st.selectbox(
        "Filter by exact rejection reason",
        reason_options,
        key="validation_reason_filter",
    )

    if selected_reason != "All":

        validation_view = validation_view[
            validation_view[
                "rejection_reason"
            ] == selected_reason
        ]

    validation_columns = [
        "source_file",
        "source_sheet",
        "part_number",
        "normalized_part_number",
        "brand",
        "raw_price",
        "price",
        "currency",
        "price_parse_status",
        "quantity",
        "price_type",
        "valid",
        "rejection_reason",
        "notes",
    ]

    st.write(
        f"Showing **{len(validation_view):,} row(s)**"
    )

    st.dataframe(
        validation_view[
            validation_columns
        ],
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # Source/file summary
    # --------------------------------------------------------

    st.subheader(
        "11. Source file quality summary"
    )

    source_summary = (
        combined.groupby(
            "source_file",
            dropna=False
        )
        .agg(
            rows=("source_file", "size"),
            valid=("valid", "sum"),
        )
        .reset_index()
    )

    source_summary["invalid"] = (
        source_summary["rows"]
        - source_summary["valid"]
    )

    st.dataframe(
        source_summary,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # Downloads
    # --------------------------------------------------------

    st.subheader(
        "12. Export"
    )

    csv_bytes = combined.to_csv(
        index=False
    ).encode("utf-8-sig")

    excel_buffer = io.BytesIO()

    with pd.ExcelWriter(
        excel_buffer,
        engine="openpyxl"
    ) as writer:

        combined.to_excel(
            writer,
            index=False,
            sheet_name="benchmark"
        )

        rejection_summary = (
            invalid_rows[
                "rejection_reason"
            ]
            .fillna("unknown")
            .replace(
                "",
                "unknown"
            )
            .value_counts()
            .rename_axis(
                "rejection_reason"
            )
            .reset_index(
                name="rows"
            )
        )

        rejection_summary.to_excel(
            writer,
            index=False,
            sheet_name="rejections"
        )

        source_summary.to_excel(
            writer,
            index=False,
            sheet_name="source_summary"
        )

    excel_buffer.seek(0)

    download_col1, download_col2 = st.columns(2)

    with download_col1:

        st.download_button(
            "Download combined CSV",
            csv_bytes,
            file_name="benchmark_R04.csv",
            mime="text/csv",
            key="download_csv",
        )

    with download_col2:

        st.download_button(
            "Download combined Excel",
            excel_buffer.getvalue(),
            file_name="benchmark_R04.xlsx",
            mime=(
                "application/vnd.openxmlformats-"
                "officedocument.spreadsheetml.sheet"
            ),
            key="download_excel",
        )

    # --------------------------------------------------------
    # Methodology reminder
    # --------------------------------------------------------

    st.info(
        "R04 is still the deterministic ingestion and "
        "validation layer. It does not infer the final "
        "Saudi market price. Observed price, adjusted "
        "benchmark observation, and future predicted price "
        "remain separate."
    )
