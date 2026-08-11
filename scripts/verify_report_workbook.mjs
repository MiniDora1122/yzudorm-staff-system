import fs from "node:fs/promises";
import path from "node:path";

const artifactToolModule = process.env.ARTIFACT_TOOL_MODULE ?? "@oai/artifact-tool";
const { FileBlob, SpreadsheetFile } = await import(artifactToolModule);

const [inputPath, outputPath, previewPath, inspectionPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !previewPath || !inspectionPath) {
  throw new Error("Usage: node verify_report_workbook.mjs <input> <output> <preview> <inspection>");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItemAt(0);
const used = sheet.getUsedRange();
const summary = await workbook.inspect({
  kind: "workbook,sheet,table,formula,computedStyle",
  sheetId: sheet.name,
  range: used?.address ?? "A1:J200",
  maxChars: 30000,
  options: { maxResults: 1000 },
});
const inspection = summary.ndjson ?? String(summary);
if (/#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/.test(inspection)) {
  throw new Error("Formula error found in generated report");
}
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(path.dirname(previewPath), { recursive: true });
const preview = await workbook.render({ sheetName: sheet.name, autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
await fs.writeFile(inspectionPath, inspection, "utf8");
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
