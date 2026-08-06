# Operations & Troubleshooting Guide

**Purpose:** Every entry below is a real issue encountered and resolved
during this project's actual development — not speculative. Each
follows the same structure: symptom, root cause, fix, and the general
lesson worth carrying forward. Organized roughly in the order these
tend to surface when operating this platform.

---

## Database & Connectivity

### "password authentication failed" against the Postgres container

**Symptom:** `psql`/Python connections fail with a password error, even
though `.env` clearly has the right password, and `docker exec ... psql`
works fine with no password prompt at all.

**Root cause:** `docker exec` connects via a local Unix socket inside
the container, which the official Postgres image trusts without a
password by default — it never actually tests the password. Meanwhile,
Postgres only applies `POSTGRES_PASSWORD` the *first time* a fresh, empty
data volume initializes. If the named volume already existed from an
earlier attempt (even one you don't remember), the container silently
keeps whatever password was set back then, ignoring the current
environment variable.

**Fix:** stop and remove both the container and its volume, then
recreate from scratch so `POSTGRES_PASSWORD` genuinely takes effect:
```bash
docker stop <container> && docker rm <container>
docker volume rm <volume-name>
docker run ...   # recreate
```
Then re-apply schema DDL, since a fresh volume has no tables.

**General lesson:** `docker exec` verification doesn't prove
credentials work over the network — it bypasses authentication
entirely. Always test the actual connection path an application will
really use.

---

### Connection refused / wrong port entirely

**Symptom:** connection fails, or worse, silently succeeds against the
*wrong* database (a native install, not the container).

**Root cause:** a native Windows PostgreSQL service (or another Docker
project's Postgres container) is already listening on the port you
assumed was free.

**Diagnosis:**
```bash
netstat -ano | findstr :5432
docker ps --format "table {{.Names}}\t{{.Ports}}"
```
More than one `LISTENING` line on the same port is the definitive
signal — not a guess.

**Fix:** remap the host-side port (`"5433:5432"` in `docker-compose.yml`
or the `docker run -p` flag), update every `.env`/`.env.docker`
reference to the new port. Never assume a fix worked without re-running
`docker ps -a` and confirming a genuinely new container/timestamp — a
partial cleanup (e.g., `docker rm` failing silently because the
container wasn't stopped first) can leave the *old* state still active.

**This project's specific fix:** host port 5433 for Postgres, documented
directly in `docker-compose.yml`'s `ports:` comment.

---

### `.env` values not taking effect inside a container

**Symptom:** a Python script inside a container connects using
Windows-local values (e.g. `localhost:5435`) instead of the correct
container-network values (`postgres:5432`).

**Root cause:** no `.dockerignore` existed, so `COPY . .` in the
Dockerfile copied the host's own `.env` file into the image.
`load_dotenv(override=True)` then found and loaded that stale, wrong
file, overriding the correct values Compose's `env_file:` had already
injected.

**Fix:** add `.env` (but *not* `.env.docker`) to `.dockerignore`, and
rebuild the image (`.dockerignore` only takes effect at build time, not
via a plain restart).

**General lesson:** `.dockerignore` is not optional hygiene — assume
`COPY . .` copies everything unless you've explicitly proven otherwise.

---

## Prefect / Orchestration

### `alembic.util.exc.CommandError: Can't locate revision ...`

**Symptom:** Prefect server (local or containerized) crashes on
startup during its own database migration step.

**Root cause:** Prefect's local database (SQLite by default) already
exists at the location it's checking, but was created by a different,
incompatible Prefect version — often from an unrelated past project on
the same machine, or a stale shared `~/.prefect` location.

**Fix (local):** set `PREFECT_HOME` to a project-local directory, not
the shared default:
```bash
export PREFECT_HOME="$(pwd)/.prefect"
```
Must be exported **in every new terminal** before running the `prefect`
CLI directly — it is not read from `.env` automatically by the bare
CLI, only by our own Python scripts via `load_dotenv()`.

**Fix (Docker, if it recurs under continuous load):** see the SQLite
locking entry below — the real, permanent fix at that point is a
PostgreSQL-backed metadata store, not just isolating the SQLite file.

---

### `sqlite3.OperationalError: database is locked` (repeating indefinitely)

**Symptom:** Prefect server running continuously in Docker eventually
starts throwing this error on every scheduler tick, forever.

**Root cause:** SQLite allows only one writer at a time. Prefect's
background scheduler and its API server both write concurrently under
continuous operation — light, occasional local testing never surfaces
this, but an always-on containerized deployment does, reliably, within
minutes.

**Fix:** point Prefect at PostgreSQL instead of SQLite:
```yaml
environment:
  PREFECT_API_DATABASE_CONNECTION_URL: postgresql+asyncpg://user:pass@postgres:5432/prefect
```
Use a **separate database** (`prefect`), not shared with application
schemas — Prefect's own metadata is a distinct concern.

**General lesson:** SQLite's single-writer limitation is not a tuning
problem — no configuration fixes it. Any tool run continuously,
concurrently, needs a real concurrent-write-capable database.

---

### Worker exits with code 137 (SIGKILL) on shutdown

**Symptom:** `docker compose ps -a` shows the worker container exited
with code 137 after a `docker compose down` or an interrupted `up`.

**Root cause:** the container's command was `sh -c "cmd1 && cmd2 &&
final_long_running_cmd"`. The shell (`sh`) is PID 1 and receives
Docker's shutdown signal, but does not forward it to `final_long_running_cmd`,
which runs as a child process. Docker waits its grace period, then
force-kills everything.

**Fix:** prefix the final, long-running command with `exec`:
```bash
sh -c "cmd1 && cmd2 && exec final_long_running_cmd"
```
`exec` replaces the shell process with the target command, so it
becomes PID 1 and receives shutdown signals directly.

**Verification:** `docker compose restart <service>` should show it
back `Up` within a second or two, not after a ~10-second forced-kill
delay.

---

### Deployment crashes with `FileNotFoundError` on a Windows path, inside a Linux container

**Symptom:** a triggered flow run fails immediately with something like
`No such file or directory: 'C:\Users\...\project'`.

**Root cause:** Prefect's deployment "pull step" (`set_working_directory`)
bakes in a literal path at deploy time. A deployment configured
interactively on the Windows host embeds a Windows path; that same
`prefect.yaml`, copied into a Linux container, is meaningless there.

**Fix:** maintain two deployment spec files — one per environment —
each with the correct `directory:` for that filesystem, deployed with
`prefect deploy --all --prefect-file <the-right-one>.yaml`.

---

## Docker Build Issues

### `ModuleNotFoundError` for a package you explicitly installed in the Dockerfile

**Symptom:** `pip install X` succeeds with no errors during `docker
build`, but the running application still can't import `X`.

**Root cause:** the image runs its actual application out of a
*different* Python environment than the one `pip install` targeted by
default (e.g. an internal venv at `/app/.venv/`, separate from system
Python). The install succeeded — into the wrong place.

**Diagnosis:** check which Python the app actually runs (`which python`
inside the container, or read the base image's own entrypoint/docs),
and whether that environment even has a `pip` binary at all — some
images manage their venv with a different tool entirely (e.g. `uv`),
with no pip present by design.

**Fix:** use the base image's own documented installation mechanism if
one exists (check for a script like `/app/docker/pip-install.sh`)
rather than guessing at direct pip/python invocations — official images
often ship the correct method precisely because this is a common
gotcha.

---

### Git Bash mangles container-side absolute paths

**Symptom:** `docker compose exec svc ls /app/foo` fails with an error
referencing a Windows path like `C:/Program Files/Git/app/foo`.

**Root cause:** Git Bash's MSYS layer auto-translates any `/`-leading
argument into a Windows path, assuming a host path was meant — even
when the argument is clearly meant for the container's filesystem.

**Fix:** prefix the single command with `MSYS_NO_PATHCONV=1`:
```bash
MSYS_NO_PATHCONV=1 docker compose exec svc ls /app/foo
```

---

### Compose creates a *different* volume than the one you expect

**Symptom:** a service starts fresh/empty even though a same-named
volume with real data already exists.

**Root cause:** Compose prefixes volume names with the project name by
default unless told not to — `myvolume:` in `docker-compose.yml` may
actually create `projectname_myvolume`, distinct from a
manually-created `myvolume`.

**Diagnosis:** `docker volume ls` — look for both the bare name and a
prefixed variant.

**Fix:** pin the literal name explicitly:
```yaml
volumes:
  myvolume:
    name: myvolume
```

---

## Power BI

### PostgreSQL connector doesn't appear in "Get Data"

**Root cause:** missing the Npgsql .NET driver, which Power BI's
Postgres connector depends on separately from Power BI itself.

**Fix:** install **Npgsql 4.0.10 specifically** (not the latest release
— newer versions dropped the GAC installation mechanism Power BI
relies on). During install, explicitly select the **"Npgsql GAC
Installation"** component — it is sometimes not selected by default.
Fully restart Power BI Desktop afterward (it only scans for providers
at startup).

### "Encryption Support" dialog on connect

Expected for a local dev database with no SSL configured. Safe to
proceed with an unencrypted connection for local development; would
need real TLS certificates for anything beyond that.

### DAX formula fails: table/column name not recognized

**Root cause:** Power BI may store an imported table under a name that
differs from what you expect (e.g. retaining a schema prefix as part of
the display name: `gold vw_shipment_details`, space-separated, not
`vw_shipment_details`).

**Fix:** never type table names by hand in DAX — type the function name
and opening parenthesis, then select the correct name from Power BI's
own autocomplete dropdown, guaranteeing exact match including any
required quoting (`'gold vw_shipment_details'`).

---

## General Debugging Discipline (the pattern behind all of the above)

1. **Verify, don't assume.** A command "probably" working isn't
   confirmed until its actual output is checked (`docker ps -a`, not
   just `docker ps`; `git status` before every commit).
2. **When a fix doesn't work, gather more direct evidence before trying
   a variation of the same fix.** `netstat`, `docker logs`, `cat` the
   actual file — not another guess.
3. **Full file rewrites over incremental patches**, once a file has had
   more than one failed partial edit — a merged/corrupted line (two
   statements concatenated with no newline) is a real, recurring
   failure mode of iterative patching.
4. **Distinguish "ran without error" from "did the right thing."**
   Several incidents in this project (the inflated `rows_passed`
   counter, the silent `.env` override) were cases where nothing
   crashed, but the result was still wrong.