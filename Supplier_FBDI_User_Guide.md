# Trinamix Conversion Workbench — Supplier FBDI User Guide

**Goal:** Take a single supplier extract (e.g. a NetSuite "All Vendors" dump) and produce **all 7 Oracle Fusion supplier FBDI files** — mapped, filled with data, and downloadable as one zip.

**App:** https://tx-conversion-workbench.onrender.com
**Login:** `admin@trinamix.com` / `admin123`

---

## The supplier load sequence

Oracle Fusion loads supplier master data as **7 FBDI files, in this order**. The tool generates and sequences them for you:

| Step | FBDI file | Interface |
|------|-----------|-----------|
| 1 | Supplier Import | `POZ_SUPPLIERS_INT` |
| 2 | Supplier Address | `POZ_SUPPLIER_ADDRESSES_INT` |
| 3 | Supplier Site | `POZ_SUPPLIER_SITES_INT` |
| 4 | Supplier Site Assignment | `POZ_SITE_ASSIGNMENTS_INT` |
| 5 | Supplier Contacts | `POZ_SUP_CONTACTS_INT` |
| 6 | Supplier Contact Addresses | `POZ_SUP_CONTACT_ADDRESSES_INT` |
| 7 | Supplier Banks | Import Supplier Bank Accounts |

> **Note:** The tool must have each of these FBDI templates seeded (FBDI Library → Templates). Six are seeded today; upload a **Supplier Contact Addresses** template to make the set a full 7.

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
Go to the engagement (**Projects → open your engagement**, or **Open →**). You'll see all the supplier conversions under **Conversion Objects**, ordered by load sequence, and a **Load Order** dependency map.

### 8. Step through the files, then download all 7
Open any supplier conversion (**Open →**). On the conversion screen you'll see a **Load Sequence** panel showing all 7 files connected in order, with the current one highlighted.

- Use **Next file →** and **← Previous file** to move through the supplier files one at a time (the indicator shows *File 2 of 7*, etc.).
- For each file: run **AI Auto Map**, review/adjust in **Mapping Review**, then **Generate Output**.
- When all files are generated, open **Output Preview** and click **Download all FBDI (.zip)** — you get **all 7 supplier FBDI files, filled with mapped data**, named and ordered by the load sequence.

---

## Tips
- **Review the mapping.** Auto-map typically nails ~30–40% up front. In **Mapping Review**, each target field shows **Alternative source columns** — pick a better match in one click, or filter by FBDI file using the chips / dropdown at the top.
- **Only generated files go in the zip.** Make sure you've run **Generate Output** on each supplier file before downloading all.
- **Load them into Fusion in order** (Import → Address → Site → Site Assignment → Contacts → Contact Addresses → Banks). Each output is raw FBDI CSV — column headers + data only, no instruction rows.
- **Same pattern for Customer & Item.** Those are single multi-sheet FBDI workbooks (one conversion, all sheets populated) rather than 7 separate files — "Generate set" resolves them to their one template.

---

*Trinamix Conversion Workbench — internal user guide. Questions: reach out to Subrato.*
