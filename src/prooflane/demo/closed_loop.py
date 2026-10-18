"""Compatibility entry for the checkout demo's full requirements-to-regression loop."""

from pathlib import Path

from prooflane.dispatcher import run_task
from prooflane.store import Store


def run_closed_loop(
    store: Store,
    requirements: Path,
    output: Path,
    *,
    planner: str = "fixture",
    browser_enabled: bool = True,
    channel: str | None = None,
) -> dict:
    """Run the demo end to end; stage selection is delegated to the dispatcher."""
    return run_task(
        store,
        "完成下单支付完整闭环",
        output,
        requirements=requirements,
        target="regress",
        planner=planner,
        browser_enabled=browser_enabled,
        channel=channel,
    )
