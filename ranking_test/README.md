# Ranking postprocessing test fixture

This folder is a standalone 25-record test setup for the Melder enroll ranking postprocessor. It includes:

- `ranking_test_data.csv` — five synthetic client groups, each with an exact duplicate pair plus progressively weaker variants.
- `systemcode_hierarchy.json` — the same ranking threshold and priority list used by the current test setup.
- `ranking_test_config.yaml` — isolated Melder config, cache, state/WAL, and output paths.
- `run_ranking_test.py` — submits all 25 fixture records to a running Melder enroll server, saves the raw API response, and invokes the local postprocessor.
- `rank_enroll_edges.py` — a copy of the ranking postprocessor.

SystemCodes in the fixture are drawn only from the configured hierarchy. Each client group contains an exact duplicate pair; same-group records include different code-priority/count combinations to exercise the secondary ranking rules. WCIS IDs are distinct within the allowed numeric range.

## Run

Run these commands from the repository root.

Start a **fresh** server with this fixture's config in the first PowerShell terminal:

```powershell
.\target\release\meld.exe enroll --config test-data\ranking-test\ranking_test_config.yaml --port 8090
```

The config omits `dataset:`, so enroll-mode starts with an empty pool. Then, in a second terminal, submit and rank the fixture:

```powershell
py test-data\ranking-test\run_ranking_test.py
```

If you started the test server on another port, pass its URL, e.g.
`--base-url http://localhost:8091`.

The runner writes:

- `ranking_test_enroll_response.json` — raw Melder response.
- `ranking_test_ranked_response.json` — response with edges annotated and ranked.
- `ranking_test_ranked_matches.csv` — flat view with score, ranking basis, best SystemCode, hierarchy-code count, and WCIS ID.

High-score edges (`score >= melder_score_threshold`) sort by Melder score first; exact score ties then use SystemCode hierarchy priority, distinct in-hierarchy code count, and ascending WCIS ID. Lower-score edges use hierarchy priority, distinct in-hierarchy code count, ascending WCIS ID, then score.

The config points at `state_fresh/`, an isolated WAL path. If you want to rerun against a modified fixture without replaying prior test enrollments, remove that state folder before starting the server again. Stop the server before restarting it.
