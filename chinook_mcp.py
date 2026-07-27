from mcp.server.fastmcp import FastMCP
import sqlite3
import json

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
            return json.dumps({"results": result}, indent=2)
    except Exception as e:
        # Returning the SQL error is crucial so the Agent can fix its mistakes
        return f"SQL Error: {str(e)}"

if __name__ == "__main__":
    # Start the server using standard input/output (the MCP communication layer)
    mcp.run()