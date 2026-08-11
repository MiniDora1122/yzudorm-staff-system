import fs from "node:fs/promises";

const artifactToolModule =
  process.env.ARTIFACT_TOOL_MODULE ?? "@oai/artifact-tool";
const { FileBlob, SpreadsheetFile } = await import(artifactToolModule);

const [inputPath, outputDir] = process.argv.slice(2);
if (!inputPath || !outputDir) {
  throw new Error("Usage: node inspect_reference_workbook.mjs <input.xlsx> <output-dir>");
}

await fs.mkdir(outputDir, { recursive: true });
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const summary = await workbook.inspect({
  kind: "workbook,sheet,table,definedName,drawing",
  maxChars: 12000,
  tableMaxRows: 20,
  tableMaxCols: 20,
  tableMaxCellChars: 120,
});
await fs.writeFile(`${outputDir}/summary.txt`, summary.ndjson ?? String(summary), "utf8");

const sheets = workbook.worksheets.items;
const details = [];
for (const sheet of sheets) {
  const used = sheet.getUsedRange();
  const region = await workbook.inspect({
    kind: "region",
    sheetId: sheet.name,
    range: used?.address ?? "A1:Z80",
    maxChars: 30000,
  });
  const formulas = await workbook.inspect({
    kind: "formula",
    sheetId: sheet.name,
    range: used?.address ?? "A1:Z80",
    maxChars: 12000,
    options: { maxResults: 500 },
  });
  const styles = await workbook.inspect({
    kind: "computedStyle",
    sheetId: sheet.name,
    range: used?.address ?? "A1:Z80",
    maxChars: 20000,
  });
  details.push(
    `### ${sheet.name}\nUSED=${used?.address ?? "unknown"}\n` +
      `REGION\n${region.ndjson ?? region}\nFORMULAS\n${formulas.ndjson ?? formulas}\n` +
      `STYLES\n${styles.ndjson ?? styles}\n`,
  );

  const preview = await workbook.render({
    sheetName: sheet.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  const safeName = sheet.name.replace(/[<>:"/\\|?*]/g, "_");
  await fs.writeFile(
    `${outputDir}/${safeName}.png`,
    new Uint8Array(await preview.arrayBuffer()),
  );
}
await fs.writeFile(`${outputDir}/details.txt`, details.join("\n"), "utf8");
