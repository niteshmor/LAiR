# Benchmark harnesses

These scripts produce the measurements in [`../BENCHMARKS.md`](../BENCHMARKS.md).
Each loads a model (or sweeps all six) through the live stack and records one metric:

| Script | Measures |
|---|---|
| `accuracy.py`   | Auto-graded accuracy: reasoning, math, unit-tested code, tool-calling, long-context retrieval. |
| `perf_curve.py` | Decode throughput (tok/s) as the context fills. |
| `ctx_audit.py`  | Memory and speed at 128K vs 200K context. |
| `realtask.py`   | Perceived performance — output tokens and wall-clock on real tasks. |
| `research.py`   | End-to-end web research through the live SearXNG/Tor stack. |

See [`../BENCHMARKS.md`](../BENCHMARKS.md) for methodology and results, and the
`Run:` header in each file for invocation flags.

## A note on the code style

These are throwaway, single-purpose measurement harnesses — generated largely by an
LLM and kept only because they document exactly how the numbers were produced. They
are not meant as exemplary Python, and are not representative of the project's code
style.

In particular, some lines staple several unrelated statements together with `;` — a
machine-generated artifact, not a deliberate convention. (Dense *data* on a single
line is a different thing and is intentional: a test case or a model's full sampler
config per row is meant to be read as one unit. The part to overlook is the
multi-statement cramming, not the long data rows.) They run, they're reproducible,
and that is all they are for.
