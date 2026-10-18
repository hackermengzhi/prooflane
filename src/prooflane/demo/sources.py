"""Source-version evidence for the checkout demo's broken and fixed revisions."""

import difflib
import platform
import shutil
from pathlib import Path

from prooflane.artifacts import digest, write_json
from prooflane.demo import payment_broken, payment_fixed


def compare_sources(directory: Path) -> None:
    paths = [Path(payment_broken.__file__), Path(payment_fixed.__file__)]
    versions = {}
    for name, path in zip(["broken", "fixed"], paths):
        target = directory / f"payment_{name}.py"
        shutil.copyfile(path, target)
        versions[name] = {"file": target.name, "sha256": digest(target)}
    write_json(directory / "source-versions.json", versions)
    source_root = Path(__file__).parent.parent
    write_json(
        directory / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "source_files": [
                {"path": str(path.relative_to(source_root)), "sha256": digest(path)}
                for path in sorted(source_root.rglob("*"))
                if path.is_file() and path.suffix in {".py", ".html", ".md"}
            ],
        },
    )
    difference = difflib.unified_diff(
        paths[0].read_text().splitlines(keepends=True),
        paths[1].read_text().splitlines(keepends=True),
        fromfile="payment_broken.py",
        tofile="payment_fixed.py",
    )
    (directory / "source-comparison.diff").write_text("".join(difference), encoding="utf-8")
