# Repo standard

All of Kyle's repos use this same structure so any session, human or agent, can find things the same way.

## Roles

- **README.md**: what the project is, how to install and run it. Not history or decisions.
- **CHANGELOG.md**: what changed, per release (Keep a Changelog). Not design rationale.
- **DECISIONS.md**: why things are the way they are. Append-only, newest last: date, decision, reason. Never edit past entries. Not status or to-dos.
- **AGENTS.md**: the rules for AI agents working in this repo. Source of truth. Not status.
- **CLAUDE.md**: one line pointing to AGENTS.md. Nothing else.
- **docs/RESUME.md**: session handoff. Short (under 30 lines), overwritten at the end of every session. Not history.
- **docs/REPO_STANDARD.md**: this standard, plus this repo's file map.
- Optional reference docs (STATUS.md, plans, manuals) stay as they are and are linked from RESUME.md under "Read next".

## Agent rules

- At session start, read docs/RESUME.md first, then what it links under "Read next".
- At the end of every session, OVERWRITE RESUME.md (never append history) so a cold start works with no memory of this session.
- Update CHANGELOG.md in the same PR as any user-visible change.
- Append to the decisions record when a design decision is made. Never edit or delete past entries. Never reword Kyle's DECISION entries anywhere.
- Update README.md when setup or usage changes.
- Test counts and "complete/sealed" claims live in one place only: the repo's existing verification doc, or RESUME.md "Checks at stop". Nowhere else.
- Never apply this standard to an exempt repo (see Exemptions).

## Checking `review`

`gh api repos/<owner>/<repo>/branches/<default>/protection` only reports classic branch protection and returns 404 when a repo uses rulesets. Also run `gh api repos/<owner>/<repo>/rules/branches/<default>`; a `pull_request` rule there means PRs are required (`review: yes`). This repo uses a ruleset.

## This repo's map

| Role | File in this repo |
|---|---|
| README | [README.md](../README.md) |
| CHANGELOG | [CHANGELOG.md](../CHANGELOG.md) |
| DECISIONS | No DECISIONS.md. Mapped to [STATUS.md § Decisions](STATUS.md) and the decision table in [instructions/BBS_V3_REFACTOR_PLAN.md § 5](instructions/BBS_V3_REFACTOR_PLAN.md) |
| AGENTS | [AGENTS.md](../AGENTS.md) |
| CLAUDE | [CLAUDE.md](../CLAUDE.md) |
| RESUME | [RESUME.md](RESUME.md) |
| REPO_STANDARD | this file |
| Verification doc (test results) | [STATUS.md § Verified](STATUS.md) |
| Reference | [STATUS.md](STATUS.md), [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md) (active plan), [instructions/BBS_REFACTOR_PLAN.md](instructions/BBS_REFACTOR_PLAN.md) (original v2 plan), [guild_file.md](guild_file.md) |

## Exemptions

UPSF repos (kewhiteh/upsf-vault, kewhiteh/upsf-cleanroom, ~/workspace/claude/upsf/*) are exempt until the framework is fully built. They keep their own resume mechanism ("go") and state files. Agents in any repo must not add standard files to them, change how "go" works, or read across UPSF folders from inside another repo's session. Only Kyle edits this list.
