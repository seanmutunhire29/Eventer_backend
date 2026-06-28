# Eventer — Backend

Backend service for **Eventer**, a campus event discovery app for Dartmouth College. This repo contains the API, the scraper pipeline, and the admin portal. The mobile app (React Native) lives in a separate repository and consumes this service over HTTP.

---

## 1. Overview

The backend is responsible for:

- Scraping campus event sources on a schedule and normalizing them into a clean event/building dataset
- Resolving messy scraped location strings into canonical buildings via an alias system
- Exposing a read-focused REST API for the mobile app
- Providing an admin portal for staff to manage events, buildings, aliases, and scrape sources

The mobile app owns its own local SQLite database for user preferences, reminders, and dismissals — none of that lives here. This backend is the single source of truth for **events**, **buildings**, and **scrape sources** only.

---

## 2. Tech Stack

| Layer           | Technology                                                                                                                 |
| --------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Framework       | Django + Django REST Framework                                                                                             |
| Database        | SQLite (initial) → PostgreSQL (planned migration before real user load)                                                    |
| Scheduled tasks | Celery + Celery Beat (or Django management command on cron, as an interim option)                                          |
| Admin           | Standalone HTML/CSS/JS web app, served over the internet, talking to the backend exclusively through a dedicated admin API |
| Auth            | Public API: none (read-only, no accounts). Admin API: session or token-based auth, staff-only                              |

> Note on the mobile app's Leaflet map: building footprints come from a static GeoJSON asset. This backend is responsible for keeping `buildings` and `building_aliases` in sync with that GeoJSON's `name`/`geojson_id` values, but does not serve the GeoJSON itself (it's bundled with or served as a static asset to the app).

---

## 3. Data Model

### 3.1 `events`

| Column      | Type           | Notes                                                                         |
| ----------- | -------------- | ----------------------------------------------------------------------------- |
| id          | UUID / AutoInt | Primary key                                                                   |
| event_name  | VARCHAR(255)   |                                                                               |
| building_id | FK → buildings |                                                                               |
| start_time  | DATETIME       | Stored UTC, displayed in local time by clients                                |
| end_time    | DATETIME       | UTC                                                                           |
| description | TEXT           |                                                                               |
| category    | VARCHAR(50)    | Enum — see Section 7                                                          |
| other_info  | JSON           | `{has_food, needs_registration, needs_invite, guests_allowed, contact_email}` |
| source_url  | VARCHAR(500)   | Page the event was scraped from                                               |
| created_at  | DATETIME       |                                                                               |
| updated_at  | DATETIME       | Drives delta sync for clients                                                 |
| is_active   | BOOLEAN        | Admin can soft-delete/hide; also set by the scraper lifecycle                 |
| is_verified | BOOLEAN        | Admin-verified vs. raw scrape                                                 |

### 3.2 `buildings`

| Column        | Type         | Notes                                        |
| ------------- | ------------ | -------------------------------------------- |
| id            | AutoInt      | Primary key                                  |
| official_name | VARCHAR(255) | Matches the Leaflet/GeoJSON name             |
| lat           | FLOAT        |                                              |
| lng           | FLOAT        |                                              |
| geojson_id    | VARCHAR(100) | Links to the GeoJSON feature used by the app |

### 3.3 `building_aliases`

| Column      | Type           | Notes                                     |
| ----------- | -------------- | ----------------------------------------- |
| id          | AutoInt        | Primary key                               |
| building_id | FK → buildings |                                           |
| alias       | VARCHAR(255)   | Student nickname or scraper-produced name |
| source      | VARCHAR(100)   | e.g. `scraper`, `admin`, `student_report` |

### 3.4 `scrape_sources`

| Column                | Type         | Notes                                                                                  |
| --------------------- | ------------ | -------------------------------------------------------------------------------------- |
| id                    | AutoInt      |                                                                                        |
| url                   | VARCHAR(500) | URL to scrape                                                                          |
| label                 | VARCHAR(100) | Human-readable name, e.g. "Dartmouth Mirror Events"                                    |
| is_active             | BOOLEAN      | Toggle scraping on/off per source                                                      |
| scrape_interval_hours | INT          | Default: 3                                                                             |
| last_scraped_at       | DATETIME     |                                                                                        |
| last_scrape_status    | VARCHAR(50)  | `success`, `failed`, `partial`                                                         |
| last_scrape_log       | TEXT         | Error messages or summary                                                              |
| selector_config       | JSON         | CSS selectors / parsing rules for this source (admin-editable, no code changes needed) |

---

## 4. Scraper Pipeline

The scraper runs on a schedule (default every 3 hours, configurable per source) via Celery Beat or a cron-triggered management command. For each **active** `scrape_source`:

1. **Fetch** the page HTML from the source URL.
2. **Parse** using that source's `selector_config` — a JSON document defining CSS selectors / XPath for event name, time, location, description, etc. This keeps source-specific logic out of code and in the admin.
3. **Normalize location** — run the scraped location string through `building_aliases`. If a match is found, map to the canonical `building_id`. If not, log it as an **unresolved location** and surface it in the admin for manual mapping.
4. **Deduplicate** — before inserting, check for an existing event with the same `event_name` + `building_id` + `start_time`. If found, update rather than insert.
5. **Lifecycle management**:
   - New scraped events are inserted with `is_active = True`.
   - Events present in a previous scrape but missing from the current one are **not** deleted immediately — they're flagged `is_active = False` after **2 consecutive missed scrapes**, and fully deleted **7 days after their `end_time`**.
6. **Logging** — update `last_scraped_at`, `last_scrape_status`, and `last_scrape_log` on the source record after every run.

---

## 5. Public API (consumed by the mobile app)

All endpoints are read-only and require no authentication.

| Method | Endpoint            | Description                                                                      |
| ------ | ------------------- | -------------------------------------------------------------------------------- |
| GET    | `/api/events/`      | All active events. Query params: `?date=YYYY-MM-DD`, `?category=food`, `?days=7` |
| GET    | `/api/events/<id>/` | Single event detail                                                              |
| GET    | `/api/buildings/`   | All buildings with lat/lng and their alias list                                  |
| GET    | `/api/categories/`  | All categories with icon metadata                                                |

**Sync behavior expected by clients:** the app fetches `/api/events/?days=7` on launch and polls every 30 minutes while open. The `updated_at` field on events is what enables delta syncs — clients should be able to request only events changed since their last sync (e.g. via a `?since=<timestamp>` style param, or by filtering client-side on `updated_at`). Keep `updated_at` accurate on every write path (scraper updates **and** admin edits).

---

## 6. Admin Portal

The admin portal is **not** Django Admin. It's a standalone web app built with plain HTML/CSS/JS, but it lives **inside this repo**, at `static/admin-frontend/`, and is served directly by Django as a static site (e.g. via a catch-all view or `whitenoise`/Django's static file handling) rather than being deployed as a separate project. It's still accessible over the internet at a path or subdomain of the backend's own domain (e.g. `https://api.eventer.app/admin-frontend/`).

It is a pure frontend client — all of its functionality is powered by a dedicated **admin API** exposed by this same backend (separate from the public read-only API in Section 5). Because it's served same-origin from Django, CORS is not a concern for it the way it would be for a separately hosted app; it can call `/api/admin/*` directly using relative paths.

Required modules (each maps to a page/view inside `static/admin-frontend/`):

### 6.1 Events Manager

- Sortable/filterable list (date, category, building, scrape source, `is_active`, `is_verified`)
- Inline editing of any field
- Manual event creation (for events not present on any scraped page)
- Soft delete (`is_active = False`) and hard delete
- Bulk actions: bulk delete, bulk mark verified, bulk change category
- **Unresolved Locations** view: events where the scraper couldn't match a building. Admin manually assigns a building, optionally saving the mapping as a new alias for future scrapes.

### 6.2 Buildings Manager

- List + map view of all buildings
- CRUD: official name, lat/lng, GeoJSON ID
- Alias manager per building: add/edit/delete aliases with source label
- **Suggest Aliases** view: surfaces unique unresolved location strings from recent scrapes for quick assignment to buildings

### 6.3 Scrape Sources Manager

- List with status indicators (last scrape status, last scraped time)
- Add/edit/delete sources: URL, label, interval, `selector_config` (JSON editor with syntax highlighting)
- Per-source `is_active` toggle
- **Run Now** — trigger an immediate out-of-schedule scrape for a specific source
- Scrape log viewer — last N logs per source with timestamps and error detail

### 6.4 Dashboard

- Summary stats: events today, active events this week, events by category, scrape health (% success in last 24h)
- Recent scrape activity feed
- Unresolved location count with a quick link into the alias manager

### 6.5 Admin API (backend surface for the portal)

The HTML/JS admin app authenticates against the backend (e.g. a login form posting to a token/session endpoint) and then drives everything through endpoints like:

| Method                      | Endpoint                                                         | Description                                           |
| --------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------- |
| POST                        | `/api/admin/login/`                                              | Authenticate, returns session/token                   |
| GET / PATCH / DELETE        | `/api/admin/events/` , `/api/admin/events/<id>/`                 | List/filter, inline edit, soft/hard delete events     |
| POST                        | `/api/admin/events/`                                             | Manually create an event                              |
| POST                        | `/api/admin/events/bulk/`                                        | Bulk delete / verify / change category                |
| GET                         | `/api/admin/events/unresolved/`                                  | Events with unmatched locations                       |
| GET / POST / PATCH / DELETE | `/api/admin/buildings/` , `/api/admin/buildings/<id>/`           | CRUD for buildings                                    |
| GET / POST / PATCH / DELETE | `/api/admin/buildings/<id>/aliases/`                             | CRUD for aliases on a building                        |
| GET                         | `/api/admin/aliases/suggestions/`                                | Unresolved location strings awaiting alias assignment |
| GET / POST / PATCH / DELETE | `/api/admin/scrape-sources/` , `/api/admin/scrape-sources/<id>/` | CRUD for scrape sources, including `selector_config`  |
| POST                        | `/api/admin/scrape-sources/<id>/run-now/`                        | Trigger an immediate scrape                           |
| GET                         | `/api/admin/scrape-sources/<id>/logs/`                           | Scrape log history for a source                       |
| GET                         | `/api/admin/dashboard/`                                          | Aggregated dashboard stats                            |

All `/api/admin/*` endpoints require staff authentication; they are entirely separate from the public `/api/*` endpoints in Section 5, which remain open and read-only. Since the admin frontend is served by this same Django app from `static/admin-frontend/`, no CORS configuration is needed for it — it's same-origin. CORS only becomes relevant here if the mobile app or some other external client ever needs to call `/api/*` from a browser context.

---

## 7. Event Categories

Fixed enum used by both `events.category` and the `/api/categories/` endpoint. Icon files and accent colors are metadata served alongside the category list — actual icon assets are owned by the frontend, but the backend should store/serve the mapping.

| Category              | Accent Color    |
| --------------------- | --------------- |
| Academic / Lecture    | Dartmouth Green |
| Social / Party        | Purple          |
| Free Food             | Orange          |
| Sports / Athletics    | Red             |
| Arts / Performance    | Pink            |
| Career / Professional | Blue            |
| Club / Org Meeting    | Teal            |
| Religious / Spiritual | Gold            |
| Volunteer / Community | Lime            |
| Health / Wellness     | Mint            |

---

## 8. Campus GeoJSON (Backend's Role)

The campus building GeoJSON itself is a static asset consumed by the mobile map, but this backend owns the **data that keeps it meaningful**:

1. Building footprints are exported once from OpenStreetMap (Overpass API or JOSM) for the Dartmouth campus.
2. Each GeoJSON feature's `name` property must correspond to a `geojson_id` in the `buildings` table.
3. Any name mismatch between scraper/student usage and the official GeoJSON name becomes a `building_aliases` entry.
4. The admin portal's Building Manager is the **ongoing** tool for maintaining this mapping as new aliases surface through scraping — this is expected to be a living dataset, not a one-time setup.

---

## 9. Migration Path: SQLite → PostgreSQL

SQLite is the starting point for development. Before any real user load, the project should migrate to PostgreSQL. Since this is Django, it's primarily a settings/config change (`DATABASES`) plus running migrations against the new database — but plan the timing early rather than treating it as a last-minute switch, and avoid SQLite-specific assumptions (e.g. in JSON field usage or raw queries) that won't translate cleanly.

---

## 10. Future Considerations

These are not in current scope but should inform schema/API decisions now:

- **Event submission by organizers** — a future form-based submission path that bypasses the scraper for events that never appear on a scraped page.
- **User accounts** — currently all user state (preferences, dismissals, reminders) lives client-side only. If accounts are added later, that local data needs a migration path into a cloud profile — keep this in mind when evolving the API surface.
- **Scrape source tooling** — `selector_config` will need documentation and ideally a **"test scrape" / preview** feature in the admin, so a new source can be validated before being activated, without needing a code change or a blind first run.
- **Push notification delivery for event changes** — notifications are currently scheduled and managed entirely client-side (local notifications). A future version could use FCM silent push so the backend can proactively wake the app and trigger a re-sync when an event's time changes server-side, rather than waiting for the next poll.

---

## 11. Explicit Non-Goals of This Repo

To keep the boundary with the mobile frontend repo clean:

- No user accounts, login, or auth-gated content on the **public** API — it's fully open and read-only.
- No local device storage logic (reminders, dismissals, theme, onboarding state) — that's entirely client-owned by the mobile app.
- No mobile UI, map rendering, or GeoJSON rendering logic — this repo produces data for the mobile app, not its presentation layer.
- The **admin frontend** (`static/admin-frontend/`) is the one exception to "no UI" — it's a thin HTML/CSS/JS client that lives in this repo and is served by Django, but it contains no business logic of its own beyond calling the admin API.
