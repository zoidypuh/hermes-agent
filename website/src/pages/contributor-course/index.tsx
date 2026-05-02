import React, {useEffect, useMemo, useState} from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
import styles from './styles.module.css';

type SourcePath = {
  path: string;
  note: string;
};

type ReadingLink = {
  label: string;
  to: string;
};

type QuizQuestion = {
  prompt: string;
  options: string[];
  answer: number;
  feedback: string;
};

type WalkthroughPoint = {
  title: string;
  body: string;
};

type Lesson = {
  id: string;
  title: string;
  level: string;
  time: string;
  outcome: string;
  summary: string[];
  walkthrough: WalkthroughPoint[];
  sourcePaths: SourcePath[];
  readings: ReadingLink[];
  readingMoves: string[];
  quiz: QuizQuestion[];
};

const STORAGE_KEY = 'hermes-codebase-course-progress-v2';

const LESSONS: Lesson[] = [
  {
    id: 'system-map',
    title: 'System Map',
    level: 'Overview',
    time: '18 min',
    outcome: 'Build a mental model of how entry points, services, runtime code, and extension surfaces fit together.',
    summary: [
      'Hermes is easier to understand if you start with boundaries instead of files. A user can enter through the terminal CLI, a gateway platform, ACP, cron, batch execution, or direct library use. Those surfaces look different, but they mostly prepare input and session state for the same shared runtime.',
      'The center of the system is the AIAgent loop in run_agent.py. That loop is not just a model wrapper. It assembles the model context, resolves provider behavior, starts API calls, executes tools, handles interrupts and budgets, and writes the resulting trajectory back to storage.',
      'Delivery surfaces should be read as adapters around that center. CLI code cares about terminal interaction. Gateway code cares about external platforms, active-session guards, authorization, and response delivery. ACP code cares about a protocol boundary. They should not duplicate the core model turn.',
      'The repo also has extension surfaces. Tools add executable capabilities. Skills add instructions and workflows. Plugins package skills, MCP servers, and app integrations. Profiles configure runtime behavior. Understanding those extension surfaces keeps the repo from feeling like one huge script.',
      'When reading unfamiliar code, ask two questions before diving into implementation details: what boundary am I crossing, and what shape does the data have at this boundary? Those two answers make the directory tree much less ambiguous.',
    ],
    walkthrough: [
      {
        title: 'Start from the outermost surface',
        body: 'Pick a concrete path, such as a terminal message or a Slack event. Identify which entry point receives it, which object represents it internally, and where control is handed to AIAgent. This prevents you from confusing platform concerns with runtime concerns.',
      },
      {
        title: 'Separate long-lived services from turn execution',
        body: 'GatewayRunner and similar service code can stay alive across many conversations. A model turn is narrower: assemble context, call the provider, dispatch tools, and persist the result. Bugs often become clearer once you know which lifetime you are dealing with.',
      },
      {
        title: 'Treat generated docs as indexes',
        body: 'The docs and generated catalogs are useful navigation aids, but source paths define behavior. Read docs first for vocabulary, then jump into the owning file to verify the actual control flow.',
      },
    ],
    sourcePaths: [
      {path: 'run_agent.py', note: 'Core AIAgent loop, prompt assembly calls, tool dispatch, compression, persistence.'},
      {path: 'cli.py', note: 'Interactive terminal entry point and CLI-facing control flow.'},
      {path: 'gateway/run.py', note: 'Long-running gateway service for Discord, Slack, Telegram, email, and API platforms.'},
      {path: 'acp_adapter/', note: 'Agent Client Protocol surface that adapts external clients into Hermes sessions.'},
    ],
    readings: [
      {label: 'Architecture guide', to: '/developer-guide/architecture'},
      {label: 'Agent loop internals', to: '/developer-guide/agent-loop'},
      {label: 'Gateway internals', to: '/developer-guide/gateway-internals'},
    ],
    readingMoves: [
      'Draw one request path from entry point to AIAgent before reading implementation details.',
      'Mark each file you touch as delivery, runtime, provider, tool, persistence, or documentation.',
      'When a function crosses a boundary, write down the data shape before and after the call.',
      'Use the developer guide for names and maps, then verify control flow with source search.',
    ],
    quiz: [
      {
        prompt: 'You are tracing a Slack message that eventually calls a model. Which description best matches the intended boundary split?',
        options: [
          'The Slack adapter normalizes platform details, GatewayRunner manages service/session policy, and AIAgent owns the model turn.',
          'The Slack adapter constructs provider-native messages directly, while GatewayRunner only streams final text back to Slack.',
          'The CLI command layer normalizes the event first, because all human-facing commands share the terminal parser.',
          'Prompt assembly receives the raw Slack payload so the provider adapter can infer platform context.',
        ],
        answer: 0,
        feedback: 'Platform adapters normalize delivery details, the gateway owns service policy, and AIAgent owns the runtime turn.',
      },
      {
        prompt: 'Why is run_agent.py central but still not the right place to understand every behavior first?',
        options: [
          'It is central, but many behaviors are shaped before or after the turn by entry points, storage, tools, providers, or platform delivery.',
          'It only contains provider adapters, so most runtime behavior actually lives in gateway/run.py.',
          'It is a generated file, so source-level debugging should start in docs and generated catalogs.',
          'It only handles CLI sessions; gateway and ACP sessions use separate agent loops.',
        ],
        answer: 0,
        feedback: 'run_agent.py is the runtime center, but delivery, provider, tool, and persistence code still own important surrounding behavior.',
      },
      {
        prompt: 'A behavior looks different in CLI and gateway, but the model turn appears identical once AIAgent starts. What is the best next reading path?',
        options: [
          'Read provider adapters first, because identical model turns mean the provider changed the behavior.',
          'Compare the CLI and gateway setup paths before the AIAgent handoff, especially session config and message normalization.',
          'Read tools/registry.py first, because all surface differences are caused by toolset filtering.',
          'Read website docs first, because Docusaurus route generation controls entry point behavior.',
        ],
        answer: 1,
        feedback: 'If the turn is identical after handoff, the difference likely happens in setup, normalization, or session configuration before AIAgent.',
      },
    ],
  },
  {
    id: 'local-orientation',
    title: 'Local Orientation Loop',
    level: 'Workflow',
    time: '16 min',
    outcome: 'Know how the checkout is generated, tested, and rebuilt while you study the code.',
    summary: [
      'Before deep reading, make the repo runnable in your head and on disk. Hermes has Python runtime code, generated website data, generated skill references, and Docusaurus static output. Knowing what is generated prevents you from mistaking build artifacts for hand-authored source.',
      'Python checks should go through scripts/run_tests.sh. The wrapper probes expected virtualenv locations and gives the repo one consistent test entry point. If a subsystem has focused tests, use those after the general wrapper to shorten the feedback loop while reading.',
      'The website has its own prebuild step. Docusaurus imports generated skills data from website/src/data/skills.json and writes llms.txt files. If Python dependencies such as PyYAML are missing from the interpreter used by npm, the prebuild can fall back to an empty skills file so the site still builds locally.',
      'That fallback is useful but easy to misread. An empty Skills page during local development may mean the generator could not import yaml, not that the repo has no skills. Running npm scripts with the repo venv first in PATH gives the prebuild access to the same Python dependencies as the project.',
      'When learning the code, keep a small notebook of commands that prove each surface still works. That command list becomes a map of the repo: Python tests for runtime code, website typecheck and build for Docusaurus pages, and narrower tests for specific providers, tools, or gateway paths.',
    ],
    walkthrough: [
      {
        title: 'Identify generated files',
        body: 'Check .gitignore and the website scripts before treating a file as source. Generated files such as skills.json and llms.txt exist to make the site work, but the behavior they summarize comes from skills, optional-skills, docs, and scripts.',
      },
      {
        title: 'Use checks as code-reading tools',
        body: 'A failing test is not only a gate. It tells you which contract the subsystem thinks it owns. Run the smallest relevant test when you are trying to understand a module boundary.',
      },
      {
        title: 'Match the interpreter to the task',
        body: 'npm scripts can call python3, while the repo runtime may use venv/bin/python. If generated data looks wrong, confirm which interpreter ran the generator before looking for source bugs.',
      },
    ],
    sourcePaths: [
      {path: 'scripts/run_tests.sh', note: 'Preferred test wrapper for Python test runs.'},
      {path: 'website/package.json', note: 'Docusaurus scripts for start, typecheck, build, and diagram linting.'},
      {path: 'website/scripts/prebuild.mjs', note: 'Generates site data before Docusaurus start and build commands.'},
      {path: 'website/scripts/extract-skills.py', note: 'Builds the Skills Hub data from local and external skill indexes.'},
    ],
    readings: [
      {label: 'Contributing guide', to: '/developer-guide/contributing'},
      {label: 'Architecture reading order', to: '/developer-guide/architecture#recommended-reading-order'},
      {label: 'Creating skills', to: '/developer-guide/creating-skills'},
    ],
    readingMoves: [
      'Read package scripts and Python wrappers before assuming how a command works.',
      'Separate generated website data from source directories that feed the generator.',
      'When a local build succeeds with empty generated data, inspect the prebuild fallback path.',
      'Use test selection to learn ownership, not only to validate changes.',
    ],
    quiz: [
      {
        prompt: 'During npm run build, the Skills Hub renders but contains no skills. Which interpretation best fits the website prebuild behavior?',
        options: [
          'The prebuild may have failed to import PyYAML under the python3 used by npm, written an empty skills.json fallback, and allowed Docusaurus to continue.',
          'Docusaurus intentionally strips skills from production builds because generated docs are only available in development mode.',
          'TypeScript typechecking removes skills.json when the skills page imports JSON without an explicit schema.',
          'The docs sidebar routeBasePath controls skill extraction, so an empty Skills Hub usually means the route is wrong.',
        ],
        answer: 0,
        feedback: 'prebuild.mjs has a non-fatal fallback for extract-skills.py failures, commonly caused by missing PyYAML in the python3 used by npm.',
      },
      {
        prompt: 'Why is scripts/run_tests.sh the right default command to understand Python test behavior in this repo?',
        options: [
          'It runs only tests that changed in the current git diff, which mirrors CI exactly.',
          'It probes the expected virtualenv locations and gives the repo a consistent test entry point.',
          'It replaces pytest with Docusaurus checks so Python and website tests share one command.',
          'It bypasses slow integration tests by default and therefore cannot expose environment issues.',
        ],
        answer: 1,
        feedback: 'The wrapper standardizes environment selection before invoking the Python tests.',
      },
      {
        prompt: 'You are reading a Docusaurus React page that stores local quiz progress. Which checks best match that surface?',
        options: [
          'npm run typecheck for TypeScript and npm run build for static route generation.',
          'scripts/run_tests.sh only, because React pages are compiled through the Python test wrapper.',
          'website/scripts/extract-skills.py only, because every page depends on generated skill data.',
          'No local checks, because client-side state is only validated after deployment.',
        ],
        answer: 0,
        feedback: 'React and CSS module changes need the website TypeScript and Docusaurus static build checks.',
      },
    ],
  },
  {
    id: 'agent-loop',
    title: 'Agent Loop',
    level: 'Core runtime',
    time: '24 min',
    outcome: 'Understand what AIAgent owns and how one model turn becomes a durable trajectory.',
    summary: [
      'The agent loop is the best place to understand Hermes runtime behavior, but it is dense because it coordinates many contracts at once. A single turn can involve prompt assembly, model selection, provider translation, streaming, tool calls, interrupt handling, token budgets, context compression, callbacks, and persistence.',
      'AIAgent keeps a provider-neutral internal view of messages. Even when the upstream API is Anthropic Messages or Codex Responses, the loop works with a stable internal representation and lets adapters translate at the boundary. That keeps most runtime code from branching on provider protocols.',
      'Tool execution makes the loop iterative. The model can request a tool, Hermes validates and dispatches it, the tool result is inserted into the trajectory, and the model may continue. Message ordering matters because providers expect strict relationships between assistant tool calls and tool results.',
      'Compression and caching are part of the runtime story, not an afterthought. Long sessions can trigger summarization, budget checks, and cache decisions before the next provider call. If you only read the happy path from user message to final text, you will miss many real-world behaviors.',
      'Persistence turns the live turn into a durable record. The trajectory is useful for resuming sessions, debugging tool behavior, searching previous context, and reproducing bugs. Read persistence code with the same attention you give the provider call.',
    ],
    walkthrough: [
      {
        title: 'Read one iteration, then read the loop',
        body: 'First follow a simple user message through prompt assembly and one provider response. Then add a tool call and follow the second model step. The loop structure makes more sense after you see why a single user request can require multiple provider calls.',
      },
      {
        title: 'Watch the message shape',
        body: 'Whenever code appends or transforms messages, ask whether the shape is internal, provider-native, or persisted. Many subtle bugs come from mixing those layers.',
      },
      {
        title: 'Track side effects separately',
        body: 'Streaming callbacks, budget accounting, tool execution, and persistence are separate side effects around the provider call. Keeping them separate while reading makes run_agent.py less intimidating.',
      },
    ],
    sourcePaths: [
      {path: 'run_agent.py', note: 'Turn loop, message management, tool execution, budgets, fallback, and persistence.'},
      {path: 'prompt_builder.py', note: 'System prompt assembly and context selection.'},
      {path: 'agent/', note: 'Provider adapters and mode-specific response handling.'},
      {path: 'tools/task_utils.py', note: 'Agent-loop helper behavior used during tool execution.'},
    ],
    readings: [
      {label: 'Agent loop internals', to: '/developer-guide/agent-loop'},
      {label: 'Prompt assembly', to: '/developer-guide/prompt-assembly'},
      {label: 'Provider runtime', to: '/developer-guide/provider-runtime'},
    ],
    readingMoves: [
      'Follow one non-tool model turn, then follow one tool-using turn.',
      'Label every message transformation as internal, provider-native, or persisted.',
      'Search for where callbacks and compression are invoked instead of reading only the provider call.',
      'When a tool result appears, verify which prior assistant tool call it answers.',
    ],
    quiz: [
      {
        prompt: 'What is the main reason Hermes keeps a stable internal message representation while supporting multiple upstream APIs?',
        options: [
          'It lets most of the agent loop reason about turns, tools, and persistence without embedding provider-specific protocol rules everywhere.',
          'It allows provider adapters to skip validation because all upstream APIs accept the same OpenAI-compatible payload.',
          'It makes persisted trajectories provider-native, which is required for replaying every exact HTTP request.',
          'It lets the gateway choose tools before AIAgent starts, avoiding tool dispatch inside the loop.',
        ],
        answer: 0,
        feedback: 'The loop stays provider-neutral while adapters translate between the internal shape and provider-native protocols.',
      },
      {
        prompt: 'A session works until the model requests two tools, then the next provider call fails. Which reading path is most likely to explain it?',
        options: [
          'Compare website route generation and search index output, because Docusaurus builds tool schemas.',
          'Inspect assistant tool-call ordering, tool result insertion, and provider adapter conversion for multi-tool turns.',
          'Read gateway authorization, because multiple tool calls are represented as multiple platform users.',
          'Read context cache TTL settings only, because provider failures after tool calls are always cache misses.',
        ],
        answer: 1,
        feedback: 'Multi-tool turns stress the ordering and pairing contract between assistant tool calls and tool results.',
      },
      {
        prompt: 'Why should compression and persistence be read as part of the agent loop instead of as unrelated helpers?',
        options: [
          'They can change what context reaches the provider and what trajectory is available for resume, search, and debugging.',
          'They are gateway-only features, so reading them explains why CLI sessions do not support long conversations.',
          'They replace provider adapters when token budgets are exceeded, so the model never sees compressed context.',
          'They run only during website builds, but they document runtime behavior for generated docs.',
        ],
        answer: 0,
        feedback: 'Compression affects the next model context, and persistence affects durable session state and debugging.',
      },
    ],
  },
  {
    id: 'context-provider',
    title: 'Prompt And Provider Runtime',
    level: 'Model boundary',
    time: '22 min',
    outcome: 'Know how instructions, skills, context, provider modes, and fallbacks are assembled.',
    summary: [
      'Prompt assembly decides what the model is allowed to know for the next turn. It can include system instructions, project instructions, skills, context references, files, tool definitions, session summaries, and runtime state. A bug in this layer can look like a model problem even when the provider call is correct.',
      'Provider runtime decides how that assembled context is sent upstream. Hermes supports multiple API modes, including OpenAI-compatible chat completions, Codex Responses-style behavior, and Anthropic Messages. Each mode has different details for tools, streaming, reasoning metadata, and response parsing.',
      'The important architectural move is that entry points do not become provider experts. CLI, gateway, ACP, and cron configure sessions and select model settings. The runtime provider layer resolves the active provider, model, endpoint, API mode, and fallback behavior.',
      'Fallbacks and token budgets make provider behavior dynamic. The configured model may not be the only provider involved in a turn. If the primary call fails or a budget limit is reached, the runtime can select a fallback path while preserving the semantic contract expected by AIAgent.',
      'When studying this part of the code, read both directions: how Hermes builds a provider request, and how provider output is normalized back into internal assistant content and tool calls. The second direction is just as important as request construction.',
    ],
    walkthrough: [
      {
        title: 'Read prompt assembly before blaming the model',
        body: 'If the model seems to ignore a rule, first verify whether that rule actually reached the prompt and whether another instruction later in assembly changed its priority.',
      },
      {
        title: 'Read adapters bidirectionally',
        body: 'Provider adapters are not only serializers. They also parse streamed chunks, tool calls, final text, errors, and sometimes provider-specific metadata back into a shape AIAgent can use.',
      },
      {
        title: 'Keep selection separate from translation',
        body: 'Runtime provider code chooses which provider and mode to use. Adapter code handles the protocol shape. Mixing those concepts while reading makes fallback behavior hard to understand.',
      },
    ],
    sourcePaths: [
      {path: 'hermes_cli/runtime_provider.py', note: 'Runtime provider selection and environment-derived configuration.'},
      {path: 'agent/openai_adapter.py', note: 'OpenAI-compatible response and tool-call translation.'},
      {path: 'agent/anthropic_adapter.py', note: 'Anthropic message conversion and tool protocol handling.'},
      {path: 'context_compression/', note: 'Budget management and fallback context behavior.'},
    ],
    readings: [
      {label: 'Provider runtime', to: '/developer-guide/provider-runtime'},
      {label: 'Context compression and caching', to: '/developer-guide/context-compression-and-caching'},
      {label: 'Adding providers', to: '/developer-guide/adding-providers'},
    ],
    readingMoves: [
      'For a provider issue, trace both request serialization and response normalization.',
      'When model behavior seems wrong, inspect prompt assembly before provider transport.',
      'Look for where fallback configuration is resolved, not only where the primary model is named.',
      'Treat streaming and tool-call parsing as first-class behavior, not incidental output formatting.',
    ],
    quiz: [
      {
        prompt: 'A new provider returns streamed tool calls in a different chunk shape. Where should you expect most of the protocol-specific reading to happen?',
        options: [
          'In the provider adapter that converts provider-native chunks into Hermes internal assistant/tool-call events.',
          'In each gateway platform adapter, because external platforms decide how tool calls are streamed.',
          'In prompt_builder.py, because prompt assembly should normalize streamed chunks before the provider call.',
          'In website scripts, because generated docs define the supported tool-call schema.',
        ],
        answer: 0,
        feedback: 'Provider adapters are the boundary for provider-native request and response protocol details.',
      },
      {
        prompt: 'A change adds more project instructions to every turn. Which secondary behavior should you study before assuming the only impact is better guidance?',
        options: [
          'Context budget accounting, compression triggers, cache reuse, and fallback behavior.',
          'Gateway active-session guards, because longer prompts create additional conversation locks.',
          'Tool registry import order, because instructions are loaded as executable tools.',
          'Docusaurus broken anchor handling, because longer instructions are generated from docs headings.',
        ],
        answer: 0,
        feedback: 'Prompt shape affects budgets, compression triggers, and cache reuse.',
      },
      {
        prompt: 'What is the best way to reason about CLI, gateway, and ACP provider behavior?',
        options: [
          'They should configure sessions and model preferences, while provider selection and protocol translation stay in the shared runtime/provider layer.',
          'They should each construct provider-native payloads so platform-specific context is preserved all the way to the API call.',
          'They should bypass provider adapters for simple text-only messages and use adapters only for tool calls.',
          'They should store provider-native messages directly because persistence is easier when no translation is required.',
        ],
        answer: 0,
        feedback: 'Entry points configure sessions and hand off to the shared runtime boundary.',
      },
    ],
  },
  {
    id: 'tools-toolsets',
    title: 'Tools And Toolsets',
    level: 'Extension surface',
    time: '23 min',
    outcome: 'Understand how executable capabilities are registered, filtered, exposed, and dispatched.',
    summary: [
      'Tools are executable capabilities. The model sees a schema, chooses a tool call, and Hermes dispatches that call into Python code. That makes tools more powerful than skills and also more constrained: they need precise schemas, availability checks, permissions, and reliable error behavior.',
      'Hermes uses self-registration. Tool modules register metadata and handlers with tools/registry.py, while model_tools.py imports tool modules, gathers available definitions, filters them through toolsets and checks, and dispatches calls during the agent loop.',
      'Toolsets are a second layer of control. They define which groups of tools are available for a session or risk profile. A tool can be implemented and registered but still invisible to the model because the active toolset excludes it or a check function says the local environment is not ready.',
      'When reading tool code, distinguish definition-time behavior from execution-time behavior. Definition-time code describes what the model may call. Execution-time code handles the actual arguments, side effects, permissions, and errors.',
      'Skills and tools often solve adjacent problems. A skill tells the model how to do something with existing capabilities. A tool gives the model a new executable action. If the code only needs guidance, read the skill system first. If the code needs side effects, read the tool path.',
    ],
    walkthrough: [
      {
        title: 'Start with the registry entry',
        body: 'A tool definition tells you the public contract: name, description, schema, handler, toolsets, and checks. Read that before jumping into helper functions.',
      },
      {
        title: 'Then trace exposure',
        body: 'Follow model_tools.py to see whether the registered tool is imported, filtered, and returned to the model. This explains why implemented tools may not appear in a session.',
      },
      {
        title: 'Finally trace dispatch',
        body: 'Execution paths often include argument validation, shell or file side effects, approval gates, and formatted error output. Read those as part of the user-visible tool contract.',
      },
    ],
    sourcePaths: [
      {path: 'tools/registry.py', note: 'register() API and metadata store for tool definitions.'},
      {path: 'model_tools.py', note: 'Tool import/discovery, definition filtering, and dispatch bridge.'},
      {path: 'toolsets.py', note: 'Named toolset composition for different sessions and risk profiles.'},
      {path: 'tools/', note: 'Concrete built-in tool implementations.'},
    ],
    readings: [
      {label: 'Tools runtime', to: '/developer-guide/tools-runtime'},
      {label: 'Adding tools', to: '/developer-guide/adding-tools'},
      {label: 'Creating skills', to: '/developer-guide/creating-skills'},
    ],
    readingMoves: [
      'Read the registry metadata before the handler implementation.',
      'Check discovery and toolset filtering before assuming a registered tool is visible.',
      'Treat schema design as part of runtime behavior because it shapes model calls.',
      'Read error formatting and permission checks as part of the tool contract.',
    ],
    quiz: [
      {
        prompt: 'A tool is implemented and registered, but the model never sees it in one profile. What sequence best explains where to look?',
        options: [
          'Check import/discovery, active toolset membership, and any availability check before debugging the handler body.',
          'Check only the handler body, because registered tools are always exposed and filtering happens after execution.',
          'Check prompt_builder.py first, because tool definitions are generated from system prompt prose.',
          'Check gateway platform adapters first, because platform delivery decides which Python functions can be imported.',
        ],
        answer: 0,
        feedback: 'Registration, discovery, toolset filtering, and check functions all affect exposure.',
      },
      {
        prompt: 'Which distinction best separates a skill from a tool in Hermes?',
        options: [
          'A skill adds reusable model guidance; a tool adds an executable action with schema, dispatch, and side effects.',
          'A skill is always optional; a tool is always built in and cannot be disabled by a toolset.',
          'A skill handles provider-native messages; a tool handles only OpenAI-compatible messages.',
          'A skill is Python code loaded by model_tools.py; a tool is markdown loaded by the prompt builder.',
        ],
        answer: 0,
        feedback: 'Skills encode guidance. Tools add executable actions and need stronger runtime guarantees.',
      },
      {
        prompt: 'A shell-backed tool requires a local binary and should not appear when that binary is missing. Which design best matches the existing tool model?',
        options: [
          'Attach an availability check to the tool definition and keep execution-time validation for race conditions or better errors.',
          'Always expose the schema and let the handler fail, because visibility should not depend on local environment.',
          'Put the requirement only in docs so the model can decide whether the binary probably exists.',
          'Hide the entire toolset whenever any one tool in that toolset is missing a dependency.',
        ],
        answer: 0,
        feedback: 'Availability checks belong with tool metadata, and handlers still need robust runtime errors.',
      },
    ],
  },
  {
    id: 'gateway-cli',
    title: 'Gateway, CLI, And Commands',
    level: 'User surfaces',
    time: '22 min',
    outcome: 'Distinguish interactive command behavior from long-running gateway delivery.',
    summary: [
      'The CLI and gateway both let people talk to Hermes, but their lifetimes and responsibilities are different. The CLI is local and interactive. The gateway is a long-running service that receives external events, authorizes callers, prevents conflicting active sessions, routes slash commands, and delivers responses back to a platform.',
      'Gateway platform adapters should translate platform-specific receive and send details into Hermes concepts. They should not become separate agent loops. GatewayRunner owns the shared service flow: session keys, authorization, active-session guards, command dispatch, hooks, and model turn orchestration.',
      'Slash commands are a useful place to study shared behavior versus surface behavior. A command concept can be available in the local CLI, the gateway, and the TUI, but each surface may need different input parsing, feedback, cancellation behavior, and tests.',
      'The gateway has concurrency concerns that the CLI usually does not. Active-session guards prevent overlapping work for a conversation or user scope. If you are reading a bug involving duplicate responses, stuck sessions, or ignored messages, start by understanding those guards.',
      'The TUI and gateway-adjacent code add another layer of presentation and control. Read them as clients of the same runtime concepts, not as alternate implementations of AIAgent.',
    ],
    walkthrough: [
      {
        title: 'Trace a MessageEvent',
        body: 'For platform behavior, find where the adapter creates or normalizes the MessageEvent. Then follow how GatewayRunner builds the session key, checks authorization, and starts or resumes work.',
      },
      {
        title: 'Compare commands by surface',
        body: 'A command name may be shared, but input source, output channel, cancellation, and permissions can differ. Read command definitions together with the surface that exposes them.',
      },
      {
        title: 'Read guards before delivery',
        body: 'When messages overlap or responses seem missing, the active-session guard and session lifecycle usually explain more than the final send method.',
      },
    ],
    sourcePaths: [
      {path: 'hermes_cli/commands.py', note: 'Shared command definitions and dispatch support.'},
      {path: 'cli.py', note: 'Interactive command handling and local terminal UX.'},
      {path: 'gateway/run.py', note: 'GatewayRunner, active-session guards, slash commands, hooks, delivery loop.'},
      {path: 'gateway/platforms/', note: 'Platform adapters and MessageEvent normalization.'},
    ],
    readings: [
      {label: 'Gateway internals', to: '/developer-guide/gateway-internals'},
      {label: 'Extending the CLI', to: '/developer-guide/extending-the-cli'},
      {label: 'Adding platform adapters', to: '/developer-guide/adding-platform-adapters'},
    ],
    readingMoves: [
      'For platform bugs, trace adapter normalization before reading AIAgent.',
      'For duplicate or blocked work, read session keys and active-session guards before delivery code.',
      'When reading a command, identify which surfaces expose it and which parser each one uses.',
      'Keep service lifetime concerns separate from one-turn runtime concerns.',
    ],
    quiz: [
      {
        prompt: 'What is the most accurate responsibility split between a gateway platform adapter and GatewayRunner?',
        options: [
          'The adapter handles platform-specific receive/send normalization; GatewayRunner owns shared service policy, session guards, commands, and AIAgent orchestration.',
          'The adapter owns authorization and active-session locking; GatewayRunner only formats final responses.',
          'The adapter builds provider-native model messages; GatewayRunner chooses the platform send method.',
          'The adapter and GatewayRunner each run their own AIAgent loop so platform behavior can diverge safely.',
        ],
        answer: 0,
        feedback: 'Adapters own platform I/O; GatewayRunner and AIAgent own shared service and runtime behavior.',
      },
      {
        prompt: 'A user sends two messages quickly in the same gateway conversation and one appears to wait or get ignored. Which subsystem should you read first?',
        options: [
          'Active-session guards and session key construction, because the gateway prevents overlapping work for the same scope.',
          'Provider adapter streaming code, because all delayed messages are caused by chunk parsing.',
          'Tool registry discovery, because duplicate messages usually mean duplicate tool imports.',
          'Website search indexing, because gateway routing uses the Docusaurus docs index for slash command lookup.',
        ],
        answer: 0,
        feedback: 'The guards prevent conflicting concurrent work in a conversation or user session.',
      },
      {
        prompt: 'A slash command exists in both local CLI and gateway contexts. What is the best way to understand its behavior?',
        options: [
          'Read the shared command definition plus each surface parser, permission path, feedback channel, and cancellation behavior.',
          'Read only hermes_cli/commands.py, because command names imply identical behavior on every surface.',
          'Read only gateway/run.py, because local CLI commands are forwarded through the gateway stack.',
          'Read only toolsets.py, because slash commands are implemented as tools exposed to the model.',
        ],
        answer: 0,
        feedback: 'Shared command concepts often need surface-specific parsing, permissions, feedback, and tests.',
      },
    ],
  },
  {
    id: 'extension-map',
    title: 'Extension Points Map',
    level: 'Specific practice',
    time: '20 min',
    outcome: 'Choose the right subsystem to read when behavior involves skills, tools, plugins, profiles, or memory.',
    summary: [
      'Once you understand the central runtime, the next challenge is choosing the right extension surface. Hermes has many: built-in skills, optional skills, tools, provider adapters, platform adapters, plugins, memory providers, context engines, profiles, environments, and docs.',
      'These are not interchangeable. Skills are instruction bundles. Tools are executable capabilities. Provider adapters translate model protocols. Platform adapters translate external messaging services. Plugins package larger integrations. Profiles select behavior. Context and memory plugins change what the agent can recall or retrieve.',
      'The fastest way to understand an extension surface is to read an existing example beside the one you care about. Most subsystems have a registration pattern, metadata shape, config entry, and docs page. Those local patterns are more reliable than guessing from the directory name.',
      'When behavior spans multiple extension surfaces, identify the source of truth. A profile may enable a toolset, but it does not implement the tool. A plugin may provide a skill, but prompt assembly decides when it is loaded. A memory provider may store data, but the agent loop decides when retrieved context enters the model turn.',
      'The goal of this course is not to memorize every file. It is to learn how to ask sharper questions: Is this guidance, execution, provider translation, platform delivery, persisted memory, or runtime selection? Once you can answer that, the repo becomes navigable.',
    ],
    walkthrough: [
      {
        title: 'Classify the behavior first',
        body: 'Before reading implementation, decide whether the behavior is instruction, execution, transport, platform delivery, storage, retrieval, or configuration. That classification points to the right subsystem.',
      },
      {
        title: 'Find the nearest working example',
        body: 'Hermes favors repeated local patterns. Existing skills, tools, plugins, and providers show the expected metadata shape, registration call, docs placement, and tests.',
      },
      {
        title: 'Trace configuration into runtime',
        body: 'Profiles and config files often select behavior implemented elsewhere. Follow the config value until it reaches the runtime code that acts on it.',
      },
    ],
    sourcePaths: [
      {path: 'skills/', note: 'Built-in instruction bundles and workflows.'},
      {path: 'optional-skills/', note: 'Installable skills kept outside the default skill loadout.'},
      {path: 'plugins/', note: 'Plugin package structure for skills, MCP servers, and apps.'},
      {path: 'profiles/', note: 'Reusable runtime and tool configuration profiles.'},
    ],
    readings: [
      {label: 'Creating skills', to: '/developer-guide/creating-skills'},
      {label: 'Memory provider plugin', to: '/developer-guide/memory-provider-plugin'},
      {label: 'Context engine plugin', to: '/developer-guide/context-engine-plugin'},
    ],
    readingMoves: [
      'Classify a behavior as guidance, execution, provider translation, platform delivery, memory, context, or configuration.',
      'Read an existing implementation in the same subsystem before reading an unrelated abstraction.',
      'Follow config and profile values until they reach runtime code.',
      'When two subsystems touch, name which one owns selection and which one owns execution.',
    ],
    quiz: [
      {
        prompt: 'A behavior consists of reusable instructions that help the model use existing tools correctly. Which subsystem is the closest conceptual fit?',
        options: [
          'A skill, because the behavior is guidance rather than a new executable capability.',
          'A provider adapter, because all model-facing instructions must be converted into provider-native protocol code.',
          'A platform adapter, because reusable guidance should be attached to each external messaging service.',
          'A context engine plugin, because all instruction text should be retrieved dynamically instead of bundled.',
        ],
        answer: 0,
        feedback: 'Skills are the lightest-weight fit for reusable instructions and workflow guidance.',
      },
      {
        prompt: 'A profile enables a different toolset, and that changes which tools the model sees. Which ownership statement is most accurate?',
        options: [
          'The profile owns selection of configured behavior, while tool registration and dispatch still belong to the tools runtime.',
          'The profile owns tool execution because any tool enabled by a profile is executed from profiles/.',
          'The tool handler owns profile parsing because handlers decide whether they should be enabled.',
          'Prompt assembly owns profile selection because toolsets are just prompt text.',
        ],
        answer: 0,
        feedback: 'Profiles select behavior; the tools runtime still owns tool definitions, filtering, and dispatch.',
      },
      {
        prompt: 'A memory provider stores retrieved facts, but a model response ignores those facts. Which path best respects subsystem boundaries?',
        options: [
          'Check provider storage/retrieval, then trace whether retrieved context is selected and inserted into prompt assembly for the turn.',
          'Change the platform adapter first, because ignored memory is usually caused by message delivery formatting.',
          'Change the tool schema first, because memories are always passed to the model as tool results.',
          'Change Docusaurus generated docs first, because memory providers read docs pages as their source of truth.',
        ],
        answer: 0,
        feedback: 'Memory storage and prompt inclusion are related but separate boundaries; read both before deciding where behavior changes.',
      },
    ],
  },
];

function emptyProgress() {
  return LESSONS.map(() => false);
}

function readStoredProgress() {
  if (typeof window === 'undefined') {
    return emptyProgress();
  }

  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      return emptyProgress();
    }

    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed)) {
      return emptyProgress();
    }

    return LESSONS.map((_, index) => Boolean(parsed[index]));
  } catch {
    return emptyProgress();
  }
}

function ContributorCourse() {
  const banner = useBaseUrl('/img/hermes-agent-banner.png');
  const [activeIndex, setActiveIndex] = useState(0);
  const [passedLessons, setPassedLessons] = useState<boolean[]>(emptyProgress);
  const [hydrated, setHydrated] = useState(false);
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    setPassedLessons(readStoredProgress());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated || typeof window === 'undefined') {
      return;
    }

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(passedLessons));
  }, [hydrated, passedLessons]);

  const activeLesson = LESSONS[activeIndex];

  useEffect(() => {
    setAnswers({});
    setSubmitted(false);
  }, [activeLesson.id]);

  const completedCount = passedLessons.filter(Boolean).length;
  const progressPercent = Math.round((completedCount / LESSONS.length) * 100);
  const unlockedCount = LESSONS.filter((_, index) => index === 0 || passedLessons[index - 1]).length;

  const allAnswered = activeLesson.quiz.every((_, index) => answers[index] !== undefined);
  const allCorrect = useMemo(
    () => activeLesson.quiz.every((question, index) => answers[index] === question.answer),
    [activeLesson, answers],
  );
  const activePassed = passedLessons[activeIndex];

  function canOpenLesson(index: number) {
    return index === 0 || passedLessons[index - 1];
  }

  function submitQuiz() {
    setSubmitted(true);
    if (!allCorrect) {
      return;
    }

    setPassedLessons((current) =>
      current.map((passed, index) => (index === activeIndex ? true : passed)),
    );
  }

  function openLesson(index: number) {
    if (!canOpenLesson(index)) {
      return;
    }

    setActiveIndex(index);
  }

  function nextLesson() {
    if (!activePassed || activeIndex >= LESSONS.length - 1) {
      return;
    }

    setActiveIndex(activeIndex + 1);
  }

  function resetProgress() {
    setPassedLessons(emptyProgress());
    setActiveIndex(0);
    setAnswers({});
    setSubmitted(false);
  }

  return (
    <Layout
      title="Codebase Course"
      description="A guided Hermes Agent course for Python developers who want to understand the codebase."
    >
      <main className={styles.page}>
        <section className={styles.hero}>
          <div className={styles.heroCopy}>
            <p className={styles.eyebrow}>Hermes Agent codebase course</p>
            <h1 className={styles.heroTitle}>Learn the repo by understanding its systems.</h1>
            <p className={styles.heroLead}>
              A guided lesson path for Python developers who already know how to code and want a practical
              mental model of Hermes architecture, runtime flow, and extension points.
            </p>
            <div className={styles.heroActions}>
              <a className={styles.primaryLink} href="#course">
                Start lesson {activeIndex + 1}
              </a>
              <Link className={styles.secondaryLink} to="/developer-guide/architecture">
                Open architecture guide
              </Link>
            </div>
          </div>

          <div className={styles.heroVisual} aria-label="Hermes Agent banner">
            <img src={banner} alt="" />
            <div className={styles.statusStrip}>
              <span>{LESSONS.length} lessons</span>
              <span>{completedCount} passed</span>
              <span>{unlockedCount} unlocked</span>
            </div>
          </div>
        </section>

        <section id="course" className={styles.courseShell}>
          <aside className={styles.rail} aria-label="Course lessons">
            <div className={styles.railHeader}>
              <div>
                <p className={styles.railLabel}>Progress</p>
                <strong>{progressPercent}% complete</strong>
              </div>
              <span className={styles.lessonCount}>
                {completedCount}/{LESSONS.length}
              </span>
            </div>
            <div className={styles.progressTrack} aria-hidden="true">
              <div className={styles.progressFill} style={{width: `${progressPercent}%`}} />
            </div>

            <div className={styles.lessonList}>
              {LESSONS.map((lesson, index) => {
                const unlocked = canOpenLesson(index);
                const active = index === activeIndex;
                const passed = passedLessons[index];
                const className = [
                  styles.lessonTab,
                  active ? styles.lessonTabActive : '',
                  passed ? styles.lessonTabPassed : '',
                  !unlocked ? styles.lessonTabLocked : '',
                ]
                  .filter(Boolean)
                  .join(' ');

                return (
                  <button
                    className={className}
                    disabled={!unlocked}
                    key={lesson.id}
                    onClick={() => openLesson(index)}
                    type="button"
                    aria-current={active ? 'step' : undefined}
                  >
                    <span className={styles.lessonNumber}>{String(index + 1).padStart(2, '0')}</span>
                    <span className={styles.lessonTabBody}>
                      <span className={styles.lessonTabTitle}>{lesson.title}</span>
                      <span className={styles.lessonTabMeta}>
                        {lesson.level} - {lesson.time}
                      </span>
                    </span>
                    <span className={styles.lessonState}>
                      {passed ? 'Passed' : unlocked ? 'Open' : 'Locked'}
                    </span>
                  </button>
                );
              })}
            </div>

            <button className={styles.resetButton} onClick={resetProgress} type="button">
              Reset course progress
            </button>
          </aside>

          <article className={styles.lessonPanel}>
            <header className={styles.lessonHeader}>
              <div>
                <p className={styles.lessonKicker}>
                  Lesson {activeIndex + 1} - {activeLesson.level}
                </p>
                <h2>{activeLesson.title}</h2>
                <p>{activeLesson.outcome}</p>
              </div>
              <div className={styles.lessonBadge}>{activePassed ? 'Quiz passed' : 'Quiz required'}</div>
            </header>

            {!canOpenLesson(activeIndex) && (
              <div className={styles.lockedNotice}>Pass the previous quiz to unlock this lesson.</div>
            )}

            <div className={styles.sectionGrid}>
              <section className={styles.sectionWide}>
                <h3>What To Learn</h3>
                {activeLesson.summary.map((paragraph) => (
                  <p key={paragraph}>{paragraph}</p>
                ))}
              </section>

              <section className={styles.sectionWide}>
                <h3>Code Walkthrough</h3>
                <div className={styles.walkthroughList}>
                  {activeLesson.walkthrough.map((point) => (
                    <div className={styles.walkthroughItem} key={point.title}>
                      <strong>{point.title}</strong>
                      <p>{point.body}</p>
                    </div>
                  ))}
                </div>
              </section>

              <section className={styles.section}>
                <h3>Source Map</h3>
                <div className={styles.pathList}>
                  {activeLesson.sourcePaths.map((source) => (
                    <div className={styles.pathItem} key={source.path}>
                      <code>{source.path}</code>
                      <span>{source.note}</span>
                    </div>
                  ))}
                </div>
              </section>

              <section className={styles.section}>
                <h3>How To Read This Code</h3>
                <ul className={styles.bullets}>
                  {activeLesson.readingMoves.map((move) => (
                    <li key={move}>{move}</li>
                  ))}
                </ul>
              </section>

              <section className={styles.sectionWide}>
                <h3>Read Next</h3>
                <div className={styles.readingList}>
                  {activeLesson.readings.map((reading) => (
                    <Link className={styles.readingLink} key={reading.to} to={reading.to}>
                      {reading.label}
                    </Link>
                  ))}
                </div>
              </section>
            </div>

            <section className={styles.quiz}>
              <div className={styles.quizHeader}>
                <div>
                  <p className={styles.railLabel}>Gate quiz</p>
                  <h3>Pass to unlock the next lesson</h3>
                </div>
                <span>{activeLesson.quiz.length} questions</span>
              </div>

              {activeLesson.quiz.map((question, questionIndex) => {
                const selected = answers[questionIndex];
                const correct = selected === question.answer;
                const showResult = submitted && selected !== undefined;
                const missing = submitted && selected === undefined;

                return (
                  <div className={styles.question} key={question.prompt}>
                    <p className={styles.questionPrompt}>
                      {questionIndex + 1}. {question.prompt}
                    </p>
                    <div className={styles.answerGrid}>
                      {question.options.map((option, optionIndex) => {
                        const selectedOption = selected === optionIndex;
                        const resultClass =
                          showResult && selectedOption
                            ? correct
                              ? styles.answerCorrect
                              : styles.answerWrong
                            : '';

                        return (
                          <button
                            className={[styles.answer, selectedOption ? styles.answerSelected : '', resultClass]
                              .filter(Boolean)
                              .join(' ')}
                            key={option}
                            onClick={() =>
                              setAnswers((current) => ({...current, [questionIndex]: optionIndex}))
                            }
                            type="button"
                          >
                            {option}
                          </button>
                        );
                      })}
                    </div>
                    {missing && <p className={styles.feedback}>Choose an answer for this question.</p>}
                    {showResult && !correct && <p className={styles.feedback}>{question.feedback}</p>}
                    {showResult && correct && <p className={styles.feedbackGood}>{question.feedback}</p>}
                  </div>
                );
              })}

              <div className={styles.quizActions}>
                <button
                  className={styles.secondaryButton}
                  disabled={!allAnswered}
                  onClick={submitQuiz}
                  type="button"
                >
                  Submit quiz
                </button>
                <button
                  className={styles.primaryButton}
                  disabled={!activePassed || activeIndex === LESSONS.length - 1}
                  onClick={nextLesson}
                  type="button"
                >
                  Next lesson
                </button>
              </div>

              {submitted && allCorrect && (
                <div className={styles.passMessage}>
                  Lesson passed. The next lesson is unlocked.
                </div>
              )}
              {submitted && !allCorrect && (
                <div className={styles.failMessage}>
                  Review the feedback, adjust your answers, and submit again.
                </div>
              )}
              {activePassed && activeIndex === LESSONS.length - 1 && (
                <div className={styles.passMessage}>
                  Course complete. Use the source map and code reading moves as your subsystem map.
                </div>
              )}
            </section>
          </article>
        </section>
      </main>
    </Layout>
  );
}

export default ContributorCourse;
