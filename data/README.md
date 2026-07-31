# Demo enterprise workbook

`knowledge_base.xlsx` is intentionally excluded from source control and from the
container image. Mount a sanitized workbook at `data/knowledge_base.xlsx` before
running Docker Compose. The existing workbook contract is seven sheets, with
employee-owned HR, IT, and Finance rows containing an `EMP_ID` column.

For a shareable demo, provide a sanitized copy with the same sheet names and
columns. Do not use production employee or operational data in a laptop demo.
