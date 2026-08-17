# Characterization goldens

Compact, gzipped snapshots of known-good generated FBDI artifacts (normalised cell
grids, not the multi-MB .xlsm binaries). They are the behaviour contract every
refactoring slice must keep.

## Capture a golden (once, from a verified-correct download)
    python tests/characterization/verify_goldens.py --save /path/to/01_Customer_Import.xlsm \
        tests/characterization/goldens/customer_new_1308.json

## Check a fresh regeneration against it
    python tests/characterization/verify_goldens.py --check \
        tests/characterization/goldens/customer_new_1308.json /path/to/fresh.xlsm
    # exit 0 = identical, exit 1 = drift (report printed)

Recommended goldens: one per object — Customer, Supplier (netsuite), Supplier (eBOS),
BOM, Employee.
