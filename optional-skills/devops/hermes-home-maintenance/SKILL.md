---
name: hermes-home-maintenance
description: Maintain Hermes homes while preserving live state.
version: 1.0.0
author: "Gismar (@zoidypuh), Hermes Agent"
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [hermes, maintenance, backups, profiles, handoff]
    category: devops
---

# Hermes Home Maintenance Skill

Inspect a Hermes home, repair directory handoffs and reclaim verified redundant
material. Configuration, credentials, memory, transcripts and unpublished source
work are retained unless their removal is explicitly part of the request.

## When to Use

Use for repeated session directories, large recovery archives, obsolete
worktrees or a profile that has accumulated temporary development environments.

## Prerequisites

- Resolve the intended home with `hermes config path`.
- Use `terminal` and `read_file` to inspect the local installation and readers.
- Directory handoffs require rsync on both participating hosts.
- Review the owning repository instructions before modifying tracked files.

## How to Run

Use `scripts/state_copy.py` from a handoff helper to construct directory-content
copies. Execute the returned argument list directly through `terminal` or the
helper's subprocess runner; do not join it into a shell command.

```python
from state_copy import directory_copy_command

argv = directory_copy_command(
    source, destination, exclude=("/session/",), delete=True,
)
```

For remote paths, pass `host:/absolute/directory`. The helper supplies the
trailing slash required by rsync even when pathlib removed it.

## Quick Reference

| Need | Existing interface |
|---|---|
| Checkpoint inventory | `hermes checkpoints status` |
| Bounded checkpoint pruning | `hermes checkpoints prune` |
| Managed environment collection | `hermes pm gc` |
| Session totals | `hermes sessions stats` |
| Preserve selected history | `hermes sessions export` |
| Inspect tracked ephemeral output | `/disk-cleanup dry-run` |
| Inspect shared skills | `hermes curator status` |

## Procedure

1. Record allocated disk usage, symlink boundaries and the active profile.
   Logical file bytes differ from physical usage, especially for tiny files.
2. Identify owners before moving state. A directory name, old timestamp or
   empty lock file does not establish inactivity.
3. Correct the producer before deduplicating its output. Test repeated copies
   into an already-existing destination, not only the first copy.
4. For WhatsApp auth, exclude `/session/` when copying the contents of the
   actual session directory. For legacy transcripts exclude `/sessions/`.
   Excluded destination trees remain protected from `--delete`.
5. Compare nested variants by path and content. Preserve unique versions;
   equal filenames do not imply equal credentials or transcript history.
6. Retain a recoverable archive and manifest before retiring a candidate tree.
   Validate the archive before modifying its source.
7. For worktrees inspect dirty/untracked/ignored files and commits outside
   the integration branch. Use Git's worktree removal, followed by PM collection.
8. Use each subsystem's maintenance command for checkpoints, environments and
   history. Record what was removed and the actual allocated-space change.

## Pitfalls

- `str(Path("session/"))` is `"session"`: rsync then copies the directory,
  rather than its contents, into an existing destination.
- Never truncate existing config.yaml, .env or auth.json to ensure they exist.
- A completed Kanban card can still have a dirty worktree.
- A large state database may have almost no free pages to reclaim.
- Do not enable a periodic cleanup job just because a one-time audit was asked.
- Keep changes to prompt-affecting configuration at fresh-session boundaries.

## Verification

Verify a second handoff does not introduce another directory level. Retained
session history must remain searchable, active credentials must remain usable,
and preserved source patches must match their recorded hashes.
