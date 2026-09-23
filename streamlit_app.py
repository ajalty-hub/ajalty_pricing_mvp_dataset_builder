
import io
import re
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


# ============================================================
# Ajalty Intelligent Pricing Engine — MVP
# G1 Dataset Builder / R02
#
# Design principles:
# - Multiple files can be added sequentially.
# - Each file has its own source/market/brand context.
# - Toyota is the current MVP scope, but is NOT hard-coded.
# - Missing brand can be assigned explicitly at file level.
# - Raw observed price is preserved.
# - Benchmark adjustment is separate from observed price.
# - No pricing prediction is performed here.
# ============================================================

st.set_page_config(
    page_title="Ajalty Pricing Engine — Dataset Builder",
    layout="wide",
)

# ------------------------------------------------------------
# Canonical dataset schema
# ------------------------------------------------------------

CANONICAL_FIELDS = [
    "part_number",
    "normalized_part_number",
    "brand",
    "brand_source",
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
    "source_file",
    "source_sheet",
]

# Current MVP scope only. Architecture remains dynamic.
MVP_ALLOWED_BRANDS = ["TOYOTA"]

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
    "part_number": [
        "part number", "part_number", "part no", "part no.",
        "item", "item number", "pn", "clean pn",
        "part", "رقم الصنف"
    ],
    "description": ["description", "desc", "product", "الوصف"],
    "quantity": ["qty", "quantity", "order qty", "الكمية"],
    "price": [
        "price", "new price aed", "new price", "target price",
        "target price ", "unit price", "selling price", "purchase price"
    ],
    "currency": ["currency", "curr"],
    "brand": ["brand", "make", "manufacturer", "oem brand"],
    "category": ["category", "product category", "type"],
    "date": ["date", "order date", "observation date"],
    "source_url": ["source url", "url", "link"],
    "vat_rate": ["vat", "vat rate", "tax", "tax rate"],
    "supplier": ["supplier", "seller", "vendor"],
}

PRICE_TYPES = [
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
]

SOURCE_TYPES = [
    "DEALERSHIP",
    "MARKETPLACE",
    "ACTUAL_TRANSACTION",
    "SUPPLIER",
    "OEM",
    "OTHER",
]

BRAND_SOURCE_OPTIONS = [
    "COLUMN_IN_FILE",
    "USER_ASSIGNED_FILE_LEVEL",
    "USER_ASSIGNED_ROW_LEVEL",
    "EXTERNAL_VERIFICATION",
    "UNKNOWN",
]


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def clean_col(value):
    if value is None:
        return ""
    return str(value).strip().lower().replace("\n", " ")


def normalize_part_number(value):
    if pd.isna(value):
        return ""
    s = str(value).strip().upper()
    return re.sub(r"[\s\-_./]+", "", s)


def parse_numeric(value):
    if pd.isna(value) or value == "":
        return None

    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    s = re.sub(r"[^\d,.\-]", "", s)

    if not s:
        return None

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
        try:
            return {"Sheet1": pd.read_csv(io.BytesIO(data))}
        except UnicodeDecodeError:
            return {
                "Sheet1": pd.read_csv(
                    io.BytesIO(data),
                    encoding="latin1"
                )
            }

    xls = pd.ExcelFile(io.BytesIO(data))
    return {
        sheet: pd.read_excel(
            io.BytesIO(data),
            sheet_name=sheet
        )
        for sheet in xls.sheet_names
    }


def suggest_mapping(columns):
    suggestions = {}
    normalized = {c: clean_col(c) for c in columns}

    for field, aliases in FIELD_ALIASES.items():
        best = None

        # Exact alias match first
        for col, value in normalized.items():
            if value in aliases:
                best = col
                break

        # Then partial match
        if best is None:
            for col, value in normalized.items():
                if any(alias in value for alias in aliases):
                    best = col
                    break

        suggestions[field] = best

    return suggestions


def add_file_to_session(uploaded):
    """
    Sequential file storage.
    Each file is retained independently.
    """
    file_id = f"{uploaded.name}__{len(st.session_state.files)}"

    sheets = read_uploaded(uploaded)

    st.session_state.files[file_id] = {
        "filename": uploaded.name,
        "sheets": sheets,
        "active_sheet": list(sheets.keys())[0],
        "processed": False,
    }

    return file_id


def remove_file(file_id):
    st.session_state.files.pop(file_id, None)
    st.session_state.mapped_outputs.pop(file_id, None)


def build_rows(
    df,
    mapping,
    metadata,
    filename,
    sheet_name,
):
    out = pd.DataFrame(index=df.index)

    # Initialize all fields
    for field in CANONICAL_FIELDS:
        out[field] = ""

    def get(field):
        col = mapping.get(field)

        if col and col in df.columns:
            return df[col]

        return pd.Series(
            [""] * len(df),
            index=df.index
        )

    raw_part = get("part_number")
    raw_price = get("price")
    raw_qty = get("quantity")
    raw_date = get("date")

    out["part_number"] = raw_part
    out["normalized_part_number"] = raw_part.map(
        normalize_part_number
    )

    out["description"] = get("description")
    out["brand"] = get("brand")
    out["category"] = get("category")
    out["quantity"] = raw_qty.map(parse_numeric)
    out["price"] = raw_price.map(parse_numeric)

    # File-level context
    for key in [
        "brand_source",
        "market",
        "country",
        "currency",
        "price_type",
        "supplier",
        "supplier_type",
        "authorized_status",
        "source",
        "source_type",
        "price_evidence_level",
        "source_url",
        "source_location",
        "shipping_adjustment_pct",
        "vat_rate",
        "vat_status",
        "notes",
    ]:
        if key in metadata:
            out[key] = metadata[key]

    # If a brand column is mapped, it overrides file-level brand.
    if mapping.get("brand"):
        out["brand"] = get("brand")
        out["brand_source"] = "COLUMN_IN_FILE"

    # Observation date
    out["observation_date"] = metadata.get(
        "observation_date",
        str(date.today())
    )

    if mapping.get("date"):
        parsed_dates = pd.to_datetime(
            raw_date,
            errors="coerce"
        )

        fallback_date = metadata.get(
            "observation_date",
            str(date.today())
        )

        out["observation_date"] = (
            parsed_dates
            .dt.strftime("%Y-%m-%d")
            .fillna(fallback_date)
        )

    # VAT calculations
    vat_rate = pd.to_numeric(
        out["vat_rate"],
        errors="coerce"
    )

    out["vat_rate"] = vat_rate

    out["vat_status"] = (
        out["vat_status"]
        .replace("", "VAT_UNKNOWN")
    )

    out["price_ex_vat"] = None
    out["price_inc_vat"] = None

    for i, row in out.iterrows():
        price = row["price"]
        vr = row["vat_rate"]
        status = str(
            row["vat_status"]
        ).upper()

        if pd.isna(price):
            continue

        if (
            status == "VAT_INCLUDED"
            and pd.notna(vr)
        ):
            out.at[i, "price_ex_vat"] = (
                price / (1 + vr / 100)
            )
            out.at[i, "price_inc_vat"] = price

        elif status == "VAT_EXCLUDED":
            out.at[i, "price_ex_vat"] = price

            if pd.notna(vr):
                out.at[i, "price_inc_vat"] = (
                    price * (1 + vr / 100)
                )

        else:
            out.at[i, "price_ex_vat"] = price

    # --------------------------------------------------------
    # Benchmark adjustment
    #
    # IMPORTANT:
    # observed price is never changed.
    # --------------------------------------------------------

    pct = pd.to_numeric(
        out["shipping_adjustment_pct"],
        errors="coerce"
    ).fillna(0)

    out["shipping_adjustment"] = (
        pd.to_numeric(
            out["price"],
            errors="coerce"
        ) * pct / 100
    )

    out["benchmark_price"] = (
        pd.to_numeric(
            out["price"],
            errors="coerce"
        )
        + out["shipping_adjustment"]
    )

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    out["valid"] = True
    out["rejection_reason"] = ""

    for i, row in out.iterrows():
        reasons = []

        if not str(
            row["part_number"]
        ).strip():
            reasons.append(
                "missing_part_number"
            )

        if pd.isna(row["price"]):
            reasons.append(
                "missing_or_unreadable_price"
            )

        if not str(
            row["currency"]
        ).strip():
            reasons.append(
                "missing_currency"
            )

        if not str(
            row["market"]
        ).strip():
            reasons.append(
                "missing_market"
            )

        if (
            not str(
                row["price_type"]
            ).strip()
            or str(
                row["price_type"]
            ).upper() == "UNKNOWN"
        ):
            reasons.append(
                "price_type_unknown"
            )

        if not str(
            row["source"]
        ).strip():
            reasons.append(
                "missing_source"
            )

        if not str(
            row["brand"]
        ).strip():
            reasons.append(
                "missing_brand"
            )

        if reasons:
            out.at[i, "valid"] = False
            out.at[i, "rejection_reason"] = (
                "; ".join(reasons)
            )

    out["source_file"] = filename
    out["source_sheet"] = sheet_name

    return out


def combined_output():
    if not st.session_state.mapped_outputs:
        return None

    return pd.concat(
        st.session_state.mapped_outputs.values(),
        ignore_index=True
    )


# ------------------------------------------------------------
# Session state
# ------------------------------------------------------------

if "files" not in st.session_state:
    st.session_state.files = {}

if "mapped_outputs" not in st.session_state:
    st.session_state.mapped_outputs = {}

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------

st.title(
    "Ajalty Intelligent Pricing Engine — MVP Dataset Builder"
)

st.caption(
    "G1 Dataset Preparation • R02 • "
    "Sequential multi-file workflow"
)

st.info(
    "Current MVP scope: genuine OEM Toyota. "
    "The application architecture is brand-dynamic; "
    "Toyota is a configurable MVP scope rather than a hard-coded "
    "assumption."
)


# ------------------------------------------------------------
# Current dataset status
# ------------------------------------------------------------

combined = combined_output()

if combined is not None:
    total = len(combined)
    valid = int(combined["valid"].sum())
    review = total - valid
else:
    total = valid = review = 0

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Files added",
    len(st.session_state.files)
)

c2.metric(
    "Observations",
    total
)

c3.metric(
    "Valid",
    valid
)

c4.metric(
    "Needs review",
    review
)


# ============================================================
# ADD FILE
# ============================================================

st.header("1. Add source file")

uploaded = st.file_uploader(
    "Upload one Excel/CSV file",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=False,
    key=f"uploader_{st.session_state.uploader_key}",
)

if uploaded is not None:

    existing_names = [
        item["filename"]
        for item in st.session_state.files.values()
    ]

    if uploaded.name not in existing_names:

        try:
            file_id = add_file_to_session(
                uploaded
            )

            st.success(
                f"Added: {uploaded.name}"
            )

            # Reset uploader so the next file can be selected.
            st.session_state.uploader_key += 1

            st.rerun()

        except Exception as e:
            st.error(
                f"Could not read {uploaded.name}: {e}"
            )

    else:
        st.warning(
            "This filename is already in the current dataset."
        )


# ============================================================
# FILE PROCESSING
# ============================================================

if st.session_state.files:

    st.header("2. Configure and map files")

    for number, (file_id, file_info) in enumerate(
        list(st.session_state.files.items()),
        start=1,
    ):

        filename = file_info["filename"]
        sheets = file_info["sheets"]

        with st.expander(
            f"{number}. {filename}",
            expanded=(not file_info["processed"]),
        ):

            # ------------------------------------------------
            # Sheet
            # ------------------------------------------------

            active_sheet = st.selectbox(
                "Sheet",
                list(sheets.keys()),
                index=list(
                    sheets.keys()
                ).index(
                    file_info["active_sheet"]
                ),
                key=f"sheet_{file_id}",
            )

            file_info["active_sheet"] = active_sheet

            df = sheets[active_sheet].copy()

            # ------------------------------------------------
            # Preview
            # ------------------------------------------------

            st.markdown(
                "### File preview"
            )

            st.dataframe(
                df.head(8),
                use_container_width=True,
                height=250,
            )

            st.caption(
                f"{len(df):,} rows × "
                f"{len(df.columns):,} columns"
            )

            # ------------------------------------------------
            # File designation
            # ------------------------------------------------

            st.markdown(
                "### Source designation"
            )

            preset_name = st.selectbox(
                "What does this file represent?",
                list(SOURCE_PRESETS.keys()),
                key=f"preset_{file_id}",
            )

            preset = SOURCE_PRESETS[
                preset_name
            ].copy()

            # ------------------------------------------------
            # Brand context
            # ------------------------------------------------

            st.markdown(
                "### Brand identification"
            )

            brand_column_options = [
                "— No brand column —"
            ] + list(df.columns)

            suggestions = suggest_mapping(
                df.columns.tolist()
            )

            suggested_brand = suggestions.get(
                "brand"
            )

            default_brand_index = (
                brand_column_options.index(
                    suggested_brand
                )
                if suggested_brand
                in brand_column_options
                else 0
            )

            brand_column = st.selectbox(
                "Brand column (if the file contains one)",
                brand_column_options,
                index=default_brand_index,
                key=f"brand_column_{file_id}",
            )

            if (
                brand_column
                != "— No brand column —"
            ):

                brand_source = (
                    "COLUMN_IN_FILE"
                )

                brand_value = ""

                st.success(
                    f"Brand will be taken from: "
                    f"{brand_column}"
                )

            else:

                brand_source = (
                    "USER_ASSIGNED_FILE_LEVEL"
                )

                brand_value = st.text_input(
                    "Brand for the entire file",
                    value="TOYOTA",
                    key=f"brand_value_{file_id}",
                    help=(
                        "Use this when every row in the "
                        "file belongs to the same brand. "
                        "Do not infer silently."
                    ),
                ).strip().upper()

                if brand_value:

                    if brand_value not in [
                        b.upper()
                        for b in MVP_ALLOWED_BRANDS
                    ]:

                        st.warning(
                            f"{brand_value} is outside the "
                            f"current MVP brand scope "
                            f"({', '.join(MVP_ALLOWED_BRANDS)}). "
                            "It can still be stored for future "
                            "multi-brand use, but should not be "
                            "included in the current Toyota MVP."
                        )

            # ------------------------------------------------
            # Context
            # ------------------------------------------------

            st.markdown(
                "### Market / source context"
            )

            c1, c2, c3, c4 = st.columns(4)

            with c1:

                market = st.text_input(
                    "Target market",
                    value=preset["market"],
                    key=f"market_{file_id}",
                )

                country = st.text_input(
                    "Country",
                    value=preset["country"],
                    key=f"country_{file_id}",
                )

            with c2:

                currency = st.text_input(
                    "Currency",
                    value=preset["currency"],
                    key=f"currency_{file_id}",
                ).strip().upper()

                source_type = st.selectbox(
                    "Source type",
                    SOURCE_TYPES,
                    index=(
                        SOURCE_TYPES.index(
                            preset["source_type"]
                        )
                        if preset["source_type"]
                        in SOURCE_TYPES
                        else 5
                    ),
                    key=f"source_type_{file_id}",
                )

            with c3:

                price_type = st.selectbox(
                    "Price type",
                    PRICE_TYPES,
                    index=PRICE_TYPES.index(
                        preset["price_type"]
                    ),
                    key=f"price_type_{file_id}",
                )

                evidence = st.selectbox(
                    "Evidence level",
                    [1, 2, 3, 4, 5],
                    index=(
                        int(
                            preset[
                                "price_evidence_level"
                            ]
                        ) - 1
                    ),
                    key=f"evidence_{file_id}",
                    help=(
                        "1 = strongest evidence; "
                        "5 = general marketplace/web evidence."
                    ),
                )

            with c4:

                source_location = st.text_input(
                    "Price/source location",
                    value=preset[
                        "source_location"
                    ],
                    key=f"location_{file_id}",
                )

                obs_date = st.date_input(
                    "Observation date",
                    value=date.today(),
                    key=f"date_{file_id}",
                )

            source_name = st.text_input(
                "Source name / reference",
                value=(
                    preset_name
                    if preset_name
                    != "Other / custom"
                    else filename
                ),
                key=f"source_{file_id}",
            )

            supplier = st.text_input(
                "Supplier / seller",
                value=preset[
                    "supplier_type"
                ],
                key=f"supplier_{file_id}",
            )

            # ------------------------------------------------
            # Adjustments
            # ------------------------------------------------

            st.markdown(
                "### Benchmark adjustment"
            )

            st.caption(
                "This is deliberately separate from the "
                "observed price. It is an assumption used "
                "only to create a comparable benchmark."
            )

            a1, a2, a3 = st.columns(3)

            with a1:

                shipping_pct = st.number_input(
                    "Shipping / location adjustment (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=0.0,
                    step=0.5,
                    key=f"shipping_{file_id}",
                )

            with a2:

                vat_status = st.selectbox(
                    "VAT status",
                    [
                        "VAT_UNKNOWN",
                        "VAT_INCLUDED",
                        "VAT_EXCLUDED",
                    ],
                    key=f"vat_status_{file_id}",
                )

            with a3:

                st.write(
                    "Example:"
                )
                st.code(
                    "Observed 100\n"
                    "Adjustment 5%\n"
                    "Benchmark 105"
                )

            # ------------------------------------------------
            # Column mapping
            # ------------------------------------------------

            st.markdown(
                "### Column mapping"
            )

            st.caption(
                "Review every important mapping. "
                "The suggested mapping is only a starting point."
            )

            mapping = {}

            mapping_fields = [
                "part_number",
                "description",
                "quantity",
                "price",
                "brand",
                "category",
                "date",
                "source_url",
            ]

            map_cols = st.columns(3)

            for n, field in enumerate(
                mapping_fields
            ):

                with map_cols[n % 3]:

                    options = [
                        "— Not mapped —"
                    ] + list(df.columns)

                    suggested = suggestions.get(
                        field
                    )

                    default_index = (
                        options.index(
                            suggested
                        )
                        if suggested
                        in options
                        else 0
                    )

                    selected = st.selectbox(
                        field,
                        options,
                        index=default_index,
                        key=f"map_{file_id}_{field}",
                    )

                    if (
                        selected
                        != "— Not mapped —"
                    ):

                        mapping[field] = (
                            selected
                        )

            # If brand is assigned from a file-level value,
            # don't allow a stale brand mapping to override it.
            if (
                brand_column
                != "— No brand column —"
            ):

                mapping["brand"] = (
                    brand_column
                )

            # ------------------------------------------------
            # Process
            # ------------------------------------------------

            metadata = {
                "brand_source": brand_source,
                "market": market,
                "country": country,
                "currency": currency,
                "price_type": price_type,
                "source": source_name,
                "source_type": source_type,
                "price_evidence_level": evidence,
                "source_location": source_location,
                "observation_date": str(
                    obs_date
                ),
                "shipping_adjustment_pct": (
                    shipping_pct
                ),
                "vat_status": vat_status,
                "supplier": supplier,
                "supplier_type": preset[
                    "supplier_type"
                ],
                "authorized_status": preset[
                    "authorized_status"
                ],
                "notes": "",
            }

            # File-level brand only if there is no brand column.
            if (
                brand_column
                == "— No brand column —"
            ):

                metadata["brand"] = (
                    brand_value
                )

            if st.button(
                "Process / Update this file",
                key=f"process_{file_id}",
                type="primary",
            ):

                result = build_rows(
                    df=df,
                    mapping=mapping,
                    metadata=metadata,
                    filename=filename,
                    sheet_name=active_sheet,
                )

                st.session_state.mapped_outputs[
                    file_id
                ] = result

                file_info[
                    "processed"
                ] = True

                st.success(
                    f"Processed {len(result):,} rows."
                )

            # ------------------------------------------------
            # Preview processed
            # ------------------------------------------------

            if (
                file_id
                in st.session_state.mapped_outputs
            ):

                result = (
                    st.session_state
                    .mapped_outputs[file_id]
                )

                valid_count = int(
                    result["valid"].sum()
                )

                invalid_count = (
                    len(result)
                    - valid_count
                )

                p1, p2, p3 = st.columns(3)

                p1.metric(
                    "Rows imported",
                    len(result),
                )

                p2.metric(
                    "Valid",
                    valid_count,
                )

                p3.metric(
                    "Needs review",
                    invalid_count,
                )

                st.dataframe(
                    result[
                        [
                            "part_number",
                            "normalized_part_number",
                            "brand",
                            "quantity",
                            "currency",
                            "price",
                            "benchmark_price",
                            "price_type",
                            "source_type",
                            "valid",
                            "rejection_reason",
                        ]
                    ].head(20),
                    use_container_width=True,
                    height=300,
                )

            # ------------------------------------------------
            # Remove
            # ------------------------------------------------

            if st.button(
                "Remove this file",
                key=f"remove_{file_id}",
            ):

                remove_file(file_id)

                st.rerun()


# ============================================================
# COMBINED DATASET
# ============================================================

combined = combined_output()

if combined is not None:

    st.divider()

    st.header(
        "3. Combined benchmark dataset"
    )

    total = len(combined)
    valid_count = int(
        combined["valid"].sum()
    )
    review_count = (
        total - valid_count
    )

    m1, m2, m3, m4 = st.columns(4)

    m1.metric(
        "Total observations",
        total,
    )

    m2.metric(
        "Valid observations",
        valid_count,
    )

    m3.metric(
        "Needs review",
        review_count,
    )

    m4.metric(
        "Unique parts",
        combined[
            "normalized_part_number"
        ]
        .replace("", pd.NA)
        .nunique(),
    )

    # --------------------------------------------------------
    # Brand summary
    # --------------------------------------------------------

    st.markdown(
        "### Brand summary"
    )

    brand_summary = (
        combined["brand"]
        .replace("", "UNKNOWN")
        .value_counts()
        .rename_axis("brand")
        .reset_index(
            name="observations"
        )
    )

    st.dataframe(
        brand_summary,
        hide_index=True,
        use_container_width=True,
    )

    # Current MVP warning
    non_toyota = combined[
        ~combined["brand"]
        .astype(str)
        .str.upper()
        .isin(
            MVP_ALLOWED_BRANDS
        )
        & combined["brand"].astype(str).ne("")
    ]

    if len(non_toyota) > 0:

        st.warning(
            f"{len(non_toyota):,} observations "
            "are outside the current Toyota MVP scope. "
            "They remain in the dataset for future "
            "multi-brand support but should not yet be "
            "used by the Toyota MVP model."
        )

    # --------------------------------------------------------
    # Main table
    # --------------------------------------------------------

    display_cols = [
        c for c in [
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
        if c in combined.columns
    ]

    st.dataframe(
        combined[display_cols].head(200),
        use_container_width=True,
        height=500,
    )

    # --------------------------------------------------------
    # Data quality
    # --------------------------------------------------------

    st.markdown(
        "### Data quality summary"
    )

    summary = pd.DataFrame(
        {
            "metric": [
                "Total observations",
                "Valid observations",
                "Rows needing review",
                "Unique normalized parts",
                "Toyota observations",
                "Unknown brand observations",
                "Saudi observations",
                "UAE observations",
                "Marketplace observations",
                "Actual transaction observations",
                "Dealership observations",
            ],
            "value": [
                len(combined),
                int(
                    combined[
                        "valid"
                    ].sum()
                ),
                int(
                    (
                        ~combined[
                            "valid"
                        ]
                    ).sum()
                ),
                combined[
                    "normalized_part_number"
                ]
                .replace("", pd.NA)
                .nunique(),
                int(
                    combined[
                        "brand"
                    ]
                    .astype(str)
                    .str.upper()
                    .eq("TOYOTA")
                    .sum()
                ),
                int(
                    combined[
                        "brand"
                    ]
                    .astype(str)
                    .str.strip()
                    .eq("")
                    .sum()
                ),
                int(
                    combined[
                        "market"
                    ]
                    .astype(str)
                    .str.contains(
                        "Saudi",
                        case=False,
                        na=False,
                    )
                    .sum()
                ),
                int(
                    combined[
                        "market"
                    ]
                    .astype(str)
                    .str.contains(
                        "UAE",
                        case=False,
                        na=False,
                    )
                    .sum()
                ),
                int(
                    combined[
                        "source_type"
                    ]
                    .astype(str)
                    .eq("MARKETPLACE")
                    .sum()
                ),
                int(
                    combined[
                        "source_type"
                    ]
                    .astype(str)
                    .eq(
                        "ACTUAL_TRANSACTION"
                    )
                    .sum()
                ),
                int(
                    combined[
                        "source_type"
                    ]
                    .astype(str)
                    .eq("DEALERSHIP")
                    .sum()
                ),
            ],
        }
    )

    st.dataframe(
        summary,
        hide_index=True,
        use_container_width=True,
    )

    st.warning(
        "Review rows marked invalid/needs review before "
        "using the dataset as evidence. Missing information "
        "is not silently guessed."
    )

    # --------------------------------------------------------
    # Downloads
    # --------------------------------------------------------

    csv_bytes = combined[
        display_cols
    ].to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )

    xlsx_buffer = io.BytesIO()

    with pd.ExcelWriter(
        xlsx_buffer,
        engine="openpyxl",
    ) as writer:

        combined[
            display_cols
        ].to_excel(
            writer,
            index=False,
            sheet_name="benchmark_v01",
        )

        summary.to_excel(
            writer,
            index=False,
            sheet_name="data_quality",
        )

        brand_summary.to_excel(
            writer,
            index=False,
            sheet_name="brand_summary",
        )

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
            mime=(
                "application/"
                "vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet"
            ),
            use_container_width=True,
        )


else:

    st.markdown(
        """
        ### Workflow

        **Add File → Preview → Designate → Identify Brand →
        Map Columns → Process → Add Another File → Combine → Export**

        The current MVP is Toyota-only for model development,
        but the dataset itself supports multiple brands.
        """
    )
