---
name: report-generation
description: Generate structured Chinese research reports, analysis reports, Markdown documents, and PDFs from web search, knowledge-base results, database evidence, or uploaded files. Use when the user asks for a report, research brief, summary document, PDF output, industry analysis, project analysis, or formal deliverable.
allowed-tools: generate_markdown convert_md_to_pdf read_file_content internet_search get_assistant_list create_ask_delete list_sql_tables get_table_data execute_sql_query
---

# Report Generation

Use this skill when the user wants a report-style deliverable rather than a short answer.

## Workflow

1. Identify the requested deliverable type: research report, analysis report, executive summary, comparison report, database report, or PDF.
2. Clarify only if the output format, audience, or scope is genuinely missing.
3. Gather evidence before writing:
   - Use uploaded files first when the prompt mentions attached or uploaded material.
   - Use knowledge-base tools for private/domain documents.
   - Use database tools for business data, metrics, inventory, sales, or table-backed questions.
   - Use web search for current or external facts.
4. Draft a clear report structure before generating final prose.
5. Separate evidence from interpretation. Do not invent numbers, source names, table fields, or file contents.
6. Generate Markdown when the user wants a saved document, long report, or PDF.
7. Convert Markdown to PDF when the user explicitly asks for PDF or a formal file output.

## Recommended Structure

For most reports, use:

```text
# Title

## Executive Summary

## Background

## Evidence and Findings

## Analysis

## Risks or Limitations

## Recommendations

## Appendix or Data Sources
```

Adjust the sections to fit the task. For short reports, keep only the sections that add value.

## Output Rules

- Write in Chinese unless the user asks for another language.
- Keep claims traceable to tool results, uploaded files, database rows, or clearly stated assumptions.
- For database-backed reports, include the SQL intent and key fields used, but do not dump excessive raw rows.
- For web-backed reports, mention source names or search basis when available.
- Save generated files inside the current session working directory.
- Prefer Markdown first, then PDF conversion. Do not generate a PDF before the Markdown content is coherent.

