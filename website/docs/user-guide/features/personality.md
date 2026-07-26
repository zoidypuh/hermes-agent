---
sidebar_position: 9
title: "Personality & Profile Prompts"
description: "Customize Hermes Agent's personality with composite prompt files, SOUL.md, built-in personalities, and custom persona definitions"
---

# Personality & Profile Prompts

Hermes Agent's personality is fully customizable. For current profile-based agents, the system prompt is assembled from profile-specific `soul.md` plus shared root `frontlobe.md`, `memory.md`, and `projects.md`.

- `soul.md` — durable profile persona and voice
- `frontlobe.md` — shared root behavior and executive posture
- `memory.md` — shared root standing prompt context, distinct from `memories/MEMORY.md`
- `projects.md` — shared root map of current projects and directories, appended last
- `SOUL.md` — legacy durable persona file for older prompt paths
- built-in or custom `/personality` presets — session-level system-prompt overlays
- `agent.system_prompt` — a config-level extra overlay appended on top of the baseline prompt

If you want to change who a profile is, edit that profile's `soul.md`.

## How profile fragments work

Hermes looks for a strict composite prompt across the active profile and root Hermes directory:

```text
$HERMES_HOME/soul.md
<root Hermes dir>/frontlobe.md
<root Hermes dir>/memory.md
<root Hermes dir>/projects.md
```

For the default profile, that usually means:

```text
~/.hermes/soul.md
~/.hermes/frontlobe.md
~/.hermes/memory.md
~/.hermes/projects.md
```

For a named profile, it usually means:

```text
~/.hermes/profiles/<name>/soul.md
~/.hermes/frontlobe.md
~/.hermes/memory.md
~/.hermes/projects.md
```

All four files must exist and contain content. Lowercase names are canonical, but case-only variants such as `SOUL.md` are accepted. Hermes joins profile `soul.md` + root `frontlobe.md` + root `memory.md` + root `projects.md` and treats the result as the baseline system prompt. In this mode, Hermes does not also inject the legacy identity, project-context, memory snapshot, timestamp, platform-hint, active-profile, tool, or skills prompt layers.

`agent.system_prompt` in `config.yaml`, `HERMES_EPHEMERAL_SYSTEM_PROMPT`, and `/personality` remain additive overlays. They do not replace the profile fragments; they add temporary or deployment-specific flavor on top of them.

## How SOUL.md works now

Legacy code paths can still load the older `SOUL.md` identity model. Hermes seeds a default `SOUL.md` automatically in:

```text
~/.hermes/SOUL.md
```

More precisely, it uses the current instance's `HERMES_HOME`, so if you run Hermes with a custom home directory, it will use:

```text
$HERMES_HOME/SOUL.md
```

### Important behavior

- **SOUL.md is the agent's primary identity.** It occupies slot #1 in the system prompt, replacing the hardcoded default identity.
- Hermes creates a starter `SOUL.md` automatically if one does not exist yet
- Existing user `SOUL.md` files are never overwritten
- Hermes loads `SOUL.md` only from `HERMES_HOME`
- Hermes does not look in the current working directory for `SOUL.md`
- If `SOUL.md` exists but is empty, or cannot be loaded, Hermes falls back to a built-in default identity
- If `SOUL.md` has content, that content is injected verbatim after security scanning and truncation
- SOUL.md is **not** duplicated in the context files section — it appears only once, as the identity

That makes `SOUL.md` a true per-user or per-instance identity, not just an additive layer.

## Why this design

This keeps personality predictable.

If Hermes loaded `SOUL.md` from whatever directory you happened to launch it in, your personality could change unexpectedly between projects. By loading only from `HERMES_HOME`, the personality belongs to the Hermes instance itself.

That also makes it easier to teach users:
- "Edit `~/.hermes/SOUL.md` to change Hermes' default personality."

## Where to edit it

For most users:

```bash
~/.hermes/SOUL.md
```

If you use a custom home:

```bash
$HERMES_HOME/SOUL.md
```

## What should go in SOUL.md?

Use it for durable voice and personality guidance, such as:
- tone
- communication style
- level of directness
- default interaction style
- what to avoid stylistically
- how Hermes should handle uncertainty, disagreement, or ambiguity

Use it less for:
- one-off project instructions
- file paths
- repo conventions
- temporary workflow details

Those belong in `AGENTS.md`, not `SOUL.md`.

## Good SOUL.md content

A good SOUL file is:
- stable across contexts
- broad enough to apply in many conversations
- specific enough to materially shape the voice
- focused on communication and identity, not task-specific instructions

### Example

```markdown
# Personality

You are a pragmatic senior engineer with strong taste.
You optimize for truth, clarity, and usefulness over politeness theater.

## Style
- Be direct without being cold
- Prefer substance over filler
- Push back when something is a bad idea
- Admit uncertainty plainly
- Keep explanations compact unless depth is useful

## What to avoid
- Sycophancy
- Hype language
- Repeating the user's framing if it's wrong
- Overexplaining obvious things

## Technical posture
- Prefer simple systems over clever systems
- Care about operational reality, not idealized architecture
- Treat edge cases as part of the design, not cleanup
```

## What Hermes injects into the prompt

With profile fragments, the joined lowercase files are the baseline system prompt. Hermes adds only optional overlays supplied through config, environment variables, API calls, or `/personality`.

On legacy paths, `SOUL.md` content goes directly into slot #1 of the system prompt — the agent identity position. No wrapper language is added around it.

The content goes through:
- prompt-injection scanning
- truncation if it is too large

If a required lowercase fragment is missing or empty, Hermes raises a prompt-build error listing the exact file path. Legacy `SOUL.md` still falls back to a built-in default identity when used by an older prompt path.

## Security scanning

`SOUL.md` is scanned like other context-bearing files for prompt injection patterns before inclusion.

That means you should still keep it focused on persona/voice rather than trying to sneak in strange meta-instructions.

## SOUL.md vs AGENTS.md

This is the most important distinction.

### SOUL.md
Use for:
- identity
- tone
- style
- communication defaults
- personality-level behavior

### AGENTS.md
Use for:
- project architecture
- coding conventions
- tool preferences
- repo-specific workflows
- commands, ports, paths, deployment notes

A useful rule:
- if it should follow you everywhere, it belongs in `SOUL.md`
- if it belongs to a project, it belongs in `AGENTS.md`

## Profile fragments vs `/personality`

The lowercase composite prompt is your durable baseline. Only `soul.md` is profile-specific; root `frontlobe.md`, `memory.md`, and `projects.md` are shared. `SOUL.md` is the legacy durable default personality for older prompt paths.

`/personality` is a session-level overlay that changes or supplements the current system prompt.

So:
- profile `soul.md` + root `frontlobe.md` + root `memory.md` + root `projects.md` = baseline prompt
- `SOUL.md` = legacy baseline voice
- `/personality` = temporary mode switch

Examples:
- keep a pragmatic default SOUL, then use `/personality teacher` for a tutoring conversation
- keep a concise SOUL, then use `/personality creative` for brainstorming

## Built-in personalities

Hermes ships with built-in personalities you can switch to with `/personality`.

| Name | Description |
|------|-------------|
| **helpful** | Friendly, general-purpose assistant |
| **concise** | Brief, to-the-point responses |
| **technical** | Detailed, accurate technical expert |
| **creative** | Innovative, outside-the-box thinking |
| **teacher** | Patient educator with clear examples |
| **kawaii** | Cute expressions, sparkles, and enthusiasm ★ |
| **catgirl** | Neko-chan with cat-like expressions, nya~ |
| **pirate** | Captain Hermes, tech-savvy buccaneer |
| **shakespeare** | Bardic prose with dramatic flair |
| **surfer** | Totally chill bro vibes |
| **noir** | Hard-boiled detective narration |
| **uwu** | Maximum cute with uwu-speak |
| **philosopher** | Deep contemplation on every query |
| **hype** | MAXIMUM ENERGY AND ENTHUSIASM!!! |

## Switching personalities with commands

### CLI

```text
/personality
/personality concise
/personality technical
```

### Messaging platforms

```text
/personality teacher
```

These are convenient overlays, but your profile fragments or legacy `SOUL.md` still give Hermes its persistent default personality unless the overlay meaningfully changes it.

## Custom personalities in config

You can also define named custom personalities in `~/.hermes/config.yaml` under `agent.personalities`.

```yaml
agent:
  personalities:
    codereviewer: >
      You are a meticulous code reviewer. Identify bugs, security issues,
      performance concerns, and unclear design choices. Be precise and constructive.
```

Then switch to it with:

```text
/personality codereviewer
```

## Recommended workflow

A strong default setup is:

1. Keep thoughtful profile fragments in `~/.hermes/` or `~/.hermes/profiles/<name>/`
2. Put project instructions in `AGENTS.md`
3. Use `/personality` only when you want a temporary mode shift

That gives you:
- a stable voice
- project-specific behavior where it belongs
- temporary control when needed

## How personality interacts with the full prompt

At a high level, the prompt stack includes:
1. **profile soul.md + root frontlobe.md + root memory.md + root projects.md**
2. optional system-prompt overlays such as `agent.system_prompt`, `HERMES_EPHEMERAL_SYSTEM_PROMPT`, API system instructions, or `/personality`

Legacy prompt paths use `SOUL.md` or built-in identity, tool guidance, memory/user context, skills, project context files, timestamp, platform hints, and optional overlays.

The lowercase fragments are the foundation for profile-owned agents; `SOUL.md` is the foundation for older prompt paths.

## Related docs

- [Context Files](/user-guide/features/context-files)
- [Configuration](/user-guide/configuration)
- [Tips & Best Practices](/guides/tips)
- [SOUL.md Guide](/guides/use-soul-with-hermes)

## CLI appearance vs conversational personality

Conversational personality and CLI appearance are separate:

- `SOUL.md`, `agent.system_prompt`, and `/personality` affect how Hermes speaks
- `display.skin` and `/skin` affect how Hermes looks in the terminal

For terminal appearance, see [Skins & Themes](./skins.md).
