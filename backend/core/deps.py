from dataclasses import dataclass
from datetime import date
from typing import Callable

from config import Settings
from core.llm import LLMClient
from core.portal import PortalConnector
from core.retrieval import PolicyStore


@dataclass
class PipelineDeps:
    # everything an agent needs from the outside world, so agents stay pure functions of (state, deps)
    settings: Settings
    llm: LLMClient
    portal: PortalConnector
    policies: PolicyStore
    find_duplicate: Callable[[int, dict], dict | None]
    emit: Callable[[dict], None]
    today: Callable[[], date] = date.today
