from mcp.server.fastmcp import FastMCP
import sqlite3
import json
import os
from datetime import datetime

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

# Initialize the MCP Server
mcp = FastMCP("Chinook-SQL-Toolbox")
DB_PATH = "chinook.db"

def get_connection():
    """Helper to get a read-only database connection."""
    # URI with ro=1 ensures SQLite opens in read-only mode for safety
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
            # Safely format the query for the IN clause
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
    # Basic safety block against prompt-injection attempts to modify data
    forbidden_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"]
    if any(keyword in sql_query.upper() for keyword in forbidden_keywords):
        return "Error: Only SELECT queries are permitted by this tool."

    try:
        with get_connection() as conn:
            # Return rows as dictionaries mapping column names to values
            conn.row_factory = sqlite3.Row 
            cursor = conn.cursor()
            cursor.execute(sql_query)
            
            # Limit results to prevent massive token usage
            rows = cursor.fetchmany(100) 
            
            # Convert to a list of dicts for clean JSON output
            result = [dict(row) for row in rows]
            response = json.dumps({"results": result}, indent=2)
            append_log(f"SQL answer: {response}")
            return response
    except Exception as e:
        # Returning the SQL error is crucial so the Agent can fix its mistakes
        error_message = f"SQL Error: {str(e)}"
        append_log(f"SQL answer: {error_message}")
        return error_message

if __name__ == "__main__":
    # Start the server using standard input/output (the MCP communication layer)
    mcp.run()