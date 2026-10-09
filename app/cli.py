"""Management commands: `python -m app.cli seed` loads demo kids, passages, and a list."""

from __future__ import annotations

import sys
from datetime import timedelta

from sqlalchemy import select

from app.db.session import get_session_factory
from app.models import Child, ListAssignment, Passage, PassageAssignment, SpellingList, SpellingWord
from app.services import recite
from app.services.stats import today

PSALM_23 = """1 The LORD is my shepherd; I shall not want.
2 He maketh me to lie down in green pastures: he leadeth me beside the still waters.
3 He restoreth my soul: he leadeth me in the paths of righteousness for his name's sake.
4 Yea, though I walk through the valley of the shadow of death, I will fear no evil: for thou art with me; thy rod and thy staff they comfort me.
5 Thou preparest a table before me in the presence of mine enemies: thou anointest my head with oil; my cup runneth over.
6 Surely goodness and mercy shall follow me all the days of my life: and I will dwell in the house of the LORD for ever."""

FROST = """Whose woods these are I think I know.
His house is in the village though;
He will not see me stopping here
To watch his woods fill up with snow."""

WORDS = [
    ("because", "", False),
    ("friend", "", False),
    ("their", "They lost their dog at the park.", True),
    ("there", "Put the box over there.", True),
    ("through", "We walked through the forest.", False),
    ("enough", "", False),
]


def seed() -> None:
    with get_session_factory()() as db:
        if db.scalar(select(Child.id).limit(1)) is not None:
            print("database already has data; skipping seed")
            return
        kids = [
            Child(name="Ada", avatar="🦉", color="violet", sort_order=1),
            Child(name="Ben", avatar="🦖", color="emerald", sort_order=2),
        ]
        db.add_all(kids)
        db.flush()
        due = today() + timedelta(days=5)
        for i, (title, ref, kind, text) in enumerate(
            [
                ("The Lord is my Shepherd", "Psalm 23 (KJV)", "verse", PSALM_23),
                ("Stopping by Woods", "Robert Frost", "poem", FROST),
            ]
        ):
            p = Passage(
                title=title,
                reference=ref,
                type=kind,
                text=text,
                chunk_unit="verse" if kind == "verse" else "line",
                due_date=due + timedelta(days=7 * i),
                sort_order=i,
            )
            db.add(p)
            db.flush()
            recite.resection(db, p)
            for k in kids:
                p.assignments.append(PassageAssignment(child_id=k.id))
        sl = SpellingList(title="Week 6", due_date=due)
        db.add(sl)
        for i, (w, sentence, homophone) in enumerate(WORDS):
            sl.words.append(SpellingWord(word=w, sentence=sentence, homophone=homophone, ordinal=i))
        db.flush()
        for k in kids:
            sl.assignments.append(ListAssignment(child_id=k.id))
        db.commit()
        print("seeded 2 kids, 2 passages, 1 spelling list")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed":
        seed()
    else:
        print("usage: python -m app.cli seed")
        sys.exit(2)
