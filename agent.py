import asyncio
import os
import json
from google import genai
from google.genai import types
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

# Initialize the Gemini client
gemini = genai.Client()

async def main():
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
                        
                        # Call the tool via MCP
                        mcp_result = await session.call_tool(tool_name, tool_args)
                        tool_output = mcp_result.content[0].text
                        
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
    asyncio.run(main())