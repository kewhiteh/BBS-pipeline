# AGENTS.md

# Universal Agent Directives: USGS Breeding Bird Survey (BBS) Pipeline

You are an automated implementation engine executing a deterministic build specification for the USGS BBS Pipeline[cite: 2, 3].
Before taking any action, modifying files, or generating code, read and adhere strictly to the repository blueprints:

1. Universal Engineering Invariants: `docs/01_PLAYBOOK.md`[cite: 2]
2. Architecture, Schemas & Toolchain Whitelist: `docs/02_ARCHITECTURE.md`[cite: 2]
3. Upstream Schemas, Formats & Domain Models: `docs/03_SCHEMAS.md`[cite: 2]
4. Phased Milestone Roadmap & Task Verification: `docs/04_TASKS.md`[cite: 2]
5. Negative Boundaries & Domain Invariants: `docs/05_CONSTRAINTS.md`[cite: 2]
6. Step-by-Step Operator Runbook: `docs/06_OPERATOR_RUNBOOK.md`[cite: 2]

---

### Hard Operational Boundaries:
- **Profile A Data Invariant (The Arithmetic Principle):** Only mathematical operands are numeric[cite: 1, 3]. All identifiers, codes, state/route numbers, and dates must be zero-padded `pl.String`[cite: 1, 3]. `pl.Object` is banned[cite: 1].
- **Zero-Disk In-Memory Mandate:** Under no circumstances may raw archives, unpacked CSVs, temporary files, intermediate Parquet caches, DuckDB files, or SQLite databases touch the local disk during network streaming, ingestion, zero-filling, or transform routines[cite: 1, 3]. Intermediate operations must strictly utilize RAM buffers (`io.BytesIO`) wrapped in context managers[cite: 1, 3]. Disk writes are restricted solely to the final export command[cite: 1, 3].
- **Tool Lock:** Pandas is strictly banned from ingestion, transformation, filtering, and zero-filling routines[cite: 3]. Polars (`polars>=0.20.0,<1.0.0`) must be used exclusively[cite: 3]. Pandas/GeoPandas is permitted solely at the final boundary serialization for OGC GeoPackage output[cite: 3].
- **Zero Schema Inference:** Polars schema inference is prohibited[cite: 3]. All CSV reads must supply an explicit `schema_overrides` dictionary[cite: 1, 3].
- **Sequence Lock:** Execute tasks strictly in the order listed in `docs/04_TASKS.md`[cite: 2]. Never skip ahead[cite: 2].
- **Blast-Radius Lock:** Modify only files designated in the active phase prompt[cite: 1, 2]. Do not refactor previous milestones[cite: 1].
- **Ambiguity Halt:** If a specification is ambiguous or incomplete, halt immediately and prompt the operator[cite: 2].