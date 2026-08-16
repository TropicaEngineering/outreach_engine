import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import {
  AlignmentType,
  BorderStyle,
  Document,
  Footer,
  Header,
  HeadingLevel,
  LevelFormat,
  Packer,
  PageBreak,
  PageNumber,
  Paragraph,
  ShadingType,
  TextRun,
} from "docx";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const args = process.argv.slice(2);
const outputIndex = args.indexOf("--output-dir");
if (outputIndex < 0 || !args[outputIndex + 1]) {
  throw new Error("--output-dir is required");
}
const outputDir = path.resolve(args[outputIndex + 1]);
const input = await new Promise((resolve, reject) => {
  let body = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", chunk => { body += chunk; });
  process.stdin.on("end", () => {
    try { resolve(JSON.parse(body)); } catch (error) { reject(error); }
  });
  process.stdin.on("error", reject);
});

await fs.mkdir(outputDir, { recursive: true });
const generated = [];

const colours = {
  ink: "28241E",
  muted: "746C60",
  blue: "315E6D",
  paleBlue: "E9F0F1",
  cream: "FAF6EC",
  gold: "9A6A2F",
  line: "D9D1C2",
};

const safeText = value => String(value ?? "").replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");

function textRuns(value, options = {}) {
  const text = safeText(value);
  const parts = text.split(/(\*\*[^*]+\*\*|\[[^\]]+\])/g).filter(Boolean);
  return parts.map(part => {
    const bold = part.startsWith("**") && part.endsWith("**");
    const companyInput = part.startsWith("[COMPANY INPUT REQUIRED:");
    return new TextRun({
      text: bold ? part.slice(2, -2) : part,
      bold: bold || options.bold,
      italics: companyInput || options.italics,
      color: companyInput ? colours.gold : (options.color || colours.ink),
      size: options.size || 22,
      font: "Calibri",
    });
  });
}

function bodyParagraph(text) {
  return new Paragraph({
    children: textRuns(text),
    alignment: AlignmentType.JUSTIFIED,
    spacing: { before: 0, after: 160, line: 320, lineRule: "auto" },
    widowControl: true,
  });
}

function contentParagraphs(content) {
  const lines = safeText(content).split("\n");
  const children = [];
  for (let index = 0; index < lines.length; index += 1) {
    const trimmed = lines[index].trim();
    if (!trimmed) continue;
    const bullet = trimmed.match(/^-\s+(.+)$/);
    const numberedHeading = trimmed.match(/^(\d+)\.\s+(.+)$/);
    if (bullet) {
      children.push(new Paragraph({
        children: textRuns(bullet[1]),
        numbering: { reference: "proposal-bullets", level: 0 },
        spacing: { before: 0, after: 80, line: 290, lineRule: "auto" },
        keepLines: true,
      }));
    } else if (numberedHeading) {
      children.push(new Paragraph({
        children: textRuns(trimmed, { bold: true, color: colours.blue }),
        spacing: { before: 140, after: 60, line: 280, lineRule: "auto" },
        keepNext: true,
      }));
    } else if (/^\[COMPANY INPUT REQUIRED:/.test(trimmed)) {
      children.push(new Paragraph({
        children: textRuns(trimmed, { italics: true, color: colours.gold }),
        shading: { type: ShadingType.CLEAR, fill: colours.cream, color: "auto" },
        border: {
          left: { color: colours.gold, style: BorderStyle.SINGLE, size: 14, space: 8 },
        },
        indent: { left: 180, right: 120 },
        spacing: { before: 100, after: 140, line: 290, lineRule: "auto" },
      }));
    } else {
      children.push(bodyParagraph(trimmed));
    }
  }
  return children;
}

function buildDocx(file) {
  const sections = file.sections || [];
  const cover = [
    new Paragraph({
      children: textRuns(input.bidder, { bold: true, color: colours.muted, size: 24 }),
      alignment: AlignmentType.CENTER,
      spacing: { before: 180, after: 180 },
    }),
    new Paragraph({
      children: textRuns(input.opportunityTitle, { bold: true, color: colours.ink, size: 48 }),
      alignment: AlignmentType.CENTER,
      spacing: { before: 240, after: 120 },
    }),
    new Paragraph({
      children: textRuns("Bid response", { color: colours.blue, size: 30 }),
      alignment: AlignmentType.CENTER,
      spacing: { before: 0, after: 360 },
    }),
    new Paragraph({
      children: textRuns(`Prepared for ${input.buyer}`, { bold: true, color: colours.muted, size: 21 }),
      alignment: AlignmentType.CENTER,
      spacing: { after: 80 },
    }),
    new Paragraph({
      children: textRuns(`Prepared by ${input.bidder}`, { color: colours.muted, size: 21 }),
      alignment: AlignmentType.CENTER,
      spacing: { after: 420 },
    }),
    new Paragraph({ children: [new PageBreak()] }),
  ];

  const body = [];
  for (const section of sections) {
    body.push(new Paragraph({
      text: safeText(section.title),
      heading: HeadingLevel.HEADING_1,
      keepNext: true,
    }));
    body.push(...contentParagraphs(section.draft_content || ""));
  }

  return new Document({
    creator: "SignalRoute",
    title: safeText(input.title),
    description: "Editable first-pass tender response",
    styles: {
      default: {
        document: {
          run: { font: "Calibri", size: 22, color: colours.ink },
          paragraph: { spacing: { before: 0, after: 160, line: 320, lineRule: "auto" } },
        },
      },
      paragraphStyles: [
        {
          id: "Heading1",
          name: "Heading 1",
          basedOn: "Normal",
          next: "Normal",
          quickFormat: true,
          run: { font: "Calibri", size: 32, bold: true, color: colours.blue },
          paragraph: { spacing: { before: 360, after: 200 }, keepNext: true, outlineLevel: 0 },
        },
        {
          id: "Heading2",
          name: "Heading 2",
          basedOn: "Normal",
          next: "Normal",
          quickFormat: true,
          run: { font: "Calibri", size: 26, bold: true, color: colours.blue },
          paragraph: { spacing: { before: 240, after: 120 }, keepNext: true, outlineLevel: 1 },
        },
        {
          id: "Heading3",
          name: "Heading 3",
          basedOn: "Normal",
          next: "Normal",
          quickFormat: true,
          run: { font: "Calibri", size: 24, bold: true, color: colours.blue },
          paragraph: { spacing: { before: 160, after: 80 }, keepNext: true, outlineLevel: 2 },
        },
      ],
    },
    numbering: {
      config: [{
        reference: "proposal-bullets",
        levels: [{
          level: 0,
          format: LevelFormat.BULLET,
          text: "•",
          alignment: AlignmentType.LEFT,
          style: {
            paragraph: { indent: { left: 540, hanging: 280 } },
            run: { font: "Calibri", color: colours.blue },
          },
        }],
      }],
    },
    sections: [{
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 2304, left: 1440, header: 708, footer: 576 },
        },
      },
      headers: {
        default: new Header({ children: [new Paragraph({
          children: textRuns(`${input.bidder}  |  ${input.buyer}`, { color: colours.muted, size: 17 }),
          alignment: AlignmentType.RIGHT,
          spacing: { after: 0 },
        })] }),
      },
      footers: {
        default: new Footer({ children: [new Paragraph({
          children: [
            ...textRuns("First-pass editable response  •  Page ", { color: colours.muted, size: 17 }),
            new TextRun({ children: [PageNumber.CURRENT], color: colours.muted, size: 17, font: "Calibri" }),
          ],
          alignment: AlignmentType.RIGHT,
        })] }),
      },
      children: [...cover, ...body],
    }],
  });
}

function money(value) {
  const match = safeText(value).replaceAll(",", "").match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

async function buildPricing(file) {
  const pricing = file.pricing || {};
  const lines = pricing.line_items || [];
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("Pricing Schedule");
  sheet.showGridLines = false;

  sheet.getRange("A1:F1").merge();
  sheet.getRange("A1").values = [["Pricing Schedule"]];
  sheet.getRange("A1:F1").format = {
    fill: "#315E6D",
    font: { bold: true, color: "#FFFFFF", size: 18 },
    verticalAlignment: "center",
  };
  sheet.getRange("A1:F1").format.rowHeight = 32;

  sheet.getRange("A2:F2").merge();
  sheet.getRange("A2").values = [[input.opportunityTitle]];
  sheet.getRange("A2:F2").format = {
    fill: "#E9F0F1",
    font: { bold: true, color: "#28241E", size: 12 },
    verticalAlignment: "center",
  };
  sheet.getRange("A2:F2").format.rowHeight = 25;

  sheet.getRange("A4:B6").values = [
    ["Bidder", input.bidder],
    ["Budget ceiling", money(pricing.budget_ceiling) ?? pricing.budget_ceiling ?? "Not stated"],
    ["Target total", money(pricing.target_total) ?? pricing.target_total ?? "Commercial input required"],
  ];
  sheet.getRange("A4:A6").format = { font: { bold: true, color: "#746C60" } };
  sheet.getRange("B5:B6").setNumberFormat('"£"#,##0.00');

  const headerRow = 8;
  sheet.getRange(`A${headerRow}:F${headerRow}`).values = [[
    "Cost item", "Quantity", "Unit", "Unit price", "Total", "Basis / check",
  ]];
  sheet.getRange(`A${headerRow}:F${headerRow}`).format = {
    fill: "#D8E4E6",
    font: { bold: true, color: "#28241E" },
    borders: { preset: "outside", style: "thin", color: "#9BAFB4" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange(`A${headerRow}:F${headerRow}`).format.rowHeight = 28;

  const firstDataRow = headerRow + 1;
  if (lines.length) {
    const values = lines.map(line => [
      line.item || "",
      money(line.quantity) ?? line.quantity ?? "",
      line.unit || "",
      money(line.unit_price) ?? line.unit_price ?? "",
      null,
      line.basis || "",
    ]);
    const lastDataRow = firstDataRow + values.length - 1;
    sheet.getRange(`A${firstDataRow}:F${lastDataRow}`).values = values;
    for (let row = firstDataRow; row <= lastDataRow; row += 1) {
      const qtyNumeric = typeof values[row - firstDataRow][1] === "number";
      const rateNumeric = typeof values[row - firstDataRow][3] === "number";
      if (qtyNumeric && rateNumeric) {
        sheet.getRange(`E${row}`).formulas = [[`=B${row}*D${row}`]];
      } else {
        sheet.getRange(`E${row}`).values = [[money(lines[row - firstDataRow].total) ?? lines[row - firstDataRow].total ?? ""]];
      }
    }
    sheet.getRange(`B${firstDataRow}:B${lastDataRow}`).setNumberFormat("0.00");
    sheet.getRange(`D${firstDataRow}:E${lastDataRow}`).setNumberFormat('"£"#,##0.00');
    sheet.getRange(`B${firstDataRow}:B${lastDataRow}`).format.horizontalAlignment = "center";
    sheet.getRange(`A${firstDataRow}:F${lastDataRow}`).format = {
      borders: { insideHorizontal: { style: "thin", color: "#DDD6C8" } },
      verticalAlignment: "center",
      wrapText: true,
    };
    sheet.getRange(`A${lastDataRow + 1}:D${lastDataRow + 1}`).merge();
    sheet.getRange(`A${lastDataRow + 1}`).values = [["TOTAL"]];
    sheet.getRange(`E${lastDataRow + 1}`).formulas = [[`=SUM(E${firstDataRow}:E${lastDataRow})`]];
    sheet.getRange(`A${lastDataRow + 1}:F${lastDataRow + 1}`).format = {
      fill: "#315E6D",
      font: { bold: true, color: "#FFFFFF" },
      borders: { preset: "outside", style: "thin", color: "#315E6D" },
    };
    sheet.getRange(`E${lastDataRow + 1}`).setNumberFormat('"£"#,##0.00');

    const assumptionsRow = lastDataRow + 4;
    sheet.getRange(`A${assumptionsRow}:F${assumptionsRow}`).merge();
    sheet.getRange(`A${assumptionsRow}`).values = [["Commercial assumptions and checks"]];
    sheet.getRange(`A${assumptionsRow}:F${assumptionsRow}`).format = {
      fill: "#FAF6EC",
      font: { bold: true, color: "#9A6A2F" },
    };
    const assumptions = pricing.assumptions || [];
    if (assumptions.length) {
      const assumptionValues = assumptions.map(value => ["•", value, null, null, null, null]);
      sheet.getRange(`A${assumptionsRow + 1}:F${assumptionsRow + assumptions.length}`).values = assumptionValues;
      sheet.getRange(`B${assumptionsRow + 1}:F${assumptionsRow + assumptions.length}`).merge(true);
      sheet.getRange(`A${assumptionsRow + 1}:F${assumptionsRow + assumptions.length}`).format = {
        wrapText: true,
        verticalAlignment: "top",
      };
    }
    const noteRow = assumptionsRow + Math.max(assumptions.length, 1) + 2;
    sheet.getRange(`A${noteRow}:F${noteRow}`).merge();
    sheet.getRange(`A${noteRow}`).values = [[pricing.strategy_note || "Commercial approval required before submission."]];
    sheet.getRange(`A${noteRow}:F${noteRow}`).format = {
      fill: "#FFF4D6",
      font: { italic: true, color: "#7A5A00" },
      wrapText: true,
    };
    if (input.sourceUrl) {
      sheet.getRange(`A${noteRow + 2}:F${noteRow + 2}`).merge();
      sheet.getRange(`A${noteRow + 2}`).values = [[`Source notice: ${input.sourceUrl}`]];
      sheet.getRange(`A${noteRow + 2}:F${noteRow + 2}`).format = {
        font: { color: "#746C60", size: 9 },
        wrapText: true,
      };
    }
  }

  sheet.getRange("A:A").format.columnWidth = 34;
  sheet.getRange("B:B").format.columnWidth = 14;
  sheet.getRange("C:C").format.columnWidth = 21;
  sheet.getRange("D:E").format.columnWidth = 14;
  sheet.getRange("F:F").format.columnWidth = 42;
  sheet.freezePanes.freezeRows(headerRow);

  const check = await workbook.inspect({
    kind: "table",
    range: "Pricing Schedule!A1:F40",
    include: "values,formulas",
    tableMaxRows: 40,
    tableMaxCols: 8,
  });
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "final formula error scan",
  });
  const preview = await workbook.render({
    sheetName: "Pricing Schedule",
    autoCrop: "all",
    scale: 1.5,
    format: "png",
  });
  await fs.writeFile(path.join(outputDir, ".pricing-preview.png"), new Uint8Array(await preview.arrayBuffer()));
  await fs.writeFile(path.join(outputDir, ".pricing-inspect.ndjson"), `${check.ndjson}\n${errors.ndjson}\n`);

  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(outputDir, file.filename));
}

for (const file of input.files || []) {
  if (file.kind === "docx") {
    const document = buildDocx(file);
    await fs.writeFile(path.join(outputDir, file.filename), await Packer.toBuffer(document));
  } else if (file.kind === "xlsx") {
    await buildPricing(file);
  } else {
    continue;
  }
  generated.push({
    id: file.id,
    kind: file.kind,
    filename: file.filename,
    label: file.label,
    reason: file.reason,
  });
}

await fs.writeFile(path.join(outputDir, "generated.json"), JSON.stringify({ files: generated }, null, 2));
