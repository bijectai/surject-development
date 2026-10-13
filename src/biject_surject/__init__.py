"""Surject: a lightweight monitor that catches when an agent does something
its user didn't intend."""

__version__ = "0.0.1"

from biject_surject.checker import ToolCall, Verdict, Violation  # noqa: E402
from biject_surject.monitor import (  # noqa: E402
    Monitor,
    PolicyViolation,
    check,
    guard,
    set_default_monitor,
)
from biject_surject.policy import Policy, PolicyError, load_policy  # noqa: E402

__all__ = [
    "Monitor",
    "Policy",
    "PolicyError",
    "PolicyViolation",
    "ToolCall",
    "Verdict",
    "Violation",
    "check",
    "guard",
    "load_policy",
    "set_default_monitor",
]
