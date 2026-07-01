const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun,
  Header, Footer, AlignmentType, LevelFormat, TableOfContents, HeadingLevel,
  BorderStyle, WidthType, ShadingType, PageNumber, PageBreak,
} = require("/tmp/node_modules/docx");

const INK = "1A2233", MUT = "5A6472", BRAND = "1E6FBD", LINE = "CCCCCC", HEAD = "D6E4F2";
const CW = 9360;

const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const P = (t) => new Paragraph({ spacing: { after: 140, line: 300 },
  children: Array.isArray(t) ? t : [new TextRun(t)] });
const BULLET = (t) => new Paragraph({ numbering: { reference: "b", level: 0 }, spacing: { after: 70, line: 290 },
  children: Array.isArray(t) ? t : [new TextRun(t)] });
const b = (t) => new TextRun({ text: t, bold: true });
const r = (t) => new TextRun(t);
const code = (t) => new TextRun({ text: t, font: "Consolas", size: 20 });

const border = { style: BorderStyle.SINGLE, size: 1, color: LINE };
const borders = { top: border, bottom: border, left: border, right: border };
function cell(text, w, head) {
  return new TableCell({ borders, width: { size: w, type: WidthType.DXA },
    shading: head ? { fill: HEAD, type: ShadingType.CLEAR } : undefined,
    margins: { top: 70, bottom: 70, left: 120, right: 120 },
    children: [new Paragraph({ children: [new TextRun({ text: text, bold: !!head, size: 21 })] })] });
}
function table(widths, rows) {
  return new Table({ width: { size: CW, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map((cells, i) => new TableRow({ tableHeader: i === 0,
      children: cells.map((c, j) => cell(c, widths[j], i === 0)) })) });
}

const children = [];

children.push(
  new Paragraph({ spacing: { before: 1600 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Trinamix Conversion Workbench", bold: true, size: 52, color: INK })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120 },
    children: [new TextRun({ text: "Solution Architecture", size: 36, color: BRAND })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 240 },
    children: [new TextRun({ text: "Oracle E-Business Suite to Oracle Fusion Cloud data-migration platform", italics: true, size: 24, color: MUT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 1400 },
    children: [new TextRun({ text: "Architecture Overview & Technical Reference", size: 22, color: INK })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 80 },
    children: [new TextRun({ text: "Version 1.0", size: 20, color: MUT })] }),
  new Paragraph({ children: [new PageBreak()] }),
);

children.push(H1("Contents"));
children.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));
children.push(new Paragraph({ children: [new PageBreak()] }));

children.push(H1("1. Executive overview"));
children.push(P("The Trinamix Conversion Workbench is a web-based platform that migrates master and transactional data from Oracle E-Business Suite (EBS) into Oracle Fusion Cloud using Oracle's supported File-Based Data Import (FBDI) mechanism. It compresses a traditionally manual, spreadsheet-heavy migration effort into a guided, repeatable workflow: connect to the source, map source fields to Fusion targets with AI assistance, apply transformation rules, generate validated FBDI files, and load them into Fusion through the ERP Integration Service, with monitoring, error traceback, and governance throughout."));
children.push(P([b("Who uses it. "), r("Migration consultants and functional/technical analysts running an EBS-to-Fusion implementation. Each engagement (project) holds a set of conversion objects, for example the 17 Supply Chain objects such as Items, Units of Measure, Suppliers, Customers, On-Hand Balances and Sales Orders, progressing from planning through mapping, output generation, and load.")]));
children.push(P([b("What it solves. "), r("It removes the three biggest sources of friction in a Fusion data load: discovering and reading the EBS source correctly, producing a correctly-shaped FBDI file for each Fusion interface table, and getting that file accepted and run by Fusion. The workbench automates column-to-field mapping with a learning matcher, builds the FBDI artifact from live or extracted source data, and drives Oracle's bulk-import API end to end.")]));

children.push(H1("2. Architecture at a glance"));
children.push(P("The system is a single-page web application backed by a REST API and a document database, integrating outward to two Oracle systems (EBS as source, Fusion as target) and, optionally, an LLM provider for the AI Copilot."));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 120 },
  children: [new ImageRun({ type: "png", data: fs.readFileSync("/tmp/architecture.png"),
    transformation: { width: 624, height: 390 },
    altText: { title: "System architecture", name: "architecture", description: "Layered architecture: React SPA over a FastAPI backend and MongoDB Atlas, integrating with Oracle EBS via JDBC and Oracle Fusion via REST/ERP Integration." } })] }));
children.push(P([r("At the top, a "), b("React single-page app"), r(" renders the workbench UI. It talks over HTTPS to a "), b("FastAPI"), r(" backend whose routers expose REST endpoints and whose domain services hold the business logic. State persists in "), b("MongoDB Atlas"), r(" through the Beanie ODM. The backend reaches "), b("Oracle EBS"), r(" over a JDBC connection to read source schema and rows, and reaches "), b("Oracle Fusion Cloud"), r(" over REST to test connectivity, submit FBDI imports, and poll job status.")]));

children.push(H1("3. Component layers"));
children.push(H2("3.1 Client (frontend)"));
children.push(P("A React 18 + TypeScript application built with Vite and styled with Tailwind CSS, deployed as a static site. Routing is handled by React Router; server state is fetched with axios; lightweight global state (authentication) uses Zustand. Charts are rendered with Recharts, graph and dataflow views with ReactFlow, and data grids with TanStack Table."));
children.push(P([r("The UI is organised around the migration lifecycle: "), b("Projects"), r(" and "), b("Conversion Objects"), r(" for scope, "), b("Mapping Review"), r(" for field mapping, "), b("Recommendations"), r(" and "), b("Output Preview"), r(" for data quality and FBDI preview, and "), b("Load Management, Migration Monitor and Error Traceback"), r(" for execution. A single axios client centralises the API base URL, attaches the JWT, handles 401 redirects, and drives an app-wide processing indicator for every request via an activity interceptor.")]));

children.push(H2("3.2 API layer"));
children.push(P("FastAPI exposes the backend as REST endpoints under /api. Routers are thin: they validate input with Pydantic schemas, enforce authentication, and delegate to domain services. The principal routers are summarised below."));
children.push(table([2600, 6760], [
  ["Router", "Responsibility"],
  ["auth", "Login, token issue, current-user (JWT, HS256)"],
  ["projects / conversions", "Engagements and conversion objects; auto-populate from the module catalog; cascade delete"],
  ["datasets / fbdi / fbdi_seed", "Uploaded source extracts; FBDI templates and target fields; standard-field seeding"],
  ["discovery / source_connections", "EBS connection, scans, canonical-table resolution and live counts"],
  ["mapping / learned", "Mapping suggestions, transformation rules, preview, learned-mapping reuse"],
  ["operations", "Output generation/preview, simulate-load, load runs, workflows, dependencies, dashboard"],
  ["fusion", "Fusion connection/test, FBDI targets, pre-flight, load-to-Fusion, job-status poll"],
  ["copilot / coa / governance / audit", "AI Copilot, chart-of-accounts, sign-offs and approvals, audit events"],
]));

children.push(H2("3.3 Domain services"));
children.push(P("Business logic lives in services that the routers call. Key services include the mapping service (live EBS column and row access plus the AI matcher), the output service (which builds the converted FBDI dataframe from either an uploaded file or live EBS rows and applies mappings and rules), the fusion service (interface-table catalog, connection test, pre-flight, import submission, and status polling), and supporting services for datasets, learning, quality, dashboards, and project setup."));

children.push(H2("3.4 Data store"));
children.push(P("MongoDB Atlas is the system of record, accessed asynchronously through the Beanie ODM on top of Motor. Roughly thirty document collections model the domain, from projects and conversions to mappings, templates, outputs, load runs, and the full governance set. Beanie registers all document models at startup; no relational migrations are required."));

children.push(H2("3.5 External systems"));
children.push(BULLET([b("Oracle EBS"), r(" — the source ERP. Reached over JDBC using the Oracle thin driver, primarily to read table schemas (resolving APPS synonyms) and to stream source rows for mapping and output.")]));
children.push(BULLET([b("Oracle Fusion Cloud"), r(" — the target SaaS. Reached over REST: a basic-auth connectivity test, a pre-flight capability probe, and the ERP Integration importBulkData call that stages the FBDI zip to UCM and submits the import job.")]));
children.push(BULLET([b("Anthropic API (optional)"), r(" — powers the AI Copilot when an API key is configured; the rule-based matcher works with no external AI dependency.")]));

children.push(H1("4. Technology stack"));
children.push(table([2400, 6960], [
  ["Area", "Technologies"],
  ["Frontend", "React 18, TypeScript, Vite, Tailwind CSS, React Router, axios, Zustand, Recharts, ReactFlow, TanStack Table, lucide-react"],
  ["Backend", "Python 3.12, FastAPI, Pydantic, Beanie ODM, Motor, Uvicorn, pandas, openpyxl"],
  ["Database", "MongoDB Atlas (managed)"],
  ["EBS connectivity", "jaydebeapi + JPype1 with the Oracle ojdbc11 JDBC driver (Java runtime in the backend container)"],
  ["Fusion connectivity", "httpx (async HTTP), Oracle ERP Integration Service REST, FBDI (zip + base64)"],
  ["AI", "Pluggable provider: rule-based matcher (always on) plus optional Anthropic LLM"],
  ["Auth", "JWT (HS256) bearer tokens"],
  ["Deployment", "Render: backend as a Docker web service, frontend as a static site; MongoDB Atlas"],
]));

children.push(H1("5. Core workflows and data flow"));
children.push(P("The platform moves data through six stages. Each conversion object advances independently, and its status (planning, mapped, loaded, failed) is tracked throughout."));
children.push(H2("5.1 Discovery and source onboarding"));
children.push(P("The backend connects to Oracle EBS over JDBC. Because EBS objects are exposed to the APPS schema as synonyms (and so do not appear under the owner in the catalog views), the workbench resolves columns by describing the object directly (a zero-row select) and reads live row counts per canonical table. Alternatively, analysts can upload source extracts as datasets."));
children.push(H2("5.2 Project and conversion setup"));
children.push(P("A setup wizard scopes an engagement by module. Auto-populate then creates one conversion per object from the Fusion module catalog (for Supply Chain, the full set of 17 objects), binding each to its EBS source table hint and its FBDI target template, with duplicate-safe normalised matching."));
children.push(H2("5.3 AI-assisted mapping"));
children.push(P("Mapping Review places the source columns (live from EBS or from the uploaded file) beside the target FBDI fields. A rule-based matcher, tokenizing and splitting acronym boundaries so that, for example, UOMCode aligns to UOM Code, proposes mappings with confidence scores, reusing previously learned mappings and knowledge-base entries. Analysts approve, edit, drag-to-map, and attach transformation rules; everything is persisted so mappings survive restarts and do not need re-running."));
children.push(H2("5.4 Transformation and output generation"));
children.push(P("Output generation builds a converted dataframe: for EBS-sourced conversions it fetches live rows over JDBC; for file-sourced conversions it parses the upload. It then applies the approved mappings and transformation rules and shapes the result into the target FBDI column layout, producing the converted output and the on-screen Output Preview."));
children.push(H2("5.5 Load to Fusion"));
children.push(P("Load Management packages the converted FBDI CSV into a zip, base64-encodes it, and calls Oracle's ERP Integration importBulkData, which stages the file to UCM and submits the product import job (for example, Import Units of Measure). A pre-flight check first probes whether the pod and user can run the import. The returned request id, interface tables, and target work area are recorded on the load run; a status action polls Oracle for the live job phase (Succeeded, Warning, Error, Running)."));
children.push(H2("5.6 Monitoring, governance and audit"));
children.push(P("Migration Monitor and Load Runs surface submission and import status; Error Traceback categorises failures by root cause and dependency. A dependency graph shows load-order relationships, and the governance set (audit events, approvals and sign-offs, cutover tasks, reconciliation checks) supports a controlled go-live."));

children.push(H1("6. Data model (key collections)"));
children.push(P("All collections are Beanie documents in MongoDB. The most important are listed below; the full set also includes datasets, validation issues, environments, learned mappings, chart-of-accounts structures, and the cutover and governance entities."));
children.push(table([2900, 6460], [
  ["Collection", "Purpose"],
  ["Project", "An engagement: source system, selected modules, status rollups"],
  ["Conversion", "One object to migrate: source type (EBS or dataset), table hint, target template, status"],
  ["FBDITemplate / FBDISheet / FBDIField", "Fusion target template and its interface fields"],
  ["MappingSuggestion / LearnedMapping", "Source-to-target field mappings and reusable learned mappings"],
  ["TransformationRule / Crosswalk", "Per-field transforms and value crosswalks applied at output time"],
  ["ConvertedOutput", "Generated FBDI dataset for a conversion"],
  ["LoadRun / LoadError", "Fusion submissions (request id, job state, tables, raw response) and error rows"],
  ["SourceConnection", "Stored EBS and Fusion connection settings"],
  ["AuditEvent / SignOff / CutoverTask / ReconciliationCheck", "Governance and cutover control"],
]));

children.push(H1("7. Integration details"));
children.push(H2("7.1 Oracle EBS (source, JDBC)"));
children.push(P([r("The backend container ships a Java runtime and the Oracle ojdbc11 driver; jaydebeapi and JPype open a JDBC connection to EBS. Schema is resolved with a describe-style query ("), code("SELECT * ... WHERE 1=0"), r(") to handle synonym-exposed objects, and rows are streamed with bounded selects for mapping samples and output generation. A diagnostic explains any empty result (no connection, JDBC error, or table not found).")]));
children.push(H2("7.2 Oracle Fusion Cloud (target, ERP Integration)"));
children.push(P([r("Connectivity is verified with a basic-auth GET against the Fusion REST catalog. Loads use the ERP Integration resource: the FBDI CSV is zipped, base64-encoded, and posted as "), code("importBulkData"), r(" with the object's document account and ESS job name. The workbench treats a returned request id of -1 as a real failure (the call was accepted but the job was not queued, typically a UCM or privilege issue), and polls "), code("getESSJobStatus"), r(" for the live phase. A pre-flight runs two probes, reading an SCM data resource and reaching the ERP Integration endpoint, so a read-only user that cannot run imports is flagged before a load is attempted.")]));
children.push(H2("7.3 AI provider"));
children.push(P("The matcher that drives mapping suggestions is deterministic and requires no external service. When an Anthropic API key is configured, the Copilot adds natural-language assistance (for example, suggesting default values). The provider is pluggable and disabled by default."));

children.push(H1("8. Deployment and operations"));
children.push(P("The application is deployed on Render as two services plus a managed database, defined in a single deployment manifest."));
children.push(BULLET([b("Backend"), r(" — a Docker web service (Python 3.12 image with a Java runtime and the ojdbc11 driver baked in) running FastAPI under Uvicorn. Configuration is by environment variable: the MongoDB connection string, JWT secret, seed-admin credentials, and the optional AI provider and key.")]));
children.push(BULLET([b("Frontend"), r(" — a static site built with Vite; the build is published and served with an SPA rewrite so client-side routes resolve. The API base URL is injected at build time.")]));
children.push(BULLET([b("Database"), r(" — MongoDB Atlas, with the connection string supplied to the backend at runtime.")]));
children.push(P([b("Operational notes. "), r("On the free tier the backend can cold-start after idle, which briefly surfaces as a network or CORS error during the redeploy window; the UI is resilient to this with retry affordances. Because JDBC calls are blocking, heavy concurrent EBS operations are best kept modest on a single instance.")]));

children.push(H1("9. Security and cross-cutting concerns"));
children.push(BULLET([b("Authentication. "), r("Username and password login issues a JWT (HS256). The token is sent as a bearer header on every API call; a 401 clears the session and redirects to login.")]));
children.push(BULLET([b("Secrets. "), r("Connection credentials and API keys are held server-side as environment variables or stored connection records; the frontend never holds Oracle credentials beyond what the user types into the connection dialog.")]));
children.push(BULLET([b("Resilience. "), r("API calls degrade gracefully: timed-out or cold-start requests show errors with retry rather than spinning indefinitely, and EBS or Fusion failures return actionable diagnostics.")]));
children.push(BULLET([b("Observability. "), r("A global activity indicator reflects in-flight API work; load runs capture Oracle's raw responses for troubleshooting; audit events record governance-relevant actions.")]));
children.push(P([b("Note on CORS. "), r("The API currently allows all origins, which is convenient for the hosted demo but should be tightened to the known frontend origins for a production deployment.")]));

children.push(H1("10. Known constraints and scalability notes"));
children.push(P("The current design favours clarity and a fast demo footprint. For scale and production hardening, the main considerations are: moving long-running EBS reads and Fusion loads onto background workers (so blocking JDBC and large imports do not tie up request handlers); running more than one backend instance behind the queue once workers exist; restricting CORS and rotating the seed-admin credentials; and confirming, per Fusion pod, that the connecting user holds the ERP Integration privileges required for bulk imports. None of these affect the architecture's shape; they are configuration and horizontal-scaling steps on the same component model."));

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22, color: INK } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: "Arial", color: BRAND },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, font: "Arial", color: INK },
        paragraph: { spacing: { before: 220, after: 110 }, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [
    { reference: "b", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 4 } },
      children: [new TextRun({ text: "Trinamix Conversion Workbench — Solution Architecture", size: 16, color: MUT })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: "Page ", size: 16, color: MUT }), new TextRun({ children: [PageNumber.CURRENT], size: 16, color: MUT })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  const out = "/sessions/adoring-fervent-goodall/mnt/trinamix-conversion-workbench/docs/Trinamix_Conversion_Workbench_Architecture.docx";
  fs.writeFileSync(out, buf);
  console.log("WROTE", out, buf.length, "bytes");
});
