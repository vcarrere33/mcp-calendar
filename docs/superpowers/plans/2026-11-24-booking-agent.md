# Booking Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Mode for this project:** Claude implements each task (test first) and commits it; Vincent reviews the task's diff and test output before the next task starts. Steps marked **[Vincent]** need their Google accounts or a browser: Claude prepares everything around them, then waits.

**Goal:** A PydanticAI command-line chat that signs in to the meeting-rooms MCP server with Google (MCP OAuth) and books rooms from natural language.

**Architecture:** The agent is a pure MCP client in its own package (`booking_agent`), which never imports `meeting_rooms`. It connects over streamable HTTP with `pydantic_ai.mcp.MCPToolset`, using FastMCP's `OAuth` helper with a file token store so the browser sign-in happens once. The first task is a time-boxed spike that decides whether agent-side OAuth works; if it does not, Task 3 adds a static access-key fallback resolved by the server's same thin layer.

**Tech Stack:** Python 3.13, uv, pydantic-ai-slim 2.74 (`[mcp,openai]`, the `openai` extra is what pydantic-ai's Ollama model uses), local Ollama, FastMCP 4.2 client auth (`fastmcp.client.auth.OAuth`), `key_value.aio.stores.filetree.FileTreeStore`.

**Spec:** `docs/superpowers/specs/2026-11-24-meeting-rooms-mcp-design.md`

**Order of work across the two plans:** Task 1 here (first hour) → server plan, Tasks 1–10 → Tasks 2–4 here.

## Global Constraints

- Same repo and same `pyproject.toml` as the server; separate package `src/booking_agent/`, which must not import `meeting_rooms`.
- Default model `ollama:qwen2.5:7b` (local Ollama, tool calling), overridable with `AGENT_MODEL`; `OLLAMA_BASE_URL=http://localhost:11434/v1` in `.env`. No API key.
- Expect ~25 s per agent turn with the local model; test timeouts and the manual demo must allow for it. Offline tests use `TestModel` / `FunctionModel`, never Ollama.
- MCP server URL default `http://localhost:8000/mcp`, overridable with `MCP_SERVER_URL`.
- OAuth tokens cached in `.tokens/agent-oauth/` (gitignored by the server plan's `.tokens/` rule).
- Default timezone `Europe/Paris`; the agent must tell the model today's date and timezone, or "tomorrow at 2pm" cannot be resolved.
- Agent-side OAuth is nice-to-have; the agent itself is essential. Time-box the spike to one hour.

## Review Focus

- Relative dates ("tomorrow", "next Monday") resolve against today in Paris, not the model's training date → Task 2 `test_instructions_contain_today_and_timezone`.
- A refused tool call (room taken, not your booking) reaches the model as text it can explain, instead of crashing the chat → Task 2 `test_tool_error_is_shown_to_model` with `tool_error_behavior="failed"`.
- An expired or revoked cached token (Google testing mode: 7 days) triggers a new browser sign-in rather than a stack trace → Task 2 manual step 6 (delete `.tokens/agent-oauth/`, run again).
- Ctrl-D / Ctrl-C / `exit` end the chat cleanly → Task 2 `test_repl_exits_on_eof_and_exit_word`.
- Server not running → one clear line ("cannot reach the meeting-rooms server at …"), not a traceback → Task 2 manual step 7.

---

## File Structure

```
spikes/whoami_server.py   throwaway: FastMCP + GoogleProvider + one whoami tool
spikes/whoami_agent.py    throwaway: PydanticAI agent calling whoami with OAuth
src/booking_agent/
  __init__.py
  settings.py   AgentSettings (env)
  agent.py      build_toolset(), build_agent(), instructions()
  chat.py       run_repl() and main() entry point
tests/test_agent.py
```

---

### Task 1: OAuth spike (first hour, before the server plan)

Answers one question: can a PydanticAI agent complete the MCP OAuth flow against a FastMCP server using Google sign-in?

**Files:**
- Create: `spikes/whoami_server.py`, `spikes/whoami_agent.py` (and Task 1 of the server plan's `uv init` + `.gitignore` if not done yet: `.env`, `client_secret*.json`, `.tokens/` must be ignored before anything else)

**Interfaces:**
- Produces: a decision, written as two lines at the bottom of this task: `OAuth: works | fails`, `Mechanism: auth=OAuth(...) | auth="oauth" | static key fallback`.

- [ ] **Step 1 [Vincent]: Google Cloud console** — new project; OAuth consent screen "External", testing mode, scopes `openid` + `email`; add both Gmail accounts as test users; create an OAuth client of type **Web application** with redirect URI `http://localhost:8000/auth/callback`. Put its id/secret in `.env` as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
- [x] **Step 2: Dependencies** — `uv add fastmcp "pydantic-ai-slim[mcp,openai]" python-dotenv` (switched from `anthropic` to `openai` on 2026-11-25 for Ollama).
- [x] **Step 3: `whoami_server.py`** — `FastMCP("whoami", auth=GoogleProvider(client_id=..., client_secret=..., base_url="http://localhost:8000", required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"]))`, one tool `whoami() -> str` returning `get_access_token().claims.get("email")`; `run(transport="http", port=8000)`.
- [x] **Step 4: `whoami_agent.py`** — `MCPToolset("http://localhost:8000/mcp", auth="oauth")`, an `Agent("ollama:qwen2.5:7b", toolsets=[toolset])`, `await agent.run("Call whoami and tell me my email.")`, print the output.
- [ ] **Step 5 [Vincent]: Run it** — server in one terminal, agent in another. Expected: browser opens, Google sign-in, FastMCP consent page, then the agent prints your email. **Pass = the email printed comes from Google.**
- [ ] **Step 6 [Vincent]: Token caching** — replace `auth="oauth"` with `auth=OAuth(mcp_url=URL, token_storage=FileTreeStore(data_directory=".tokens/agent-oauth"))` (`from fastmcp.client.auth import OAuth`). Run twice: the second run must not open the browser.
- [ ] **Step 7: Decide and record** — if steps 5–6 pass: Task 3 is skipped. If OAuth fails after ~1h (try passing an `httpx2.AsyncClient(auth=OAuth(...))` as `http_client=` before giving up): record why and do Task 3 after the server plan.
- [ ] **Step 8: Commit** — `git add spikes .gitignore pyproject.toml uv.lock && git commit -m "spike: PydanticAI agent signs in to FastMCP with Google OAuth"`

Decision (2026-11-24, provisional until steps 5–6 pass with real Google credentials):
- Checked offline, Google replaced by FastMCP's `InMemoryOAuthProvider`: a PydanticAI agent with `MCPToolset(url, auth="oauth")` completes the full MCP OAuth flow (dynamic client registration, PKCE, local callback, tool call as the signed-in client). With `auth=OAuth(..., token_storage=FileTreeStore(...))` a second run reuses the cached token and opens no browser.
- `whoami_server.py` with dummy Google credentials: unauthenticated `/mcp` → 401, OAuth metadata served, client registration works, `/authorize` → FastMCP consent page (next hop would be Google).
- 2026-11-25: same offline check with the real local model `ollama:qwen2.5:7b` instead of `TestModel`: it calls `whoami` after sign-in and answers correctly (~25 s).
- `OAuth: works (offline)`, `Mechanism: auth=OAuth(...) with a FileTreeStore` → Task 3 likely skipped.
- Gotchas for Task 2: `MCPToolset` defaults to `init_timeout=5` s, and the browser sign-in happens inside that handshake, so a human never has time to sign in ("Failed to initialize server session"): pass `init_timeout=300` (FastMCP's callback timeout) and add a test `test_toolset_allows_time_to_sign_in` asserting it; `FileTreeStore` needs `FileTreeV1KeySanitizationStrategy` / `FileTreeV1CollectionSanitizationStrategy` (keys contain the server URL) and its folder must exist before the strategies are built; on Vincent's machine ports 8000 and 8001 are taken by another local app, so the port is configurable (`MCP_PORT`, `MCP_SERVER_URL`) and the Google redirect URI must use the same port.

---

### Task 2: The booking agent CLI

**Files:**
- Create: `src/booking_agent/__init__.py`, `settings.py`, `agent.py`, `chat.py`
- Modify: `pyproject.toml` (`[project.scripts]` `booking-agent = "booking_agent.chat:main"`; under `[tool.hatch.build]` or equivalent, make sure both packages in `src/` are included)
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: the running server (server plan Task 9); the spike's auth mechanism (Task 1).
- Produces:
  - `class AgentSettings(BaseSettings)`: `mcp_server_url: str = "http://localhost:8000/mcp"`, `agent_model: str = "ollama:qwen2.5:7b"`, `token_dir: Path = Path(".tokens/agent-oauth")`, `access_key: str | None = None` (used only by Task 3).
  - `instructions(today: date) -> str` — role ("you book meeting rooms for the signed-in user"), today's date with weekday, timezone `Europe/Paris`, "always pass ISO 8601 times", "confirm the room, time and title back to the user after booking", "ask before cancelling".
  - `build_toolset(settings: AgentSettings) -> MCPToolset` — the mechanism recorded in Task 1, with `tool_error_behavior="failed"` so refusals reach the model as text.
  - `build_agent(model: str | Model, toolsets: list) -> Agent` — instructions passed as a function so the date is computed per run.
  - `run_repl(agent: Agent, read_line: Callable[[str], str] = input, write: Callable[[str], None] = print) -> None` (async) — keeps `message_history` across turns; stops on `EOFError`, `KeyboardInterrupt`, or input `exit`/`quit`.
  - `main()` — loads settings and `.env`, builds everything, runs the REPL inside `async with agent:`; connection errors print one line naming the URL and exit with code 1.

- [ ] **Step 1: Write the failing tests** (offline: `pydantic_ai.models.test.TestModel` / `FunctionModel`, and a local `FunctionToolset` that stands in for the MCP server)

```python
def test_instructions_contain_today_and_timezone():
    text = instructions(date(2026, 12, 3))
    assert "2026-12-03" in text and "Thursday" in text and "Europe/Paris" in text

async def test_agent_calls_tools_and_replies(): ...
    # TestModel + FunctionToolset with a fake book_room → result.output is non-empty and the fake was called

async def test_tool_error_is_shown_to_model(): ...
    # fake book_room raises ModelRetry("Room Atlas is already booked") →
    # FunctionModel sees that text in the tool-return part of the next request

async def test_repl_exits_on_eof_and_exit_word(): ...
    # read_line yields "hello", then "exit" → one agent turn, returns cleanly;
    # read_line raising EOFError immediately → returns cleanly
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_agent.py -v`
- [ ] **Step 3: Implement `settings.py`, `agent.py`, `chat.py`**
- [ ] **Step 4: Run the whole suite** — `uv run pytest -v` → all green (server tests included).
- [ ] **Step 5 [Vincent]: Manual demo as the employee** — `uv run meeting-rooms-server`, then `uv run booking-agent`: "Find me a room for 5 people with a screen tomorrow 14:00–15:00", "Book it, title 'Sprint review'", "What have I booked?", "Cancel the booking Vincent made" → polite refusal.
- [ ] **Step 6 [Vincent]: Expired token** — delete `.tokens/agent-oauth/`, run again → browser sign-in, then works.
- [ ] **Step 7: Server down** — stop the server, run the agent → one clear line, exit code 1.
- [ ] **Step 8: Commit** — `feat: PydanticAI booking agent CLI`

---

### Task 3 (only if the spike failed): static access-key fallback

**Files:**
- Modify: `src/meeting_rooms/server.py` (`main()`), `src/meeting_rooms/config.py`, `src/booking_agent/agent.py`, `.env.example`, `users.toml.example`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `build_server(..., auth=...)` and `email_from_oauth_token` (server plan Tasks 4, 7).
- Produces:
  - `load_access_keys(path: Path) -> dict[str, str]` — `[access_keys]` table in `users.toml`, `"<key>" = "<email>"`; the email must also be in `[users]`, else `ValueError`.
  - Server auth becomes `MultiAuth(server=GoogleProvider(...), verifiers=[StaticTokenVerifier(tokens={key: {"client_id": "booking-agent", "scopes": [], "email": email}})])`. **Check first** that the extra `email` key lands in `AccessToken.claims`; if not, map key → email inside `email_from_oauth_token` instead. Either way the same `resolve_caller` produces the `Caller`.
  - Agent: `build_toolset` uses `auth=settings.access_key` (a bearer string) when set.

- [ ] **Step 1: Failing tests** — `test_load_access_keys_rejects_unknown_email`; `test_access_key_caller_gets_role_from_users_toml` (gateway with an `email_source` fed by a key lookup → employee cannot cancel admin's booking).
- [ ] **Step 2: Run to fail**, **Step 3: implement**, **Step 4: run the suite to pass**.
- [ ] **Step 5 [Vincent]: Manual demo** — agent with `ACCESS_KEY` set books and lists; Claude Code still signs in with Google.
- [ ] **Step 6: Commit** — `feat: static access key for the agent (OAuth fallback)`

---

### Task 4: Two-account demo and README section

**Files:**
- Modify: `README.md`

- [ ] **Step 1 [Vincent]: Full demo with both roles** — employee account (second Gmail) books Atlas; admin account (Vincent) lists everyone's bookings and cancels it; employee tries to cancel an admin booking and is refused. Note any tool description that confused the model and tighten its docstring in `tools.py` (commit separately: `fix: clearer tool descriptions`).
- [ ] **Step 2: README "Agent demo" section** — how to set up Ollama (`ollama pull qwen2.5:7b`, `OLLAMA_BASE_URL`) and how to switch `AGENT_MODEL` to a hosted model, run server + agent, a short transcript of the refusal case (that transcript is the portfolio's point), and, if Task 3 ran, an honest "Known limitation: the agent uses a static access key; OAuth for the agent is the next step."
- [ ] **Step 3: Final check** — `uv run pytest -v` green; `git status` clean; `git grep` shows no real email, key or secret.
- [ ] **Step 4: Commit** — `docs: agent demo`; then **[Vincent]** switches `gh` to `vcarrere33`, creates the public repo and pushes.
