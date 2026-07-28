import json
import os
import sqlite3
from datetime import datetime

from mcp.server.fastmcp import FastMCP

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".logs")
os.makedirs(LOG_DIR, exist_ok=True)


def get_prompt_log_path() -> str:
    env_path = os.getenv("PROMPT_LOG_PATH")
    if env_path:
        return env_path
    return os.path.join(LOG_DIR, "mcp-default.log")


def append_log(message: str) -> None:
    """Append a timestamped entry to the prompt-specific MCP log file."""
    path = get_prompt_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


mcp = FastMCP("Chinook-SQL-Toolbox")
DB_PATH = "chinook.db"


def get_connection():
    """Helper to get a read-only database connection."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


@mcp.tool()
def list_database_tables() -> str:
    """
    Retrieves a high-level list of all available tables in the database.
    Use this first to understand what data exists before asking for schemas.
    """
    append_log("Function call: list_database_tables")
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            return json.dumps({"tables": tables})
    except Exception as e:
        return f"Error connecting to database: {str(e)}"


@mcp.tool()
def get_table_schema(table_names: list[str]) -> str:
    """
    Retrieves the exact DDL (schema) for specific tables.
    Pass an array of table names (e.g., ["Artist", "Album"]).
    """
    append_log(f"Function call: get_table_schema | table_names={json.dumps(table_names)}")
    if not table_names:
        return "Error: Please provide a list of table names."

    schemas = {}
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(table_names))
            query = f"SELECT name, sql FROM sqlite_master WHERE type='table' AND name IN ({placeholders});"
            cursor.execute(query, table_names)

            for row in cursor.fetchall():
                schemas[row[0]] = row[1]

        return json.dumps({"schemas": schemas}, indent=2)
    except Exception as e:
        return f"Error retrieving schema: {str(e)}"


@mcp.tool()
def execute_read_query(sql_query: str) -> str:
    """
    Executes a valid SQL SELECT query against the target database and returns the result set.
    Only read operations are supported.
    """
    append_log(f"SQL query: {sql_query}")
    forbidden_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"]
    if any(keyword in sql_query.upper() for keyword in forbidden_keywords):
        return "Error: Only SELECT queries are permitted by this tool."

    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql_query)
            rows = cursor.fetchmany(100)
            result = [dict(row) for row in rows]
            response = json.dumps({"results": result}, indent=2)
            append_log(f"SQL answer: {response}")
            return response
    except Exception as e:
        error_message = f"SQL Error: {str(e)}"
        append_log(f"SQL answer: {error_message}")
        return error_message


@mcp.tool()
def generate_chart(data: list[dict], chart_type: str, x_field: str, y_field: str, title: str = "") -> str:
    """
    Prepares a chart specification from tabular data for rendering in the UI.
    chart_type must be one of: "bar", "line", "pie", "scatter".
    data should be the list of row dicts (e.g. from execute_read_query's "results").
    x_field and y_field must be keys present in each row of data.
    Call this AFTER execute_read_query when the user's question implies a
    visual comparison, trend, or distribution (e.g. "plot", "chart", "compare",
    "over time", "breakdown by").
    """
    append_log(f"Function call: generate_chart | chart_type={chart_type} | x_field={x_field} | y_field={y_field}")
    allowed_types = {"bar", "line", "pie", "scatter"}
    if chart_type not in allowed_types:
        return "Error: chart_type must be one of: bar, line, pie, scatter."

    if not isinstance(data, list) or not data:
        return "Error: data must be a non-empty list of row objects."

    if not isinstance(data[0], dict):
        return "Error: data must be a list of dictionaries."

    if not isinstance(x_field, str) or not isinstance(y_field, str) or not x_field or not y_field:
        return "Error: x_field and y_field must be non-empty strings."

    if x_field not in data[0] or y_field not in data[0]:
        return "Error: x_field and y_field must exist in the first row of data."

    chart_spec = {
        "chart_type": chart_type,
        "x_field": x_field,
        "y_field": y_field,
        "title": title,
        "data": data,
    }
    response = json.dumps({"chart_spec": chart_spec})
    append_log(f"Chart spec: {response}")
    return response


if __name__ == "__main__":
    mcp.run()