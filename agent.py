import asyncio
import json
import os
import sys
from datetime import datetime

from google import genai
from google.genai import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".logs")
os.makedirs(LOG_DIR, exist_ok=True)


def clean_schema(schema: object) -> object:
    """Recursively strip unsupported keys (e.g. additionalProperties) from MCP tool schemas for Gemini."""
    if isinstance(schema, dict):
        cleaned = {}
        for k, v in schema.items():
            if k in ("additionalProperties", "additional_properties"):
                continue
            cleaned[k] = clean_schema(v)
        return cleaned
    elif isinstance(schema, list):
        return [clean_schema(item) for item in schema]
    return schema


def get_prompt_log_path(log_path: str | None = None) -> str:
    if log_path:
        return log_path
    env_path = os.getenv("PROMPT_LOG_PATH")
    if env_path:
        return env_path
    return os.path.join(LOG_DIR, "default.log")


def append_log(message: str, log_path: str | None = None) -> None:
    """Append a timestamped entry to the prompt-specific log file."""
    path = get_prompt_log_path(log_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def load_gemini_api_key() -> str | None:
    """Load a Gemini API key from the environment or a local .env file."""
    for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()

    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(dotenv_path):
        with open(dotenv_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in {"GEMINI_API_KEY", "GOOGLE_API_KEY"} and value:
                    return value

    return None


def create_gemini_client():
    api_key = load_gemini_api_key()
    if not api_key:
        print("No Gemini API key found.")
        print("Set GEMINI_API_KEY or GOOGLE_API_KEY in your shell, or create a .env file in this folder with:")
        print("GEMINI_API_KEY=your_key_here")
        raise SystemExit(1)

    return genai.Client(api_key=api_key)


async def main():
    gemini = create_gemini_client()
    server_params = StdioServerParameters(command=sys.executable, args=["chinook_mcp.py"])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("==================================================")
            print("✅ Connected to Chinook-SQL-Toolbox MCP Server")
            print("🤖 Text-to-SQL Agent Ready!")
            print("Type 'exit' or 'quit' to end the program.")
            print("==================================================\n")

            mcp_tools_response = await session.list_tools()
            gemini_tools = []
            for tool in mcp_tools_response.tools:
                gemini_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=tool.name,
                                description=tool.description,
                                parameters=clean_schema(tool.inputSchema),
                            )
                        ]
                    )
                )

            config = types.GenerateContentConfig(
                tools=gemini_tools,
                temperature=0.0,
                system_instruction=(
                    "You are a data analyst agent. Use your tools to inspect the database schema, "
                    "write accurate SQLite queries, execute them, and answer the user's question. "
                    "When a visual would help (for example to compare values, trace a trend, or break down categories), "
                    "call generate_chart after retrieving data with execute_read_query."
                ),
            )

            while True:
                user_prompt = input("\n💬 Ask a question about Chinook DB: ").strip()
                append_log(f"Prompt: {user_prompt}")

                if user_prompt.lower() in ["exit", "quit"]:
                    print("\n👋 Goodbye!")
                    break

                if not user_prompt:
                    continue

                print("\n🤖 Agent is working...")

                chat = gemini.chats.create(model="gemini-3.5-flash-lite", config=config)
                response = chat.send_message(user_prompt)

                while response.function_calls:
                    for function_call in response.function_calls:
                        tool_name = function_call.name
                        tool_args = function_call.args

                        print(f"  🔧 Executing: {tool_name}({tool_args})")
                        append_log(f"Function call: {tool_name} | args={json.dumps(tool_args, ensure_ascii=False)}")

                        mcp_result = await session.call_tool(tool_name, tool_args)
                        tool_output = mcp_result.content[0].text
                        append_log(f"Function result: {tool_name} | output={tool_output}")

                        try:
                            payload = json.loads(tool_output) if tool_output.lstrip().startswith("{") else tool_output
                        except json.JSONDecodeError:
                            payload = tool_output

                        response = chat.send_message(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": payload},
                            )
                        )

                print(f"\n✅ Result:\n{response.text}\n")
                print("-" * 50)


if __name__ == "__main__":
    print("Run the chat UI with: streamlit run app.py")
    asyncio.run(main())