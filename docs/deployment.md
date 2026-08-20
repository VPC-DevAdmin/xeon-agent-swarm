# Hosting the demos behind Cloudflare

Concrete runbook for the R470 (`dellr470`), written against the box's **actual**
setup as inspected on 2026-08-20.

**Why not a Worker / Pages upload:** this demo is a live application — FastAPI
backend, WebSocket event stream, scheduler, calls to the local tier router. A Worker
can't run Python or hold the WS connection. The right primitive is the **Cloudflare
Tunnel you already have**.

## What's on the box today

| | |
|---|---|
| Tunnel | `router-origin` · `f3f00468-5489-466d-b5e0-a7d65c855bfa` (locally-managed, healthy) |
| Domain | `enterpriseai.center` |
| Live hostname | `router-origin.enterpriseai.center` → `localhost:8900` (tier router gateway) |
| Old config | `~/work/repos/semantic-router/config/cloudflared.yml` (non-standard path) |
| How it ran | **by hand** — no systemd unit ⇒ **this is why it died on reset** |

Target state — one tunnel, three hostnames, everything under systemd:

```
demos.enterpriseai.center     → :8088   hub (pick a demo)
agents.enterpriseai.center    → :8010   Agent Orchestrator (UI+API+WS, one origin)
router-origin.enterpriseai…   → :8900   tier router gateway (unchanged)
```

---

## 0. DNS — prerequisite (already applied)

Spectrum's resolver was hijacking `api.cloudflare.com` → `98.8.56.210`
(`activate.spectrum.net`), which breaks `cloudflared tunnel route dns`. Fixed by
pointing systemd-resolved at public DNS:

```bash
sudo sed -i 's/^#\?DNS=.*/DNS=1.1.1.1 8.8.8.8/; s/^#\?FallbackDNS=.*/FallbackDNS=1.0.0.1 8.8.4.4/; s/^#\?Domains=.*/Domains=~./' /etc/systemd/resolved.conf && sudo systemctl restart systemd-resolved
```

`/etc/systemd/resolved.conf` is persistent, but **re-verify after the reboot test**
(step 5) — DHCP can reassert link DNS. Check with `getent hosts api.cloudflare.com`;
it must return `104.19.x` / `2606:4700:` addresses, not a Spectrum IP.

## 1. Build the SPA (single origin)

`make serve` / the systemd unit serve the UI, REST, and WebSocket from **one port**,
so the tunnel points at one service — no CORS, no `ws` vs `wss` mismatch. The
frontend reads its API base from the page's own origin
([`frontend/src/lib/origin.ts`](../frontend/src/lib/origin.ts)), so the same build
works on any hostname with **no rebuild**.

```bash
cd ~/work/repos/xeon-agent-swarm && git pull && npm --prefix frontend run build
```

> Rebuild after every `git pull` — the service only serves `frontend/dist`.

## 2. Services (survive reboot)

```bash
sudo cp deploy/xeon-agents.service deploy/xeon-hub.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now xeon-agents xeon-hub && systemctl --no-pager status xeon-agents xeon-hub | head -20
```

**The semantic-router app needs one too** — it has the same reboot problem. Copy
`deploy/xeon-agents.service` as a template, swap `WorkingDirectory`, `ExecStart`,
and drop the `FRONTEND_DIST` line.

## 3. Move the tunnel config and install it as a service

The tunnel is currently run by hand from a path inside the semantic-router repo.
Move it to the standard location so `cloudflared service install` manages it:

```bash
sudo mkdir -p /etc/cloudflared && sudo cp ~/work/repos/xeon-agent-swarm/deploy/cloudflared.yml /etc/cloudflared/config.yml && sudo cp ~/.cloudflared/f3f00468-5489-466d-b5e0-a7d65c855bfa.json /etc/cloudflared/ && sudo chmod 600 /etc/cloudflared/f3f00468-5489-466d-b5e0-a7d65c855bfa.json && cloudflared tunnel ingress validate --config /etc/cloudflared/config.yml
```

Route the two new hostnames (the existing one is already routed):

```bash
cloudflared tunnel route dns router-origin agents.enterpriseai.center && cloudflared tunnel route dns router-origin demos.enterpriseai.center
```

Stop the hand-started tunnel and install the service:

```bash
pkill -f 'cloudflared tunnel --config' ; sudo cloudflared service install && sudo systemctl enable --now cloudflared && sleep 5 && cloudflared tunnel info router-origin
```

*(Optional: `cloudflared` is on 2026.3.0, current is 2026.8.2 — upgrade before
installing the service if you want to be current.)*

## 4. Gate access to specific people

This demo runs the **live router and real tools**, so it must not be open to the
world. In **Zero Trust → Access → Applications → Add → Self-hosted**:

- Application domain: `agents.enterpriseai.center` (repeat for `demos.`)
- Policy: **Allow** → Include → *Emails* (or *Emails ending in* your domain)
- Access authenticates at the edge, in front of the tunnel — the WebSocket is
  covered too.

> **Hub status dots:** the hub polls each demo's `/health` cross-origin. With Access
> in front, that poll is redirected to the login page and the card may read
> "offline" even when the demo is up. Either add a **Bypass** policy for path
> `/health` on each app, or treat the hub purely as a link page.

## 5. Verify — including the reboot

```bash
systemctl is-enabled xeon-agents xeon-hub cloudflared && curl -sf localhost:8010/health && curl -sfo /dev/null -w 'hub:%{http_code}\n' localhost:8088/ && echo "--- all green; now: sudo reboot ---"
```

After rebooting, browse **https://demos.enterpriseai.center**, sign in through
Access, open the Agent Orchestrator, and run a prompt — the plan, the live agent
timeline, and the streamed answer all ride the tunnel over `wss://`.

## Runbook

| Symptom | Check |
|---|---|
| 502 from the edge | `systemctl status xeon-agents` — app down, tunnel fine |
| Tunnel offline | `systemctl status cloudflared`; `cloudflared tunnel info router-origin` |
| `route dns` fails with a TLS/cert error | DNS hijack is back — re-check step 0 |
| UI loads, runs never start | WebSocket blocked — confirm `https` (so `wss`) and Access session |
| Stale UI after a pull | `npm --prefix frontend run build && sudo systemctl restart xeon-agents` |
| Schema error after a pull | `make reset-db && sudo systemctl restart xeon-agents` |
