<!-- standard:core v1 — copied verbatim from Kyle's repo-standard kit. Do not edit in a repo; change the kit and re-sync. -->
# Repo standard

All of Kyle's repos use this same structure so any session, human or agent, finds things the same way. The text between the `standard:core` markers is identical in every repo. Only "This repo's map" (below the closing marker) differs.

## Roles

- **README.md**: what the project is, how to install and run it. Not history, decisions, or session notes.
- **CHANGELOG.md**: what changed, per release ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)). Not design reasoning or plans.
- **Decisions record**: why things are the way they are. New repos use `DECISIONS.md`: append-only, newest last, each entry dated with the decision and the reason. A repo that already keeps decisions elsewhere maps that file in "This repo's map" instead. Not status or to-do lists.
- **AGENTS.md**: the rules for AI agents working in this repo; source of truth. Not project docs.
- **CLAUDE.md**: one line pointing to AGENTS.md. Nothing else.
- **docs/RESUME.md**: session handoff. Under 30 lines, overwritten at the end of every session. Not history.
- **docs/REPO_STANDARD.md**: this standard plus this repo's map. Repo-specific rules go in AGENTS.md, not here.
- **Reference docs** (STATUS.md, plans, manuals) are optional, stay as they are, and are linked from RESUME.md under "Read next".

## RESUME.md format

Frontmatter keys, in this order: `project`, `aliases`, `state` (active | parked | archive), `updated` (YYYY-MM-DD), `review`, `last_audit`. Headings, exactly: `# Resume`, `## Next`, `## Where I left off`, `## Checks at stop`, `## Blocked / DECISION needed`, `## Read next`. Scripts parse these; do not rename them.

## Agent rules

- At session start, read docs/RESUME.md first, then what it links under "Read next".
- Before trusting "Where I left off", check it against git: a PR it calls open may have merged since. Correct RESUME.md in your first change if it is stale.
- At the end of every session, OVERWRITE docs/RESUME.md (never append history) so a cold start works with no memory of this session.
- Update CHANGELOG.md in the same PR as any user-visible change.
- Append to the decisions record when a design decision is made. Never edit or delete past entries. Never reword Kyle's DECISION entries anywhere.
- Update README.md when setup or usage changes.
- Test counts and check results live only in docs/RESUME.md "Checks at stop". Reference docs may say a check passes, without counts, and link there.
- Kyle is the author and decides. Anything not derivable from the repo, git, or GitHub settings is "UNKNOWN (ask Kyle)". Never guess.
- Never apply this standard to an exempt repo (see Exemptions).

## Checking `review`

`review: yes` means a pull request is required to change the default branch and Kyle approves every merge. GitHub has two kinds of branch rules; check both:

1. Rulesets: `gh api repos/<owner>/<repo>/rules/branches/<default>`. A rule with `"type": "pull_request"` means yes.
2. Classic protection: `gh api repos/<owner>/<repo>/branches/<default>/protection`. `required_pull_request_reviews` means yes. A 404 here only means no classic protection; it does not mean the branch is unprotected.

If neither shows a required PR, or both calls fail, write `UNKNOWN (ask Kyle)`.

`last_audit` records only an audit with a date and a report or PR you can link. Otherwise `none recorded`. Never infer an audit from the code looking clean.

## Exemptions

UPSF repos (kewhiteh/upsf-vault, kewhiteh/upsf-cleanroom, `~/workspace/claude/upsf/*`) are exempt until the framework is fully built. They keep their own resume mechanism ("go") and state files. Agents in any repo must not add standard files to them, change how "go" works, or read across UPSF folders from inside another repo's session. Only Kyle edits this list.
<!-- /standard:core -->

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
| Check results / test counts | [RESUME.md § Checks at stop](RESUME.md). [STATUS.md § Verified](STATUS.md) says checks pass, without counts, and links there |
| Reference | [STATUS.md](STATUS.md), [instructions/BBS_V3_REFACTOR_PLAN.md](instructions/BBS_V3_REFACTOR_PLAN.md) (active plan), [instructions/BBS_REFACTOR_PLAN.md](instructions/BBS_REFACTOR_PLAN.md) (original v2 plan), [guild_file.md](guild_file.md) |
