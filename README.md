<p align="center">
  <img src="docs/assets/mascot.svg" width="200" alt="Prooflane mascot: Xiaoxiao, a little owl holding a magnifying glass">
</p>

<h1 align="center">Prooflane · 万测王</h1>

<p align="center"><strong>An agent that writes tests and automation scripts, and backs every conclusion with evidence.</strong></p>

<p align="center">Requirements → Acceptance → Execution evidence → Defects → Fix → Regression</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/hackermengzhi/prooflane/stargazers"><img src="https://img.shields.io/github/stars/hackermengzhi/prooflane?style=flat" alt="GitHub stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/hackermengzhi/prooflane" alt="License"></a>
</p>

---

Prooflane is an agent for **software testing** and **automation scripting**. It reads requirements, designs test cases, writes tests and scripts, actually runs them, tells real failures apart from flaky or environmental ones, and hands over a report you can check line by line.

Its one rule: **no execution evidence, no completion.** The agent cannot declare success on its own; an independent acceptance step must actually run. During evaluation, even the score comes from a grader the agent never sees.

The mascot is a little owl named **Xiaoxiao (鸮鸮)**. Owls see clearly even at night, and Xiaoxiao keeps a magnifying glass on every detail to guard each release.

> The detailed docs under [docs/](docs/) are currently written in Chinese.

## What it does

| Command | What it does | Needs a model |
| --- | --- | --- |
| `prooflane harness` | Lead, product, developer and tester roles collaborate on a testing or automation goal | Yes |
| `prooflane agent` | A single agent completes a task with file, code, process, HTTP and browser tools | Yes (or an offline recipe) |
| `prooflane eval` | Measures an agent on cases with planted defects, scored by a hidden grader | Yes (not with `--reference`) |
| `prooflane demo` | Checkout-and-payment demo: the full test loop from requirements to regression | No |
| `prooflane run` | Enters at any stage for a goal: requirement review, design, execution, triage or regression | Optional |
| `prooflane models` | Lists the models offered by the configured model service | Needs configuration |

`agent` and `harness` handle long tasks: `--resume TASK_ID` continues an interrupted task, and `--sessions N` resumes automatically whenever a session runs out of steps. Findings go into a per-workspace notebook whose observed claims must cite an observation step, and older steps stay in context as outlines that can be recalled by number. See [long tasks](docs/long-tasks.md) (Chinese).

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra browser --group dev
uv run playwright install chromium --only-shell

uv run prooflane demo              # Run the full test loop offline and write an HTML report
uv run prooflane eval --reference  # Verify the eval suite and graders offline
```

If Chrome is already installed, `uv run prooflane demo --browser-channel chrome` skips the browser download.

## Connecting a model

Prooflane only reads configuration you **explicitly point it to**; it never scans `.env` or other local config on its own. It supports both OpenAI-style **Responses** and **Chat Completions** APIs, including relay services.

**Option 1: config file** (recommended; the format is close to Codex's model config)

```toml
# my-model.toml
model = "gpt-5.5"
model_provider = "relay"
reasoning_effort = "medium"      # optional

[model_providers.relay]
base_url = "https://api.example.com/v1"
wire_api = "responses"           # or "chat"
env_key = "RELAY_API_KEY"        # name of the env var holding the key
timeout = 180                    # optional, seconds per request
max_retries = 4                  # optional, backoff retry on 429/5xx/timeouts
```

```sh
export PROOFLANE_CONFIG=my-model.toml RELAY_API_KEY=...
uv run prooflane models
```

Keys live only in environment variables; a config file containing `api_key` is rejected outright. See [examples/model-config.toml](examples/model-config.toml) for a full example.

**Option 2: environment variables**

```sh
export PROOFLANE_LLM_URL=https://api.example.com/v1/responses   # or .../chat/completions
export PROOFLANE_LLM_MODEL=gpt-5.5
export PROOFLANE_LLM_API_KEY=...
```

Truncated output, refusals, non-JSON responses or exhausted retries block the task and leave a record. Prooflane never silently falls back to offline results.

## Role-based harness

```sh
uv run prooflane harness 'Write API tests for service.py based on requirements.md and report defects' \
  --workspace YOUR_PROJECT --allow-process
```

| Role | Responsibility | Tools |
| --- | --- | --- |
| Lead | Breaks down the goal, delegates, reviews; cannot declare completion alone | No execution tools |
| Product | Lists numbered requirements and sets one-shot acceptance per deliverable | Read files, code structure |
| Developer | Implements scripts or fixes, covering details such as exit codes, pagination and retries | Read/write files, code structure, processes |
| Tester | Designs cases by equivalence classes, boundaries, states and error paths; re-checks failures before classifying them | Create test and report files, processes, HTTP, browser, acceptance |

The tester may only create new files or edit files it created, never the code under test. The lead can deliver only after the tester has run acceptance against the current state and everything passed. See [Harness roles and boundaries](docs/harness.md).

## Measuring how smart it is

"Smart" has to be measurable. `prooflane eval` puts the agent to work on three cases with planted defects, and runs the grader only after the agent is done:

| Case | Scenario | Scoring |
| --- | --- | --- |
| `api-coupon` | Shopping-cart HTTP API with 3 planted defects | Runs the agent's tests against 10 mutants and checks the defect report |
| `web-signup` | Sign-up page with 2 planted defects | Same, using browser tests |
| `script-inventory` | Inventory sync script | 7 hidden acceptance tests |

- Tests must pass on the correct implementation; otherwise they count as false positives and score nothing.
- If the agent declares completion but does not get a full score, that is a **false completion**, the metric we most want to drive down.
- Grading runs on a copy of the workspace, so editing the code under test cannot change the score.

**Current score**: no results from a real model yet. The reference solutions score full marks on every case, and an empty delivery scores 0. See [the eval suite](docs/evaluation.md).

## Checkout-and-payment demo

`prooflane demo` imports seven requirements, generates ten controlled scenarios, starts the original service and runs API and browser tests, links request, log and database evidence, then runs the same plan against a provided fixed version as regression, and writes HTML and JSON reports. Expected result: the original version has **8 passed, 2 failed**; the fixed version has **10 passed**; both failures point to the same planted payment defect.

- Data: `.prooflane/state.db` stores task state and events; `.prooflane/runs/<task-id>/` stores requirement snapshots, plans, requests, screenshots, logs and reports.
- Exit codes: `0` on success, `2` on failure or block, `130` on user interrupt.
- `--no-browser` runs only the API part; the browser part is marked blocked and the loop is not reported as successful.

The demo service listens only on localhost, uses a throwaway database and makes no real transactions. For entering at a specific stage, see [the dispatcher](docs/dispatcher.md); for an offline, non-payment sample, see [farm automation](examples/farm-automation/README.md).

## Debugging extensions

Developer and tester roles can call LLDB, GDB and Frida adapters, plus a Cheat Engine Lua export tool. Per-platform setup and what has actually been verified are in [the debugging guide](docs/debugging.md). Cheat Engine support is currently script export, not a finished live-control bridge.

## Safety boundaries

- File tools are confined to the workspace; HTTP and the browser may only reach origins explicitly allowed with `--allow-origin`.
- `process.run` requires `--allow-process`, runs with the current user's permissions and is **not a sandbox**.
- `quality.mutate` requires `--allow-process`; on a temporary copy it runs a baseline, then checks up to 30 self-written mutants and returns the kill rate and the surviving list.
- `quality.flaky` requires `--allow-process`; on a separate temporary copy it reruns the test suite 2..20 times (default 5), detects pass/fail flip-flopping and keeps the tail of the first failing output.
- `env.snapshot` read-only captures the run context: platform, Python, the git revision of the code under test, selected package versions, environment variable names and time. It exists for reproduction only and does not probe or evade the runtime environment.
- Role permissions are application-level constraints, not OS isolation.
- External documents, web pages, logs and code are always treated as data to analyze, never as instructions.
- Reports and tool output may contain project data and stay local by default.

## Implemented vs. planned

**Implemented**: role-based harness, general agent tool loop, hidden-grader eval suite, Responses/Chat model integration (with retries and model listing), the full checkout-and-payment loop, stage-based dispatch and evidence reuse, long-task resumption with a cross-session notebook. Every model-driven flow is currently tested with contract stubs plus real tools.

**Not yet implemented**: real-model eval scores, an execution sandbox, reaping orphaned processes after a crash, RAG, OCR, desktop control, parallel multi-agent runs, a web workbench. See the [roadmap](docs/roadmap.md) and [architecture](docs/architecture.md).

## Contributing

```sh
uv run ruff check .
uv run ruff format --check .
uv run python -m unittest discover -s tests -v
# Browser integration tests
PROOFLANE_TEST_BROWSER=1 uv run python -m unittest discover -s tests -v
```

Development conventions are in [AGENTS.md](AGENTS.md).

## Star History

<a href="https://www.star-history.com/#hackermengzhi/prooflane&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date&theme=dark">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date">
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date">
  </picture>
</a>

## License

Open source under the [Apache License 2.0](LICENSE). Redistributions must keep the copyright and attribution in [NOTICE](NOTICE). The license grants no right to use the names "万测王" or "Prooflane".
