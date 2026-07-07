# Trinamix Conversion Workbench — Supplier FBDI User Guide

**Goal:** Take a single supplier extract (e.g. a NetSuite "All Vendors" dump) and produce **all 6 Oracle Fusion supplier FBDI files** — mapped, filled with data, and downloadable as one zip.

**App:** https://tx-conversion-workbench.onrender.com
**Login:** `admin@trinamix.com` / `admin123`

---

## The supplier load sequence

Oracle Fusion loads supplier master data as **6 FBDI files, in this order**. The tool generates and sequences them for you:

| Step | FBDI file | Interface |
|------|-----------|-----------|
| 1 | Supplier Import | `POZ_SUPPLIERS_INT` |
| 2 | Supplier Address | `POZ_SUPPLIER_ADDRESSES_INT` |
| 3 | Supplier Site | `POZ_SUPPLIER_SITES_INT` |
| 4 | Supplier Site Assignment | `POZ_SITE_ASSIGNMENTS_INT` |
| 5 | Supplier Contacts (2 sheets) | `POZ_SUP_CONTACTS_INT` + `POZ_SUPP_CONTACT_ADDRESSES_INT` |
| 6 | Supplier Banks | Import Supplier Bank Accounts |

> **Note:** *Supplier Contact Addresses* is **not** a separate file — it's the second worksheet inside the Supplier Contacts template, so it comes out as an extra CSV when that file's output is generated. All 6 templates must be seeded in the tool (FBDI Library → Templates).

---

## Step-by-step

### 1. Start a new engagement
Click **+ Create** (top right). A **"New engagement"** pop-up appears asking how you'll bring in source data.

### 2. Choose "File upload"
Pick **File upload** (the right-hand card). *(Use "DB connection" only when reading live from Oracle EBS / NetSuite.)* This opens the **Convert files** screen.

### 3. Create the engagement
Click **+ New engagement** (next to the Engagement dropdown) and fill in:
- **Engagement name** — e.g. `Tx FBDI Supplier`
- **Client** — e.g. `Next Power`
- **Source system** — e.g. `NetSuite`
- **Target** — `FBDI File download`

Click **Create engagement**. The new engagement is now selected.

### 4. Upload the input file
Under **Source files**, click **Add CSV / XLSX files** and select your supplier extract (e.g. `All Vendors - SS All Vendors - Phoenix-Shrt.xlsx`).

### 5. Upload & analyze
Click **Upload & analyze**. The AI detects the file's **source system** and the **target FBDI template**, and shows them in the Files table (you can override either).

### 6. Generate the full supplier set
In the file's row, click **⧉ Generate set**.

This is the key step: from the **one dataset** it creates **all 7 supplier conversions** at once — each auto-mapped to its FBDI template and placed in the correct load order. Any template that isn't seeded (e.g. Supplier Contact Addresses) is reported as *missing* so you know what to upload.

> Clicking **Create 1 conversion & map** instead makes only the single detected file — use **Generate set** when you want the whole supplier set.

### 7. Open the engagement
Go to the engagement (**Projects → open your engagement**, or **Open →**). You'll see:

- **Conversion Objects** — all 6 supplier files listed and numbered **in load order**:

  | # | Object | Target FBDI |
  |---|--------|-------------|
  | 1 | Supplier Import | Supplier Import |
  | 2 | Supplier Address | Supplier Address Import |
  | 3 | Supplier Site | ApSupplierSitesImport |
  | 4 | Supplier Site Assignment | Supplier Site Assignment |
  | 5 | Supplier Contacts | Supplier Contacts |
  | 6 | Supplier Banks | BankAccountImport |

  Each row has its own **FBDI download** and **Open →**.

- **Load Order** — a map showing the 6 objects connected left-to-right in the sequence they load into Fusion.

### 8. Step through the files, then download all 6
Open any supplier conversion (**Open →**). On the conversion screen you'll see a **Load Sequence** panel showing all 6 files connected in Fusion load order:

- The **current** file is highlighted; files already produced show a green **✓ generated**; the position reads **"File 3 of 6 in this engagement."**
- Use **Next file →** and **← Previous file** to move through the 6 supplier files one at a time — no hunting. (Previous is disabled on step 1 / Supplier Import; Next is disabled on step 6 / Supplier Banks.)
- For each file: run **AI Auto Map**, review/adjust in **Mapping Review**, then **Generate Output**.
- When all files are generated, open **Output Preview** and click **Download all FBDI (.zip)** — you get **all 6 supplier FBDI files, filled with mapped data**, named and ordered by the load sequence (the Contacts file includes its contact-addresses CSV).

**What's inside the download** — `<Engagement>_FBDI.zip` contains the 6 files, prefixed with their load-order number so they sort correctly:

| In the zip | File |
|------------|------|
| `01_… .csv` | Supplier Import |
| `02_… .csv` | Supplier Address |
| `03_… .csv` | Supplier Site |
| `04_… .csv` | Supplier Site Assignment |
| `05_… .zip` | **Supplier Contacts** — a nested zip holding **two CSVs** (contacts + contact addresses), because it's a 2-sheet template |
| `06_… .csv` | Supplier Banks |

Load them into Fusion in that numbered order; unzip `05` first to get its two CSVs.

---

## Tips
- **Review the mapping.** Auto-map typically nails ~30–40% up front. In **Mapping Review**, each target field shows **Alternative source columns** — pick a better match in one click, or filter by FBDI file using the chips / dropdown at the top.
- **Only generated files go in the zip.** Make sure you've run **Generate Output** on each supplier file before downloading all.
- **Load them into Fusion in order** (Import → Address → Site → Site Assignment → Contacts → Banks). Each output is raw FBDI CSV — column headers + data only, no instruction rows. The Supplier Contacts file yields two CSVs (contacts + contact addresses).
- **Same pattern for Customer & Item.** Those are single multi-sheet FBDI workbooks (one conversion, all sheets populated) rather than 7 separate files — "Generate set" resolves them to their one template.

---

*Trinamix Conversion Workbench — internal user guide. Questions: reach out to Subrato.*
