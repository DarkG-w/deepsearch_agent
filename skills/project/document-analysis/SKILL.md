---
name: document-analysis
description: Analyze uploaded documents, PDFs, Word files, Markdown, text files, spreadsheets, and private knowledge-base material. Use when the user asks to summarize, extract key points, compare documents, answer questions from files, inspect uploaded content, or turn source material into structured conclusions.
allowed-tools: read_file_content get_assistant_list create_ask_delete generate_markdown convert_md_to_pdf
---

# Document Analysis

Use this skill when the task depends on uploaded files, session files, or private knowledge-base documents.

## Workflow

1. Check whether the prompt references uploaded files, filenames, document types, or knowledge-base content.
2. Read uploaded/session files before answering when they are relevant.
3. If multiple files exist, inspect names first and choose the files most likely to answer the question.
4. For private-domain knowledge not present in uploaded files, use the knowledge-base assistant tools.
5. Extract structure before conclusions:
   - title or document purpose
   - main sections
   - key facts, entities, numbers, dates, and constraints
   - assumptions and missing information
6. Answer the user from the document evidence, not from generic background knowledge.

## Analysis Patterns

For summaries:

```text
核心结论
关键依据
重要细节
风险或不确定点
```

For comparisons:

```text
共同点
差异点
影响
建议
```

For extraction:

```text
字段/主题
原文依据或位置线索
提取结果
备注
```

## Output Rules

- State when a file could not be read or when evidence is incomplete.
- Avoid pretending to inspect files that were not actually read.
- Preserve important names, numbers, dates, table fields, and document-specific terminology.
- For long documents, summarize first, then drill into the requested section.
- When producing a deliverable from documents, combine this skill with `report-generation`.

