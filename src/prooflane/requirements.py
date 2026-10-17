"""Import numbered Markdown requirements without interpreting embedded instructions."""

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from prooflane.artifacts import digest


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str
    line: int


def import_requirements(path: Path) -> dict:
    requirements = []
    identifiers = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = re.match(r"^(\d+)\.\s+(.+)$", line)
        if match:
            identifier = f"R{match[1]}"
            if identifier in identifiers:
                raise ValueError(f"Duplicate requirement identifier: {identifier}")
            identifiers.add(identifier)
            requirements.append(asdict(Requirement(identifier, match[2].strip(), line_number)))
    if not requirements:
        raise ValueError("No numbered Markdown requirements found")
    return {"source_name": path.name, "sha256": digest(path), "requirements": requirements}
