# Meeting Rooms MCP: design

Date: 2026-11-24 · Owner: Vincent (GitHub: vcarrere33)

## Intent

A small, public, portfolio-grade **MCP server for booking meeting rooms**, backed by real Google Calendar data, used by a PydanticAI agent. It shows hands-on work with MCP, Python and tests, and above all **an agent tool boundary that knows who is calling and what they may do**: every tool call should go through identity, permissions, confirmations and audit.

The data is deliberately mundane; the point is the boundary between agent and system.

## Goals

- MCP server exposing room-booking tools over Google Calendar.
- Real per-user identity via OAuth sign-in (users log in with their Google account).
- A thin layer that every tool call passes through, holding the caller's identity. In week 2
  it only resolves identity and enforces "you can only cancel your own bookings unless admin".
  In week 3 it gains per-tool permissions by role, confirmations for sensitive actions, and an audit log.
- A PydanticAI agent that connects to the server (with OAuth) and books rooms from natural language.
- pytest tests for every tool, running offline against an in-memory fake calendar backend.
- A clear README: what it is, why the boundary matters, Google setup steps, how to run the demo.

## Non-goals

- Production deployment or hosting (runs on localhost).
- Outlook / Microsoft Graph (possible later, behind the same backend interface).
- Google Workspace "resource" rooms (needs a paid Workspace; we simulate rooms with calendars).
- Recurring meetings, invitations to attendees, UI.
- Running the server against the fake backend outside tests (fake backend is test-only).

## Anti-goals (failure even if it works)

- Secrets committed (OAuth client secrets, tokens). `.env` and token files are gitignored.
- An agent able to cancel someone else's booking without the admin role.
- Tool logic that bypasses the thin layer (identity must be impossible to forget).

## Constraints

- ~10 hours of work for the week 2 version (Nov 28 → Dec 4).
- Free Gmail account(s) only. Google Cloud OAuth app stays in "testing" mode: test users must be
  added by email, and refresh tokens expire after 7 days (acceptable for a demo).
- Demoing two roles needs two Google accounts (e.g. Vincent's Gmail as admin, a second Gmail as employee).
- Repo published under GitHub user **vcarrere33** (the local `gh` is logged into another account; switch first).
- One repo, two packages: `meeting_rooms` (the server) and `booking_agent` (the agent). The agent never
  imports the server; it talks to it only over MCP, like any other client.

## How we work

- **Claude writes the code**, one plan task at a time, test first, and commits each task.
- **Vincent reviews each task** (diff + test output) before the next one starts. Review comments are
  fixed in the same task.
- **Vincent does the steps that need their accounts or a browser**: Google Cloud console, Google sign-ins,
  the second Gmail, manual demos, switching `gh` and pushing. These steps are marked **[Vincent]** in the plans.
- Plans: `docs/superpowers/plans/2026-11-24-meeting-rooms-server.md` and
  `docs/superpowers/plans/2026-11-24-booking-agent.md`.

## Approaches considered (how the server learns who is calling)

1. **Local process (stdio), user set in config.** Simplest; but anyone can claim any identity, so the
   identity layer has nothing real to check. Rejected.
2. **HTTP server, static access key per user** mapped to a person and role in config. Realistic gateway
   shape, ~1h more than (1). Not chosen.
3. **HTTP server with full OAuth sign-in per user (chosen).** Matches the MCP authorization spec and shows
   real identity and credentials handling. Cheaper than first estimated: FastMCP ships an
   OAuth proxy with a Google provider, so users sign in with Google and the server receives a verified
   email. Estimated +3–5h over option 2, mostly Google console setup and client-side OAuth.

Chosen by Vincent: **option 3**, with Google Calendar as the only real backend, plus an in-memory fake
backend used only by tests.

## Design

### Two separate Google logins

| Purpose | Who logs in | Scope | How |
|---|---|---|---|
| **User identity** (who is calling the MCP server) | each user, via the MCP OAuth flow | `openid email` | FastMCP Google OAuth provider |
| **Calendar access** (reading/writing room calendars) | the owner of the room calendars (Vincent), once | Google Calendar read/write | one-time local login script, token stored in a gitignored file |

Users never get calendar rights themselves; the server acts on the room calendars on their behalf,
and the thin layer decides what each user may do. That split is the gateway pattern the project is meant to show.

### Rooms

- One secondary Google calendar per room in the owner's Gmail ("Room Atlas", "Room Bora", ...).
- `rooms.toml` lists rooms: id, display name, Google calendar id, capacity, equipment (e.g. screen, whiteboard).
- Availability uses Google's free/busy query on the room calendars.

### Bookings

- A booking is an event on the room's calendar.
- The booker's verified email is stored in the event's private extended properties (`booked_by`),
  and also in the event description for human readability.
- Times are ISO 8601; default timezone Europe/Paris.

### Tools (week 2)

| Tool | Does | Kind |
|---|---|---|
| `search_rooms(start, end, min_capacity=None, equipment=None)` | rooms free for the whole slot that match the filters | read |
| `list_my_bookings(from=None, to=None)` | the caller's upcoming bookings (admins: optional `all=True`) | read |
| `book_room(room_id, start, end, title)` | creates the booking if the room is still free; fails cleanly on conflict | write |
| `cancel_booking(booking_id)` | cancels it if the caller booked it or is admin | write, sensitive |

Errors are returned as clear tool errors the agent can explain (room taken, not your booking, unknown room, end before start).

### The thin layer

- One place resolves the caller per request: verified email from the OAuth token, role from
  `users.toml` (email → `employee` | `admin`). Unknown emails are rejected even if Google sign-in succeeded.
- Tool implementations receive a `Caller(email, role)` object and the backend; they cannot run without one.
- Week 2: identity resolution + the cancel ownership rule.
- Week 3 (designed for, not built now): a per-tool permission table by role, confirmation step for
  sensitive tools, append-only audit log of every call (who, tool, arguments, outcome, time).
- *Claude's call:* week 2 tool signatures stay as listed; the confirmation mechanism (MCP elicitation
  vs. a confirm-token argument) is decided in week 3, once client support is known. The layer just
  needs a hook point before sensitive tools run.

### Calendar backend

- An interface (`CalendarBackend`) with the operations the tools need: free/busy for rooms, create,
  list and delete events with the `booked_by` property.
- `GoogleCalendarBackend`: the real one.
- `InMemoryBackend`: test-only fake, same interface.

### Agent

- A small PydanticAI command-line chat that connects to the server over streamable HTTP and performs
  the MCP OAuth flow (browser opens for Google sign-in, token cached locally).
- Default model: Claude Sonnet 5.5 via an Anthropic API key (any PydanticAI model works; set in `.env`).
- The server should also work from any MCP client supporting OAuth (e.g. Claude Code), which the README will show.

### Tests

- pytest, offline: every tool against `InMemoryBackend` with fake callers (employee, admin), covering
  the happy path and the key refusals (conflict, cancelling someone else's booking, unknown user).
- No test hits Google. The Google backend is checked manually via the demo.

### Stack

Python with uv, FastMCP, pydantic-ai, google-api-python-client + google-auth-oauthlib, pytest.
Python 3.13. Versions checked on 2026-11-24: FastMCP 4.2, pydantic-ai 2.73 (which supports MCP OAuth
natively via `MCPToolset(url, auth=...)`), MCP SDK 2.3. Local machine has Python 3.14.

## Left to the builder (Claude)

- Package layout, module names, config file formats in detail.
- Exact FastMCP auth wiring and how the PydanticAI client does OAuth. **Verify early** that the
  PydanticAI MCP client can use the MCP SDK's OAuth client; if it can't, fall back to the FastMCP client
  for the OAuth handshake and pass the token to PydanticAI.
- Room seed script (creating the room calendars) vs. manual setup documented in the README.
- Wording of tool descriptions (they matter for agent behaviour; keep them precise).

## Priorities if time runs short (decided by Vincent)

The PydanticAI agent is essential to the week 2 version; agent-side OAuth is not.

1. **First hour:** prove the PydanticAI agent can complete the MCP OAuth flow against a minimal FastMCP
   server with Google sign-in (try the MCP SDK OAuth client, then the FastMCP-client fallback).
2. **If that fails or eats the budget:** the agent authenticates with a static access key mapped to a
   user and role in config (option 2's mechanism), resolved by the same thin layer into the same `Caller`.
   Human users and OAuth-capable clients (e.g. Claude Code) still go through Google sign-in.
   The README states this honestly as a known limitation and next step.
3. Then: thin layer, backend interface, tools, tests, README.

*Claude's calls (minor, from the builder review):* booking ids are the Google event ids; a conflict check
runs right before creating an event (free/busy re-query) to limit double bookings; `users.toml.example`
and `rooms.toml.example` are committed while the real files are gitignored.

## Risks

- OAuth client support in PydanticAI is the main unknown; check it in the first hour.
- Google testing-mode token expiry every 7 days.
- Scope creep into week 3 features; keep the week 2 cut strict.
