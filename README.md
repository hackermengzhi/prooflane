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

Prooflane brings requirements, code and test evidence into one workflow. Give it a goal: a lead, product, developer and tester collaborate to analyze, build, execute and verify, delivering code, tests and a traceable report.

Meet **Xiaoxiao (鸮鸮)**, the little owl with a magnifying glass watching over every delivery.

## Put Prooflane to work

- **Requirements to tests:** turn acceptance criteria into boundary, state and error-path tests.
- **Goals to automation:** build scripts that handle pagination, retries, timeouts and repeat execution.
- **Failures to reproductions:** connect requests, logs and observations, report defects and verify fixes.
- **Passing to dependable:** assess test strength with mutation testing, detect flaky suites and capture environment snapshots.
- **APIs to browsers and debuggers:** combine file, code, process, HTTP, browser and debugging tools.
- **One session to long tasks:** resume automatically when a session runs out of steps, or continue an interrupted task with `--resume`; findings go into a per-workspace notebook that cites observed evidence, so research is not repeated. See [long tasks](docs/long-tasks.md) (Chinese).

## Quick start

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/), then clone the repository:

```sh
git clone https://github.com/hackermengzhi/prooflane.git
cd prooflane
uv sync --extra browser --group dev
uv run playwright install chromium --only-shell
```

Try the complete business demo without a model:

```sh
uv run prooflane demo
```

It runs API and browser tests, compares a defective service with its fixed version, and produces an HTML report. Use `--browser-channel chrome` with an installed Chrome browser.

## Connect a model and give it a goal

Responses and Chat Completions endpoints are supported. Configure your provider:

```sh
export PROOFLANE_LLM_URL=https://api.example.com/v1/responses
export PROOFLANE_LLM_MODEL=your-model-id
export PROOFLANE_LLM_API_KEY=your-api-key

uv run prooflane harness 'Write API tests for service.py from requirements.md and report defects' \
  --workspace YOUR_PROJECT --allow-process
```

Replace `YOUR_PROJECT` with the project directory. `--allow-process` enables local program execution. For Chat Completions, use the full `/chat/completions` endpoint.

Alternatively, point `PROOFLANE_CONFIG` to a [TOML configuration](examples/model-config.toml) for model selection, reasoning effort, request timeouts and retry settings.

## A team organized around the goal

| Role | Delivers |
| --- | --- |
| Lead | Task breakdown, delegation, coordination and delivery |
| Product | Requirements, clarifications and acceptance criteria |
| Developer | Implementations, automation scripts and fixes |
| Tester | Test design, execution evidence, findings and regression checks |

The team follows the work: failures go back to development; ambiguous requirements go to product. Tool observations, execution history and acceptance results stay with the task for review.

[Role collaboration](docs/harness.md) · [Architecture](docs/architecture.md)

## Pick your entry point

| Command | Use it for |
| --- | --- |
| `prooflane harness` | Four-role collaboration on a goal |
| `prooflane agent` | Single-agent coding, testing and automation |
| `prooflane run` | Stage-based requirement review, design, execution, investigation or regression |
| `prooflane demo` | The checkout-and-payment testing loop |
| `prooflane eval` | Hidden-grader evaluation of actual deliverables |
| `prooflane eval-compare` | Side-by-side evaluation comparisons |
| `prooflane models` | Provider model discovery |

## Build an automation script

The farm example uses an offline recipe to create a harvest script and test ready-plot selection, repeated execution and error handling:

```sh
uv run prooflane agent 'Build farm harvesting automation and verify repeat execution' \
  --workspace examples/farm-automation \
  --recipe examples/farm-automation/recipe.json --allow-process
```

[Farm automation tutorial](examples/farm-automation/README.md) · [Stage dispatch](docs/dispatcher.md) · [LLDB, GDB, Frida and Cheat Engine Lua tools](docs/debugging.md)

## Measure the work delivered

The evaluation suite covers API tests, browser tests, inventory synchronization, log analysis, sessions, legacy characterization and defect reproduction. Hidden acceptance tests, mutation scores and defect-report scoring help compare models, agents and versions.

```sh
uv run prooflane eval --reference  # Validate reference solutions and grading
uv run prooflane eval             # Evaluate the configured model and role team
```

Evaluation results are saved under `.prooflane/eval-runs/`. Demo reports live in `.prooflane/runs/`; general agent and role-team reports live in `.prooflane/agent-runs/` and `.prooflane/harness-runs/`.

[Evaluation and comparison guide](docs/evaluation.md) · [Roadmap](docs/roadmap.md)

## Contributing

```sh
uv run ruff check .
uv run ruff format --check .
uv run python -m unittest discover -s tests -v
PROOFLANE_TEST_BROWSER=1 uv run python -m unittest discover -s tests -v
```

Development follows the [eight code quality principles](AGENTS.md). Detailed guides are currently in Chinese.

## Star History

<a href="https://www.star-history.com/#hackermengzhi/prooflane&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date&theme=dark">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date">
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date">
  </picture>
</a>

## License

[Apache License 2.0](LICENSE) · Copyright Cherry · [NOTICE](NOTICE)
