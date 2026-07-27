# Hermes Agent upstream sync and local patch handoff

Started: 2026-07-26 UTC

Final upstream verification: 2026-07-27 UTC

## Run this branch

The supported local runtime is:

- Branch: `patches/gismar-runtime-20260726`
- Upstream base: `b792bd0529ca21bde168b17e9a00ca8dad992b90`
- Code tip before this handoff-only commit: `b654d900153d270a1345d6381e523860a05ed31b`
- Tracking branch: `origin/patches/gismar-runtime-20260726`
- Installed checkout: `/home/gismar/.hermes/hermes-agent`
- Virtual environment: `/home/gismar/.hermes/hermes-agent/.venv`

Do not run either isolated memory feature branch by default. They are clean,
tested candidates, but were intentionally kept out of the aggregate runtime.

The requested voice-duel fast-finalization patch was not implemented or
started. It remains the next isolated branch after this baseline.

## Remotes and upstream state

- Official upstream fetch: `upstream https://github.com/NousResearch/hermes-agent.git`
- Official upstream push: disabled
- Gismar fork fetch/push: `origin git@github.com:zoidypuh/hermes-agent.git`
- Latest official `upstream/main`: `b792bd0529ca21bde168b17e9a00ca8dad992b90`
- Clean local `main`: `b792bd0529ca21bde168b17e9a00ca8dad992b90`
- Published fork `origin/main`: `b792bd0529ca21bde168b17e9a00ca8dad992b90`
- Previous local/fork `main`: `bb7ff7dc302cbcbe41cf6bc09424ffc9fb2d062f`

Fork `main` moved from `bb7ff7dc3` through `136f8dab6` to `b792bd052` by
normal fast-forwards. Upstream advanced by four commits during the final
audit, so all three patch branches were backed up again, rebased onto
`b792bd052`, retested, and published with explicit force-with-lease. No blind
force push was used.

## Final patch branches

### Aggregate runtime branch

`patches/gismar-runtime-20260726`, based directly on latest upstream:

1. `5c416b6d9354a5b09548229198cea4139f422231` — narrow the
   deception-hide verification false positive while retaining concealment
   detection.
2. `5d814f68d2e9881a2f174b88f023852821af8f15` — bound generic recalled
   memory, remove provider headings, and label it as stale/non-user-authored.
3. `b654d900153d270a1345d6381e523860a05ed31b` — make one-shot requests
   honor configured reasoning, service-tier/fast-mode, and runtime request
   overrides.

These are the only runtime patches installed locally.

### Isolated coherent feature branches

- `feature/hindsight-summary-retention-20260726`
  - Tip: `97f0c1153df3a53c44fc667bcca62ef0380704d2`
  - Adds Hindsight summary retention and recall controls.
  - Published to the fork; not merged into the aggregate branch.
- `feature/mem0-safe-local-runtime-20260726`
  - Tip: `ccf1cfaac196fdfe1dc1761fef30353223877df7`
  - Adds safe local Mem0 runtime controls, local REST support, candidate
    quarantine/audit, explicit-only injection, and recency guardrails.
  - Uses `get_hermes_home()` for profile-safe paths.
  - Published to the fork; not merged into the aggregate branch.

## Durable recovery material

Recovery directory:

`/home/gismar/.hermes/hermes-agent-recovery/20260726T235731Z`

Artifacts:

| Artifact | SHA-256 | Notes |
|---|---|---|
| `local-fork-pre-cleanup.bundle` | `a97cb95992750bd30cc37970169faff718ead37bcf920c9b491069a1128f399b` | Complete verified Git history, 90 refs |
| `current-index.patch` | `0bc30bfbaf6bda9bd8d2ca02c45c555c2d5a6d49bb434bc1e27a6890b5040c2b` | Original 40-file staged WIP |
| `current-worktree.patch` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | Empty because all tracked main-worktree changes were staged |
| `current-untracked.tar.gz` | `0f9c6be4bd74e2f24d204067409431aaa0f7a72bc4acd680d552e648de2d090d` | Five original untracked files |
| `lcm-worktree.patch` | `3e547ec1243cc28f4d6b33325fa436c5d4244b8e3120f4945846d3fd210a47bb` | Original LCM worktree dirt |
| `mem0-review-worktree.patch` | `24f5efdd9f2b0c3630f62c2f3d4c1014e4760d4398c7fc452389ddeb9b45404d` | Original Mem0 review worktree dirt |
| `voice-worktree.patch` | `735b1519c9b432584884ec76702f686b1cf5f0bce3dc5a1a0826c8b2a7a60326` | Original voice worktree dirt |

The bundle verifies as complete. Every patch artifact passed an applicability
check against its original clean base before mutation.

### Pushed safety refs

All of these exist locally and on `origin`:

| Ref suffix under `safety/20260726T235731Z/` | SHA |
|---|---|
| `current-pre-cleanup` | `b8880f124537acc5a6215718dd154eadc5af1515` |
| `main-pre-cleanup` | `bb7ff7dc302cbcbe41cf6bc09424ffc9fb2d062f` |
| `fork-main-pre-cleanup` | `bb7ff7dc302cbcbe41cf6bc09424ffc9fb2d062f` |
| `lcm-worktree-pre-cleanup` | `00a33a5c887576b83d8229005d33ba35ca0eb9d7` |
| `mem0-review-worktree-pre-cleanup` | `4db85fcefb9322e4f42df6a20dfadeb730d120a9` |
| `voice-worktree-pre-cleanup` | `00546684171bb426d778fcc3a682c896a48a1896` |
| `stash-current` | `f5302a7798f694ba17e45b7106875b1134cc9778` |
| `stash-voice` | `a8665b854602ed06a3ae16f536665fae94983ca0` |
| `stash-mem0-review` | `16f384560bcfb343ae9024e752851676dd3d8450` |
| `stash-lcm` | `e20fdeca68b6efabf6425060a9a38ed7251e1626` |
| `existing-stash-0` | `917bbb9e38de03f32d1905aa662727d658864da3` |
| `existing-stash-1` | `d6a6d57d1b5b903f16cad5d74cb453352ea8bd2e` |
| `existing-stash-2` | `912d89eb4d373b811fa07ac5d6f4a87774758f90` |
| `existing-stash-3` | `f4fa1df80824a57c7480fa32241efaff59ad1a06` |
| `existing-stash-4` | `c72788b57e2c7750ae8c4a83850f32b690564a20` |
| `existing-stash-5` | `4a2b4cea0c814e8d72737a1437fcb966121fa7e7` |

The ten stash entries remain in the local stash list as a second recovery
route.

### Final-sync safety refs

Upstream moved after the first final audit. Before rebasing again, these refs
were created and pushed under `safety/20260727T001429Z/`:

| Ref suffix | SHA |
|---|---|
| `aggregate-pre-final-sync` | `dc9282aaa91e39ca5aa6669b9f55982447afa66d` |
| `hindsight-pre-final-sync` | `4d761954f2b968fb8f94ed72fe9d80ce93960223` |
| `mem0-pre-final-sync` | `a45d2103873a909cd2a1d0ff897317d517293d8c` |
| `main-pre-final-sync` | `136f8dab6709ac1a9caf8aade60446dd8cbab7a9` |

### Pushed archive refs

| Ref under `archive/20260726/` | SHA | Purpose |
|---|---|---|
| `current-staged-wip` | `f5302a7798f694ba17e45b7106875b1134cc9778` | Full original staged/untracked WIP |
| `historical-fork-aggregate` | `24b13f2b98b5464434ed9a63c3065c4ce19c9b11` | Latest mixed historical fork stack |
| `historical-runtime-stack` | `6ca1cb97c2e9e3036defff0e2ab88eb82f9c0f1b` | Proxy/vision/runtime stack |
| `historical-voice-stack` | `6f755e69eb3a68fbc78d4cc0e16f1156de2585f2` | Voice relay/TTS/STT stack |
| `historical-external-voice-ingress` | `00546684171bb426d778fcc3a682c896a48a1896` | External transcript ingress |
| `historical-honcho-stack` | `e9e439af6afe3aa58627cbc8485e718e290a892d` | Honcho filtering/audit stack |
| `historical-mem0-stack` | `ddb3d56e9b6fb10ad03222084f9863a1eea54388` | Historical Mem0 stack |
| `historical-codebase-course` | `5f342839b151321bb23e67be30a0b666ddedc954` | Generated course/minimal-prompt stack |
| `tool-approval-wip` | `7216c744bc733c3a60a77a00e7673a845950a7a4` | Old approval-hook WIP |
| `lcm-preflight-pre-upstream` | `00a33a5c887576b83d8229005d33ba35ca0eb9d7` | Pre-upstream LCM implementation |

The original local branch refs were left intact. The complete bundle covers
every original head, remote-tracking ref, stash safety ref, and tag.

## Patch classification

The initial branch/fork graph contained 68 non-merge commits and two merge
commits not exact ancestors of current upstream. `git cherry` found no exact
patch-ID matches; the "upstreamed" results below were established by semantic
comparison with current upstream, not by matching hashes.

Every one of the 68 commits is accounted for in the following table:

| Original SHA(s) | Classification | Disposition and reason |
|---|---|---|
| `2e251a583` | Already upstreamed | Per-request agent options are covered by upstream `d66a82000c8b3729a01c9d0b92d424c3c2aa3733`; the useful one-shot caller fix is the smaller aggregate commit `b654d9001`. |
| `b32b4825b` | Obsolete | Generated codebase-course website; large, stale, and unrelated to runtime. Archived. |
| `c6753bd5b`, `5f342839b`, `61ccf7f06` | Duplicate/incoherent | Minimal/composite prompt variants bypass current prompt layers and configuration semantics. Useful request-option behavior was ported narrowly; composite prompt behavior was rejected. |
| `f36e6122f`, `fecb27f1c`, `68e016a4d` | Duplicate/obsolete | Three copies of outbound session-ID header forwarding. It adds an unnecessary identifier and is absent from current upstream. Archived. |
| `ebb00f356`, `74b66ab20`, `439428664` | Duplicate/obsolete | Three copies of a generated searchable docs index. Stale generated content; archived. |
| `e3db2e2dc`, `5b0be9563`, `d64e34d51`, `8324f4c35`, `d5c79fbd1`, `317cb84ca`, `f6bfe657a`, `afb947954`, `366cd4cca`, `be2c2e97a`, `d875427cb`, `c81bc9078`, `e9e439af6` | Obsolete/duplicate | Historical Honcho injection gates target an old provider architecture. Current upstream provider lifecycle and the generic bounded-memory aggregate fix supersede them. Archived as one coherent historical stack. |
| `12f1b671a`, `80c019a4e`, `ad0c2961b`, `937969deb` | Obsolete | Honcho-specific audit/debug tools for that same retired path. Archived with the Honcho stack. |
| `f8e5b501c`, `be663613d`, `d9e1b8695`, `2b62b9516`, `c99fdcb91`, `6846bb278`, `6f755e69e`, `eb7ae985b`, `005466841` | Duplicate/obsolete | Old Discord relay, Supertonic, slash-say, voice WIP, and external ingress implementations. They are broad and stale against the current voice/plugin architecture. Preserved on voice archive refs only. |
| `ce36bc94e`, `5c286f03b`, `0a6f2f57f` | Duplicate/already upstreamed | Three copies of local Parakeet STT support; superseded by upstream's generic STT provider registry in `d3ffbc640940d8ce78ba2f5b44f0bc761e99dd45`. |
| `b286f95f2`, `38baee828` | Duplicate/superseded | Configurable proxy upstream variants; current upstream has a substantially evolved routing/configuration layer. Archived. |
| `f0a1dce04`, `45a827ee9` | Duplicate/already upstreamed | xAI proxy adapter is covered upstream by `1d6f3753dec9df571cdab8da816bfda964d9c87b`. |
| `e20bc080b`, `ab18f4f9`, `ba48a27b3`, `877793ac8` | Duplicate/incoherent aggregate | Mixed "Mara runtime" and prompt/proxy aggregate commits combine unrelated old patches. Useful current pieces were split and reimplemented; mixed commits remain archived. |
| `6b57d9f34`, `d6d1a7c27`, `6ca1cb97c`, `7bf2b2429` | Superseded/already upstreamed | Old xAI media, proxy body-limit, image-edit, and vision-routing fixes are covered by current upstream media/proxy/vision code. Archived for provenance. |
| `00a33a5c8` | Already upstreamed | LCM engine preflight is covered upstream by `929c952596a4b8d40c49c96b1f1feb80655ca044`. |
| `71cf1a163` | Coherent feature | Local Mem0 endpoint intent was ported onto current upstream in `ccf1cfaac196fdfe1dc1761fef30353223877df7`. |
| `4db85fcef`, `ca0d62687` | Duplicate/coherent feature | Two copies of Mem0 auto-context quarantine. Current generic bounding is in the aggregate branch; provider-specific safe controls are in the isolated Mem0 branch. |
| `d2895a191`, `848aa8cf5` | Historical snapshot/superseded | Large old Mem0 snapshots/safeguards were reviewed; sensible current controls were reimplemented on the isolated Mem0 branch. Originals remain archived. |
| `ddb3d56e9` | Already upstreamed/coherent feature | Current-turn Mem0 recall is covered upstream by `c6eb7f9e7284c5268ceed0c3fc92e1f2a5d892c7`; additional safe controls live on the isolated Mem0 branch. |
| `21cbae39c` | Mixed/coherent feature | Mixed gateway/Hindsight runtime patch. Hindsight summary behavior was isolated cleanly in `97f0c1153`; unrelated legacy pieces were dropped. |
| `de5641f85` | Superseded | Context-engine reconciliation targets an older integration. Current upstream owns provider lifecycle and `on_pre_compress` behavior (`924bc67eee35cc2fbb24d7cbc5649c820beb4406`). |
| `13816bd17`, `e90c46e2a` | Duplicate/incoherent | SYSTEM.md/composite prompt variants can return early and drop tool, skill, platform, and context layers. Current `agent.system_prompt`/ephemeral overlays cover the coherent intent. Archived. |
| `663bc0a1f` | Already upstreamed | Profile no-bundled-skills opt-out is covered upstream by `2ed96372ade3e2f6797b68fb88bf0a53f52f2ee8`. |
| `999b770d5` | Incoherent policy/config | Autosuggest disablement uses new non-secret `HERMES_*` environment configuration, contrary to repository config policy. Archived, not ported. |
| `24b13f2b9` | Incoherent | Deletes project `AGENTS.md`; explicitly rejected. Preserved only by archive/bundle. |
| `aa48905f3`, `7216c744b` | Obsolete/WIP | Old approval documentation/hooks are stale against current upstream approval infrastructure. Archived. |

The two non-upstream merge commits, `b3061acb7` and `9c821150d`, are
topology-only historical merges. They are preserved in the bundle/archive
history and were not replayed.

### Original dirty work classified separately

The staged/untracked WIP at `f5302a7798f694ba17e45b7106875b1134cc9778`
was not treated as one patch. It was split as follows:

- Small compatible aggregate patches: deception-hide scanner correction,
  bounded/stale generic memory context, and one-shot request options.
- Coherent isolated feature patches: Hindsight summary controls and safe local
  Mem0 controls.
- Rejected/archive-only:
  - Composite prompt mode: ungated, requires four lowercase files globally,
    and can discard current prompt layers.
  - Multiple simultaneous external memory providers: conflicts with
    upstream's one-external-provider and tool-footprint policy.
  - WhatsApp/Naturgy history/watch code: hard-coded personal identifiers,
    non-secret environment configuration, and no tests.
  - Bounded autosuggest WIP: adds non-secret `HERMES_*` settings.
  - Langfuse/Grok blanket-disable: disables observation without a reproduced
    underlying fault.
  - User-specific Hindsight import script and its bank/tags/defaults.
  - Three WhatsApp bridge `.bak-*Naturgy*` files.

The five untracked files are preserved in `current-untracked.tar.gz`:

- `scripts/import_hindsight_export.py`
- three WhatsApp bridge `.bak-*Naturgy*` files
- `tests/hermes_cli/test_bounded_history_autosuggest.py`

The three other dirty worktrees only contained already-upstreamed/superseded
feature changes plus local `AGENTS.md` deletions. They were stashed and
archived; the deletions were rejected.

## Tests

All tests were run through `scripts/run_tests.sh`.

- Aggregate runtime branch: 346 targeted tests passed.
  - Security/skills/cron threat-pattern coverage: 209.
  - Memory provider and streaming-context scrubber coverage: 128.
  - One-shot request-option and usage-file coverage: 9.
- Hindsight feature branch: 127 targeted tests passed.
- Mem0 feature branch: 139 targeted tests passed.
- Dependency validation: all 118 installed packages are compatible.
- Git connectivity check passed.

The full repository suite was not run; testing was scoped to every changed
runtime area and its adjacent regression coverage.

## Install and verification

The aggregate branch was installed editable with the `all` extra into the
existing `.venv`.

- Package: `hermes-agent 0.19.0`
- Editable project location: `/home/gismar/.hermes/hermes-agent`
- Python: `3.13.13`
- Final `hermes --version` verification reports upstream `b792bd05` and four
  carried commits. Three are the runtime patches ending at code tip
  `b654d900`; the fourth is this handoff-only documentation commit and has no
  runtime code effect.
- Verified imports:
  - `run_agent`
  - `hermes_cli.main`
  - `hermes_cli.oneshot`
  - `agent.memory_manager`
  - `tools.threat_patterns`
- `hermes --help` exits successfully.

The existing Python runtime links SQLite `3.50.4`. Hermes warns that this
version is affected by the WAL-reset bug and recommends SQLite `3.51.3+` or a
backport (`3.50.7`/`3.44.6`). This is an existing runtime warning, not a patch
or dependency failure, and no service/runtime restart or updater was invoked.

## Operational state

- Main checkout is clean on `patches/gismar-runtime-20260726`.
- The original LCM, Mem0-review, and voice-ingress worktrees remain registered
  and clean, with their prior dirt preserved in stashes and safety refs.
- The audit worktree remains clean on
  `feature/mem0-safe-local-runtime-20260726`.
- The stale missing `/tmp/hermes-agent-custom-patches` worktree registration
  was pruned; its `custom/patches` branch and complete history remain intact.
- Mara, Vera, gateways, proxies, and tmux sessions were not restarted,
  stopped, or modified.
