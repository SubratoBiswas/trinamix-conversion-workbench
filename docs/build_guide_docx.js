// Markdown -> .docx for the Codebase Guide.
// Handles the subset the guide uses: headings, tables, fenced code, bullets,
// numbered lists, blockquotes, rules, and inline **bold** / `code` / [links].
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  TableOfContents, PageBreak, LevelFormat, PageOrientation, ExternalHyperlink,
} = require("docx");

const SRC = process.argv[2];
const OUT = process.argv[3];
const md = fs.readFileSync(SRC, "utf8").split("\n");

const MONO = "Consolas";
const BODY = "Calibri";
const INK = "1F2933";
const MUTED = "52606D";
const ACCENT = "2F5597";
const CODE_BG = "F4F5F7";
const HEAD_BG = "E8EDF5";

// ── inline: **bold**, `code`, [text](url), *italic* ─────────────────────────
function inline(text, base = {}) {
  const runs = [];
  const re = /(\*\*[^*]+\*\*)|(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*[^*]+\*)/g;
  let last = 0, m;
  const push = (t, extra) => {
    if (!t) return;
    runs.push(new TextRun({ text: t, font: BODY, size: 21, color: INK, ...base, ...extra }));
  };
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      // Recurse — `code` nested inside **bold** was being pushed raw, backticks
      // and all.
      runs.push(...inline(tok.slice(2, -2), { ...base, bold: true }));
    } else if (tok.startsWith("`")) {
      runs.push(new TextRun({
        text: tok.slice(1, -1), font: MONO,
        size: base.size ? Math.round(base.size * 0.9) : 19,
        color: base.color ? base.color : "8A3B12",
        bold: !!base.bold,
        shading: base.size ? undefined : { type: ShadingType.CLEAR, fill: CODE_BG },
      }));
    } else if (tok.startsWith("[")) {
      const mm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      if (mm && mm[2].startsWith("http")) {
        runs.push(new ExternalHyperlink({
          link: mm[2],
          children: [new TextRun({ text: mm[1], font: BODY, size: 21,
                                   color: ACCENT, underline: {} })],
        }));
      } else {
        push(mm ? mm[1] : tok);   // internal anchor — keep the label only
      }
    } else {
      push(tok.slice(1, -1), { italics: true });
    }
    last = m.index + tok.length;
  }
  push(text.slice(last));
  return runs.length ? runs : [new TextRun({ text: "", font: BODY, size: 21 })];
}

function codeLines(lines) {
  return lines.map((l, i) => new Paragraph({
    children: [new TextRun({ text: l || " ", font: MONO, size: 17, color: "23303B" })],
    shading: { type: ShadingType.CLEAR, fill: CODE_BG },
    spacing: { before: i === 0 ? 100 : 0, after: i === lines.length - 1 ? 140 : 0,
               line: 240 },
    indent: { left: 240, right: 240 },
    border: {
      left: { style: BorderStyle.SINGLE, size: 12, color: "C9D2DD", space: 6 },
    },
  }));
}

// ── table ───────────────────────────────────────────────────────────────────
const TOTAL = 9360;                                   // Letter minus 1" margins
function splitRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
}
function buildTable(rows) {
  const header = splitRow(rows[0]);
  const bodyRows = rows.slice(2).map(splitRow);
  const n = header.length;
  // Weight columns by their longest cell so a "file path" column gets room.
  const w = header.map((_, i) => {
    const len = Math.max(header[i].length,
      ...bodyRows.map(r => (r[i] || "").length));
    return Math.min(Math.max(len, 14), 70);   // floor stops "suggeste/d" wrapping
  });
  const sum = w.reduce((a, b) => a + b, 0);
  const MINW = 1420;                          // ~0.8in — fits a short code token
  let widths = w.map(x => Math.max(MINW, Math.round((x / sum) * TOTAL)));
  const over = widths.reduce((a, b) => a + b, 0) - TOTAL;
  if (over > 0) {                             // take the excess off the widest
    const big = widths.indexOf(Math.max(...widths));
    widths[big] -= over;
  }
  widths[n - 1] = TOTAL - widths.slice(0, n - 1).reduce((a, b) => a + b, 0);

  const cell = (txt, i, isHead) => new TableCell({
    width: { size: widths[i], type: WidthType.DXA },
    shading: isHead ? { type: ShadingType.CLEAR, fill: HEAD_BG } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({
      children: inline(txt, isHead ? { bold: true } : {}),
      spacing: { before: 0, after: 0 },
    })],
  });

  return new Table({
    columnWidths: widths,
    width: { size: TOTAL, type: WidthType.DXA },
    rows: [
      new TableRow({ tableHeader: true,
                     children: header.map((h, i) => cell(h, i, true)) }),
      ...bodyRows.map(r => new TableRow({
        children: widths.map((_, i) => cell(r[i] || "", i, false)),
      })),
    ],
  });
}

// ── walk the markdown ───────────────────────────────────────────────────────
const kids = [];
let i = 0;
let seenTitle = false;
const headings = [];      // for the static contents page
let numInstance = 0;      // one counter per numbered list, not per document
let lastWasNum = false;

while (i < md.length) {
  const line = md[i];
  const wasNum = lastWasNum;
  lastWasNum = false;

  // fenced code
  if (/^```/.test(line)) {
    const buf = [];
    i++;
    while (i < md.length && !/^```/.test(md[i])) { buf.push(md[i]); i++; }
    i++;
    kids.push(...codeLines(buf));
    continue;
  }

  // table
  if (/^\s*\|/.test(line) && i + 1 < md.length && /^\s*\|[\s:|-]+\|/.test(md[i + 1])) {
    const buf = [];
    while (i < md.length && /^\s*\|/.test(md[i])) { buf.push(md[i]); i++; }
    kids.push(buildTable(buf));
    kids.push(new Paragraph({ spacing: { after: 160 }, children: [] }));
    continue;
  }

  // headings
  let m;
  if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
    const level = m[1].length, text = m[2];
    if (level === 1 && !seenTitle) {
      seenTitle = true;      // the cover page already carries the title
    } else if (level === 2 && /^contents$/i.test(text.trim())) {
      // Skip the hand-written contents list — the generated one replaces it.
      i++;
      while (i < md.length && !/^##\s/.test(md[i])) i++;
      continue;
    } else {
      const H = { 1: HeadingLevel.HEADING_1, 2: HeadingLevel.HEADING_1,
                  3: HeadingLevel.HEADING_2, 4: HeadingLevel.HEADING_3 }[level];
      const size = { 1: 30, 2: 30, 3: 25, 4: 22 }[level];
      headings.push({ level, text: text.replace(/`/g, "") });
      kids.push(new Paragraph({
        heading: H,
        children: inline(text, { bold: true, color: ACCENT, size }),
        spacing: { before: level <= 2 ? 320 : 220, after: 120 },
        keepNext: true,
        ...(level <= 2 ? {
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "C9D2DD", space: 4 } },
        } : {}),
      }));
    }
    i++; continue;
  }

  // horizontal rule
  if (/^---+\s*$/.test(line)) {
    kids.push(new Paragraph({
      children: [], spacing: { before: 100, after: 100 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "DCE2EA", space: 2 } },
    }));
    i++; continue;
  }

  // blockquote (may span lines)
  if (/^>\s?/.test(line)) {
    const buf = [];
    while (i < md.length && /^>\s?/.test(md[i])) { buf.push(md[i].replace(/^>\s?/, "")); i++; }
    kids.push(new Paragraph({
      children: inline(buf.join(" ").trim(), { italics: true, color: MUTED }),
      spacing: { before: 100, after: 160, line: 280 },
      indent: { left: 300 },
      border: { left: { style: BorderStyle.SINGLE, size: 16, color: ACCENT, space: 8 } },
    }));
    continue;
  }

  // bullet
  if ((m = /^(\s*)-\s+(.*)$/.exec(line))) {
    const depth = Math.min(Math.floor(m[1].length / 2), 2);
    const buf = [m[2]];
    i++;
    while (i < md.length && /^\s{2,}\S/.test(md[i]) && !/^\s*[-*]\s/.test(md[i])
           && !/^\s*\|/.test(md[i]) && !/^```/.test(md[i])) {
      buf.push(md[i].trim()); i++;
    }
    kids.push(new Paragraph({
      children: inline(buf.join(" ")),
      numbering: { reference: "bul", level: depth },
      spacing: { after: 70, line: 280 },
    }));
    continue;
  }

  // numbered
  if ((m = /^(\s*)(\d+)\.\s+(.*)$/.exec(line))) {
    const depth = Math.min(Math.floor(m[1].length / 3), 2);
    // A new list starts wherever the previous element was not a numbered item —
    // one shared counter made section 5.8 open at "27." and section 6 at "32.".
    if (!wasNum) numInstance++;
    const buf = [m[3]];
    i++;
    while (i < md.length && /^\s{3,}\S/.test(md[i]) && !/^\s*\d+\.\s/.test(md[i])
           && !/^```/.test(md[i]) && !/^\s*\|/.test(md[i])) {
      buf.push(md[i].trim()); i++;
    }
    kids.push(new Paragraph({
      children: inline(buf.join(" ")),
      numbering: { reference: "num", level: depth, instance: numInstance },
      spacing: { after: 70, line: 280 },
    }));
    lastWasNum = true;
    continue;
  }

  if (line.trim() === "") { i++; continue; }

  // paragraph (join wrapped lines)
  const buf = [line.trim()];
  i++;
  while (i < md.length && md[i].trim() !== "" && !/^[#>`|-]/.test(md[i])
         && !/^\s*\d+\.\s/.test(md[i])) {
    buf.push(md[i].trim()); i++;
  }
  kids.push(new Paragraph({
    children: inline(buf.join(" ")),
    spacing: { after: 140, line: 280 },
  }));
}

// ── assemble ────────────────────────────────────────────────────────────────
const front = [
  new Paragraph({
    children: [new TextRun({ text: "Trinamix Conversion Workbench",
                             font: BODY, size: 52, bold: true, color: INK })],
    spacing: { before: 2600, after: 100 },
    alignment: AlignmentType.CENTER,
  }),
  new Paragraph({
    children: [new TextRun({ text: "Codebase Guide",
                             font: BODY, size: 40, color: ACCENT })],
    spacing: { after: 300 },
    alignment: AlignmentType.CENTER,
  }),
  new Paragraph({
    children: [new TextRun({
      text: "Architecture, the conversion flow, and change recipes for editing the code by hand",
      font: BODY, size: 22, italics: true, color: MUTED })],
    alignment: AlignmentType.CENTER,
    spacing: { after: 1400 },
  }),
  new Paragraph({
    children: [new TextRun({
      text: "Generated from docs/CODEBASE_GUIDE.md — that Markdown file is the "
          + "canonical version and lives in the repository. Regenerate this "
          + "document after editing it.",
      font: BODY, size: 18, color: MUTED })],
    alignment: AlignmentType.CENTER,
  }),
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({
    children: [new TextRun({ text: "Contents", font: BODY, size: 30, bold: true, color: ACCENT })],
    spacing: { after: 160 },
  }),
  ...headings.filter(h => h.level <= 3).map(h => new Paragraph({
    children: [new TextRun({
      text: h.text, font: BODY,
      size: h.level <= 2 ? 22 : 20,
      bold: h.level <= 2,
      color: h.level <= 2 ? INK : MUTED,
    })],
    spacing: { after: h.level <= 2 ? 90 : 50 },
    indent: { left: h.level <= 2 ? 0 : 360 },
  })),
  new Paragraph({ children: [new PageBreak()] }),
];

const body = kids;

const doc = new Document({
  creator: "Trinamix Conversion Workbench",
  title: "Codebase Guide",
  numbering: {
    config: [
      { reference: "bul", levels: [0, 1, 2].map(l => ({
          level: l, format: LevelFormat.BULLET, text: ["•", "–", "·"][l],
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360 + l * 300, hanging: 240 } } },
        })) },
      { reference: "num", levels: [0, 1, 2].map(l => ({
          level: l, format: LevelFormat.DECIMAL, text: `%${l + 1}.`,
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360 + l * 300, hanging: 260 } } },
        })) },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840, orientation: PageOrientation.PORTRAIT },
        margin: { top: 1080, bottom: 1080, left: 1440, right: 1440 },
      },
    },
    children: front.concat(body),
  }],
});

Packer.toBuffer(doc).then(b => { fs.writeFileSync(OUT, b); console.log("wrote", OUT, b.length, "bytes"); });
