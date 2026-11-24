# Meeting Rooms MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Mode for this project:** Claude implements each task (test first) and commits it; Vincent reviews the task's diff and test output before the next task starts. Steps marked **[Vincent]** need their Google accounts or a browser: Claude prepares everything around them, then waits.

**Goal:** An HTTP MCP server with Google sign-in that books meeting rooms (simulated as Google calendars), where every tool call goes through one thin layer that knows who is calling.

**Architecture:** Pure tool logic (`tools.py`) takes a `ToolContext(caller, backend, rooms)` and cannot run without one. A `Gateway` is the only way to register tools on the FastMCP server: it resolves the caller from the OAuth token, builds the context, runs a hook before sensitive tools, and turns domain errors into MCP tool errors. Storage sits behind a `CalendarBackend` protocol with a Google implementation and an in-memory test fake.

**Tech Stack:** Python 3.13, uv, FastMCP 4.2 (`GoogleProvider`), pydantic 2 + pydantic-settings, google-api-python-client + google-auth-oauthlib, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-24-meeting-rooms-mcp-design.md`

**Order of work across the two plans:** Agent plan Task 1 (OAuth spike, first hour) → this plan, Tasks 1–10 → agent plan, Tasks 2–4.

## Global Constraints

- Never commit secrets: `.env`, `rooms.toml`, `users.toml`, `client_secret*.json`, `.tokens/` are gitignored from Task 1. Commit only `*.example` files.
- Default timezone `Europe/Paris`; times are ISO 8601. A naive datetime means Paris time.
- Roles are exactly `employee` and `admin`. Users come from `users.toml`; an email not listed is rejected even after a successful Google sign-in.
- Identity scope for users: `openid email`. Calendar scope (owner only, once): `https://www.googleapis.com/auth/calendar`.
- Booking id = the Google event id. The booker's email goes in the event's private extended property `booked_by` and in the description.
- No test touches the network or Google. The in-memory backend is used only by tests.
- Week 2 cut only: no permission table, no confirmations, no audit log. Leave a `before_sensitive` hook and stop there.
- Deviation from the spec's tool table, on purpose: `list_my_bookings(from, to, all)` becomes `list_my_bookings(start, end, all_users)` because `from` is a Python keyword and `all` shadows a builtin. Same meaning.
- Public repo under GitHub user `vcarrere33` (switch `gh auth` before the first push).

## Review Focus

- Naive datetimes from the agent ("2026-12-01T14:00") are Paris time, and compare correctly with aware ones → Task 1 `test_as_paris_*`, Task 5 `test_book_room_naive_times_are_paris`.
- Back-to-back bookings (one ends at 10:00, the next starts at 10:00) are not a conflict: slots are half-open `[start, end)` → Task 3 `test_adjacent_events_do_not_overlap`, Task 5 `test_book_room_back_to_back_ok`.
- Email case: Google may return `Vincent@Gmail.com` while `users.toml` says `vincent@gmail.com`; both are the same user → Task 2 `test_load_users_lowercases_emails`, Task 4 `test_resolve_caller_is_case_insensitive`.
- Events someone created by hand in a room calendar (no `booked_by`) still block the room, and only an admin may cancel them → Task 3 `test_foreign_event_makes_room_busy`, Task 6 `test_employee_cannot_cancel_foreign_event`.
- A zero-length slot (`start == end`) is refused like `end < start` → Task 1 `test_validate_slot_rejects_empty_slot`.

---

## File Structure

```
pyproject.toml                      deps, scripts, pytest config
.gitignore  .env.example  rooms.toml.example  users.toml.example
src/meeting_rooms/
  models.py         Room, Booking, Role, Caller, errors, as_paris(), validate_slot()
  config.py         Settings (env), load_rooms(), load_users()
  backend/
    base.py         CalendarBackend protocol
    memory.py       InMemoryBackend (tests only)
    google.py       GoogleCalendarBackend + pure event<->Booking mapping
  identity.py       resolve_caller(), email_from_oauth_token()
  tools.py          ToolContext + the four tool functions (pure logic)
  gateway.py        Gateway: the only way to register a tool
  server.py         build_server() for tests/real use, main() entry point
  calendar_login.py one-time owner login → .tokens/calendar.json
  seed_rooms.py     creates the room calendars, prints rooms.toml
tests/
  conftest.py  test_models.py  test_config.py  test_memory_backend.py
  test_identity.py  test_tools_booking.py  test_tools_listing.py
  test_server.py  test_google_mapping.py
```

---

### Task 1: Project scaffold and domain models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/meeting_rooms/__init__.py`, `src/meeting_rooms/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `PARIS: ZoneInfo` (`ZoneInfo("Europe/Paris")`)
  - `class Role(StrEnum)`: `EMPLOYEE = "employee"`, `ADMIN = "admin"`
  - `@dataclass(frozen=True) class Caller`: `email: str`, `role: Role`; property `is_admin: bool`
  - `class Room(BaseModel)`: `id: str`, `name: str`, `calendar_id: str`, `capacity: int`, `equipment: list[str] = []`
  - `class Booking(BaseModel)`: `id: str`, `room_id: str`, `title: str`, `start: datetime`, `end: datetime`, `booked_by: str | None`
  - Errors: `class BookingError(Exception)` and subclasses `InvalidSlot`, `UnknownRoom`, `RoomTaken`, `UnknownBooking`, `NotYourBooking`, `NotAuthenticated`, `UnknownUser`. The message of each is what the agent will read, so make it a full sentence.
  - `as_paris(dt: datetime) -> datetime` — naive → attach `PARIS`; aware → unchanged.
  - `validate_slot(start: datetime, end: datetime) -> tuple[datetime, datetime]` — returns both through `as_paris`; raises `InvalidSlot` when `end <= start`.

- [ ] **Step 1: Scaffold the project**

`uv init --package --name meeting-rooms --python 3.13`, then `uv add fastmcp pydantic-settings google-api-python-client google-auth-oauthlib` and `uv add --dev pytest pytest-asyncio`. In `pyproject.toml` set `[tool.pytest.ini_options] asyncio_mode = "auto"`, `testpaths = ["tests"]`. `.gitignore`: `.venv/`, `__pycache__/`, `.env`, `rooms.toml`, `users.toml`, `client_secret*.json`, `.tokens/`. `.env.example` lists the variables from Task 2 with placeholder values.

- [ ] **Step 2: Write the failing tests**

```python
def test_as_paris_attaches_paris_to_naive():
    assert as_paris(datetime(2026, 12, 1, 14, 0)).tzinfo == PARIS

def test_as_paris_keeps_aware_datetimes():
    utc = datetime(2026, 12, 1, 13, 0, tzinfo=UTC)
    assert as_paris(utc) == utc  # same instant

def test_validate_slot_rejects_end_before_start():
    with pytest.raises(InvalidSlot):
        validate_slot(datetime(2026, 12, 1, 15), datetime(2026, 12, 1, 14))

def test_validate_slot_rejects_empty_slot():
    with pytest.raises(InvalidSlot):
        validate_slot(datetime(2026, 12, 1, 14), datetime(2026, 12, 1, 14))

def test_caller_is_admin():
    assert Caller("a@x.com", Role.ADMIN).is_admin
    assert not Caller("e@x.com", Role.EMPLOYEE).is_admin
```

- [ ] **Step 3: Run them to see them fail** — `uv run pytest tests/test_models.py -v` → FAIL (import errors).
- [ ] **Step 4: Implement `models.py`** with the interfaces above.
- [ ] **Step 5: Run them to see them pass** — same command → 5 passed.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: project scaffold and domain models"` (check `git status` first: no secrets).

---

### Task 2: Configuration loading

**Files:**
- Create: `src/meeting_rooms/config.py`, `rooms.toml.example`, `users.toml.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `Room`, `Role` (Task 1)
- Produces:
  - `load_rooms(path: Path) -> dict[str, Room]` — keyed by room id; reads `[[rooms]]` tables with `tomllib`.
  - `load_users(path: Path) -> dict[str, Role]` — reads a `[users]` table `"email" = "role"`; keys lowercased; an unknown role string raises `ValueError`.
  - `class Settings(BaseSettings)` (env file `.env`): `google_client_id: str`, `google_client_secret: str`, `base_url: str = "http://localhost:8000"`, `rooms_file: Path = Path("rooms.toml")`, `users_file: Path = Path("users.toml")`, `calendar_token_file: Path = Path(".tokens/calendar.json")`. Load with `Settings()` only in `main()` (Task 9), never at import time, so tests need no `.env`.

`rooms.toml.example`:

```toml
[[rooms]]
id = "atlas"
name = "Room Atlas"
calendar_id = "REPLACE_ME@group.calendar.google.com"
capacity = 6
equipment = ["screen", "whiteboard"]
```

`users.toml.example`:

```toml
[users]
"admin@example.com" = "admin"
"employee@example.com" = "employee"
```

- [ ] **Step 1: Write the failing tests** (write toml into `tmp_path`)

```python
def test_load_rooms_reads_all_fields(tmp_path): ...
    # rooms["atlas"] == Room(id="atlas", name="Room Atlas", calendar_id="c1", capacity=6, equipment=["screen"])

def test_load_rooms_example_file_is_valid():
    assert "atlas" in load_rooms(Path("rooms.toml.example"))

def test_load_users_lowercases_emails(tmp_path): ...
    # '"Vincent@Gmail.com" = "admin"' → {"vincent@gmail.com": Role.ADMIN}

def test_load_users_rejects_unknown_role(tmp_path): ...
    # '"a@x.com" = "superuser"' → pytest.raises(ValueError)
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_config.py -v`
- [ ] **Step 3: Implement `config.py` and the two example files**
- [ ] **Step 4: Run to pass** — 4 passed.
- [ ] **Step 5: Commit** — `feat: load rooms, users and settings`

---

### Task 3: Calendar backend protocol and in-memory fake

**Files:**
- Create: `src/meeting_rooms/backend/__init__.py`, `backend/base.py`, `backend/memory.py`
- Test: `tests/test_memory_backend.py`

**Interfaces:**
- Consumes: `Room`, `Booking` (Task 1)
- Produces `class CalendarBackend(Protocol)` (all times aware; intervals are half-open `[start, end)`):
  - `busy_room_ids(rooms: list[Room], start: datetime, end: datetime) -> set[str]` — rooms with any event overlapping the slot.
  - `create_booking(room: Room, start: datetime, end: datetime, title: str, booked_by: str) -> Booking`
  - `list_bookings(rooms: list[Room], start: datetime, end: datetime | None, booked_by: str | None) -> list[Booking]` — sorted by start; `booked_by=None` means everyone's; `end=None` means no upper bound.
  - `get_booking(rooms: list[Room], booking_id: str) -> Booking | None`
  - `delete_booking(booking: Booking) -> None`
- Produces `class InMemoryBackend` implementing it, plus a test helper `add_foreign_event(room_id: str, start: datetime, end: datetime, title: str = "Manual event") -> Booking` (an event with `booked_by=None`, like one created by hand in Google Calendar). Ids: `uuid4().hex`.

- [ ] **Step 1: Write the failing tests** (a module-level `ATLAS = Room(...)` and `BORA = Room(...)`; times as aware Paris datetimes)

```python
def test_created_booking_makes_room_busy(): ...
    # create 10:00–11:00 in atlas → busy_room_ids([ATLAS, BORA], 10:30, 10:45) == {"atlas"}

def test_adjacent_events_do_not_overlap(): ...
    # booking 10:00–11:00 → busy_room_ids([ATLAS], 11:00, 12:00) == set()

def test_foreign_event_makes_room_busy(): ...
    # add_foreign_event("atlas", 10:00, 11:00) → "atlas" busy at 10:00–10:30

def test_list_bookings_filters_by_booker_and_sorts(): ...
    # two bookings by a@x, one by b@x → booked_by="a@x" returns a's two, earliest first

def test_get_and_delete_booking(): ...
    # get_booking(rooms, b.id) == b; after delete_booking(b), get_booking(...) is None

def test_get_booking_unknown_id_returns_none(): ...
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_memory_backend.py -v`
- [ ] **Step 3: Implement `base.py` (Protocol) and `memory.py`** (one list of `Booking`; overlap test is `a.start < b.end and b.start < a.end`)
- [ ] **Step 4: Run to pass** — 6 passed.
- [ ] **Step 5: Commit** — `feat: calendar backend protocol and in-memory fake`

---

### Task 4: Identity resolution (the thin layer, part 1)

**Files:**
- Create: `src/meeting_rooms/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: `Caller`, `Role`, `NotAuthenticated`, `UnknownUser` (Task 1)
- Produces:
  - `resolve_caller(email: str | None, users: Mapping[str, Role]) -> Caller` — `None`/empty → `NotAuthenticated`; email (lowercased) not in `users` → `UnknownUser`; returned `Caller.email` is lowercased.
  - `EmailSource = Callable[[], str | None]`
  - `email_from_oauth_token() -> str | None` — `fastmcp.server.dependencies.get_access_token()`; return `token.claims.get("email")` or `None` if no token. Not unit-tested (needs a live request); covered by the manual check in Task 9.

- [ ] **Step 1: Write the failing tests**

```python
USERS = {"boss@x.com": Role.ADMIN, "emp@x.com": Role.EMPLOYEE}

def test_resolve_caller_known_user():
    assert resolve_caller("emp@x.com", USERS) == Caller("emp@x.com", Role.EMPLOYEE)

def test_resolve_caller_is_case_insensitive():
    assert resolve_caller("Boss@X.com", USERS) == Caller("boss@x.com", Role.ADMIN)

def test_resolve_caller_unknown_user_rejected():
    with pytest.raises(UnknownUser):
        resolve_caller("stranger@gmail.com", USERS)

@pytest.mark.parametrize("email", [None, ""])
def test_resolve_caller_without_email_rejected(email):
    with pytest.raises(NotAuthenticated):
        resolve_caller(email, USERS)
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_identity.py -v`
- [ ] **Step 3: Implement `identity.py`**
- [ ] **Step 4: Run to pass** — 5 passed.
- [ ] **Step 5: Commit** — `feat: resolve caller identity and role`

---

### Task 5: Tools — `search_rooms` and `book_room`

**Files:**
- Create: `src/meeting_rooms/tools.py`, `tests/conftest.py`
- Test: `tests/test_tools_booking.py`

**Interfaces:**
- Consumes: Tasks 1, 3.
- Produces:
  - `@dataclass(frozen=True) class ToolContext`: `caller: Caller`, `backend: CalendarBackend`, `rooms: dict[str, Room]`, `now: Callable[[], datetime] = lambda: datetime.now(PARIS)`
  - `search_rooms(ctx: ToolContext, start: datetime, end: datetime, min_capacity: int | None = None, equipment: list[str] | None = None) -> list[Room]` — validates the slot; keeps rooms with `capacity >= min_capacity` and every requested equipment (compare lowercased); drops busy rooms; sorted by capacity then id.
  - `book_room(ctx: ToolContext, room_id: str, start: datetime, end: datetime, title: str) -> Booking` — validates the slot; unknown id → `UnknownRoom` (message lists valid ids); re-checks `busy_room_ids` right before creating (`RoomTaken` if busy); `booked_by = ctx.caller.email`.
- `conftest.py` fixtures: `rooms` (atlas cap 6 `["screen","whiteboard"]`, bora cap 4 `["whiteboard"]`, cosmos cap 12 `["screen"]`), `backend` (fresh `InMemoryBackend`), `employee`, `other_employee`, `admin` (`Caller`s), and a factory `ctx_for(caller) -> ToolContext`.

- [ ] **Step 1: Write the failing tests**

```python
def test_search_rooms_returns_free_rooms(ctx_for, employee): ...
    # nothing booked, 14:00–15:00 → ids == ["bora", "atlas", "cosmos"]

def test_search_rooms_filters_capacity_and_equipment(ctx_for, employee): ...
    # min_capacity=5, equipment=["Screen"] → ["atlas", "cosmos"]

def test_search_rooms_excludes_busy_rooms(ctx_for, employee, backend): ...
    # atlas booked 14:00–15:00 → searching 14:30–15:30 excludes "atlas"

def test_book_room_creates_booking_for_caller(ctx_for, employee): ...
    # booking.booked_by == employee.email, booking.room_id == "atlas"

def test_book_room_conflict_raises_room_taken(ctx_for, employee, other_employee): ...

def test_book_room_back_to_back_ok(ctx_for, employee): ...
    # 10:00–11:00 then 11:00–12:00 in atlas → both succeed

def test_book_room_unknown_room(ctx_for, employee): ...
    # pytest.raises(UnknownRoom, match="atlas")

def test_book_room_end_before_start(ctx_for, employee): ...  # InvalidSlot

def test_book_room_naive_times_are_paris(ctx_for, employee, backend): ...
    # book naive 14:00–15:00; an aware UTC 13:30–13:45 query (= 14:30 Paris in winter) sees atlas busy
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_tools_booking.py -v`
- [ ] **Step 3: Implement `ToolContext`, `search_rooms`, `book_room`**
- [ ] **Step 4: Run to pass** — 9 passed.
- [ ] **Step 5: Commit** — `feat: search_rooms and book_room tools`

---

### Task 6: Tools — `list_my_bookings` and `cancel_booking`

**Files:**
- Modify: `src/meeting_rooms/tools.py`
- Test: `tests/test_tools_listing.py`

**Interfaces:**
- Consumes: Task 5 `ToolContext` and fixtures.
- Produces:
  - `list_my_bookings(ctx: ToolContext, start: datetime | None = None, end: datetime | None = None, all_users: bool = False) -> list[Booking]` — `start` defaults to `ctx.now()`; `all_users=True` by a non-admin → `NotYourBooking`-style refusal (raise `NotYourBooking` with a message saying only admins can list everyone's bookings); otherwise filter on the caller's email.
  - `cancel_booking(ctx: ToolContext, booking_id: str) -> Booking` — unknown id → `UnknownBooking`; `booked_by != caller.email` and not admin → `NotYourBooking`; returns the deleted booking. A foreign event (`booked_by=None`) is "not yours" for everyone except admins.

- [ ] **Step 1: Write the failing tests**

```python
def test_list_my_bookings_only_mine(...): ...           # other_employee's booking not returned
def test_list_my_bookings_hides_past(...): ...          # ctx.now = 12:00; a 10:00–11:00 booking is not listed by default
def test_admin_lists_everyone(...): ...                 # all_users=True → both employees' bookings
def test_employee_cannot_list_everyone(...): ...        # pytest.raises(NotYourBooking)
def test_cancel_own_booking(...): ...                   # then backend.get_booking(...) is None
def test_employee_cannot_cancel_others_booking(...): ...  # NotYourBooking, booking still exists
def test_admin_can_cancel_any_booking(...): ...
def test_employee_cannot_cancel_foreign_event(...): ... # backend.add_foreign_event → NotYourBooking
def test_cancel_unknown_booking(...): ...               # UnknownBooking
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_tools_listing.py -v`
- [ ] **Step 3: Implement both functions**
- [ ] **Step 4: Run the whole suite** — `uv run pytest -v` → all green.
- [ ] **Step 5: Commit** — `feat: list_my_bookings and cancel_booking with ownership rule`

---

### Task 7: Gateway and server wiring (offline)

The heart of the project: the layer that makes identity impossible to forget.

**Files:**
- Create: `src/meeting_rooms/gateway.py`, `src/meeting_rooms/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `resolve_caller`, `EmailSource`, `email_from_oauth_token` (Task 4); `ToolContext` + four tool functions (Tasks 5–6); `BookingError` (Task 1).
- Produces:
  - `SensitiveHook = Callable[[Caller, str, dict[str, Any]], None]` — (caller, tool name, arguments). Week 2 default: a no-op. Week 3 plugs confirmation/audit here.
  - `class Gateway`: `__init__(self, mcp: FastMCP, *, backend: CalendarBackend, rooms: dict[str, Room], users: Mapping[str, Role], email_source: EmailSource = email_from_oauth_token, before_sensitive: SensitiveHook = no_op)`; method `tool(self, fn, *, sensitive: bool = False)` that registers on `mcp` a wrapper which: resolves the caller via `email_source()`, builds a `ToolContext`, calls `before_sensitive` when `sensitive`, calls `fn(ctx, **kwargs)`, and converts any `BookingError` into `fastmcp.exceptions.ToolError(str(err))`.
  - `build_server(*, backend, rooms, users, auth: AuthProvider | None = None, email_source: EmailSource = email_from_oauth_token, before_sensitive: SensitiveHook = no_op) -> FastMCP` — creates `FastMCP("meeting-rooms", instructions=..., auth=auth)` and registers the four tools through the gateway only (`cancel_booking` with `sensitive=True`). `server.py` never calls `mcp.tool` directly.

The one non-obvious part: FastMCP builds the tool's JSON schema from the function signature, so the wrapper must expose the tool function's signature **minus `ctx`**:

```python
sig = inspect.signature(fn)
params = list(sig.parameters.values())[1:]           # drop ctx
wrapper.__signature__ = sig.replace(parameters=params)
wrapper.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k != "ctx"}
wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
```

Tool docstrings are what the agent reads: write them precisely in `tools.py` (what it does, time format, timezone default, what errors mean).

- [ ] **Step 1: Write the failing tests** (use `fastmcp.Client(server)` in-memory; `email_source=lambda: current["email"]` with a mutable dict so a test can switch user; `pytest.raises(ToolError)` from `fastmcp.exceptions`)

```python
async def test_exposes_exactly_four_tools_without_ctx(): ...
    # names == {"search_rooms", "list_my_bookings", "book_room", "cancel_booking"}
    # and "ctx" not in any tool.inputSchema["properties"]

@pytest.mark.parametrize("tool,args", [...one valid call per tool...])
async def test_every_tool_rejects_unauthenticated_call(tool, args): ...
    # email_source returns None → ToolError matching "sign in"/your NotAuthenticated message

async def test_unknown_user_rejected_even_if_signed_in(): ...

async def test_book_then_list_through_mcp(): ...
    # book_room as employee → list_my_bookings returns it (check result.structured_content / data)

async def test_employee_cannot_cancel_others_booking_through_mcp(): ...
    # ToolError message mentions the booking is not theirs

async def test_sensitive_hook_runs_only_for_cancel(): ...
    # recording hook: after one call of each tool, calls == [(caller, "cancel_booking", {"booking_id": ...})]
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_server.py -v`
- [ ] **Step 3: Implement `gateway.py` and `build_server` in `server.py`**
- [ ] **Step 4: Run the whole suite** — `uv run pytest -v` → all green.
- [ ] **Step 5: Commit** — `feat: gateway layer and MCP server wiring`

---

### Task 8: Google setup, calendar login and room seeding (manual + scripts)

**Files:**
- Create: `src/meeting_rooms/calendar_login.py`, `src/meeting_rooms/seed_rooms.py`
- Modify: `pyproject.toml` (`[project.scripts]`: `calendar-login = "meeting_rooms.calendar_login:main"`, `seed-rooms = "meeting_rooms.seed_rooms:main"`)

**Interfaces:**
- Produces: `.tokens/calendar.json` (authorized-user credentials, gitignored) used by Task 9; a real `rooms.toml`.

- [ ] **Step 1 [Vincent]: Google Cloud console** (reuse the project from the agent spike): enable the Google Calendar API; add the second Gmail as a test user; create an OAuth client of type **Desktop app**, download as `client_secret_calendar.json` (gitignored).
- [ ] **Step 2: `calendar_login.main()`** — `InstalledAppFlow.from_client_secrets_file("client_secret_calendar.json", ["https://www.googleapis.com/auth/calendar"]).run_local_server(port=0)`, write `creds.to_json()` to `.tokens/calendar.json` (create the folder).
- [ ] **Step 3 [Vincent]: Run it** — `uv run calendar-login` → browser login as the room owner; file exists.
- [ ] **Step 4: `seed_rooms.main()`** — for each of Atlas (6, screen+whiteboard), Bora (4, whiteboard), Cosmos (12, screen): `service.calendars().insert(body={"summary": "Room Atlas", "timeZone": "Europe/Paris"})`, then print a `rooms.toml` with the returned calendar ids. Skip a room whose calendar already exists (match on summary via `calendarList().list()`), so a re-run is safe.
- [ ] **Step 5 [Vincent]: Run it** — `uv run seed-rooms > rooms.toml`; the three calendars appear in Google Calendar. Copy `users.toml.example` to `users.toml` with the two real emails.
- [ ] **Step 6: Commit** (scripts only; `git status` must not show `rooms.toml`, `users.toml`, `.tokens/`, `client_secret*`) — `feat: calendar owner login and room seeding scripts`

---

### Task 9: Google Calendar backend and real server entry point

**Files:**
- Create: `src/meeting_rooms/backend/google.py`
- Modify: `src/meeting_rooms/server.py` (add `main()`), `pyproject.toml` (`meeting-rooms-server = "meeting_rooms.server:main"`)
- Test: `tests/test_google_mapping.py`

**Interfaces:**
- Consumes: `CalendarBackend` (Task 3), `Settings` (Task 2), `build_server` (Task 7).
- Produces:
  - `booking_to_event_body(room: Room, start: datetime, end: datetime, title: str, booked_by: str) -> dict` — `summary`, `description` (`"Booked by <email> via meeting-rooms-mcp"`), `start`/`end` as `{"dateTime": iso, "timeZone": "Europe/Paris"}`, `extendedProperties.private.booked_by`.
  - `event_to_booking(event: dict, room: Room) -> Booking` — `booked_by` from `extendedProperties.private.booked_by`, else `None`.
  - `class GoogleCalendarBackend(CalendarBackend)`: `__init__(self, credentials: Credentials)`; `from_token_file(path: Path) -> GoogleCalendarBackend` (refresh if expired, rewrite the file). API calls: `freebusy().query(body={"timeMin", "timeMax", "items": [{"id": cal}]})`; `events().insert`; `events().list(calendarId, timeMin, timeMax, singleEvents=True, orderBy="startTime", privateExtendedProperty=f"booked_by={email}")`; `events().get` (HTTP 404 → try next room; also treat `status == "cancelled"` as not found); `events().delete`.
  - `main()` — `Settings()`; `GoogleProvider(client_id=..., client_secret=..., base_url=settings.base_url, required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"])`; `build_server(..., auth=provider)`; `server.run(transport="http", host="localhost", port=8000)`.

- [ ] **Step 1: Write the failing mapping tests** (pure dicts, no network)

```python
def test_event_body_carries_booked_by(): ...
    # body["extendedProperties"]["private"]["booked_by"] == "emp@x.com"; "emp@x.com" in body["description"]
def test_event_body_uses_paris_timezone(): ...
def test_event_to_booking_round_trip(): ...       # body + {"id": "evt1"} → Booking with same fields
def test_event_without_booked_by_is_foreign(): ...  # booked_by is None
```

- [ ] **Step 2: Run to fail** — `uv run pytest tests/test_google_mapping.py -v`
- [ ] **Step 3: Implement the mappers, then `GoogleCalendarBackend`, then `main()`**
- [ ] **Step 4: Run the suite** — `uv run pytest -v` → all green.
- [ ] **Step 5 [Vincent]: Manual check with Claude Code as the client** — `uv run meeting-rooms-server`; in another terminal `claude mcp add --transport http meeting-rooms http://localhost:8000/mcp`, sign in with Google as the employee, then: search a slot, book it (event appears in Google Calendar with `booked_by`), list, cancel. Sign in as the admin and cancel an employee's booking. Book one slot by hand in Google Calendar and confirm `search_rooms` treats the room as busy.
- [ ] **Step 6: Commit** — `feat: Google Calendar backend and HTTP server entry point`

---

### Task 10: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README**, in this order: what it is (one paragraph); why the boundary matters (the gateway diagram: user → Google sign-in → MCP server → Gateway (identity, role, ownership rule, sensitive hook) → tools → calendar backend → room calendars; the two-logins table from the spec); week 3 roadmap (permission table, confirmations, audit log, plugged into `before_sensitive`); Google setup (both OAuth clients, test users, the 7-day refresh-token limit of testing mode); config files; how to run server, tests and Claude Code; the agent demo (filled in by the agent plan, Task 4).
- [ ] **Step 2: Check** — a fresh clone + the README alone is enough to run `uv run pytest`; no secret in any tracked file (`git grep -i "secret\|@gmail"` returns only placeholders).
- [ ] **Step 3: Commit** — `docs: README`
