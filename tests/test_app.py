"""End-to-end flows through the HTTP layer (typed-transcript STT fallback)."""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

POEM = "Whose woods these are I think I know.\nHis house is in the village though;\nHe will not see me stopping here"


def login(c: TestClient) -> None:
    r = c.post(
        "/parent/login", data={"password": "parent-pw", "next": "/parent"}, follow_redirects=False
    )
    assert r.status_code == 303


def add_child(c: TestClient, name: str, chunk: int = 1) -> int:
    c.post("/parent/children", data={"name": name, "chunk_size": chunk})
    from app.db.session import get_session_factory
    from app.models import Child

    with get_session_factory()() as db:
        return db.scalars(select(Child.id).where(Child.name == name)).one()


def add_passage(c: TestClient, child_ids: list[int], text: str = POEM, **extra: Any) -> int:
    data: dict[str, Any] = {
        "title": "Stopping by Woods",
        "reference": "Frost",
        "type": "poem",
        "text": text,
        "child_ids": child_ids,
    }
    data.update(extra)
    r = c.post("/parent/passages", data=data, follow_redirects=False)
    m = re.search(r"/parent/passages/(\d+)", r.headers["location"])
    assert m
    return int(m.group(1))


def assignment_ids(passage_id: int) -> dict[int, int]:
    from app.db.session import get_session_factory
    from app.models import PassageAssignment

    with get_session_factory()() as db:
        rows = db.scalars(
            select(PassageAssignment).where(PassageAssignment.passage_id == passage_id)
        )
        return {a.child_id: a.id for a in rows}


LINES = [line.rstrip() for line in POEM.split("\n")]


def say(c: TestClient, aid: int, text: str, peeked: bool = False) -> dict[str, Any]:
    r = c.post(f"/api/recite/{aid}/attempt", data={"typed": text, "peeked": str(peeked).lower()})
    assert r.status_code == 200, r.text
    return r.json()


def test_parent_area_requires_login(client: TestClient) -> None:
    r = client.get("/parent", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/parent/login")
    r = client.post("/parent/login", data={"password": "nope"})
    assert r.status_code == 401


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_two_children_independent_status_and_full_chain(client: TestClient) -> None:
    login(client)
    a_id, b_id = add_child(client, "Ada"), add_child(client, "Ben")
    pid = add_passage(client, [a_id, b_id], k_required=2)
    aids = assignment_ids(pid)
    assert set(aids) == {a_id, b_id}

    # Both see it, not started.
    assert "Stopping by Woods" in client.get(f"/c/{a_id}").text
    assert "Not started" in client.get(f"/c/{b_id}").text

    aid = aids[a_id]
    v = client.get(f"/api/recite/{aid}").json()
    assert v["stage"] == "learn" and v["learn"] == [0]

    # Chain: 1, 1..2, 1..3 → full.
    for n in range(1, 4):
        v = client.post(f"/api/recite/{aid}/ready").json()
        assert v["stage"] == "recite" and v["scope"] == list(range(n))
        v = say(client, aid, " ".join(LINES[:n]))
        assert v["attempt"]["verdict"] is True
    assert v["stage"] == "full"

    # A failed full run resets the count and drills the missed line.
    v = say(client, aid, " ".join(LINES))
    assert v["state"]["clean_full_runs"] == 1
    v = say(client, aid, " ".join(LINES[:2]) + " He will see me stopping here")
    assert v["attempt"]["verdict"] is False
    assert v["stage"] == "drill" and v["state"]["clean_full_runs"] == 0
    assert v["drill"] == {"kind": "line", "section": 2}
    v = say(client, aid, LINES[2])
    assert v["drill"] == {"kind": "transition", "section": 2}
    v = say(client, aid, " ".join(LINES[1:3]))
    assert v["stage"] == "full"

    # K=2 consecutive clean runs pass.
    say(client, aid, " ".join(LINES))
    v = say(client, aid, " ".join(LINES))
    assert v["stage"] == "passed" and v["status"] == "passed"

    # Ben is untouched.
    assert client.get(f"/api/recite/{aids[b_id]}").json()["stage"] == "learn"

    # Dashboard + trouble spots show the missed line.
    page = client.get("/parent").text
    assert "Passed" in page and "He will not see me stopping here" in page


def test_resume_where_left_off(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Cy")
    aid = assignment_ids(add_passage(client, [kid]))[kid]
    client.post(f"/api/recite/{aid}/ready")
    say(client, aid, LINES[0])
    # Simulate a closed browser: fresh GET returns the same place.
    v = client.get(f"/api/recite/{aid}").json()
    assert v["stage"] == "learn" and v["state"]["n"] == 2
    assert "Partial (2/3)" in client.get(f"/c/{kid}").text


def test_override_latest_attempt_replays_progress(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Di")
    aid = assignment_ids(add_passage(client, [kid]))[kid]
    client.post(f"/api/recite/{aid}/ready")
    v = say(client, aid, "whose woods are these")
    assert v["attempt"]["verdict"] is False and v["stage"] == "recite"
    attempt_id = v["attempt"]["id"]

    review = client.get(f"/parent/recite/{aid}").text
    assert "whose woods are these" in review and "w-miss" in review

    client.post(f"/parent/attempts/{attempt_id}/override", data={"value": "correct"})
    v = client.get(f"/api/recite/{aid}").json()
    assert v["stage"] == "learn" and v["state"]["n"] == 2  # credited: line 2 unlocked
    assert "overridden" in client.get(f"/parent/recite/{aid}").text

    client.post(f"/parent/attempts/{attempt_id}/override", data={"value": "clear"})
    v = client.get(f"/api/recite/{aid}").json()
    assert v["stage"] == "recite" and v["state"]["n"] == 1


def test_repeat_miss_on_flagged_line_triggers_drill(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Ed")
    aid = assignment_ids(add_passage(client, [kid]))[kid]
    client.post(f"/api/recite/{aid}/ready")
    say(client, aid, LINES[0])
    client.post(f"/api/recite/{aid}/ready")
    v = say(client, aid, LINES[0] + " his house is in the town")
    assert v["stage"] == "recite"  # first miss: retry
    v = say(client, aid, LINES[0] + " his house is in the town")
    assert v["stage"] == "drill" and v["drill"] == {"kind": "line", "section": 1}
    assert any(s["flagged"] for s in v["sections"])


def test_editing_text_keeps_progress_before_change(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Flo")
    pid = add_passage(client, [kid])
    aid = assignment_ids(pid)[kid]
    for n in range(1, 4):
        client.post(f"/api/recite/{aid}/ready")
        say(client, aid, " ".join(LINES[:n]))
    assert client.get(f"/api/recite/{aid}").json()["stage"] == "full"
    new_text = POEM.replace("He will not see me", "He will not see me now")
    client.post(
        f"/parent/passages/{pid}",
        data={"title": "Stopping by Woods", "type": "poem", "text": new_text, "child_ids": [kid]},
    )
    v = client.get(f"/api/recite/{aid}").json()
    assert v["stage"] == "learn" and v["state"]["n"] == 3 and v["status"] == "partial"


def test_verse_sectioning_and_merge(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Gus")
    pid = add_passage(
        client,
        [kid],
        text="1 The LORD is my shepherd; I shall not want. 2 He maketh me to lie down in green pastures.",
        type="verse",
    )
    from app.db.session import get_session_factory
    from app.models import Passage

    with get_session_factory()() as db:
        p = db.get(Passage, pid)
        assert p is not None and [s.text for s in p.sections] == [
            "The LORD is my shepherd; I shall not want.",
            "He maketh me to lie down in green pastures.",
        ]
    client.post(f"/parent/passages/{pid}/sections/merge", data={"index": 0})
    with get_session_factory()() as db:
        p = db.get(Passage, pid)
        assert p is not None and len(p.sections) == 1


# --- spelling ----------------------------------------------------------------------


def add_list(
    c: TestClient, kids: list[int], words: str, title: str = "Week 6", due: str = ""
) -> int:
    r = c.post(
        "/parent/lists",
        data={
            "title": title,
            "words": words,
            "child_ids": kids,
            "carry_forward": "true",
            "due_date": due,
        },
        follow_redirects=False,
    )
    m = re.search(r"/parent/lists/(\d+)", r.headers["location"])
    assert m
    return int(m.group(1))


def list_assignment(list_id: int, kid: int) -> int:
    from app.db.session import get_session_factory
    from app.models import ListAssignment

    with get_session_factory()() as db:
        return db.scalars(
            select(ListAssignment.id).where(
                ListAssignment.list_id == list_id, ListAssignment.child_id == kid
            )
        ).one()


def test_spelling_practice_fixup_and_test_pass_with_carry_forward(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Hal")
    lid = add_list(client, [kid], "because, friend\nthrough", due="2026-10-10")
    nxt = add_list(client, [kid], "island", title="Week 7", due="2026-10-17")
    aid = list_assignment(lid, kid)

    v = client.post(f"/api/spell/{aid}/start", data={"mode": "practice"}).json()
    assert v["mode"] == "practice" and v["remaining"] == 3 and v["current"]["show"] is None
    seen = []
    while v["round_kind"] == "full" and v["round_no"] == 1:
        word = v["current"]["speak"]
        seen.append(word)
        typed = "freind" if word == "friend" else word
        v = client.post(f"/api/spell/{aid}/answer", data={"typed": typed}).json()
        if v["feedback"].get("event"):
            break
    assert sorted(seen) == ["because", "friend", "through"]
    assert v["feedback"]["event"] == "fixup" and v["round_kind"] == "fixup" and v["remaining"] == 1
    v = client.post(f"/api/spell/{aid}/answer", data={"typed": "friend"}).json()
    assert v["feedback"]["event"] == "round_done" and v["round_no"] == 2
    # Flagged "friend" appears twice in the next round.
    assert v["remaining"] == 4

    # Test: no feedback until the end, then pass at 100%.
    v = client.post(f"/api/spell/{aid}/start", data={"mode": "test"}).json()
    while v["mode"] == "test":
        v = client.post(f"/api/spell/{aid}/answer", data={"typed": v["current"]["speak"]}).json()
        if "result" not in v["feedback"]:
            assert "correct" not in v["feedback"]
    assert v["feedback"]["result"]["passed"] is True and v["status"] == "passed"

    # Carry-forward: "friend" is still flagged → review word in Week 7 for Hal.
    nxt_aid = list_assignment(nxt, kid)
    v = client.post(f"/api/spell/{nxt_aid}/start", data={"mode": "learn"}).json()
    assert v["total"] == 2
    page = client.get(f"/parent/lists/{nxt}").text
    assert "Review words carried forward" in page and "friend" in page

    # Practicing resumes after leaving: stop, then practice again returns the same round.
    v = client.post(f"/api/spell/{aid}/start", data={"mode": "practice"}).json()
    before = (v["round_no"], v["remaining"])
    client.post(f"/api/spell/{aid}/start", data={"mode": "stop"})
    v = client.post(f"/api/spell/{aid}/start", data={"mode": "practice"}).json()
    assert (v["round_no"], v["remaining"]) == before


def test_household_unlock(client: TestClient, monkeypatch: Any) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "household_password", "family")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/unlock"
    client.post("/unlock", data={"password": "family"})
    assert client.get("/").status_code == 200


def test_security_headers(client: TestClient) -> None:
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "microphone=(self)" in r.headers["permissions-policy"]


def test_login_throttle_locks_after_repeated_failures(client: TestClient, monkeypatch: Any) -> None:
    from app.core.config import get_settings
    from app.web import login_throttle

    for _ in range(login_throttle.max_failures):
        assert client.post("/parent/login", data={"password": "nope"}).status_code == 401
    # Locked out: even the right password is refused until the window passes.
    r = client.post("/parent/login", data={"password": "parent-pw"}, follow_redirects=False)
    assert r.status_code == 429 and "Too many tries" in r.text
    # The household unlock form shares the same per-address lockout.
    monkeypatch.setattr(get_settings(), "household_password", "family")
    assert client.post("/unlock", data={"password": "family"}).status_code == 429
    # Once the failures age out, a correct password gets in and clears the slate.
    monkeypatch.setattr(login_throttle, "window_s", -1.0)
    r = client.post("/parent/login", data={"password": "parent-pw"}, follow_redirects=False)
    assert r.status_code == 303


def test_empty_recording_grades_as_silence(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Ivy")
    aid = assignment_ids(add_passage(client, [kid]))[kid]
    client.post(f"/api/recite/{aid}/ready")
    r = client.post(
        f"/api/recite/{aid}/attempt",
        files={"audio": ("clip.webm", b"", "audio/webm")},
        data={"peeked": "false"},
    )
    assert r.status_code == 200, r.text
    a = r.json()["attempt"]
    assert a["verdict"] is False and a["transcript"] == ""


def test_stt_failures_are_told_apart(client: TestClient, monkeypatch: Any) -> None:
    from app.routes import api
    from app.services.stt import SttError

    login(client)
    kid = add_child(client, "Jo")
    aid = assignment_ids(add_passage(client, [kid]))[kid]
    client.post(f"/api/recite/{aid}/ready")

    def post(status: int | None) -> int:
        def boom(*_a: Any, **_k: Any) -> Any:
            raise SttError("nope", status)

        monkeypatch.setattr(api, "transcribe", boom)
        r = client.post(
            f"/api/recite/{aid}/attempt",
            files={"audio": ("clip.webm", b"\x1aE\xdf\xa3", "audio/webm")},
        )
        return r.status_code

    assert post(422) == 422  # undecodable clip: the child should just try again
    assert post(None) == 502  # sidecar unreachable
    assert post(500) == 502


def test_parent_metrics_hard_words_and_csv(client: TestClient) -> None:
    login(client)
    kid = add_child(client, "Eve")
    aid = assignment_ids(add_passage(client, [kid]))[kid]
    client.post(f"/api/recite/{aid}/ready")
    # Two misses on "village" (line 2 isn't in scope yet, so miss "woods" twice instead).
    say(client, aid, "whose forest these are I think I know")
    say(client, aid, "whose forest these are I think I know", peeked=True)
    say(client, aid, LINES[0])

    page = client.get("/parent").text
    assert "Last 7 days" in page and "All time" in page
    assert "Export CSV" in page
    # 3 attempts, 1 clean → 33%; one attempt with help → 33%.
    assert "33%" in page
    # "woods" missed twice → listed under Hard to say.
    assert "Hard to say" in page and "woods" in page

    detail = client.get(f"/parent/recite/{aid}").text
    assert "Progress" in detail and "with help" in detail and "woods" in detail

    r = client.get(f"/parent/children/{kid}/attempts.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = r.text.strip().splitlines()
    assert rows[0].startswith("when,kind,item,step,result,right,of,accuracy_pct,help,missed")
    assert len(rows) == 4
    assert rows[1].count(",missed,") == 1 and ",woods," in rows[1]
    assert ",yes," in rows[2]
    assert ",clean," in rows[3]
