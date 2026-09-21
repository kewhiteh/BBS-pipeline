# docs/01_PLAYBOOK.md

# Engineering Playbook & Operational Invariants

## 1. Universal Engineering Invariants

### 1.1 The Blast-Radius & Anti-Regression Invariant
- **Strict Phase Immutability:** Agents are prohibited from modifying, refactoring, or deleting code verified and committed in previous milestones unless an explicit backward-compatibility defect is verified and approved[cite: 1].
- **Additive Scope Only:** Code changes must be strictly confined to the files and components designated in the active phase backlog[cite: 1].
- **Zero-Touch Fixtures:** Baseline test fixtures and assertions must remain stable; agents cannot alter test criteria to make failing implementations pass[cite: 1].

### 1.2 Tooling, Packaging & Environment Determinism
- **Lockfile Contract:** Repository initialization (Phase 1) must generate and commit a strict lockfile using `uv` (`uv.lock` or fully pinned `requirements.txt`)[cite: 1]. Dependencies must always install in frozen/locked mode[cite: 1].
- **Deterministic CLI Commands:**
  - Environment Sync: `uv sync --frozen` or `pip install --no-deps -r requirements.lock`[cite: 1]
  - Type Verification: `mypy src/`
  - Code Formatting & Linting: `ruff check src/ tests/` and `ruff format --check src/ tests/`
  - Test Suite: `pytest -v --tb=short tests/`

### 1.3 Git Discipline & Automated Verification Gates
- **Phase 1 Repository Initialization:** Prior to authoring application logic, Phase 1 must execute `git init -b main` and establish a production `.gitignore` covering virtual environments, `__pycache__`, `.pytest_cache`, `.mypy_cache`, and OS artifacts[cite: 1].
- **Deterministic TDD & Mandatory Failure Paths:**
  - Automated tests must accompany or precede implementation logic[cite: 1].
  - Every phase test suite must include at least two explicit negative assertions validating that appropriate domain exceptions or errors are raised on invalid inputs[cite: 1].
  - No phase is complete until the test suite passes with 100% success and zero unhandled warnings[cite: 1].
- **Atomic Semantic Commits:** Every milestone concludes with an isolated Conventional Commit[cite: 1]:
  ```bash
  git status # Must be clean of untracked artifacts[cite: 1]
  pytest # Must pass completely[cite: 1]
  git add <modified_files> docs/04_TASKS.md[cite: 1]
  git commit -m "<type>(<scope>): <concise milestone description>"[cite: 1]
  ```
- **Provenance Stamping:** The export engine must query the active commit hash via `git rev-parse --short HEAD` (or a fallback environment variable) and stamp it alongside semantic versioning into final export metadata[cite: 1, 3].