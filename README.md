# Ajalty Pricing Engine — MVP Dataset Builder

This is the first implementation for G1-01/G1 dataset preparation.

## Run

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Workflow

Upload one or multiple Excel/CSV files.

For each file:
1. Preview the first rows.
2. Select the source designation.
3. Enter/confirm market, currency, price type, source type and location.
4. Review suggested column mappings.
5. Process the file.
6. Add/review other files.
7. Review the combined benchmark.
8. Download `benchmark_v01.csv` or `.xlsx`.

The application intentionally does not predict prices yet.

Observed prices are preserved separately from the optional shipping/location benchmark adjustment.

## Initial source classifications

- KSA dealership genuine pricing -> DEALERSHIP / WHOLESALE
- KSA Mendoubak -> MARKETPLACE / MARKETPLACE
- Ajalty Saudi client transaction -> ACTUAL_TRANSACTION / TRANSACTION

These classifications remain editable in the UI.
