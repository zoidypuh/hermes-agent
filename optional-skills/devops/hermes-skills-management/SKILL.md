---
name: hermes-skills-management
description: Maintain shared skills, archives, pins, and plugins.
version: 1.0.0
author: Gismar (@zoidypuh), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skills, curator, plugins, maintenance]
    category: devops
    related_skills: [hermes-home-maintenance]
---

# Hermes Skills Management Skill

Maintain a shared skill library through Hermes' existing curator and skill
commands. Preserve personal edits and resolve each profile's actual library
before changing files or interpreting usage.

## When to Use

- A useful skill disappeared into the archive.
- Operational prerequisites need protection from automatic archival.
- Several profiles share skills but report different usage.
- Large skill bodies need reference files or duplicate packages need review.
- A plugin installation needs reproducible provenance and runtime validation.

## Prerequisites

- `terminal`, `read_file`, `search_files`, and `patch`.
- Hermes CLI for the owning profile, with access to its skill library.
- Read the installed `shared-skill-edits` skill when the library is shared.

## How to Run

Use `terminal` to inspect `hermes curator status`, `hermes curator usage`,
`hermes skills list`, and `hermes plugins list`. Use explicit `--profile NAME`
when inspecting a secondary profile. Do not trigger a model-backed review just
to collect an inventory.

## Quick Reference

| Need | Supported command |
|---|---|
| Recovery snapshot | `hermes curator backup` |
| Restore prerequisite | `hermes curator restore NAME` |
| Protect operational skill | `hermes curator pin NAME` |
| Inspect unmanaged skills | `hermes curator list-unmanaged` |
| Adopt one reviewed personal skill | `hermes curator adopt NAME` |
| Review archived packages | `hermes curator list-archived` |
| Skill update inventory | `hermes skills check` |
| Plugin update inventory | `hermes plugins check-updates` |
| Validate backend plugin | `hermes plugins doctor NAME --ci` |
| Export pinned plugin set | `hermes plugins pack export --enabled-only` |

## Procedure

1. Resolve symlinks and `skills.external_dirs` for every consuming profile.
   Identify one canonical package. Never make a second independent copy to fix
   visibility in another profile.
2. Take a curator backup before modifying packages. Compare usage sidecars from
   every consumer: the curator's local idle count alone does not prove a shared
   skill is unused.
3. Restore missing prerequisites before running dependent skills. Pin critical
   operational skills in each consuming profile's usage store. Keep automatic
   archive deletion disabled. For shared libraries, leave bundled-skill pruning
   off unless combined usage supports a deliberate change.
4. Keep unmanaged personal skills unmanaged unless their ownership and lifecycle
   are understood. Adopt reviewed skills individually; never blanket-adopt the
   entire library.
5. Split large guides at coherent section boundaries. Keep task selection,
   required constraints, and the short procedure in `SKILL.md`. Link supporting
   detail under `references/`; retain all prior content in a recovery snapshot.
   Verify code fences, relative links, and referenced scripts after moving text.
6. Compare entire packages before retiring archive duplicates: scripts, assets,
   references, and symlink destinations count. Identical `SKILL.md` alone is
   insufficient evidence. Preserve unique versions in the archive.
7. Prefer curated, commit-pinned plugin installs. Validate the actual surface:
   backend doctor for Python plugins, packaged entry point and Desktop discovery
   for frontend-only plugins. Keep exported packs free of credentials and local
   private configuration.
8. Record actions, recovery locations, tests, and unresolved conditions on the
   owning Kanban board. New skills become available at the next conversation;
   do not rebuild a running conversation's system prompt.

## Pitfalls

- A skill archive is recovery data, not an automatic deletion queue.
- Shared files do not imply shared usage telemetry or curator ownership.
- Pinning a dependent skill does not restore or pin its prerequisites.
- Shortening a lazily loaded skill does not necessarily reduce startup tokens.
- A frontend-only plugin may legitimately have no Python registration function.
- Installing a workflow does not authorize launching its external operations.

## Verification

For the pinned Desktop observability set, read
`references/desktop-observability.md` and inspect
`references/observability-pack.yaml` before installation.

Confirm restored packages load from the intended canonical paths, required
references resolve, pins appear in every consuming usage store, and Desktop
halves are discoverable. Re-run the appropriate inventory without starting a
conversation or a scheduled task. Report any login or host access still needed.
