import asyncio
import os
import json
from datetime import datetime
from google import genai
from google.genai import types
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".logs")
os.makedirs(LOG_DIR, exist_ok=True)


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
    # 1. Define how to start our MCP server
    server_params = StdioServerParameters(
        command="python",
        args=["chinook_mcp.py"]
    )

    # 2. Connect to the MCP Server once for the entire session
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("==================================================")
            print("✅ Connected to Chinook-SQL-Toolbox MCP Server")
            print("🤖 Text-to-SQL Agent Ready!")
            print("Type 'exit' or 'quit' to end the program.")
            print("==================================================\n")

            # Fetch the tools exposed by the MCP server
            mcp_tools_response = await session.list_tools()
            
            # Map MCP tools to Gemini's format
            gemini_tools = []
            for tool in mcp_tools_response.tools:
                gemini_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=tool.name,
                                description=tool.description,
                                parameters=tool.inputSchema
                            )
                        ]
                    )
                )

            # Configure Gemini with the MCP tools
            config = types.GenerateContentConfig(
                tools=gemini_tools,
                temperature=0.0,
                system_instruction="You are a data analyst agent. Use your tools to inspect the database schema, write accurate SQLite queries, execute them, and answer the user's question."
            )

            # 3. Interactive Input Loop
            while True:
                user_prompt = input("\n💬 Ask a question about Chinook DB: ").strip()
                append_log(f"Prompt: {user_prompt}")

                # Exit condition
                if user_prompt.lower() in ["exit", "quit"]:
                    print("\n👋 Goodbye!")
                    break

                if not user_prompt:
                    continue

                print("\n🤖 Agent is working...")

                # Create a fresh chat session for each prompt
                chat = gemini.chats.create(model="gemini-3.5-flash-lite", config=config)
                response = chat.send_message(user_prompt)

                # Execute the tool-calling loop
                while response.function_calls:
                    for function_call in response.function_calls:
                        tool_name = function_call.name
                        tool_args = function_call.args
                        
                        print(f"  🔧 Executing: {tool_name}({tool_args})")
                        append_log(f"Function call: {tool_name} | args={json.dumps(tool_args, ensure_ascii=False)}")
                        
                        # Call the tool via MCP
                        mcp_result = await session.call_tool(tool_name, tool_args)
                        tool_output = mcp_result.content[0].text
                        append_log(f"Function result: {tool_name} | output={tool_output}")
                        
                        # Send result back to Gemini
                        response = chat.send_message(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": json.loads(tool_output) if tool_output.startswith("{") else tool_output}
                            )
                        )

                # Output the result
                print(f"\n✅ Result:\n{response.text}\n")
                print("-" * 50)

if __name__ == "__main__":
    print("Run the chat UI with: streamlit run app.py")
    asyncio.run(main())