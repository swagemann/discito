"""Child-facing pages: profile picker, child home, practice pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session
from app.models import Child, ListAssignment, PassageAssignment
from app.services import stats
from app.web import (
    TOO_MANY_TRIES,
    check_password,
    client_key,
    login_throttle,
    render,
    require_household,
)

router = APIRouter()


@router.get("/unlock")
def unlock_form(request: Request) -> Any:
    return render(request, "unlock.html", {"error": None})


@router.post("/unlock")
def unlock(request: Request, password: str = Form("")) -> Any:
    key = client_key(request)
    if login_throttle.blocked(key):
        return render(request, "unlock.html", {"error": TOO_MANY_TRIES}, status=429)
    ok = check_password(password, get_settings().household_password)
    login_throttle.record(key, ok)
    if ok:
        request.session["household"] = True
        return RedirectResponse("/", status_code=303)
    return render(request, "unlock.html", {"error": "That's not it. Try again."}, status=401)


@router.get("/", dependencies=[Depends(require_household)])
def picker(request: Request, db: Session = Depends(get_session)) -> Any:
    children = db.scalars(
        select(Child).where(Child.archived.is_(False)).order_by(Child.sort_order, Child.id)
    ).all()
    return render(request, "picker.html", {"children": children})


def get_child(db: Session, child_id: int) -> Child:
    child = db.get(Child, child_id)
    if child is None or child.archived:
        raise HTTPException(404)
    return child


@router.get("/c/{child_id}", dependencies=[Depends(require_household)])
def child_home(
    request: Request, child_id: int, tab: str = "recite", db: Session = Depends(get_session)
) -> Any:
    child = get_child(db, child_id)
    tab = "spelling" if tab == "spelling" else "recite"
    rows = stats.recite_rows(db, child.id) if tab == "recite" else stats.spelling_rows(db, child.id)
    rows = stats.sort_rows(rows)
    return render(
        request,
        "child_home.html",
        {
            "child": child,
            "tab": tab,
            "active": [r for r in rows if r.status != "passed"],
            "done": [r for r in rows if r.status == "passed"],
        },
    )


@router.get("/c/{child_id}/recite/{assignment_id}", dependencies=[Depends(require_household)])
def recite_page(
    request: Request, child_id: int, assignment_id: int, db: Session = Depends(get_session)
) -> Any:
    child = get_child(db, child_id)
    a = db.get(PassageAssignment, assignment_id)
    if a is None or a.child_id != child.id or a.passage.archived:
        raise HTTPException(404)
    return render(
        request,
        "recite.html",
        {"child": child, "a": a, "typed_fallback": get_settings().stt_typed_fallback},
    )


@router.get("/c/{child_id}/spell/{assignment_id}", dependencies=[Depends(require_household)])
def spell_page(
    request: Request, child_id: int, assignment_id: int, db: Session = Depends(get_session)
) -> Any:
    child = get_child(db, child_id)
    a = db.get(ListAssignment, assignment_id)
    if a is None or a.child_id != child.id or a.spelling_list.archived:
        raise HTTPException(404)
    return render(request, "spell.html", {"child": child, "a": a})
