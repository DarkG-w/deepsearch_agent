import os
import re
from dotenv import load_dotenv, find_dotenv
from mysql.connector import connect, Error
from langchain_core.tools import tool

from api.monitor import monitor

# 强制使用项目 .env，避免被外部环境变量污染
load_dotenv(find_dotenv(), override=True)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(name: str) -> str:
    """Validate SQL identifier (table name / column name)."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    return name


def get_db_config() -> dict:
    """Get database configuration from environment variables."""
    database_name = os.getenv("MYSQL_DATABASE") or os.getenv("MYSQL_SCHEMA")
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": database_name,
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL"),
    }
    config = {k: v for k, v in config.items() if v is not None}

    required_keys = ["host", "port", "user", "password", "database"]
    missing_keys = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(f"Missing database config: {', '.join(missing_keys)}")

    return config


def _rows_to_csv(cursor, rows) -> str:
    """Format cursor result as CSV-like text."""
    description = cursor.description
    if not description:
        return "No result returned."

    columns = [desc[0] for desc in description]
    header = ",".join(columns)
    lines = [",".join(map(str, row)) for row in rows]
    return f"{header}\n" + "\n".join(lines)


@tool
def list_sql_tables() -> str:
    """列出当前数据库中的可用表。"""
    monitor.report_tool(tool_name="数据库工具:list_sql_tables", args={})
    config = get_db_config()

    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                if not tables:
                    return "No tables found."
                table_names = [t[0] for t in tables]
                return "Available tables: " + ", ".join(table_names)
    except Error as e:
        return f"Query failed: {str(e)}"


@tool
def get_table_data(table_name: str) -> str:
    """读取指定表前 100 行数据，用于快速预览。"""
    monitor.report_tool(tool_name="数据库工具:get_table_data", args={"table_name": table_name})
    config = get_db_config()

    try:
        safe_table = _safe_identifier(table_name)
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM `{safe_table}` LIMIT 100")
                rows = cursor.fetchall()
                if not rows:
                    # 仍返回表头信息
                    return _rows_to_csv(cursor, rows)
                return _rows_to_csv(cursor, rows)
    except Error as e:
        return f"Query failed: {str(e)}"
    except ValueError as e:
        return str(e)


@tool
def execute_sql_query(query: str) -> str:
    """执行自定义 SQL 查询并返回 CSV 格式结果。"""
    monitor.report_tool(tool_name="数据库工具:execute_sql_query", args={"query": query})
    config = get_db_config()

    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
                return _rows_to_csv(cursor, rows)
    except Error as e:
        return f"Query failed: {str(e)}"


if __name__ == "__main__":
    print(list_sql_tables.invoke({}))
    print(get_table_data.invoke({"table_name": "drugs"}))
    print(
        execute_sql_query.invoke(
            {"query": "SELECT generic_name, brand_name FROM drugs WHERE generic_name LIKE '%阿莫西林%'"}
        )
    )
