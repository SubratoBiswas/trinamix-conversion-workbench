const fs=require('fs');
const {Document,Packer,Paragraph,TextRun,ImageRun,AlignmentType,PageBreak,
       PageOrientation,BorderStyle,Table,TableRow,TableCell,WidthType,ShadingType}=require('docx');

const img=fs.readFileSync(__dirname+'/architecture_diagram.png');
const G="595e6b", INK="1a2233";

const p=(t,o={})=>new Paragraph({alignment:o.align,spacing:{before:o.before??0,after:o.after??140},
  children:[new TextRun({text:t,font:"Calibri",size:o.size??20,color:o.color??INK,
    bold:o.bold,italics:o.italics})]});

const cell=(t,{b=false,w=2600,fill}={})=>new TableCell({
  width:{size:w,type:WidthType.DXA},
  shading: fill?{type:ShadingType.CLEAR,fill}:undefined,
  margins:{top:80,bottom:80,left:130,right:130},
  children:[new Paragraph({spacing:{after:0},children:[
    new TextRun({text:t,font:"Calibri",size:19,bold:b,color:INK})]})]});

const rows=[
  ["Stage","What it does"],
  ["Ingest & profile","Reads the legacy extract, detects which system it came from and which Oracle object it feeds."],
  ["Map","Proposes the field mapping, constant defaults and transformations for review."],
  ["Cleanse & validate","Applies data-quality checks, coded-value handling and required-field verification."],
  ["Generate","Builds the load files in the exact layout Oracle expects, in load-sequence order."],
];

const title=(t)=>new Paragraph({spacing:{after:60},children:[new TextRun({
  text:t,font:"Calibri",size:34,bold:true,color:INK})]});

const rule=(t)=>new Paragraph({spacing:{after:60},
  border:{bottom:{style:BorderStyle.SINGLE,size:6,color:"dfe4ec",space:8}},
  children:[new TextRun({text:t,font:"Calibri",size:21,color:G})]});

const doc=new Document({
  creator:"Trinamix", title:"Trinamix Conversion Workbench — Architecture Overview",
  sections:[{
    properties:{page:{size:{width:12240,height:15840,orientation:PageOrientation.LANDSCAPE},
      margin:{top:900,bottom:900,left:1000,right:1000}}},
    children:[
      // ── page 1: the picture ────────────────────────────────────────────
      title("Trinamix Conversion Workbench"),
      rule("Legacy ERP to Oracle Fusion Cloud data conversion — architecture overview"),
      new Paragraph({spacing:{before:200,after:120},alignment:AlignmentType.CENTER,
        children:[new ImageRun({type:"png",data:img,
          transformation:{width:900,height:514}})]}),
      new Paragraph({spacing:{after:0},alignment:AlignmentType.CENTER,children:[new TextRun({
        text:"Conceptual overview. Component boundaries are indicative and do not represent "+
             "deployment topology, data model or internal design.",
        font:"Calibri",size:16,color:G,italics:true})]}),
      new Paragraph({children:[new PageBreak()]}),

      // ── page 2: what it means ──────────────────────────────────────────
      title("How a conversion flows"),
      rule("Four stages, run and re-run as often as the data requires"),
      new Paragraph({spacing:{before:200,after:0},children:[new TextRun({text:"",size:2})]}),
      new Table({columnWidths:[2800,11040],
        rows:rows.map((r,i)=>new TableRow({children:[
          cell(r[0],{b:true,w:2800,fill:i?undefined:"eef2ff"}),
          cell(r[1],{b:i===0,w:11040,fill:i?undefined:"eef2ff"})]}))}),
      p("Two properties worth noting",{bold:true,size:23,before:320,after:110}),
      p("Decisions are made once. A mapping, default or correction approved by an analyst is retained "+
        "for that client and source system, and reused automatically across every conversion — current "+
        "and future. Where decisions compete, the most recent one governs.",{after:130}),
      p("Runs are repeatable. Regenerating a conversion from the same inputs and the same decisions "+
        "produces the same output, so a load can be rebuilt and reconciled with confidence.",{after:0}),
      new Paragraph({spacing:{before:320},
        border:{top:{style:BorderStyle.SINGLE,size:6,color:"dfe4ec",space:8}},
        children:[new TextRun({
        text:"Confidential — for client discussion only.  ·  © Trinamix. All rights reserved.",
        font:"Calibri",size:16,color:G,italics:true})]}),
    ]}]});

Packer.toBuffer(doc).then(b=>{fs.writeFileSync(__dirname+'/Trinamix_Architecture_Overview.docx',b);
  console.log('written',b.length);});
