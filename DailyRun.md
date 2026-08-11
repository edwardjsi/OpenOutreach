# Daily Run — OpenOutreach Startup

## Morning startup

```bash
cd ~/OpenOutreach
make up
```

Wait ~10 seconds for the container to finish starting.

## Open the VNC screen

```
http://localhost:6080/vnc.html
```

You'll see a blank **Playwright** browser (noVNC). It shows either:

- `https://www.linkedin.com/feed/` — daemon is **already logged in**
- `https://www.linkedin.com/login` — daemon **needs** you to log in

## Run the daemon

```bash
make run
```

Wait ~10 seconds. Check the logs:

```bash
make logs
```

Look for:

| Status | Meaning | Next |
|--------|---------|------|
| `[INFO] Loading saved session for ...` | Daemon is **already logged in** — using yesterday's cookies | Done — leave it running |
| `[INFO] Fresh login sequence starting ...` | Daemon **needs** you to log in | Log in on VNC → `make dumpcookies` → `make run` |
| `[INFO] Handling task ...` | A task is being processed | Done — daemon is working |
| `[WRN] AuthenticationError` | Session expired — **login again** | `make dumpcookies` → log in → `make run` |
| `[WRN] Re-authentication skipped ...` | Session is **still** on a blocked page | Log in **again** on the VNC screen |

## First-time session dump (only needed once)

If the daemon **isn't** already logged in (you see `/login` on VNC):

1. Type your **email/password** on the VNC screen (`http://localhost:6080`)
2. Wait for `/feed` to load
3. **Press Enter** in the terminal where `make run` is running:

```bash
make dumpcookies
```

The session is saved to `LinkedInProfile.cookie_data` (6 cookies, 0 origins). The daemon **never** logs in again — it reuses your real browser session.

## Leave it running

Close the terminal. The VNC window stays open at `http://localhost:6080`.

The daemon runs in the background until you stop it with:

```bash
make stop
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| VNC shows `/login` page | `make dumpcookies` → log in → `make run` |
| Daemon says `RuntimeError: Login failed` | **New** login — `make dumpcookies` |
| VNC shows blank/white screen | Refresh `http://localhost:6080/vnc.html` |
| `make run` doesn't start | `make up` first, then `make run` |
| Container exited | `make up` (restarts everything) |
| `make dumpcookies` doesn't work | Run `make up` **first** to start the container |

To find out the stage:

cd ~/OpenOutreach && docker compose -f local.yml logs --tail=5 --no-log-prefix 2>&1 | grep -E "(INF|ERR| ▶ )" | tail -5 
