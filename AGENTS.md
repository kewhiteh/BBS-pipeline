# Universal Agent Directives: USGS Breeding Bird Survey (BBS) Pipeline v2.0

You are an automated implementation engine executing an additive build specification (Track B) on the USGS Breeding Bird Survey (BBS) Pipeline repository.
Before taking any action or generating code, read and adhere strictly to the following blueprints:
1. Engineering Invariants & Track B Protocols: `docs/01_PLAYBOOK.md`
2. System Architecture & Approved Toolchain Manifest: `docs/02_ARCHITECTURE.md`
3. Ground-Truth Schemas & Data Contracts: `docs/03_SCHEMAS.md`
4. Sequential Milestone Backlog & Verification Gates: `docs/04_TASKS.md`
5. Negative Boundaries & Domain Invariants: `docs/05_CONSTRAINTS.md`
6. Phased Operator Execution Deck: `docs/06_OPERATOR_RUNBOOK.md`

### Hard Operational Boundaries:
- **Track B Additive Invariant:** You are operating on a verified, production codebase tagged at `v1.0.0`. Baseline modules and tests are strictly read-only.
- **Blast-Radius & Anti-Regression Lock:** The following files are immutable and must NEVER be modified:
  - `src/bbs_pipeline/client/parser.py`
  - `src/bbs_pipeline/client/sciencebase.py`
  - `src/bbs_pipeline/core/discovery.py`
  - `src/bbs_pipeline/core/zero_fill.py`
  - `src/bbs_pipeline/ingestion/routes.py`
  - `src/bbs_pipeline/processing/aggregation.py`
  - `tests/test_routes_ingestion.py`
  - `tests/test_aggregations.py`
- **Zero-Touch Test Fixtures:** Established unit and integration tests from release v1.0.0 must remain 100% green without modification throughout this upgrade.
- **Branch Lock:** Never commit directly to `main`. Every milestone task must be implemented on a dedicated feature branch: `feature/phase-<N>-<milestone-name>`.
- **Tool Lock:** Adhere strictly to the Approved Toolchain Manifest in `docs/02_ARCHITECTURE.md`. Do not import or install unlisted libraries. Polars and Shapely are enforced. Pandas is restricted strictly to terminal vector export inside GeoPandas.
- **Sequence Lock:** Execute tasks strictly in the order listed in `docs/04_TASKS.md`. Never skip ahead.
- **Zero-Disk In-Memory Mandate:** All multi-archive extraction, intermediate data structures, and spatial operations must reside in RAM using `io.BytesIO`. Zero raw zip files, intermediate CSVs, SQLite/DuckDB databases, or temporary Parquet caches written to disk.
- **Ingestion Boundary Isolation:** Automatic schema inference is strictly prohibited (`infer_schema_length=0`). All columns at raw parser boundaries must default to `pl.String`. Coercion to numeric types (`pl.Int32`, `pl.Float64`) is deferred downstream with `strict=False`.
- **Scope-Restricted Zero-Filling:** Zero imputation (`0`) is legally restricted to confirmed historical breeding species for surveyed stops $1 \dots \text{TotalStops}$. Stops beyond survey completion ($k > \text{TotalStops}$) must remain native `NULL`.
- **Linear Referencing Invariant:** Routes are modeled as 24.5-mile transects with 50 discrete stops at exact 0.5-mile (804.672 m) intervals. Stop 1 is anchored at $0.0\text{ mi}$; Stop 50 is anchored at $24.5\text{ mi}$. Distances must be monotonically non-decreasing.
- **Two-Tier Spatial Filtering:** Spatial boundaries must be evaluated via Tier 1 route bounding-box pruning (STRtree) followed by Tier 2 vectorized point-in-polygon stop containment.
- **Diagnostic Visibility:** Generic `try...except` blocks that swallow errors are strictly banned. All errors must be logged with full tracebacks to `stderr`.
- **Lint & Format Lock:** All code must pass `ruff check src/ tests/` and `ruff format --check src/ tests/` with zero errors or unhandled warnings before merging.
- **Ambiguity Halt:** If a specification is ambiguous or incomplete, halt immediately and prompt the human operator.