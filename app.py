import asyncio
import os
import json
import uuid
from datetime import datetime

import streamlit as st

from agent import create_gemini_client, append_log
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from google.genai import types

st.set_page_config(page_title="Text-to-SQL", page_icon="🗄️", layout="centered")
st.title("Text-to-SQL")
st.caption("Ask questions about the Chinook database and get SQL-backed answers.")

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".logs")
os.makedirs(LOG_DIR, exist_ok=True)


def clear_logs_on_launch():
    if st.session_state.get("logs_cleared"):
        return

    for filename in os.listdir(LOG_DIR):
        file_path = os.path.join(LOG_DIR, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)

    st.session_state.logs_cleared = True


def create_prompt_log_path() -> str:
    return os.path.join(
        LOG_DIR,
        f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}.log",
    )


if "conversations" not in st.session_state:
    st.session_state.conversations = []

clear_logs_on_launch()


async def run_agent_query(user_prompt: str, log_path: str) -> str:
    os.environ["PROMPT_LOG_PATH"] = log_path
    gemini = create_gemini_client()
    server_params = StdioServerParameters(command="python", args=["chinook_mcp.py"])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools_response = await session.list_tools()

            gemini_tools = []
            for tool in mcp_tools_response.tools:
                gemini_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=tool.name,
                                description=tool.description,
                                parameters=tool.inputSchema,
                            )
                        ]
                    )
                )

            config = types.GenerateContentConfig(
                tools=gemini_tools,
                temperature=0.0,
                system_instruction="You are a data analyst agent. Use your tools to inspect the database schema, write accurate SQLite queries, execute them, and answer the user's question.",
            )

            chat = gemini.chats.create(model="gemini-3.5-flash-lite", config=config)
            response = chat.send_message(user_prompt)

            while response.function_calls:
                for function_call in response.function_calls:
                    tool_name = function_call.name
                    tool_args = function_call.args
                    append_log(f"Function call: {tool_name} | args={json.dumps(tool_args, ensure_ascii=False)}")

                    mcp_result = await session.call_tool(tool_name, tool_args)
                    tool_output = mcp_result.content[0].text
                    append_log(f"Function result: {tool_name} | output={tool_output}")

                    response = chat.send_message(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": json.loads(tool_output) if tool_output.startswith("{") else tool_output},
                        )
                    )

            return response.text


if prompt := st.chat_input("Ask a question about the Chinook database"):
    log_path = create_prompt_log_path()
    prompt_id = uuid.uuid4().hex
    st.session_state.conversations.append({
        "id": prompt_id,
        "prompt": prompt,
        "response": None,
        "log_path": log_path,
        "show_logs": False,
    })

    append_log(f"Prompt: {prompt}", log_path=log_path)

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                response_text = asyncio.run(run_agent_query(prompt, log_path))
            except Exception as exc:
                response_text = f"Sorry, I could not process that request. Error: {exc}"
        st.markdown(response_text)

    st.session_state.conversations[-1]["response"] = response_text

for conversation in st.session_state.conversations:
    with st.chat_message("user"):
        st.markdown(conversation["prompt"])

    if conversation.get("response") is not None:
        with st.chat_message("assistant"):
            st.markdown(conversation["response"])

        button_key = f"show_logs_button_{conversation['id']}"
        state_key = f"show_logs_state_{conversation['id']}"
        if state_key not in st.session_state:
            st.session_state[state_key] = False

        def toggle_logs(key: str = state_key):
            st.session_state[key] = not st.session_state[key]

        st.button("Show logs", key=button_key, on_click=toggle_logs)

        if st.session_state[state_key]:
            if os.path.exists(conversation["log_path"]):
                with open(conversation["log_path"], "r", encoding="utf-8") as handle:
                    st.text_area(
                        "Prompt logs",
                        handle.read(),
                        height=300,
                        disabled=True,
                        key=f"log_area_{conversation['id']}",
                    )
            else:
                st.warning("Log file not found.")
