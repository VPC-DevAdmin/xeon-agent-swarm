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

The deployment graphic shows three dual-socket R770 systems and one eight-GPU XE7740. All replay readings remain scoped to one CPU. The throughput note below the hardware opens the model-serving calculation.

Reduced-motion preferences start the demo paused, with decorative animation disabled. Playback also stops advancing while the browser tab is hidden.

## Browser check

With Playwright and Chrome available, run:

```sh
node docs/dashboard/check-steady-state.cjs
```

The check covers offline loading, three landscape sizes, all five archetype panels, recorded count and completion provenance, replay boundaries, controls, and reduced motion. Screenshots are saved in a temporary directory.
