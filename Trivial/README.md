# Trivial

A password-protected maths and science assistant, powered by Claude, that you can put online and
share with a link. It has two modes, picked in the sidebar:

- **General** solves and explains problems from A-level through STEP, MAT, olympiad (BMO/UKMT)
  and university maths (Gaussian integrals, Galois theory, etc.), plus Physics, Chemistry and
  Computer Science. It is told to be rigorous, to name its technique up front, to check its own
  results, and to flag clearly anything it isn't sure of (⚠️ Unverified) instead of guessing.
- **Interview** runs a mock Oxbridge interview in Mathematics, Physics, Chemistry, Computer
  Science or Engineering. It gives gentle, question-style hints that don't name the trick, and
  gives feedback when the candidate types "end interview".

Behind the scenes it uses:
- **Claude Opus** with **extended thinking**: the model works through the problem privately before
  answering, which matters a lot for hard maths. Replies can take a minute or two.
- **Code checking**: the model can run Python in Anthropic's secure sandbox to verify numbers,
  integrals, sums and small cases. When it does, the reply shows "🧮 Ran code to check the working."

**Layout:** a cream and green design in the style of Claude. Your messages appear in green
bubbles on the right and Trivial's replies on the left. The sidebar has a **New chat** button and
a **Recents** list for switching between conversations. Changing mode or interview subject partway
through a chat starts a new one and keeps the old one in the list. Recents last for the browser
session: refreshing the page or logging out starts fresh. Everything is still saved to the
database for your review page.

**Monitoring:** every conversation is saved to a database. Visitors can rate each reply 👍/👎.
When you log in with your separate admin password, you get a **Review** page where
you can filter (e.g. only 👎), search, read full transcripts, add your own notes on what to
tweak, see token usage, download everything as CSV, and delete conversations.

## What's in this folder

| File | What it does |
|---|---|
| `app.py` | The chatbot itself, including the review page |
| `db.py` | Saves conversations, feedback and your notes to the database |
| `system_prompt.md` | Shared instructions: rigour, technique-first answers, no hallucination, self-checking, uncertainty flags |
| `general_mode.md` | Extra instructions for General mode (length, answer layout) |
| `interview_mode.md` | Extra instructions for Interview mode (structure, hint style, feedback). `{subject}` is filled in automatically |
| `knowledge/` | Drop `.md`, `.txt` or `.pdf` files here for the bot to use as background knowledge |
| `requirements.txt` | The Python packages the app needs |
| `.streamlit/config.toml` | The colours and fonts. Edit the hex codes to restyle Trivial |
| `.streamlit/secrets.toml.example` | Template for your password and API key |
| `.gitignore` | Stops your secrets from being uploaded to GitHub |

---

## Step 1: Get an Anthropic API key

1. Go to **console.anthropic.com** and create an account.
2. Add a payment method and some credit.
3. **Set a monthly spending limit** (under Billing / Limits). Anyone with your password
   chats on your bill, so this is your real safety net.
4. Create an API key and copy it somewhere safe. It starts with `sk-ant-`.

## Step 2: Try it on your own computer

**Windows shortcut:** install Python (tick "Add python.exe to PATH"), then double-click
**`Start Trivial.bat`**. The first time, Notepad opens for your passwords and API key. Save, close
Notepad, and Trivial starts in your browser. After that, just double-click it whenever you want
to use Trivial. The steps below do the same thing by hand.

1. Install Python 3.10 or newer from **python.org**. On Windows, tick "Add Python to PATH".
2. Open a terminal (Terminal on Mac, PowerShell on Windows) inside this folder.
3. Install the packages:
   ```
   pip install -r requirements.txt
   ```
4. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in your
   own passwords and API key. Locally, conversations are saved to a file called
   `conversations.db` in this folder, so you don't need `DATABASE_URL` yet.
5. Start it:
   ```
   streamlit run app.py
   ```
   A browser tab opens at `http://localhost:8501`. Log in with your password and chat.
   Maths is shown as properly typeset equations.

## Step 3: Adjust its behaviour and knowledge

- Edit **`system_prompt.md`**, **`general_mode.md`** and **`interview_mode.md`** in plain
  English to change how the bot works and answers. Test it with hard and awkward questions,
  see where it goes wrong, and add a rule for that.
- Put files in **`knowledge/`**: your own notes, formula sheets, past questions you want it to
  know. The bot reads them all with every message, so keep them focused.
- To change the bot's display name, edit `BOT_NAME` near the top of `app.py`.

## Step 4: Put it online (free hosting)

1. **Create a GitHub account** at github.com, then make a new repository (it can be private).
2. **Upload this folder's files** to the repository. The easiest way is the
   "Add file → Upload files" button on GitHub. Drag everything in, including the
   `knowledge` and `.streamlit` folders.
   ⚠️ Do **not** upload `secrets.toml`. Only the `.example` file should be there.
3. Go to **share.streamlit.io** and sign in with GitHub.
4. Click **Create app**, pick your repository, and set the main file to `app.py`.
5. Open **Advanced settings → Secrets** and paste (with your own values):
   ```
   APP_PASSWORD = "your-password"
   ADMIN_PASSWORD = "your-admin-password"
   ANTHROPIC_API_KEY = "sk-ant-your-key"
   DATABASE_URL = "your Neon connection string (see Step 5)"
   ```
6. Click **Deploy**. After a minute or two you get a link like
   `https://your-app-name.streamlit.app`.

Share the link and the student password with people. Keep the admin password to yourself.
To update the bot later, change files on GitHub and the app redeploys itself.

## Step 5: Set up the database for monitoring (free)

Streamlit's hosting wipes local files whenever the app restarts, so online the conversations
need to live in a proper database. Neon's free plan is ideal: 1 GB of storage (enough for a
very large number of conversations), no credit card, and no time limit.

1. Go to **neon.com** and sign up (signing in with GitHub is easiest).
2. Create a project. Pick a region near your users, e.g. a London/Europe one.
3. On the project dashboard, click **Connect** and copy the connection string. It looks like
   `postgresql://user:password@ep-something.eu-west-2.aws.neon.tech/neondb?sslmode=require`.
4. In Streamlit, open your app's **Settings → Secrets** and add:
   ```
   DATABASE_URL = "the connection string you copied"
   ```
5. Save. The app restarts and creates its tables automatically on first use.

To check it works, chat once as a student, then log in with your admin password and open
**Review**. If the page shows a blue notice about a "local file", the
`DATABASE_URL` secret isn't being picked up.

## Using the review page

Log in with `ADMIN_PASSWORD` and switch the sidebar to **Review**.

- **Filters**: by mode, only conversations with a 👎, only ones you've annotated, and a text
  search across all messages. Your own test chats are hidden unless you tick
  "Include my own chats".
- **Totals**: conversations, questions, 👍/👎 counts and tokens used (a rough guide to cost).
- **Open a conversation** to read the full transcript. Each reply shows its rating, whether
  code was run, and its token usage, with a box to save a note on what should change.
- **Download everything as CSV** to analyse in Excel or Google Sheets.
- **Delete this conversation** if someone asks for their data to be removed.

A good routine: once a week, filter to 👎, read those replies, note what went wrong, then
add or adjust a rule in `system_prompt.md`, `general_mode.md` or `interview_mode.md`.

---

## Costs and safety

- **You pay for every message**, whoever sends it. This setup is built for maximum quality,
  not low cost: Opus is Anthropic's priciest widely available model, extended thinking adds
  hidden "thinking" tokens to every reply, and code checking is billed for sandbox time.
  Expect noticeably more per message than a basic chatbot, especially for hard problems and
  long interviews. Check current prices on Anthropic's pricing page and watch your usage
  in the console for the first few days.
- Ways to cut costs (at the top of `app.py`, or in your secrets):
  - `EFFORT = "medium"`: less thinking per reply.
  - `MODEL = "claude-sonnet-5-5"` in secrets: cheaper, still strong, but weaker on the hardest maths.
  - `CODE_CHECKING = false` in secrets: turns off code checking.
- Built-in limits (change them at the top of `app.py`):
  - `MAX_MESSAGES_PER_SESSION = 60`: messages per chat before the visitor must press "New chat"
  - `MAX_OUTPUT_TOKENS = 32000`: the most the model can think and write per reply
  - `HISTORY_MESSAGES = 40`: how much of the conversation is sent with each message
  These are speed bumps, not hard limits (someone can refresh and start again).
  **Your spending limit in the Anthropic console is the real protection.**
- Everyone shares one password. If it leaks, change `APP_PASSWORD` in the Streamlit
  secrets and the old one stops working.
- Don't put anything in `knowledge/` you wouldn't want visitors to read.
- **Saved conversations are personal data.** The login screen tells visitors their chats are
  saved and asks them not to share personal information. If you share the bot with under-18s
  or more widely, you may have duties under UK data protection law (UK GDPR), such as having a
  privacy notice and deleting data when asked. The review page can delete conversations.
  Check the ICO's guidance (ico.org.uk) if in doubt. Set `LOG_CONVERSATIONS = false` in your
  secrets to stop saving entirely.

## Limits of "no hallucination"

The instructions, extended thinking and code checking make mistakes much rarer, and the bot is
told to flag anything unverified with ⚠️ and to ask for the text of any specific past paper
question instead of recalling it from memory. No AI can guarantee it is never wrong, though,
so treat answers to hard problems like a strong student's work: check the reasoning.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No APP_PASSWORD has been set" | Add it to `secrets.toml` (local) or the Secrets box (online) |
| "The API key was rejected" | Check the key was copied fully, and that your Anthropic account has credit |
| An error mentioning the model | Your account may not have access to that model yet. Set `MODEL = "claude-sonnet-5-5"` in secrets |
| Code checking never appears | If code checking isn't available to your account or model, the app switches it off automatically and carries on |
| Review page says "Can't reach the database" | Check `DATABASE_URL` was pasted in full, in quotes. The chat keeps working without logging until it's fixed |
| Review page talks about a "local file" online | `DATABASE_URL` is missing from your Streamlit secrets |
| Bot ignores a PDF | It may be a scanned image with no real text. Convert it to text first |
| Online app didn't update | Open the app menu (⋮) on Streamlit and choose "Reboot app" |
