# Enterprise agent conference replay

Open `steady-state.html` in a browser. The page fits a landscape display without scrolling and loops the recorded capacity window. Fonts, server images, paper results, and replay data are embedded for offline use.

Playback uses only a continuous post-warmup segment at the capacity point. The builder selects the longest recorded interval where all five archetypes remain resident, then loops that interval perpetually at 1× speed. The current segment lasts 101.3 seconds, from 719.2 to 820.5 seconds into the capacity hold. Startup and drain-down never play.

Counts remain the recorded counts. The builder does not clamp zeroes, add agents, or invent completions. It stops with an error if a replacement recording has no suitable interval of at least 60 seconds.

The header carries the whitepaper's capacity findings. The five lanes count actual resident workflows per CPU. The lower workflow illustration cycles through the archetypes in one minute, separately from the recorded clock. The three-worker agents advance sequentially through review gates.

## Rebuild after a data update

From the repository root, run:

```sh
node docs/dashboard/build-steady-state.cjs
```

The builder reads `docs/whitepaper/paper-results.json`, the embedded images and fonts in the whitepaper, and `data/capacity/replay/enterprise-v23-12101.json`. To use another replay file, supply its repository-relative path:

```sh
node docs/dashboard/build-steady-state.cjs data/capacity/replay/enterprise-v23-12101.json
```

Edit `steady-state.src.html`, then rebuild. Do not use the older `scripts/build_replay_page.py` for this conference page. That script still builds the separate benchmark-ladder page.

The builder rejects replay data whose capacity point no longer matches the paper. The Details panel names the headline and replay sources separately. Archetype narrative changes require updating the short explanations in the source, alongside the paper's definitions.

## Recorded and illustrated activity

- Resident counts include workflows that arrived before the steady window. Workflows without completion timestamps remain resident until the recording ends.
- Completion messages use recorded timestamps and outcomes. The replay never assigns a successful outcome or completion time to an unfinished workflow.
- CPU utilization averages the busier hardware thread on each physical core. The stacked bar uses recorded process-family samples. Memory uses the recorded host footprint.
- The workflow illustration explains CPU services, sandbox jobs, model calls, and checks. It does not claim exact sub-task timestamps. Each short population segment represents up to four resident workflows.
- Model-serving requirements use the whitepaper's capacity averages and GPU reference. They are not replayed GPU measurements.

## Presenter controls

Space pauses the replay and motion. R restarts the recorded window. F enters full screen. Details pauses playback while the presenter reads the source notes, core map, or serving conditions. Closing Details restores the previous playback state.

Select a headline finding, an agent, a worker, a workflow stage, a server, the LLM connection, a resource reading, a CPU category, or the completion message to open its explanation. Each target also accepts Enter or Space when focused. Closing a topic restores the prior playback state. The source-reference button opens the full presenter notes.

The deployment graphic sizes whole dual-socket R770 and eight-GPU XE7740 systems to the chosen working pool; the sizing model and its assumptions are described in `SIMULATION.md`. Recorded readings remain scoped to one CPU. The throughput note below the hardware opens the model-serving calculation.

Reduced-motion preferences start the demo paused, with decorative animation disabled. The replay clock runs on a timer rather than animation frames, so it keeps advancing inside embedded viewers that report the page hidden; the clock never jumps by more than a quarter second after a throttled interval.

## Working-pool simulation

The demo opens as a simulation of 720 working agents in the Enterprise mix and lets the presenter change the incoming percentages, add or remove archetypes, and set the deployment total. `simulation-model.cjs` holds the sizing arithmetic, `simulation-profile.json` the per-workflow component costs from the methodology, `simulation-ui.js` and `simulation.css` the controls, and `detail-views.js` and `detail-views.css` the click-through views. The build embeds all of them and refuses to run against a profile or timing series that does not match the current definitions. `SIMULATION.md` explains the calculation, its assumptions, and the spot checks that would validate it.

Run the offline checks from the project root after every rebuild:

```sh
node docs/dashboard/check-simulation.cjs
node docs/dashboard/check-details.cjs
```

## Local preview

The `replay-demo` entry in `.claude/launch.json` serves this directory on http://127.0.0.1:8766; open `steady-state.html` there. Without the launcher:

```sh
python3 -m http.server 8766 --bind 127.0.0.1 --directory docs/dashboard
```

## Layout studies

`layout-studies.html` is a design exploration, not the demo: a tab bar with the current page embedded for comparison and three alternative compositions of the same content, all driven by the same paper results, simulation profile and sizing model. Study A leads with the answer as one sentence and a three-stage flow (workload, CPUs, model serving); it is live, with a slider per agent kind, working populations that ease into a new mix over six seconds, running completion counters, and CPU and memory readings that follow the recorded host trace. Study B puts the workload dial on the left and a single hardware answer on the right. Study C walks through four scenes: the work, the measurement, the sizing, and an explore step. Presets and the agent target carry across tabs. Rebuild with:

```sh
node docs/dashboard/build-layout-studies.cjs
```

The source is `layout-studies.src.html`; the current page is not changed by the studies.

## Cloudflare preview

`deploy/replay-demo` publishes the built pages as a static-assets Worker. From that directory, `npm run preview` uploads a version with its own preview URL and `npm run deploy` updates the stable one, https://xeon-replay-demo.devadmin-19b.workers.dev. Its README has the details.

## Browser check

With Playwright and Chrome available, run:

```sh
node docs/dashboard/check-steady-state.cjs
```

The check covers offline loading, three landscape sizes, all five archetype panels, recorded count and completion provenance, replay boundaries, controls, and reduced motion. Screenshots are saved in a temporary directory.
