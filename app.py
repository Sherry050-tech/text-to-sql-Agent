import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from agent import append_log, clean_schema, create_gemini_client
from auth import create_user, init_user_db, verify_user
from google.genai import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

st.set_page_config(page_title="Text-to-SQL", page_icon="🗄️", layout="centered")

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".logs")
os.makedirs(LOG_DIR, exist_ok=True)

init_user_db()

if "conversations" not in st.session_state:
    st.session_state.conversations = []
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "username" not in st.session_state:
    st.session_state.username = ""
if "logs_cleared" not in st.session_state:
    st.session_state.logs_cleared = False


def sanitize_username(username: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", username.strip())


def clear_logs_on_launch():
    if st.session_state.get("logs_cleared"):
        return

    username = st.session_state.get("username", "")
    if not username:
        st.session_state.logs_cleared = True
        return

    safe_username = sanitize_username(username)
    user_log_dir = os.path.join(LOG_DIR, safe_username)
    os.makedirs(user_log_dir, exist_ok=True)

    for filename in os.listdir(user_log_dir):
        file_path = os.path.join(user_log_dir, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)

    st.session_state.logs_cleared = True


def create_prompt_log_path(username: str | None = None) -> str:
    active_username = username or st.session_state.get("username", "") or "default"
    safe_username = sanitize_username(active_username)
    user_log_dir = os.path.join(LOG_DIR, safe_username)
    os.makedirs(user_log_dir, exist_ok=True)
    return os.path.join(
        user_log_dir,
        f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}.log",
    )


def render_login_ui():
    st.title("Text-to-SQL")
    st.caption("Ask questions about the Chinook database and get SQL-backed answers.")
    st.subheader("Sign in to continue")
    st.caption("Register a username and password, or log in with an existing account.")

    login_tab, register_tab = st.tabs(["Login", "Register"])

    with login_tab:
        with st.form("login_form"):
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log in")
            if submitted:
                if verify_user(username, password):
                    st.session_state.authenticated = True
                    st.session_state.username = sanitize_username(username)
                    st.session_state.logs_cleared = False
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

    with register_tab:
        with st.form("register_form"):
            username = st.text_input("Username", key="register_username")
            password = st.text_input("Password", type="password", key="register_password")
            submitted = st.form_submit_button("Register")
            if submitted:
                if create_user(username, password):
                    st.session_state.authenticated = True
                    st.session_state.username = sanitize_username(username)
                    st.session_state.logs_cleared = False
                    st.rerun()
                else:
                    st.error("Username already exists or is invalid.")


clear_logs_on_launch()


async def run_agent_query(user_prompt: str, log_path: str) -> dict[str, object]:
    os.environ["PROMPT_LOG_PATH"] = log_path
    gemini = create_gemini_client()
    server_params = StdioServerParameters(command=sys.executable, args=["chinook_mcp.py"])

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

            chat = gemini.chats.create(model="gemini-3.5-flash-lite", config=config)
            response = chat.send_message(user_prompt)
            chart_spec = None

            while response.function_calls:
                for function_call in response.function_calls:
                    tool_name = function_call.name
                    tool_args = function_call.args
                    append_log(f"Function call: {tool_name} | args={json.dumps(tool_args, ensure_ascii=False)}")

                    mcp_result = await session.call_tool(tool_name, tool_args)
                    tool_output = mcp_result.content[0].text
                    append_log(f"Function result: {tool_name} | output={tool_output}")

                    try:
                        payload = json.loads(tool_output) if tool_output.lstrip().startswith("{") else tool_output
                    except json.JSONDecodeError:
                        payload = tool_output

                    if tool_name == "generate_chart" and isinstance(payload, dict):
                        chart_spec = payload.get("chart_spec")

                    response = chat.send_message(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": payload},
                        )
                    )

            return {"text": response.text or "", "chart_spec": chart_spec}


if not st.session_state.get("authenticated"):
    render_login_ui()
    st.stop()

top_col1, top_col2 = st.columns([1, 4])
with top_col1:
    if st.button("Logout", key="logout_button"):
        st.session_state.authenticated = False
        st.session_state.username = ""
        st.session_state.logs_cleared = False
        st.rerun()

with top_col2:
    st.caption(f"👤 Signed in as **{st.session_state['username']}**")

st.title("Text-to-SQL")
st.caption("Ask questions about the Chinook database and get SQL-backed answers.")

if prompt := st.chat_input("Ask a question about the Chinook database"):
    log_path = create_prompt_log_path(st.session_state["username"])
    prompt_id = uuid.uuid4().hex
    st.session_state.conversations.append(
        {
            "id": prompt_id,
            "prompt": prompt,
            "response": None,
            "chart_spec": None,
            "log_path": log_path,
            "show_logs": False,
        }
    )

    append_log(f"Prompt: {prompt}", log_path=log_path)

    with st.spinner("Thinking..."):
        try:
            result = asyncio.run(run_agent_query(prompt, log_path))
        except Exception as exc:
            err_msg = exc
            if hasattr(exc, "exceptions") and exc.exceptions:
                sub = exc.exceptions[0]
                while hasattr(sub, "exceptions") and sub.exceptions:
                    sub = sub.exceptions[0]
                err_msg = sub
            result = {"text": f"Sorry, I could not process that request. Error: {err_msg}", "chart_spec": None}

    st.session_state.conversations[-1]["response"] = result["text"]
    st.session_state.conversations[-1]["chart_spec"] = result.get("chart_spec")

for conversation in st.session_state.conversations:
    with st.chat_message("user"):
        st.markdown(conversation["prompt"])

    if conversation.get("response") is not None:
        with st.chat_message("assistant"):
            st.markdown(conversation["response"])

            if conversation.get("chart_spec"):
                try:
                    chart_spec = conversation["chart_spec"]
                    df = pd.DataFrame(chart_spec["data"])

                    if chart_spec["chart_type"] == "pie":
                        fig = px.pie(
                            df,
                            names=chart_spec["x_field"],
                            values=chart_spec["y_field"],
                            title=chart_spec.get("title", ""),
                        )
                    elif chart_spec["chart_type"] == "bar":
                        fig = px.bar(
                            df,
                            x=chart_spec["x_field"],
                            y=chart_spec["y_field"],
                            title=chart_spec.get("title", ""),
                        )
                    elif chart_spec["chart_type"] == "line":
                        fig = px.line(
                            df,
                            x=chart_spec["x_field"],
                            y=chart_spec["y_field"],
                            title=chart_spec.get("title", ""),
                        )
                    else:
                        fig = px.scatter(
                            df,
                            x=chart_spec["x_field"],
                            y=chart_spec["y_field"],
                            title=chart_spec.get("title", ""),
                        )

                    st.plotly_chart(fig, use_container_width=True)
                except Exception:
                    st.warning("Could not render chart.")

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
