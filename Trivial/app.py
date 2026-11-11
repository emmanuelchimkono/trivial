"""
Trivial - a password-protected maths and science assistant with an Oxbridge interview mode,
powered by Claude.

Run locally:   streamlit run app.py
Settings:      .streamlit/secrets.toml locally, or the "Secrets" box on Streamlit Community Cloud
Instructions:  system_prompt.md (shared), general_mode.md, interview_mode.md
Knowledge:     drop .md / .txt / .pdf files into the knowledge/ folder
Monitoring:    log in with ADMIN_PASSWORD to review saved conversations (see db.py)
"""

import hmac
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import pandas as pd
import streamlit as st

import db as convo_db

# ---------------------------------------------------------------------------
# Settings you can change
# ---------------------------------------------------------------------------
BOT_NAME = "Trivial"
DEFAULT_MODEL = "claude-opus-5-5"    # Anthropic's strongest widely available model for hard maths
EFFORT = "high"                      # how hard the model thinks: "low", "medium", "high", "max"
CODE_CHECKING = True                 # let the model run Python to check its own results
MAX_OUTPUT_TOKENS = 32000            # includes the model's private thinking, so keep this generous
MAX_MESSAGES_PER_SESSION = 60        # cost guard: messages one visitor can send before "New chat"
HISTORY_MESSAGES = 40                # most recent messages sent to the model (interviews need memory)
MAX_CONTEXT_CHARS = 300_000          # warn yourself if knowledge files get bigger than this
LOG_CONVERSATIONS = True             # save conversations so you can review them
LOGGING_NOTICE = (
    "Conversations are saved so they can be reviewed to improve Trivial. "
    "Please don't share personal information."
)

MODES = ["General", "Interview"]
INTERVIEW_SUBJECTS = ["Mathematics", "Physics", "Chemistry", "Computer Science", "Engineering"]
EXAM_BOARDS = ["Not specified", "AQA", "Pearson Edexcel", "OCR", "OCR MEI", "CIE", "WJEC/Eduqas"]
CODE_TOOL = {"type": "code_execution_20250825", "name": "code_execution"}

APP_DIR = Path(__file__).parent
BASE_PROMPT_FILE = APP_DIR / "system_prompt.md"
MODE_PROMPT_FILES = {"General": APP_DIR / "general_mode.md", "Interview": APP_DIR / "interview_mode.md"}
KNOWLEDGE_DIR = APP_DIR / "knowledge"
KNOWLEDGE_TYPES = {".md", ".txt", ".pdf"}

st.set_page_config(page_title=BOT_NAME, page_icon="🎓")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_setting(name, default=None):
    """Read a setting from Streamlit secrets, falling back to environment variables."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets file at all
        pass
    return os.environ.get(name, default)


def setting_is_on(name, default):
    value = get_setting(name, default)
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}


def require_password():
    """Show a login form and stop the app until the right password is entered."""
    expected = get_setting("APP_PASSWORD")
    admin_password = get_setting("ADMIN_PASSWORD")
    if not expected:
        st.error("No APP_PASSWORD has been set. Add one to your secrets (see README.md).")
        st.stop()
    if st.session_state.get("authenticated"):
        return

    st.markdown(
        f'<div class="trivial-hello" style="margin-top:14vh"><span class="mark">∑</span> {BOT_NAME}</div>'
        '<div class="trivial-sub">Rigorous help with maths and science, and Oxbridge interview practice.</div>',
        unsafe_allow_html=True,
    )
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        with st.form("login"):
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Enter", type="primary", width="stretch")
        if logging_on():
            st.markdown(f'<div class="trivial-footnote" style="margin-top:0.8rem">{LOGGING_NOTICE}</div>',
                        unsafe_allow_html=True)
    if submitted:
        # compare_digest avoids leaking information through timing differences
        if admin_password and hmac.compare_digest(password.encode(), str(admin_password).encode()):
            st.session_state.authenticated = True
            st.session_state.is_admin = True
            st.rerun()
        elif hmac.compare_digest(password.encode(), str(expected).encode()):
            st.session_state.authenticated = True
            st.session_state.is_admin = False
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


# ---------------------------------------------------------------------------
# Conversation logging (never allowed to break the chat)
# ---------------------------------------------------------------------------
def logging_on():
    return setting_is_on("LOG_CONVERSATIONS", LOG_CONVERSATIONS)


@st.cache_resource(show_spinner=False)
def get_engine(url):
    return convo_db.make_engine(url)


def database():
    """The logging database, or None if it can't be reached."""
    try:
        return get_engine(get_setting("DATABASE_URL") or None)
    except Exception as error:
        print(f"Database unavailable: {error}")
        st.session_state.db_error = str(error)
        return None


def log(function, *args, **kwargs):
    """Run a database write; if anything goes wrong, carry on without logging."""
    if not logging_on():
        return None
    engine = database()
    if engine is None:
        return None
    try:
        return function(engine, *args, **kwargs)
    except Exception as error:
        print(f"Logging failed: {error}")
        st.session_state.db_error = str(error)
        return None


def save_feedback(message, key):
    message["feedback"] = st.session_state.get(key)
    log(convo_db.set_feedback, message["db_id"], message["feedback"])


def feedback_widget(message, key):
    """Thumbs up/down under a reply, saved to the database."""
    if message.get("db_id"):
        st.feedback("thumbs", key=key, on_change=save_feedback, args=(message, key))


def knowledge_files():
    """Files in knowledge/ the bot should read. Files starting with '_' are skipped."""
    if not KNOWLEDGE_DIR.exists():
        return []
    return sorted(
        p for p in KNOWLEDGE_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in KNOWLEDGE_TYPES and not p.name.startswith("_")
    )


def files_signature():
    """Names + modification times of every instruction and knowledge file,
    so the bot reloads automatically when you change one."""
    prompt_files = [BASE_PROMPT_FILE, *MODE_PROMPT_FILES.values()]
    paths = [p for p in prompt_files if p.exists()] + knowledge_files()
    return tuple((str(p.relative_to(APP_DIR)), p.stat().st_mtime) for p in paths)


def read_text(path):
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


@st.cache_data(show_spinner=False)
def build_system_prompt(signature, mode, subject, board):
    """Combine the shared instructions, the current mode's instructions and the knowledge files."""
    parts = []
    if BASE_PROMPT_FILE.exists():
        parts.append(BASE_PROMPT_FILE.read_text(encoding="utf-8").strip())
    else:
        parts.append("You are a helpful assistant.")

    if board != "Not specified":
        parts.append(
            f"For A-level questions, the student follows the {board} specifications: use that "
            "board's methods, notation, formula/data booklet and (for Computer Science) pseudocode style."
        )

    mode_file = MODE_PROMPT_FILES.get(mode)
    if mode_file and mode_file.exists():
        parts.append(mode_file.read_text(encoding="utf-8").strip().replace("{subject}", subject))

    prompt_names = {str(p.relative_to(APP_DIR)) for p in [BASE_PROMPT_FILE, *MODE_PROMPT_FILES.values()]}
    documents = []
    for name, _mtime in signature:
        if name in prompt_names:
            continue
        path = APP_DIR / name
        try:
            text = read_text(path).strip()
        except Exception as error:  # a broken file shouldn't take the whole bot down
            print(f"Could not read {name}: {error}")
            continue
        if text:
            documents.append(f'<document name="{path.name}">\n{text}\n</document>')

    if documents:
        parts.append(
            "Use the following documents as background knowledge when they are relevant.\n\n"
            "<documents>\n" + "\n\n".join(documents) + "\n</documents>"
        )
    knowledge_chars = sum(len(d) for d in documents)
    return "\n\n".join(parts), len(documents), knowledge_chars


@st.cache_resource
def get_client(api_key):
    return anthropic.Anthropic(api_key=api_key)


def _stream_once(client, model, system_prompt, messages, use_code, status, thinking_note):
    """One full model turn. The model thinks privately first (adaptive thinking), may run
    Python to check its work, and long code runs can pause the turn, so we resume until done."""
    convo = list(messages)
    for _ in range(10):  # safety cap on resumes
        kwargs = dict(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            # cache_control lets Anthropic reuse the long instructions between messages
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=convo,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
        )
        if use_code:
            kwargs["tools"] = [CODE_TOOL]
        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                if not status.get("started"):
                    status["started"] = True
                    thinking_note.empty()
                yield text
            final = stream.get_final_message()

        if any(getattr(block, "type", "") == "server_tool_use" for block in final.content):
            status["ran_code"] = True
        usage = getattr(final, "usage", None)
        if usage is not None:
            status["input_tokens"] = status.get("input_tokens", 0) + sum(
                getattr(usage, name, 0) or 0
                for name in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
            status["output_tokens"] = status.get("output_tokens", 0) + (getattr(usage, "output_tokens", 0) or 0)
        if final.stop_reason != "pause_turn":
            if final.stop_reason == "max_tokens":
                yield "\n\n⚠️ *The reply hit the length limit and may be incomplete.*"
            return
        # The turn paused mid-way (e.g. a long code run): send it back so the model carries on
        convo = convo + [{"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in final.content]}]


def stream_reply(client, model, system_prompt, messages, status, thinking_note):
    """Stream a reply, falling back to no code checking if the model or account doesn't support it."""
    use_code = setting_is_on("CODE_CHECKING", CODE_CHECKING) and st.session_state.get("code_ok", True)
    try:
        yield from _stream_once(client, model, system_prompt, messages, use_code, status, thinking_note)
    except anthropic.BadRequestError as error:
        if use_code and not status.get("started") and "tool" in str(error).lower():
            st.session_state.code_ok = False
            yield from _stream_once(client, model, system_prompt, messages, False, status, thinking_note)
        else:
            raise


def recent_history(messages):
    """The last few messages, always starting with a user message (the API requires that)."""
    history = messages[-HISTORY_MESSAGES:]
    while history and history[0]["role"] != "user":
        history = history[1:]
    return [{"role": m["role"], "content": m["content"]} for m in history]


CODE_NOTE = "🧮 Ran code to check the working."
FEEDBACK_LABELS = {1: "👍", 0: "👎"}


# ---------------------------------------------------------------------------
# Review page (admin only)
# ---------------------------------------------------------------------------
def render_review():
    st.title("Review conversations")
    if not logging_on():
        st.info("Conversation logging is switched off (LOG_CONVERSATIONS).")
        return
    engine = database()
    if engine is None:
        st.error(f"Can't reach the database: {st.session_state.get('db_error', 'unknown error')}")
        return
    if not get_setting("DATABASE_URL"):
        st.info(
            "Saving to a local file (conversations.db). That's fine on your own computer, but online "
            "it's wiped whenever the app restarts. Add DATABASE_URL to your secrets (see README.md)."
        )

    summary = convo_db.conversation_summary(engine)
    if summary.empty:
        st.write("No conversations yet.")
        return
    summary["started_at"] = pd.to_datetime(summary["started_at"], utc=True)

    # ---- Filters ----
    col1, col2, col3 = st.columns(3)
    modes = col1.multiselect("Mode", MODES, default=MODES)
    only_down = col2.checkbox("Only with 👎")
    only_notes = col2.checkbox("Only with my notes")
    include_mine = col3.checkbox("Include my own chats")
    search = st.text_input("Search all messages", placeholder="e.g. Galois, integral, STEP")

    view = summary[summary["mode"].isin(modes)]
    if not include_mine:
        view = view[~view["is_admin"].astype(bool)]
    if only_down:
        view = view[view["thumbs_down"] > 0]
    if only_notes:
        view = view[view["notes"] > 0]
    if search.strip():
        view = view[view["id"].isin(convo_db.search_conversation_ids(engine, search.strip()))]

    # ---- Totals ----
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Conversations", len(view))
    m2.metric("Questions", int(view["questions"].sum()))
    m3.metric("👍", int(view["thumbs_up"].sum()))
    m4.metric("👎", int(view["thumbs_down"].sum()))
    tokens_in, tokens_out = int(view["input_tokens"].sum()), int(view["output_tokens"].sum())
    m5.metric("Tokens", f"{(tokens_in + tokens_out) / 1000:,.1f}k",
              help=f"{tokens_in:,} in / {tokens_out:,} out. A rough guide to cost.")

    # ---- Overview table ----
    table = pd.DataFrame({
        "Started (UTC)": view["started_at"].dt.strftime("%d %b %Y %H:%M"),
        "Mode": view["mode"],
        "Subject": view["subject"].fillna(""),
        "Questions": view["questions"],
        "👍": view["thumbs_up"],
        "👎": view["thumbs_down"],
        "Notes": view["notes"],
        "First message": view["first_question"].fillna("").str.slice(0, 90),
    })
    st.dataframe(table, hide_index=True, width="stretch")

    st.download_button(
        "Download everything as CSV",
        convo_db.export_all(engine).to_csv(index=False),
        file_name="trivial_conversations.csv",
        mime="text/csv",
    )

    if view.empty:
        return

    # ---- One conversation in full ----
    st.divider()
    labels = {
        row.id: f"{row.started_at:%d %b %H:%M} · {row.mode} · {(row.first_question or '')[:60]}"
        for row in view.itertuples()
    }
    chosen = st.selectbox("Open a conversation", list(labels), format_func=labels.get)
    row = view[view["id"] == chosen].iloc[0]
    st.caption(
        f"Model: {row['model']} · Exam board: {row['exam_board']}"
        + (f" · Subject: {row['subject']}" if row["subject"] else "")
    )

    for msg in convo_db.conversation_messages(engine, chosen):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"] or "")
            if msg["role"] != "assistant":
                continue
            details = []
            if msg["feedback"] in FEEDBACK_LABELS:
                details.append(f"Feedback: {FEEDBACK_LABELS[msg['feedback']]}")
            if msg["ran_code"]:
                details.append("ran code")
            if msg["input_tokens"] is not None:
                details.append(f"{msg['input_tokens']:,} in / {msg['output_tokens']:,} out tokens")
            if details:
                st.caption(" · ".join(details))
            note = st.text_area(
                "Your note on this reply", value=msg["admin_note"] or "",
                key=f"note_{msg['id']}", height=70, placeholder="What should change?",
            )
            if st.button("Save note", key=f"save_note_{msg['id']}"):
                convo_db.set_note(engine, msg["id"], note.strip())
                st.toast("Note saved")

    with st.expander("Delete this conversation"):
        confirm = st.checkbox("Yes, permanently delete it", key=f"confirm_delete_{chosen}")
        if st.button("Delete", disabled=not confirm, key=f"delete_{chosen}"):
            convo_db.delete_conversation(engine, chosen)
            st.rerun()


# ---------------------------------------------------------------------------
# Look and feel (colours live in .streamlit/config.toml)
# ---------------------------------------------------------------------------
GREEN = "#2E6B4E"
CREAM = "#FBF7EE"
ASSISTANT_AVATAR = ":material/functions:"

CUSTOM_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap');

/* A centred reading column, like Claude */
[data-testid="stMainBlockContainer"] {{ max-width: 800px; padding-top: 2.5rem; padding-bottom: 2rem; }}
[data-testid="stBottomBlockContainer"] {{ max-width: 800px; background: {CREAM}; padding-bottom: 1.5rem; }}
[data-testid="stChatInput"] {{ border-radius: 1.2rem; box-shadow: 0 2px 10px rgba(46, 107, 78, 0.08); }}

/* Messages: no boxes by default */
[data-testid="stChatMessage"] {{ background: transparent; padding: 0.35rem 0; gap: 0.8rem; }}

/* Your messages: green bubble on the right, no avatar */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {{ flex-direction: row-reverse; }}
[data-testid="stChatMessageAvatarUser"] {{ display: none; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {{
    flex: 0 1 auto !important; max-width: 78%; margin-left: auto !important; margin-right: 0 !important;
    background: #E2ECE1; border: 1px solid #CFDFD0;
    border-radius: 1.15rem 1.15rem 0.35rem 1.15rem; padding: 0.6rem 1.05rem 0.55rem 1.05rem;
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stMarkdownContainer"] p {{
    margin: 0 !important;
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) :is([data-testid="stMarkdownContainer"], .stMarkdown, [data-testid="stElementContainer"]) {{
    margin-bottom: 0 !important; height: auto !important;
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stVerticalBlock"] {{
    gap: 0 !important; height: auto !important;
}}

/* Trivial's replies: on the left, serif text for comfortable reading */
[data-testid="stChatMessage"]:not(:has([data-testid="stChatMessageAvatarUser"])) [data-testid="stMarkdownContainer"] :is(p, li, h1, h2, h3, h4, blockquote, td, th) {{
    font-family: 'Source Serif 4', Georgia, 'Times New Roman', serif; font-size: 1.06rem; line-height: 1.7;
}}
[data-testid="stChatMessage"]:not(:has([data-testid="stChatMessageAvatarUser"])) [data-testid="stChatMessageContent"] code {{
    font-size: 0.9rem;
}}
[data-testid="stChatMessageAvatarCustom"] {{
    background: {GREEN}; color: {CREAM}; border-radius: 50%;
}}

/* Sidebar */
.trivial-brand {{
    display: flex; align-items: center; gap: 0.6rem; margin: 0.2rem 0 1.1rem 0;
    font-family: 'Source Serif 4', Georgia, serif; font-size: 1.6rem; font-weight: 600; color: {GREEN};
}}
.trivial-brand .mark {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 2.1rem; height: 2.1rem; border-radius: 0.6rem;
    background: {GREEN}; color: {CREAM}; font-size: 1.3rem;
}}
.trivial-section {{
    font-size: 0.75rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
    color: #7A8A80; margin: 1.1rem 0 0.3rem 0.1rem;
}}
/* Conversation list: left-aligned rows, current one highlighted in green */
[class*="st-key-chat_"] button, [class*="st-key-current_chat_"] button {{
    width: 100%; justify-content: flex-start !important; padding: 0.4rem 0.65rem !important;
    border-radius: 0.6rem !important; font-weight: 400; min-height: 2.2rem;
}}
[class*="st-key-chat_"] button > div, [class*="st-key-current_chat_"] button > div {{
    justify-content: flex-start !important; width: 100%; overflow: hidden;
}}
[class*="st-key-chat_"] button p, [class*="st-key-current_chat_"] button p {{
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: left;
}}
[class*="st-key-chat_"] button {{ color: #3A4A41 !important; background: transparent !important; border: none !important; }}
[class*="st-key-chat_"] button:hover {{ background: #E9E1CF !important; color: #24302A !important; }}
[class*="st-key-current_chat_"] button {{
    background: #E2ECE1 !important; border: 1px solid #CFDFD0 !important; color: {GREEN} !important; font-weight: 500;
}}
[class*="st-key-chat_"], [class*="st-key-current_chat_"] {{ margin-bottom: -0.6rem; }}

/* Welcome screen */
.trivial-hello {{
    text-align: center; margin-top: 12vh;
    font-family: 'Source Serif 4', Georgia, serif; font-size: 2.4rem; font-weight: 500; color: #24302A;
}}
.trivial-hello .mark {{ color: {GREEN}; }}
.trivial-sub {{ text-align: center; color: #66756C; margin: 0.4rem 0 1.8rem 0; }}
[class*="st-key-suggestion"] [data-testid="stButtonGroup"] > div,
[class*="st-key-suggestion"] div[role="radiogroup"], [class*="st-key-suggestion"] > div > div {{ justify-content: center !important; }}
.trivial-footnote {{ text-align: center; color: #8A978F; font-size: 0.8rem; margin-top: 2rem; }}
.trivial-thinking {{ color: #7A8A80; font-style: italic; }}
</style>
"""


def apply_style():
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def sidebar_brand():
    st.markdown(f'<div class="trivial-brand"><span class="mark">∑</span>{BOT_NAME}</div>', unsafe_allow_html=True)


def section(label):
    st.markdown(f'<div class="trivial-section">{label}</div>', unsafe_allow_html=True)


def greeting():
    hour = datetime.now(ZoneInfo("Europe/London")).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


SUGGESTIONS = [
    "Evaluate ∫ e^(−x²) dx over ℝ",
    "Prove that √2 is irrational",
    "Explain the Galois correspondence",
    "Set me a STEP-style inequality",
]


# ---------------------------------------------------------------------------
# Conversations (kept for this browser session; also logged to the database)
# ---------------------------------------------------------------------------
def blank_chat(mode="General", subject=INTERVIEW_SUBJECTS[0], board=EXAM_BOARDS[0]):
    return {
        "id": uuid.uuid4().hex, "mode": mode, "subject": subject, "board": board,
        "messages": [], "log_id": None,
    }


def chat_title(chat):
    if chat["mode"] == "Interview":
        return f"{chat['subject']} interview"
    first = next((m["content"] for m in chat["messages"] if m["role"] == "user"), None)
    if not first:
        return "New chat"
    first = " ".join(first.split())
    return first if len(first) <= 38 else first[:37] + "…"


def sync_widgets(chat):
    """Make the sidebar controls show the settings of the given chat."""
    st.session_state.mode_widget = chat["mode"]
    st.session_state.subject_widget = chat["subject"]
    st.session_state.board_widget = chat["board"]


def init_chats():
    if "chats" not in st.session_state:
        chat = blank_chat()
        st.session_state.chats = {chat["id"]: chat}
        st.session_state.current_chat = chat["id"]
        sync_widgets(chat)


def current_chat():
    return st.session_state.chats[st.session_state.current_chat]


def drop_empty_chats(keep):
    for chat_id in [cid for cid, c in st.session_state.chats.items() if not c["messages"] and cid != keep]:
        del st.session_state.chats[chat_id]


def new_chat():
    """'New chat' button: start a fresh conversation with the same settings."""
    chat = current_chat()
    if not chat["messages"]:
        return  # already on a blank chat
    fresh = blank_chat(chat["mode"], chat["subject"], chat["board"])
    st.session_state.chats[fresh["id"]] = fresh
    st.session_state.current_chat = fresh["id"]
    sync_widgets(fresh)


def switch_chat(chat_id):
    drop_empty_chats(keep=chat_id)
    st.session_state.current_chat = chat_id
    sync_widgets(st.session_state.chats[chat_id])


def settings_changed():
    """Mode/subject changes on a chat that's under way start a new chat; the old one stays in the list."""
    chat = current_chat()
    if st.session_state.get("mode_widget") is None:   # clicking the selected option un-selects it
        st.session_state.mode_widget = chat["mode"]
    mode = st.session_state.mode_widget
    subject = st.session_state.get("subject_widget") or chat["subject"]
    board = st.session_state.get("board_widget") or chat["board"]
    if chat["messages"] and (mode != chat["mode"] or (mode == "Interview" and subject != chat["subject"])):
        fresh = blank_chat(mode, subject, board)
        st.session_state.chats[fresh["id"]] = fresh
        st.session_state.current_chat = fresh["id"]
    else:
        chat.update(mode=mode, subject=subject, board=board)


def queue_prompt(text):
    st.session_state.pending_prompt = text


def suggestion_picked():
    choice = st.session_state.get("suggestion")
    if choice:
        st.session_state.pending_prompt = choice
    st.session_state.suggestion = None


def render_message(message):
    if message["role"] == "user":
        with st.chat_message("user"):
            st.markdown(message["content"])
    else:
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            st.markdown(message["content"])
            if message.get("ran_code"):
                st.caption(CODE_NOTE)
            feedback_widget(message, f"feedback_{message.get('db_id')}")


def logout_button():
    if st.button("Log out", icon=":material/logout:", width="stretch"):
        st.session_state.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
apply_style()
require_password()
is_admin = st.session_state.get("is_admin", False)

page = "Chat"
if is_admin:
    with st.sidebar:
        sidebar_brand()
        page = st.segmented_control("View", ["Chat", "Review"], default="Chat", key="admin_view",
                                    label_visibility="collapsed", width="stretch") or "Chat"
        if st.session_state.get("db_error") and logging_on():
            st.warning(f"Logging problem: {st.session_state.db_error}")
    if page == "Review":
        render_review()
        with st.sidebar:
            st.divider()
            logout_button()
        st.stop()

api_key = get_setting("ANTHROPIC_API_KEY")
if not api_key:
    st.error("No ANTHROPIC_API_KEY has been set. Add one to your secrets (see README.md).")
    st.stop()
model = get_setting("MODEL", DEFAULT_MODEL)

init_chats()
chat = current_chat()

# ---- Sidebar: new chat, settings, recent conversations ----
with st.sidebar:
    if not is_admin:
        sidebar_brand()
    st.button("New chat", icon=":material/add:", type="primary", width="stretch", on_click=new_chat)

    section("Mode")
    st.segmented_control("Mode", MODES, key="mode_widget", on_change=settings_changed,
                         label_visibility="collapsed", width="stretch")
    if st.session_state.get("mode_widget", chat["mode"]) == "Interview":
        if "subject_widget" not in st.session_state:
            st.session_state.subject_widget = chat["subject"]
        st.selectbox("Interview subject", INTERVIEW_SUBJECTS, key="subject_widget", on_change=settings_changed)
    with st.expander("Options", icon=":material/tune:"):
        st.selectbox("A-level exam board", EXAM_BOARDS, key="board_widget", on_change=settings_changed)

    chat = current_chat()   # settings callbacks may have switched chats
    section("Recents")
    for chat_id, item in reversed(list(st.session_state.chats.items())):
        if not item["messages"] and chat_id != chat["id"]:
            continue
        is_current = chat_id == chat["id"]
        st.button(
            chat_title(item), key=f"{'current_chat' if is_current else 'chat'}_{chat_id}", width="stretch",
            type="tertiary",
            icon=":material/school:" if item["mode"] == "Interview" else ":material/chat_bubble:",
            on_click=switch_chat, args=(chat_id,),
        )

    st.divider()
    system_prompt, doc_count, knowledge_chars = build_system_prompt(
        files_signature(), chat["mode"], chat["subject"], chat["board"]
    )
    if knowledge_chars > MAX_CONTEXT_CHARS:
        st.warning("The knowledge files are very large. Replies may be slow or expensive.")
    if is_admin:
        st.caption(f"Knowledge files loaded: {doc_count}")
    logout_button()

messages = chat["messages"]
mode, subject, board = chat["mode"], chat["subject"], chat["board"]

# ---- Read the input first, so a new message replaces the welcome screen straight away ----
sent = sum(1 for m in messages if m["role"] == "user")
limit_reached = sent >= MAX_MESSAGES_PER_SESSION
typed = st.chat_input("Ask Trivial anything…" if mode == "General" else "Your answer…",
                      disabled=limit_reached)
prompt = typed or st.session_state.pop("pending_prompt", None)

if prompt:
    if not chat["log_id"]:
        chat["log_id"] = log(
            convo_db.start_conversation, mode, subject if mode == "Interview" else None,
            board, model, is_admin,
        )
    user_db_id = log(convo_db.add_message, chat["log_id"], "user", prompt) if chat["log_id"] else None
    messages.append({"role": "user", "content": prompt, "db_id": user_db_id})

# ---- Main area ----
if not messages:
    if mode == "Interview":
        st.markdown(f'<div class="trivial-hello">{subject} interview</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="trivial-sub">A mock Oxbridge interview. Think out loud and explain your reasoning. '
            'Type “end interview” any time for feedback.</div>',
            unsafe_allow_html=True,
        )
        left, middle, right = st.columns([1, 1, 1])
        middle.button("Start interview", type="primary", width="stretch", on_click=queue_prompt,
                      args=(f"Hello, I'm ready to start my {subject} interview.",))
    else:
        st.markdown(f'<div class="trivial-hello"><span class="mark">∑</span> {greeting()}</div>',
                    unsafe_allow_html=True)
        st.markdown(
            '<div class="trivial-sub">Ask about A-level to olympiad and university maths, '
            'Physics, Chemistry or Computer Science.</div>',
            unsafe_allow_html=True,
        )
        st.pills("Try", SUGGESTIONS, key="suggestion", on_change=suggestion_picked,
                 label_visibility="collapsed")
    st.markdown('<div class="trivial-footnote">Trivial checks its work but can still make mistakes. '
                'Verify important results.</div>', unsafe_allow_html=True)

for message in messages:
    render_message(message)

if limit_reached:
    st.info("You've reached the message limit for this chat. Start a **New chat** to carry on.")

# ---- Answer the new message ----
if prompt:
    conversation_id = chat["log_id"]
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        thinking_note = st.empty()
        thinking_note.markdown(
            '<span class="trivial-thinking">Thinking… hard problems can take a minute or two.</span>',
            unsafe_allow_html=True,
        )
        status = {}
        try:
            reply = st.write_stream(
                stream_reply(
                    get_client(api_key), model, system_prompt, recent_history(messages),
                    status, thinking_note,
                )
            )
        except anthropic.AuthenticationError:
            messages.pop()
            st.error("The API key was rejected. Check ANTHROPIC_API_KEY in your secrets.")
            st.stop()
        except anthropic.RateLimitError:
            messages.pop()
            st.error("Too many requests right now. Please wait a moment and try again.")
            st.stop()
        except anthropic.APIError as error:
            messages.pop()
            st.error(f"Something went wrong talking to the model: {error}")
            st.stop()
        thinking_note.empty()

        reply = reply or ""
        assistant_db_id = None
        if conversation_id:
            assistant_db_id = log(
                convo_db.add_message, conversation_id, "assistant", reply,
                ran_code=status.get("ran_code", False),
                input_tokens=status.get("input_tokens"), output_tokens=status.get("output_tokens"),
            )
    messages.append({
        "role": "assistant", "content": reply,
        "ran_code": status.get("ran_code", False), "db_id": assistant_db_id,
    })
    st.rerun()  # redraw so the sidebar shows the chat's new title and the reply gets its 👍/👎
