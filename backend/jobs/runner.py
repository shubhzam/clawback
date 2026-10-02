import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Callable

from config import Settings, get_settings
from core.deps import PipelineDeps
from core.erp import MockERP
from core.llm import build_llm
from core.portal import FilesystemPortal
from core.retrieval import PolicyStore
from db import repo
from db.models import Case
from db.session import session_scope
from graph.pipeline import build_graph

logger = logging.getLogger(__name__)


class CaseRunner:
    def __init__(self, deps: PipelineDeps, max_workers: int):
        self.deps = deps
        self._graph = build_graph(deps)
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="case")

    def submit(self, case_id: int) -> None:
        self._pool.submit(self._run_and_follow, case_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _run_and_follow(self, case_id: int) -> None:
        try:
            for follow_up in self.process(case_id):
                self.submit(follow_up)
        except Exception:
            logger.exception(f"case {case_id} crashed outside the pipeline")

    def process(self, case_id: int) -> list[int]:
        # runs the full pipeline for one case synchronously and returns ids that need a rerun
        with session_scope() as session:
            case = session.get(Case, case_id)
            case.status = "PROCESSING"
            retailer, ref = case.retailer, case.deduction_ref
        self.deps.emit({"type": "status", "case_id": case_id, "status": "PROCESSING"})
        logger.info(f"processing case {case_id} ({retailer}/{ref})")

        try:
            state = self._graph.invoke({"case_id": case_id, "retailer": retailer, "deduction_ref": ref, "audit": []})
        except Exception as exc:
            logger.exception(f"pipeline failed for case {case_id}")
            with session_scope() as session:
                case = session.get(Case, case_id)
                case.status = "ERROR"
                case.summary = f"pipeline error: {exc}"
                repo.add_audit(session, case_id, "pipeline", "pipeline_error", str(exc))
            self.deps.emit({"type": "status", "case_id": case_id, "status": "ERROR"})
            return []

        with session_scope() as session:
            case = session.get(Case, case_id)
            repo.save_result(session, case, state)
            session.flush()
            follow_ups = repo.later_duplicates_to_requeue(session, case)
            # an earlier twin may have landed while this case was mid-run, so recheck once
            flagged = any(f.get("check") == "duplicate" for f in state.get("findings", []))
            if not flagged and repo.find_duplicate(session, case_id, state.get("notice") or {}):
                follow_ups.append(case_id)
            summary = repo.case_summary(case)
        self.deps.emit({"type": "case", "case": summary})
        return follow_ups


@dataclass
class Runtime:
    settings: Settings
    deps: PipelineDeps
    runner: CaseRunner
    erp: MockERP


def _find_duplicate(case_id: int, notice: dict) -> dict | None:
    with session_scope() as session:
        return repo.find_duplicate(session, case_id, notice)


def build_runtime(emit: Callable[[dict], None], today: Callable[[], date] = date.today) -> Runtime:
    settings = get_settings()
    deps = PipelineDeps(
        settings=settings,
        llm=build_llm(settings),
        portal=FilesystemPortal(settings.resolved_portal_root()),
        policies=PolicyStore.from_dir(settings.policies_dir),
        find_duplicate=_find_duplicate,
        emit=emit,
        today=today,
    )
    runner = CaseRunner(deps, settings.worker_threads)
    return Runtime(settings=settings, deps=deps, runner=runner, erp=MockERP(settings.resolved_erp_ledger()))
