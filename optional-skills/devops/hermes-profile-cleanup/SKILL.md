---
name: hermes-profile-cleanup
description: Reclaim profile storage while preserving live state.
version: 2.0.0
author: Gismar (@zoidypuh), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [profiles, cleanup, maintenance, recovery]
    category: devops
    related_skills: [hermes-home-maintenance, hermes-skills-management]
---

# Hermes Profile Cleanup Skill

Reclaim reproducible files and verified duplicate recovery copies in an existing
profile. Active identity, configuration, credentials, conversation history, and
memory remain part of the profile regardless of their size.

## When to Use

- Checkpoints or build artifacts dominate a profile's storage.
- Handoffs have created nested duplicate state directories.
- Old worktrees, caches, or backup copies need a documented retention decision.

## Prerequisites

- `terminal`, `read_file`, `search_files`, and `patch`.
- Resolve the profile through Hermes' profile commands and active launcher.
- Read `hermes-home-maintenance` for directory-copy and recovery procedures.

## How to Run

Use `terminal` with an explicitly selected profile. Start with its inventory,
process ownership, repository status, and `hermes --profile NAME checkpoints
list`. Record allocated bytes separately from logical file sizes.

## Quick Reference

| Category | Action |
|---|---|
| Expired/orphaned checkpoints | Native `checkpoints prune` with reviewed limits |
| Unreferenced PM environments | Native `hermes pm gc` after checking holders |
| Tracked ephemeral outputs | `/disk-cleanup --dry-run`, then `/disk-cleanup` |
| Clean merged Git worktree | Verify ignored files, then native Git removal |
| Unique recovery or dirty work | Archive and byte-verify before retirement |
| Active databases and settings | Retain; use supported retention commands |

## Procedure

1. Confirm the exact resolved home, launcher, active processes, and storage
   categories. A profile-local checkout may be intentionally selected by its
   launcher; directory names alone do not prove it is accidental.
2. Fix a duplicate-producing copy or backup command before clearing its output.
   Directory-content copies need an explicit trailing separator after path
   conversion. Exclude legacy nested trees from future transfers.
3. Use supported checkpoint, package-manager, and ephemeral-file cleanup APIs.
   Recheck their results and leave environments held by running processes alone.
4. For a finished worktree, inspect tracked, untracked, ignored, and unmerged
   work. Preserve a Git bundle plus patches and untracked files when needed.
5. For old histories or credential copies, isolate only the verified inactive
   subtree, archive every version, verify the archive against its source, and
   then remove the isolated source. Keep recovery data outside active state and
   outside paths recursively swept into routine backups.
6. Keep live SQLite databases and their WAL/SHM companions together. Use SQLite's
   backup API for recovery snapshots. Do not blindly vacuum or truncate files
   whose free-page count shows little reclaimable space.
7. Establish explicit retention classes: live state, recent verified recovery,
   unique historical data, and reproducible cache. Delete historical material
   only when its replacement and recovery path have actually been checked.

## Pitfalls

- Never truncate `config.yaml`, `.env`, or `auth.json` to “ensure” they exist.
- Never delete active `state.db`, session indexes, LCM/Honcho state, memories,
  personas, process locks, or cron records as generic cleanup.
- An intentionally disposable test profile is a separate task. Verify its
  launcher and identity before resetting it; never apply a minimal-profile
  recipe to a real profile.
- Do not replace shared skills with profile-local copies.
- On a full filesystem, start with proven reproducible caches or use a different
  volume for recovery; do not discard unique data to make room for its backup.

## Verification

Check expected files still exist, live services retain their prior health,
recovery archives verify, and new handoffs do not recreate nested copies.
Record actual reclaimed bytes, retained recovery paths, and any deferred items.
