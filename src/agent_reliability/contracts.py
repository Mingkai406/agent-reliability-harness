"""Small, framework-neutral adapter and experiment contracts."""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .faults import FaultEngine, FaultRule


@dataclass(frozen=True)
class Case:
    id: str
    adapter: str
    config: dict = field(default_factory=dict)
    faults: tuple[FaultRule, ...] = ()
    expected: str = "completed"
    expected_failed_checks: tuple[str, ...] = ()

    def __post_init__(self):
        for name in (self.id, self.adapter):
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", name):
                raise ValueError("Case and adapter names must be lowercase path-safe identifiers")
        if self.expected not in {"completed", "rejected", "violation"}:
            raise ValueError("Expected outcome must be completed, rejected or violation")
        if not isinstance(self.config, dict):
            raise ValueError("Case config must be an object")
        if (self.expected == "violation") != bool(self.expected_failed_checks):
            raise ValueError("Negative controls must name their expected failing invariant checks")
        if any(not isinstance(key, str) or not key for key in self.expected_failed_checks):
            raise ValueError("Expected failing checks must be nonempty strings")

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["faults"] = tuple(FaultRule(**rule) for rule in data.get("faults", []))
        data["expected_failed_checks"] = tuple(data.get("expected_failed_checks", []))
        return cls(**data)

    def to_dict(self):
        return asdict(self)


@dataclass
class Execution:
    receipt: dict
    snapshot: dict
    measurement: str


@dataclass
class Assessment:
    completed: bool
    safely_rejected: bool
    checks: dict[str, bool]

    def __post_init__(self):
        if not self.checks or any(type(v) is not bool for v in self.checks.values()):
            raise ValueError("An assessment requires nonempty boolean invariant checks")
        if type(self.completed) is not bool or type(self.safely_rejected) is not bool:
            raise ValueError("Assessment outcomes must be boolean")
        if self.completed and self.safely_rejected:
            raise ValueError("A task cannot both complete and be safely rejected")
        if (self.completed or self.safely_rejected) and not all(self.checks.values()):
            raise ValueError("Successful outcomes require every invariant to pass")
        if not (self.completed or self.safely_rejected) and all(self.checks.values()):
            raise ValueError("An invariant violation requires a failing check")


class ApplicationAdapter(Protocol):
    """Trusted local integration; graders inspect state independently of app success flags."""

    def validate(self, case: Case) -> None:
        """Reject unsupported config and fault boundaries before execution."""

    async def execute(self, case: Case, directory: Path, faults: FaultEngine, tracer) -> Execution:
        """Run real tools, leaving artifacts under directory. Bound execution in the adapter."""

    def assess(self, case: Case, execution: Execution) -> Assessment:
        """Read committed state; do not treat a completion message as proof."""
