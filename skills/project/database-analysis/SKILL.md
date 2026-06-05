---
name: database-analysis
description: Analyze MySQL business databases, schemas, tables, SQL query results, inventory data, sales data, and operational metrics. Use when the user asks about database information, table structure, SQL analysis, business indicators, data summaries, anomalies, trends, or report generation from database records.
allowed-tools: list_sql_tables get_table_data execute_sql_query generate_markdown convert_md_to_pdf
---

# Database Analysis

Use this skill when the task depends on database schema or table-backed facts.

## Workflow

1. Start by listing available tables unless the relevant table is already known.
2. Inspect table structure and sample rows before writing SQL that assumes fields.
3. Translate the user's business question into concrete data needs:
   - entities
   - metrics
   - filters
   - grouping dimensions
   - time range
4. Run focused SQL queries. Prefer simple, auditable queries over clever opaque ones.
5. Validate results against table meaning and units.
6. Explain findings in business language, not only SQL language.

## Query Discipline

- Use read-only analysis queries.
- Avoid destructive statements such as `DROP`, `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, or schema changes.
- Limit broad row dumps. Aggregate first when the user asks for trends, totals, rankings, or comparisons.
- If a query fails, inspect schema again and correct the field/table assumption.
- If the user's question is ambiguous, choose a reasonable interpretation and state it briefly.

## Common Analysis Types

For table overview:

```text
可用表
字段说明
数据规模
关键业务含义
```

For metric analysis:

```text
指标定义
SQL 查询逻辑
结果
业务解读
注意事项
```

For anomaly analysis:

```text
异常规则
命中记录或聚合结果
可能原因
建议动作
```

## Output Rules

- Include the relevant table names and important fields used.
- Present numbers with units when available.
- Do not overclaim causality from simple query results.
- For formal outputs, combine this skill with `report-generation`.

