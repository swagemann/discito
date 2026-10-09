"""Parent area: login, dashboard, children, passages, spelling lists, attempts, settings."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import sectioning
from app.core.config import get_settings
from app.db.session import get_session
from app.models import (
    Child,
    ListAssignment,
    Passage,
    PassageAssignment,
    ReciteAttempt,
    SpellingAttempt,
    SpellingList,
    SpellingWord,
)
from app.services import recite as recite_svc
from app.services import spelling as spelling_svc
from app.services import stats
from app.web import (
    AVATARS,
    COLORS,
    check_password,
    parse_date,
    parse_percent,
    render,
    require_parent,
)

router = APIRouter(prefix="/parent")
guarded = APIRouter(prefix="/parent", dependencies=[Depends(require_parent)])


def back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _children(db: Session) -> list[Child]:
    return list(
        db.scalars(
            select(Child).where(Child.archived.is_(False)).order_by(Child.sort_order, Child.id)
        )
    )


# --- auth ----------------------------------------------------------------------------


@router.get("/login")
def login_form(request: Request, next: str = "/parent") -> Any:
    return render(request, "admin/login.html", {"next": next, "error": False})


@router.post("/login")
def login(request: Request, password: str = Form(""), next: str = Form("/parent")) -> Any:
    if check_password(password, get_settings().parent_password):
        request.session["parent"] = int(time.time())
        request.session["household"] = True
        return back(next if next.startswith("/parent") else "/parent")
    return render(request, "admin/login.html", {"next": next, "error": True}, status=401)


@router.post("/logout")
def logout(request: Request) -> Any:
    request.session.pop("parent", None)
    return back("/")


# --- dashboard -----------------------------------------------------------------------


@guarded.get("")
def dashboard(request: Request, db: Session = Depends(get_session)) -> Any:
    cards = []
    for c in _children(db):
        rows = stats.sort_rows(stats.recite_rows(db, c.id) + stats.spelling_rows(db, c.id))
        cards.append({"child": c, "rows": rows, "trouble": stats.trouble_spots(db, c.id)})
    return render(request, "admin/dashboard.html", {"cards": cards})


# --- children ------------------------------------------------------------------------


@guarded.get("/children")
def children_page(request: Request, db: Session = Depends(get_session)) -> Any:
    return render(request, "admin/children.html", {"children": _children(db)})


def _child_fields(
    c: Child, name: str, avatar: str, color: str, chunk_size: int, tts_rate: float, show_text: bool
) -> None:
    c.name = name.strip()[:80] or "Kid"
    c.avatar = avatar if avatar in AVATARS else AVATARS[0]
    c.color = color if color in COLORS else COLORS[0]
    c.chunk_size = max(1, min(chunk_size, 10))
    c.tts_rate = max(0.5, min(tts_rate, 1.5))
    c.show_text = show_text


@guarded.post("/children")
def child_create(
    name: str = Form(...),
    avatar: str = Form(AVATARS[0]),
    color: str = Form(COLORS[0]),
    chunk_size: int = Form(1),
    tts_rate: float = Form(0.9),
    show_text: bool = Form(False),
    db: Session = Depends(get_session),
) -> Any:
    c = Child(sort_order=(db.scalar(select(func.max(Child.sort_order))) or 0) + 1)
    _child_fields(c, name, avatar, color, chunk_size, tts_rate, show_text)
    db.add(c)
    db.commit()
    return back("/parent/children")


@guarded.post("/children/{child_id}")
def child_update(
    child_id: int,
    name: str = Form(...),
    avatar: str = Form(AVATARS[0]),
    color: str = Form(COLORS[0]),
    chunk_size: int = Form(1),
    tts_rate: float = Form(0.9),
    show_text: bool = Form(False),
    db: Session = Depends(get_session),
) -> Any:
    c = db.get(Child, child_id) or _404()
    _child_fields(c, name, avatar, color, chunk_size, tts_rate, show_text)
    db.commit()
    return back("/parent/children")


@guarded.post("/children/{child_id}/archive")
def child_archive(child_id: int, db: Session = Depends(get_session)) -> Any:
    c = db.get(Child, child_id) or _404()
    c.archived = True
    db.commit()
    return back("/parent/children")


def _404() -> Any:
    raise HTTPException(404)


# --- passages ------------------------------------------------------------------------


@guarded.get("/passages")
def passages_page(
    request: Request, archived: bool = False, db: Session = Depends(get_session)
) -> Any:
    rows = db.scalars(
        select(Passage)
        .where(Passage.archived.is_(archived))
        .order_by(Passage.sort_order, Passage.id)
    ).all()
    return render(request, "admin/passages.html", {"passages": rows, "archived": archived})


@guarded.get("/passages/new")
def passage_new(request: Request, db: Session = Depends(get_session)) -> Any:
    return render(
        request,
        "admin/passage_edit.html",
        {"p": None, "children": _children(db), "assigned": set(), "units": sectioning.UNITS},
    )


def _sync_passage_assignments(db: Session, p: Passage, child_ids: list[int]) -> None:
    current = {a.child_id: a for a in p.assignments}
    for cid in child_ids:
        if cid not in current and db.get(Child, cid) is not None:
            p.assignments.append(
                PassageAssignment(
                    child_id=cid, pass_threshold=p.pass_threshold, k_required=p.k_required
                )
            )
    for cid, a in current.items():
        # Un-assigning only removes assignments that were never started, so a
        # stray click can't wipe a child's progress.
        if cid not in child_ids and a.state is None:
            p.assignments.remove(a)


@guarded.post("/passages")
def passage_create(
    title: str = Form(...),
    reference: str = Form(""),
    type: str = Form("poem"),
    text: str = Form(...),
    chunk_unit: str = Form(""),
    due_date: str = Form(""),
    pass_threshold: str = Form("100"),
    k_required: int = Form(3),
    child_ids: list[int] = Form([]),
    db: Session = Depends(get_session),
) -> Any:
    p = Passage(sort_order=(db.scalar(select(func.max(Passage.sort_order))) or 0) + 1)
    _passage_fields(
        p, title, reference, type, text, chunk_unit, due_date, pass_threshold, k_required
    )
    db.add(p)
    db.flush()
    recite_svc.resection(db, p)
    _sync_passage_assignments(db, p, child_ids)
    db.commit()
    return back(f"/parent/passages/{p.id}")


def _passage_fields(
    p: Passage,
    title: str,
    reference: str,
    type_: str,
    text: str,
    chunk_unit: str,
    due_date: str,
    pass_threshold: str,
    k_required: int,
) -> None:
    p.title = title.strip()[:200] or "Untitled"
    p.reference = reference.strip()[:200]
    p.type = "verse" if type_ == "verse" else "poem"
    p.text = text.replace("\r\n", "\n").strip()
    p.chunk_unit = chunk_unit if chunk_unit in sectioning.UNITS else sectioning.default_unit(p.type)
    p.due_date = parse_date(due_date)
    p.pass_threshold = parse_percent(pass_threshold)
    p.k_required = max(1, min(k_required, 10))


@guarded.get("/passages/{passage_id}")
def passage_edit(request: Request, passage_id: int, db: Session = Depends(get_session)) -> Any:
    p = db.get(Passage, passage_id) or _404()
    return render(
        request,
        "admin/passage_edit.html",
        {
            "p": p,
            "children": _children(db),
            "assigned": {a.child_id for a in p.assignments},
            "units": sectioning.UNITS,
        },
    )


@guarded.post("/passages/{passage_id}")
def passage_update(
    passage_id: int,
    title: str = Form(...),
    reference: str = Form(""),
    type: str = Form("poem"),
    text: str = Form(...),
    chunk_unit: str = Form(""),
    due_date: str = Form(""),
    pass_threshold: str = Form("100"),
    k_required: int = Form(3),
    child_ids: list[int] = Form([]),
    db: Session = Depends(get_session),
) -> Any:
    p = db.get(Passage, passage_id) or _404()
    old_text, old_unit = p.text, p.chunk_unit
    _passage_fields(
        p, title, reference, type, text, chunk_unit, due_date, pass_threshold, k_required
    )
    if p.text != old_text or p.chunk_unit != old_unit:
        recite_svc.resection(db, p)
    _sync_passage_assignments(db, p, child_ids)
    db.commit()
    return back(f"/parent/passages/{p.id}")


@guarded.post("/passages/{passage_id}/sections/merge")
def section_merge(
    passage_id: int, index: int = Form(...), db: Session = Depends(get_session)
) -> Any:
    p = db.get(Passage, passage_id) or _404()
    texts = [s.text for s in p.sections]
    recite_svc.set_sections(db, p, sectioning.merge(texts, index))
    db.commit()
    return back(f"/parent/passages/{p.id}#sections")


@guarded.post("/passages/{passage_id}/sections/split")
def section_split(
    passage_id: int,
    index: int = Form(...),
    marked: str = Form(...),
    db: Session = Depends(get_session),
) -> Any:
    p = db.get(Passage, passage_id) or _404()
    texts = [s.text for s in p.sections]
    recite_svc.set_sections(db, p, sectioning.split_at(texts, index, marked))
    db.commit()
    return back(f"/parent/passages/{p.id}#sections")


@guarded.post("/passages/{passage_id}/sections/auto")
def section_auto(passage_id: int, db: Session = Depends(get_session)) -> Any:
    p = db.get(Passage, passage_id) or _404()
    recite_svc.resection(db, p)
    db.commit()
    return back(f"/parent/passages/{p.id}#sections")


@guarded.post("/passages/{passage_id}/archive")
def passage_archive(
    passage_id: int, archived: bool = Form(True), db: Session = Depends(get_session)
) -> Any:
    p = db.get(Passage, passage_id) or _404()
    p.archived = archived
    db.commit()
    return back("/parent/passages")


@guarded.post("/passages/{passage_id}/move")
def passage_move(
    passage_id: int, direction: int = Form(...), db: Session = Depends(get_session)
) -> Any:
    rows = list(
        db.scalars(
            select(Passage)
            .where(Passage.archived.is_(False))
            .order_by(Passage.sort_order, Passage.id)
        )
    )
    idx = next((i for i, r in enumerate(rows) if r.id == passage_id), None)
    if idx is not None:
        j = idx + (1 if direction > 0 else -1)
        if 0 <= j < len(rows):
            rows[idx], rows[j] = rows[j], rows[idx]
        for i, r in enumerate(rows):
            r.sort_order = i
        db.commit()
    return back("/parent/passages")


# --- spelling lists ------------------------------------------------------------------


@guarded.get("/lists")
def lists_page(request: Request, archived: bool = False, db: Session = Depends(get_session)) -> Any:
    rows = db.scalars(
        select(SpellingList)
        .where(SpellingList.archived.is_(archived))
        .order_by(
            SpellingList.due_date.is_(None), SpellingList.due_date.desc(), SpellingList.id.desc()
        )
    ).all()
    return render(request, "admin/lists.html", {"lists": rows, "archived": archived})


@guarded.get("/lists/new")
def list_new(request: Request, db: Session = Depends(get_session)) -> Any:
    return render(
        request, "admin/list_edit.html", {"l": None, "children": _children(db), "assigned": set()}
    )


def _sync_list_assignments(db: Session, sl: SpellingList, child_ids: list[int]) -> None:
    current = {a.child_id: a for a in sl.assignments}
    for cid in child_ids:
        if cid not in current and db.get(Child, cid) is not None:
            a = ListAssignment(child_id=cid)
            sl.assignments.append(a)
            db.flush()
            spelling_svc.pull_carry_forward(db, a)
    for cid, a in current.items():
        if cid not in child_ids and a.state is None:
            sl.assignments.remove(a)


def _add_words(sl: SpellingList, raw: str) -> None:
    have = {w.word.casefold() for w in sl.words if w.for_child_id is None}
    n = len(sl.words)
    for w in spelling_svc.parse_words(raw):
        if w.casefold() not in have:
            sl.words.append(SpellingWord(word=w[:80], ordinal=n))
            have.add(w.casefold())
            n += 1


@guarded.post("/lists")
def list_create(
    title: str = Form(...),
    due_date: str = Form(""),
    pass_threshold: str = Form("100"),
    carry_forward: bool = Form(False),
    words: str = Form(""),
    child_ids: list[int] = Form([]),
    db: Session = Depends(get_session),
) -> Any:
    sl = SpellingList(
        title=title.strip()[:200] or "Spelling",
        due_date=parse_date(due_date),
        pass_threshold=parse_percent(pass_threshold),
        carry_forward=carry_forward,
    )
    db.add(sl)
    _add_words(sl, words)
    db.flush()
    _sync_list_assignments(db, sl, child_ids)
    db.commit()
    return back(f"/parent/lists/{sl.id}")


@guarded.get("/lists/{list_id}")
def list_edit(request: Request, list_id: int, db: Session = Depends(get_session)) -> Any:
    sl = db.get(SpellingList, list_id) or _404()
    children = {c.id: c for c in _children(db)}
    return render(
        request,
        "admin/list_edit.html",
        {
            "l": sl,
            "children": list(children.values()),
            "child_by_id": children,
            "assigned": {a.child_id for a in sl.assignments},
        },
    )


@guarded.post("/lists/{list_id}")
def list_update(
    list_id: int,
    title: str = Form(...),
    due_date: str = Form(""),
    pass_threshold: str = Form("100"),
    carry_forward: bool = Form(False),
    child_ids: list[int] = Form([]),
    db: Session = Depends(get_session),
) -> Any:
    sl = db.get(SpellingList, list_id) or _404()
    sl.title = title.strip()[:200] or sl.title
    sl.due_date = parse_date(due_date)
    sl.pass_threshold = parse_percent(pass_threshold)
    sl.carry_forward = carry_forward
    _sync_list_assignments(db, sl, child_ids)
    db.commit()
    return back(f"/parent/lists/{sl.id}")


@guarded.post("/lists/{list_id}/words")
def list_add_words(list_id: int, words: str = Form(""), db: Session = Depends(get_session)) -> Any:
    sl = db.get(SpellingList, list_id) or _404()
    _add_words(sl, words)
    db.commit()
    return back(f"/parent/lists/{sl.id}#words")


@guarded.post("/lists/{list_id}/words/{word_id}")
def word_update(
    list_id: int,
    word_id: int,
    word: str = Form(...),
    sentence: str = Form(""),
    homophone: bool = Form(False),
    db: Session = Depends(get_session),
) -> Any:
    w = db.get(SpellingWord, word_id)
    if w is None or w.list_id != list_id:
        raise HTTPException(404)
    w.word = word.strip()[:80] or w.word
    w.sentence = sentence.strip()
    w.homophone = homophone
    db.commit()
    return back(f"/parent/lists/{list_id}#w{w.id}")


@guarded.post("/lists/{list_id}/words/{word_id}/delete")
def word_delete(list_id: int, word_id: int, db: Session = Depends(get_session)) -> Any:
    w = db.get(SpellingWord, word_id)
    if w is None or w.list_id != list_id:
        raise HTTPException(404)
    db.delete(w)
    db.flush()
    # Drop the word from any in-flight round so nobody is asked a deleted word.
    for a in db.scalars(select(ListAssignment).where(ListAssignment.list_id == list_id)):
        if a.state and word_id in a.state.get("queue", []):
            a.state = {**a.state, "queue": [x for x in a.state["queue"] if x != word_id]}
    db.commit()
    return back(f"/parent/lists/{list_id}#words")


@guarded.post("/lists/{list_id}/archive")
def list_archive(
    list_id: int, archived: bool = Form(True), db: Session = Depends(get_session)
) -> Any:
    sl = db.get(SpellingList, list_id) or _404()
    sl.archived = archived
    db.flush()
    if archived and sl.carry_forward:
        for a in sl.assignments:
            spelling_svc.carry_forward(db, a)
    db.commit()
    return back("/parent/lists")


# --- per-assignment settings, reset, attempts ---------------------------------------


@guarded.get("/recite/{assignment_id}")
def recite_assignment(
    request: Request, assignment_id: int, db: Session = Depends(get_session)
) -> Any:
    a = db.get(PassageAssignment, assignment_id) or _404()
    attempts = db.scalars(
        select(ReciteAttempt)
        .where(ReciteAttempt.assignment_id == a.id)
        .order_by(ReciteAttempt.id.desc())
        .limit(100)
    ).all()
    sections = {s.id: s for s in a.passage.sections}
    lines = recite_svc.line_stats(db, a.id)
    return render(
        request,
        "admin/recite_assignment.html",
        {
            "a": a,
            "label": stats.recite_label(a),
            "attempts": attempts,
            "sections": sections,
            "lines": lines,
            "latest_id": attempts[0].id if attempts else None,
        },
    )


@guarded.post("/recite/{assignment_id}/settings")
def recite_settings(
    assignment_id: int,
    pass_threshold: str = Form("100"),
    k_required: int = Form(3),
    db: Session = Depends(get_session),
) -> Any:
    a = db.get(PassageAssignment, assignment_id) or _404()
    a.pass_threshold = parse_percent(pass_threshold)
    a.k_required = max(1, min(k_required, 10))
    db.commit()
    return back(f"/parent/recite/{a.id}")


@guarded.post("/recite/{assignment_id}/reset")
def recite_reset(assignment_id: int, db: Session = Depends(get_session)) -> Any:
    a = db.get(PassageAssignment, assignment_id) or _404()
    recite_svc.reset(db, a)
    return back(f"/parent/recite/{a.id}")


@guarded.post("/attempts/{attempt_id}/override")
def attempt_override(
    request: Request, attempt_id: int, value: str = Form(...), db: Session = Depends(get_session)
) -> Any:
    attempt = db.get(ReciteAttempt, attempt_id) or _404()
    choice = {"correct": True, "incorrect": False}.get(value)
    recite_svc.override_attempt(db, attempt, choice)
    return back(f"/parent/recite/{attempt.assignment_id}#a{attempt.id}")


@guarded.get("/spelling/{assignment_id}")
def spelling_assignment(
    request: Request, assignment_id: int, db: Session = Depends(get_session)
) -> Any:
    a = db.get(ListAssignment, assignment_id) or _404()
    words = spelling_svc.words_for(db, a)
    wstats = spelling_svc.word_stats(db, a)
    attempts = db.scalars(
        select(SpellingAttempt)
        .where(SpellingAttempt.assignment_id == a.id)
        .order_by(SpellingAttempt.id.desc())
        .limit(200)
    ).all()
    return render(
        request,
        "admin/spelling_assignment.html",
        {
            "a": a,
            "label": stats.spelling_label(db, a),
            "words": words,
            "wstats": wstats,
            "attempts": attempts,
        },
    )


@guarded.post("/spelling/{assignment_id}/reset")
def spelling_reset(assignment_id: int, db: Session = Depends(get_session)) -> Any:
    a = db.get(ListAssignment, assignment_id) or _404()
    spelling_svc.reset(db, a)
    return back(f"/parent/spelling/{a.id}")
