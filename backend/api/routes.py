import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents import dispute as dispute_agent
from agents import recovery
from api.schemas import OutcomeRequest, ReviewRequest, SyncResponse
from db import repo
from db.models import Case
from db.session import get_sessionmaker
from graph.state import audit_event
from jobs.runner import Runtime

logger = logging.getLogger(__name__)
router = APIRouter()

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def get_session():
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def _get_case(session: Session, case_id: int) -> Case:
    case = session.get(Case, case_id)
    if not case:
        raise HTTPException(404, f"case {case_id} not found")
    return case


def _emit_case(runtime: Runtime, case: Case) -> None:
    runtime.deps.emit({"type": "case", "case": repo.case_summary(case)})


@router.get("/health")
def health(runtime: Runtime = Depends(get_runtime)):
    return {"ok": True, "llm": runtime.deps.llm.name}


@router.get("/cases")
def list_cases(
    status: str | None = None,
    retailer: str | None = None,
    limit: int = Query(200, le=1000),
    session: Session = Depends(get_session),
):
    query = select(Case).order_by(Case.updated_at.desc()).limit(limit)
    if status:
        query = query.where(Case.status == status)
    if retailer:
        query = query.where(Case.retailer == retailer)
    return [repo.case_summary(c) for c in session.scalars(query)]


@router.get("/cases/{case_id}")
def get_case(case_id: int, session: Session = Depends(get_session)):
    return repo.case_detail(_get_case(session, case_id))


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    return repo.compute_stats(session)


@router.post("/portals/sync", response_model=SyncResponse, status_code=202)
def sync_portals(retailer: str | None = None, session: Session = Depends(get_session), runtime: Runtime = Depends(get_runtime)):
    # discovers deductions the portal holds that we have not seen yet and queues them
    found = runtime.deps.portal.list_deductions(retailer)
    new_ids = []
    for retailer_name, ref in found:
        case, created = repo.get_or_create_case(session, retailer_name, ref)
        if created:
            new_ids.append(case.id)
    session.commit()
    for case_id in new_ids:
        runtime.runner.submit(case_id)
    return SyncResponse(discovered=len(found), created=len(new_ids), queued=len(new_ids))


@router.post("/cases", status_code=202)
async def upload_case(
    retailer: str = Form(...),
    deduction_ref: str = Form(...),
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    runtime: Runtime = Depends(get_runtime),
):
    if not (SAFE_NAME.match(retailer) and SAFE_NAME.match(deduction_ref)):
        raise HTTPException(422, "retailer and deduction_ref may only contain letters, digits, dot, dash and underscore")
    folder = runtime.deps.portal.folder(retailer, deduction_ref)
    folder.mkdir(parents=True, exist_ok=True)
    for upload in files:
        name = upload.filename or ""
        if not SAFE_NAME.match(name):
            raise HTTPException(422, f"unsafe file name {upload.filename!r}")
        (folder / name).write_bytes(await upload.read())
    case, created = repo.get_or_create_case(session, retailer, deduction_ref)
    repo.add_audit(session, case.id, "api", "case_uploaded" if created else "case_reuploaded", f"{len(files)} files uploaded")
    session.commit()
    runtime.runner.submit(case.id)
    return repo.case_summary(case)


@router.post("/cases/{case_id}/reprocess", status_code=202)
def reprocess(case_id: int, session: Session = Depends(get_session), runtime: Runtime = Depends(get_runtime)):
    case = _get_case(session, case_id)
    if case.status == "PROCESSING":
        raise HTTPException(409, "case is already processing")
    runtime.runner.submit(case.id)
    return {"queued": case.id}


@router.post("/cases/{case_id}/review")
def review(case_id: int, body: ReviewRequest, session: Session = Depends(get_session), runtime: Runtime = Depends(get_runtime)):
    case = _get_case(session, case_id)
    if case.status != "ESCALATED":
        raise HTTPException(409, f"only escalated cases can be reviewed, this one is {case.status}")
    if body.action == "accept_deduction":
        case.status = "ACCEPTED"
        repo.add_audit(session, case.id, "human", "deduction_accepted", body.note, reviewer=body.reviewer)
    else:
        amount = body.amount_cents or case.invalid_cents
        if amount <= 0 or amount > case.claimed_cents:
            raise HTTPException(422, "dispute amount must be between 1 cent and the claimed amount")
        state = _rebuild_state(case)
        letter = dispute_agent.draft(state, runtime.deps, amount_cents=amount)
        repo.upsert_dispute(case, letter)
        case.status = "DISPUTE_DRAFTED"
        repo.add_audit(session, case.id, "human", "dispute_approved", body.note, reviewer=body.reviewer, amount_cents=amount)
    _emit_case(runtime, case)
    return repo.case_detail(case)


def _rebuild_state(case: Case) -> dict:
    stored = case.state_json
    return {
        "retailer": case.retailer,
        "notice": stored.get("notice", {}),
        "policy": stored.get("policy", {}),
        "findings": stored.get("findings", []),
        "decision": stored.get("decision", {}),
        "documents": [{"filename": d.filename, "doc_type": d.doc_type} for d in case.documents],
    }


@router.post("/cases/{case_id}/dispute/file")
def file_dispute(case_id: int, session: Session = Depends(get_session), runtime: Runtime = Depends(get_runtime)):
    case = _get_case(session, case_id)
    dispute = case.dispute
    if not dispute:
        raise HTTPException(409, "case has no dispute draft")
    if dispute.status != "DRAFTED":
        return repo.case_detail(case)
    if dispute.deadline and dispute.deadline < runtime.deps.today():
        raise HTTPException(409, f"dispute window closed {dispute.deadline.isoformat()}")
    dispute.confirmation_id = runtime.deps.portal.submit_dispute(case.retailer, case.deduction_ref, dispute.letter, dispute.attachments)
    dispute.status = "FILED"
    dispute.filed_at = datetime.now(timezone.utc)
    case.status = "DISPUTE_FILED"
    repo.add_audit(session, case.id, "dispute", "dispute_filed", f"confirmation {dispute.confirmation_id}")
    _emit_case(runtime, case)
    return repo.case_detail(case)


@router.post("/cases/{case_id}/dispute/outcome")
def record_outcome(case_id: int, body: OutcomeRequest, session: Session = Depends(get_session), runtime: Runtime = Depends(get_runtime)):
    case = _get_case(session, case_id)
    dispute = case.dispute
    if not dispute or dispute.status not in {"FILED", "WON", "PARTIAL", "LOST"}:
        raise HTTPException(409, "dispute has not been filed")
    try:
        result = recovery.apply_outcome(
            case.retailer, case.deduction_ref, dispute.amount_cents, body.outcome, body.recovered_cents, runtime.erp
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    dispute.status = result["dispute_status"]
    dispute.erp_memo_id = result["erp_memo_id"]
    case.recovered_cents = result["recovered_cents"]
    case.status = {"WON": "RECOVERED", "PARTIAL": "PARTIALLY_RECOVERED", "LOST": "LOST"}[dispute.status]
    repo.add_audit(
        session, case.id, "recovery", "outcome_recorded",
        f"{body.outcome}, recovered {result['recovered_cents']} cents, memo {result['erp_memo_id']}",
    )
    _emit_case(runtime, case)
    return repo.case_detail(case)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    manager = ws.app.state.manager
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
