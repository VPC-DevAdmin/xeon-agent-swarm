# Updating and sharing the agent-capacity paper

The paper uses the v2.3 results recorded in the September 8 methodology and the chosen 3,500-output-token-per-second GPU assumption. The headline is 143 resident agents per CPU, 85% CPU utilization, and 1.6 GPU equivalents. The Task diagram and its four model calls describe the direct handoff.

The result record identifies the source commit and seed. The capacity ledgers support 355 GB of memory used, peaks near 430 GB, and the CPU-family stack. `capacity-measurement-check.md` records the sample windows, grouping, rounding, and model-demand calculation.

## Results update

1. Edit `paper-results.json`. Keep platform, results, workload definitions, and model-serving assumptions separate. Replace the source paths with the final evidence records.
2. Check the agent definitions before treating this as a numbers-only update. In particular, confirm Task model calls and tokens against the revised direct handoff. A changed workflow also requires changes to the prose and Figures 3 and 7.
3. Run `node update-paper.cjs` from this folder. This refreshes the static result bindings, Figure 5 rows and normalized bar lengths, both response charts, accessibility labels, GPU equivalents, the mix weight, and the evidence companion. No JavaScript or separate data download is needed to read the resulting paper.
4. Run `node update-paper.cjs --check`. It detects stale generated values and changed evidence snapshots. It is a consistency check, not an independent validation of the supplied results.
5. Review the paper at desktop and phone widths, with reduced motion, without JavaScript, and as an A4 print proof. Check chart-label collisions when the response curves change. A missing response stays missing rather than being plotted as zero.

Text between `<!--result:...-->` and `<!--/result-->` is generated. Edit the result record rather than those spans. Prose outside the markers remains directly editable in the HTML. The update command does not fetch data or run a benchmark.

Ratios use CPU sockets and GPU devices. The three R770 servers and eight-GPU XE7740 remain a deployment illustration, not a dynamically sized configuration. Reassess its caption against the final ratio. A different processor, memory configuration, model, GPU, or cache assumption also needs an editorial check of the platform and source notes.

Figure 2’s stack shows shares of attributed CPU work, separate from the utilization headline. Its jar shows the twelve-request incoming mixture, not the exact resident population. Component-level CPU totals are absent from Figure 4 until corresponding evidence is available.

## Distribution

Distribute `agent-capacity-whitepaper.html` and `evidence.html` together. Images and fonts are embedded in the paper. The companion contains escaped, readable snapshots of the methodology, configuration, and selected evidence records, with SHA-256 hashes. It avoids links that depend on the reader having a repository checkout.

Keep `paper-results.json` and `update-paper.cjs` with the editable source. They are not required to read the published paper. The evidence companion is technical supporting material and is not intended to be appended to the main printout.

No files have been published externally. Before external release, review the evidence companion and check the run records against the paper’s result record. The source series is `data/capacity/series-12001-20260908-165351`. The portable companion includes the measurement check, while the full raw files remain in the repository.
