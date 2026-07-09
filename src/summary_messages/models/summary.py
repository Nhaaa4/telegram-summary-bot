from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class SummaryWindow:
    label: str
    start: datetime
    end: datetime
