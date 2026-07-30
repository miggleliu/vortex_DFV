"""
ILLUSTRATIVE / HYPOTHETICAL ONLY -- not measured data.

Two-panel model reflecting the corrected three-tier framework from the
frontdoor-vs-DFV discussion (see memory/frontdoor_vs_dfv.md):

  Panel A -- race already has a DFV hook (dcache_fill_rsp x dcache_core_req):
    DFV's incremental cost really is near-zero (a CSR combo, ~8 lines).
    Frontdoor's cost is a near-total LUMP SUM (deriving the bank-select
    formula + building the parametrized sweep harness) -- NOT a smooth
    function of N. Once paid, N (sweep breadth) is a free runtime dial
    (-n/-t flags, no code change) up to the point where samples stop being
    genuinely concurrent.

    Concurrency ceiling is REAL, not guessed: default VX_config.vh has
    NUM_CLUSTERS=1, NUM_CORES=1, NUM_WARPS=4, NUM_THREADS=4 -> 16 threads
    truly resident/concurrent at once. Beyond that, vx_spawn_threads still
    handles extra threads for free (no new code -- it already schedules more
    threads than hardware slots via sequential warp waves), but those extra
    samples come from sequential waves within the SAME launch, not a wider
    simultaneous window, and pipeline/DRAM state carries over between waves
    (less clean independence between samples).

  Panel B -- race needs a hook that doesn't exist yet (per race_conditions.md
  "Need new gates" list, e.g. MSHR replay x core req):
    DFV's incremental cost is no longer ~free -- it needs a new
    VX_dfv_req_gate instantiated + wired through the hierarchy + a new CSR +
    a counter + re-verification -- but it REUSES existing primitives/pattern
    (extending a template), so it's bounded, not from-scratch.
    Frontdoor for a race with NO software-observable consequence at all
    (e.g. ALU-commit vs LSU-commit arbiter starvation -- race_conditions.md
    notes the arbiter is stateless, no functional impact) is not just
    expensive, it's CATEGORICALLY INFEASIBLE: no amount of TDT lets a
    software-only test detect an event with no ISA-visible effect. Marked
    as an infeasible point off-scale, not a low-probability point.

All numeric placements (LOC estimates, DFV "effort units", frontdoor's p(N))
are illustrative model values carried over from the earlier plot / discussion,
not measurements. The concurrency numbers (16 threads) and the "arbiter is
stateless" fact ARE grounded in real config/RTL notes.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

W = 4025.0  # measured DRAM latency window, cycles
CONCURRENT_CEILING = 16  # NUM_CLUSTERS(1) x NUM_CORES(1) x NUM_WARPS(4) x NUM_THREADS(4), VX_config.vh defaults

def hit_prob(n):
    return 1 - (1 - 1.0 / W) ** min(n, W)

fig, (axA, axB) = plt.subplots(1, 2, figsize=(13, 5.8))

# ---------------------------------------------------------------- Panel A
dfv_existing = {"loc": 8, "p": 0.999, "label": "DFV: existing hook\n(CSR combo only)"}
fd_lump = {"loc": 150, "p": hit_prob(64), "label": "Frontdoor: sweep built\n(N=64, one-time lump)"}

ns_free = [1, 4, 16, 64, 256, 1024, 4000]
ps_free = [hit_prob(n) for n in ns_free]

axA.plot([fd_lump["loc"]] * len(ns_free), ps_free, "o--", color="tab:orange",
          label="N swept at FIXED TDT\n(free runtime dial)")
for n, p in zip(ns_free, ps_free):
    marker = "s" if n <= CONCURRENT_CEILING else "o"
    axA.plot(fd_lump["loc"], p, marker=marker, color="tab:orange")
axA.annotate(f"N={CONCURRENT_CEILING}\n(concurrency ceiling:\n1 core x 4 warps x 4 threads)",
             (fd_lump["loc"], hit_prob(CONCURRENT_CEILING)), textcoords="offset points",
             xytext=(10, -25), fontsize=7.5, color="tab:orange")
axA.annotate("N=4000\n(sequential warp waves\nbeyond ceiling -- still free,\nbut less-independent samples)",
             (fd_lump["loc"], hit_prob(4000)), textcoords="offset points",
             xytext=(10, 4), fontsize=7.5, color="tab:orange")

axA.scatter([dfv_existing["loc"]], [dfv_existing["p"]], color="tab:blue", s=110, zorder=5)
axA.annotate(dfv_existing["label"], (dfv_existing["loc"], dfv_existing["p"]),
             textcoords="offset points", xytext=(10, -4), fontsize=8.5, color="tab:blue")

axA.set_yscale("log")
axA.set_xscale("log")
axA.set_xlim(3, 400)
axA.set_xlabel("TDT proxy (effort, log scale)")
axA.set_ylabel("Per-run hit probability p (log scale)")
axA.set_title("Panel A: race already has a DFV hook\n(dcache_fill_rsp x dcache_core_req)", fontsize=10.5)
axA.grid(True, which="both", alpha=0.3)

# ---------------------------------------------------------------- Panel B
dfv_new_hook = {"loc": 60, "p": 0.995, "label": "DFV: new hook needed\n(extends existing gate/CSR template)"}
fd_infeasible_x = 500  # placed off-scale intentionally

axB.scatter([dfv_new_hook["loc"]], [dfv_new_hook["p"]], color="tab:blue", s=110, zorder=5)
axB.annotate(dfv_new_hook["label"], (dfv_new_hook["loc"], dfv_new_hook["p"]),
             textcoords="offset points", xytext=(-90, -28), fontsize=8.5, color="tab:blue")

axB.scatter([fd_infeasible_x], [3e-4], marker="x", s=160, color="tab:red", zorder=5)
axB.annotate("Frontdoor: INFEASIBLE\n(no ISA-visible consequence --\ne.g. ALU-commit vs LSU-commit\narbiter is stateless per\nrace_conditions.md; no amount\nof TDT can detect it in software)",
             (fd_infeasible_x, 3e-4), textcoords="offset points",
             xytext=(-175, 12), fontsize=8, color="tab:red")

axB.set_yscale("log")
axB.set_xscale("log")
axB.set_xlim(10, 1000)
axB.set_ylim(1e-4, 2)
axB.set_xlabel("TDT proxy (effort, log scale)")
axB.set_title("Panel B: race needs a hook that\ndoesn't exist yet (e.g. MSHR replay x core req)", fontsize=10.5)
axB.grid(True, which="both", alpha=0.3)

fig.suptitle("TDT vs hit-rate, corrected three-tier model -- ILLUSTRATIVE, NOT MEASURED DATA", fontsize=12)
fig.text(0.5, 0.01,
         "Grounded facts used: VX_config.vh defaults (NUM_CORES=1, NUM_WARPS=4, NUM_THREADS=4 -> 16-way concurrency); "
         "race_conditions.md (ALU/LSU commit arbiter is stateless). All LOC/effort/probability placements are illustrative.",
         ha="center", fontsize=7, style="italic")

fig.tight_layout(rect=[0, 0.05, 1, 0.93])
fig.savefig("/home/boxiliu2/research/vortex/tests/regression/frontdoor_race/illustrative_tdt_trt.png", dpi=150)
print("saved")
