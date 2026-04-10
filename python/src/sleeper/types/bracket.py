from __future__ import annotations
from pydantic import BaseModel


class BracketFrom(BaseModel):
    w: int | None = None  # winner of match id
    l: int | None = None  # loser of match id


class BracketMatchup(BaseModel):
    r: int  # round
    m: int  # match id
    t1: int | None = None  # roster_id of team 1
    t2: int | None = None  # roster_id of team 2
    t1_from: BracketFrom | None = None
    t2_from: BracketFrom | None = None
    w: int | None = None  # winner roster_id
    l: int | None = None  # loser roster_id
    p: int | None = None  # placement (e.g. p=1 means this match decides 1st place)
