# Cloudflare preview of the conference replay demo

Publishes the built pages in `docs/dashboard` as a static-assets Worker. Nothing here changes the demo; rebuild it first when it changes.

One-time, from this directory:

```sh
npm install
npx wrangler login
```

Then:

| Command | What it does |
|---|---|
| `npm run preview` | Uploads a new version and prints a preview URL of the form `<version>-xeon-replay-demo.<account>.workers.dev`. Nothing already deployed changes. |
| `npm run deploy` | Deploys to the stable `xeon-replay-demo.<account>.workers.dev` URL. |
| `npm run dev` | Serves the staged pages locally through workerd on http://localhost:8787. |

Both build steps copy `steady-state.html` to `index.html` and keep `agent-replay.html` and `layout-studies.html` alongside it, add a `robots.txt` and a `noindex` header, and refuse a page that still has unbuilt placeholders.

The workers.dev URLs are public to anyone holding the link. For a gated preview, put the Worker behind Cloudflare Access (Zero Trust → Access → Applications → self-hosted, using the workers.dev hostname) or restrict it with a Worker route on a zone you control.
