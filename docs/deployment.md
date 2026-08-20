# Hosting the demo behind Cloudflare

How this demo gets a public URL on your domain, gated to specific people, surviving
a server reboot — alongside your existing demo, behind one Cloudflare Tunnel.

**Why not a Worker / Pages upload:** this is a live application, not a static site —
a FastAPI backend, a WebSocket event stream, a scheduler, and calls to the local tier
router. A Worker can't run Python or hold the WS connection. The right primitive is a
**Cloudflare Tunnel** (`cloudflared`) from the box, which is what your router demo
already uses.

---

## Architecture

```
   visitor ──▶ Cloudflare edge ──▶ Cloudflare Access (email allowlist)
                     │
                     ▼  encrypted tunnel (outbound from the box — no open ports)
                cloudflared  (one process, many hostnames)
                     │
      ┌──────────────┼───────────────────────────┐
      ▼              ▼                           ▼
  demos.…:8080   agents.…:8010              router.…:<port>
   hub page      THIS demo                  your other demo
                 (UI + API + WS,
                  one origin)
```

Three systemd units keep it alive: the orchestrator, the hub, and `cloudflared`.
Nothing listens on a public port — the tunnel dials **out**.

---

## 1. One origin (already done)

`make serve` builds the SPA and serves the UI, REST, and WebSocket from a single
port, so the tunnel points at one service and there's no CORS or `ws://` vs `wss://`
problem. The frontend resolves its API base from the page's own origin
(`frontend/src/lib/origin.ts`), so **the same build works on any hostname** — no
rebuild when the domain changes.

```bash
make serve                     # builds + serves on 127.0.0.1:8010
```

## 2. Run as systemd services (survives reboot)

`sudo tee /etc/systemd/system/xeon-agents.service`:

```ini
[Unit]
Description=Agent Orchestrator (single-origin UI+API)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=devadmin
WorkingDirectory=/home/devadmin/work/repos/xeon-agent-swarm
EnvironmentFile=/home/devadmin/work/repos/xeon-agent-swarm/.env.adl
Environment=CONFIG_DIR=config
Environment=FRONTEND_DIST=frontend/dist
ExecStart=/home/devadmin/work/repos/xeon-agent-swarm/.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`sudo tee /etc/systemd/system/xeon-hub.service`:

```ini
[Unit]
Description=Demo hub landing page
After=network-online.target

[Service]
Type=simple
User=devadmin
WorkingDirectory=/home/devadmin/work/repos/xeon-agent-swarm
Environment=HUB_PORT=8080
ExecStart=/usr/bin/python3 scripts/hub_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> The frontend must be built before the service starts (`npm --prefix frontend run
> build`). Rebuild after every `git pull`; the unit only serves `frontend/dist`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xeon-agents xeon-hub
systemctl status xeon-agents --no-pager
```

Do the same for **the other demo** so it also returns after a reset — that's why it
died last time: it was running in a shell, not under systemd.

## 3. Tunnel ingress

Add hostnames to the **existing** named tunnel (one `cloudflared`, many demos).
Edit its config (`~/.cloudflared/config.yml` or `/etc/cloudflared/config.yml`) —
**order matters, first match wins, and the catch-all must stay last**:

```yaml
tunnel: <TUNNEL-ID>
credentials-file: /home/devadmin/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: demos.example.com          # the front door — pick a demo
    service: http://127.0.0.1:8080
  - hostname: agents.example.com         # THIS demo
    service: http://127.0.0.1:8010
  - hostname: router.example.com         # your existing demo
    service: http://127.0.0.1:<ROUTER_PORT>
  - service: http_status:404
```

Point DNS at the tunnel (creates the proxied CNAME automatically) and restart:

```bash
cloudflared tunnel route dns <TUNNEL-NAME> demos.example.com
cloudflared tunnel route dns <TUNNEL-NAME> agents.example.com
sudo systemctl restart cloudflared && cloudflared tunnel ingress validate
```

WebSockets need no extra config — Cloudflare Tunnel proxies them natively.

## 4. Gate it to specific people (Cloudflare Access)

The demo runs the **live router and real tools**, so it must not be open to the
internet. In **Zero Trust → Access → Applications → Add → Self-hosted**:

- Application domain: `agents.example.com` (repeat for the others, or use a wildcard)
- Policy: **Allow** → Include → *Emails* (or *Emails ending in* `@yourcompany.com`)
- Session duration: whatever suits the audience

Visitors get a one-time PIN / SSO prompt before the app is ever reached. Access
covers the WebSocket too — it authenticates at the edge, in front of the tunnel.

> **Hub status dots:** the hub checks each demo's `/health` cross-origin. With Access
> in front, that check is redirected to the login page and the card may read
> "offline" even when the demo is up. Fix by adding a **Bypass** policy for the path
> `/health` on each app, or ignore the dot and treat the hub as a link page.

## 5. Point the hub at the real URLs

Edit `hub/demos.json` — replace the `CHANGE-ME` URLs with your real hostnames. The
page fetches it on load, so no rebuild or restart is needed.

## 6. Verify

```bash
systemctl is-enabled xeon-agents xeon-hub cloudflared   # all: enabled
curl -sf localhost:8010/health && echo OK               # app up locally
sudo reboot                                             # the real test
```

After the reboot, browse `https://demos.example.com`, sign in through Access, open
the Agent Orchestrator, and run a prompt — the plan, the live agent timeline, and
the streamed answer all ride the tunnel over `wss://`.

## Runbook

| Symptom | Check |
|---|---|
| 502 from the edge | `systemctl status xeon-agents` — the app is down, tunnel is fine |
| Tunnel offline | `systemctl status cloudflared`, `cloudflared tunnel info <NAME>` |
| UI loads, runs never start | WS blocked — confirm the page is `https` (so `wss`), check Access |
| Stale UI after a pull | rebuild: `npm --prefix frontend run build && sudo systemctl restart xeon-agents` |
| Schema error after a pull | `make reset-db` then restart the service |
