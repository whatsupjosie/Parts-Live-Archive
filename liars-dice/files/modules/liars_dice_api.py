"""Liar's Dice on the Reach host: one game per signed-in person, kept on the host so it survives a restart.

    GET    /api/minigames/liarsdice          the game in progress (or null)
    POST   /api/minigames/liarsdice/start    {opponents: 1-3, wild_ones: bool, replace: bool}
    POST   /api/minigames/liarsdice/bid      {count, face, expected_move}
    POST   /api/minigames/liarsdice/call     {expected_move}
    POST   /api/minigames/liarsdice/next     {expected_move}      after a round is revealed
    DELETE /api/minigames/liarsdice          leave the table (forgets the game)

The person is whoever the verified token says (never a client-supplied name). All dice stay on the host; every reply
carries ``Game.view()`` plus the ``events`` the move caused, in order, so the table can play them back at human speed.
``expected_move`` is the ``move`` number the page last saw: if the table has moved on (a double click, a second tab)
the request is refused with 409 and the current table, instead of acting on a state the person did not see.
The game is rules-checked by modules/liars_dice.py; this file only does who, saving and the wire format.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .liars_dice import Game, RuleError, new_game
from .route_security import current_identity
from .studio_progress import StudioProgressStore, _owner

log = logging.getLogger("pubcast.liarsdice")

MAX_BODY = 2048


def create_liars_dice_router(data_dir: Path) -> APIRouter:
    store = StudioProgressStore(Path(data_dir), folder="liars_dice")
    router = APIRouter(prefix="/api/minigames/liarsdice", tags=["bella-faux-pas"])
    router.store = store  # type: ignore[attr-defined]
    locks: Dict[str, asyncio.Lock] = {}

    def lock_for(owner: str) -> asyncio.Lock:
        lock = locks.get(owner)
        if lock is None:
            lock = locks[owner] = asyncio.Lock()
        return lock

    def load(owner: str) -> Optional[Game]:
        record = store.get(owner)
        saved = ((record or {}).get("progress") or {}).get("game")
        if saved is None:
            return None
        try:
            return Game.from_record(saved)
        except ValueError as exc:
            # a damaged save must never lock the person out of the table: treat it as no game and say why in the log
            log.warning("ignoring unreadable Liar's Dice save for %s: %s", owner, exc)
            return None

    def save(owner: str, game: Optional[Game]) -> None:
        store.put(owner, {"game": game.to_record() if game else None})

    async def read_body(request: Request) -> Dict[str, Any]:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "That request is too large.")
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "The request must be JSON.") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "The request must be a JSON object.")
        return body

    def reply(game: Optional[Game], events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        return {"ok": True, "game": game.view() if game else None, "events": events or []}

    def need_game(owner: str) -> Game:
        game = load(owner)
        if game is None:
            raise HTTPException(404, "There is no game in progress. Start one first.")
        return game

    def check_move(game: Game, body: Dict[str, Any]) -> None:
        expected = body.get("expected_move")
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise HTTPException(400, "expected_move must be the move number you last saw.")
        if expected != game.move:
            raise HTTPException(409, {"message": "The table has moved on since you last looked.", "game": game.view()})

    def refuse(exc: RuleError) -> HTTPException:
        return HTTPException(400, str(exc))

    @router.get("")
    async def current(identity: Dict[str, Any] = Depends(current_identity)):
        owner = _owner(identity)
        async with lock_for(owner):
            return reply(load(owner))

    @router.post("/start")
    async def start(request: Request, identity: Dict[str, Any] = Depends(current_identity)):
        body = await read_body(request)
        replace = body.get("replace", False)
        if not isinstance(replace, bool):
            raise HTTPException(400, "replace must be true or false.")
        owner = _owner(identity)
        async with lock_for(owner):
            existing = load(owner)
            if existing is not None and existing.phase != "over" and not replace:
                raise HTTPException(409, {"message": "A game is already in progress. Starting another would end it.",
                                          "game": existing.view()})
            try:
                game = new_game(body.get("opponents"), body.get("wild_ones", False))
            except RuleError as exc:
                raise refuse(exc) from None
            save(owner, game)
            return reply(game, [{"type": "round", "round": 1, "first": game.seats[game.turn].id}])

    @router.post("/bid")
    async def bid(request: Request, identity: Dict[str, Any] = Depends(current_identity)):
        body = await read_body(request)
        owner = _owner(identity)
        async with lock_for(owner):
            game = need_game(owner)
            check_move(game, body)
            try:
                events = game.bid(game.seek_human(), body.get("count"), body.get("face"))
                events += game.advance_ai()
            except RuleError as exc:
                raise refuse(exc) from None
            save(owner, game)
            return reply(game, events)

    @router.post("/call")
    async def call(request: Request, identity: Dict[str, Any] = Depends(current_identity)):
        body = await read_body(request)
        owner = _owner(identity)
        async with lock_for(owner):
            game = need_game(owner)
            check_move(game, body)
            try:
                events = game.call(game.seek_human())
            except RuleError as exc:
                raise refuse(exc) from None
            save(owner, game)
            return reply(game, events)

    @router.post("/next")
    async def next_round(request: Request, identity: Dict[str, Any] = Depends(current_identity)):
        body = await read_body(request)
        owner = _owner(identity)
        async with lock_for(owner):
            game = need_game(owner)
            check_move(game, body)
            try:
                events = game.next_round()
                events += game.advance_ai()
            except RuleError as exc:
                raise refuse(exc) from None
            save(owner, game)
            return reply(game, events)

    @router.delete("")
    async def leave(identity: Dict[str, Any] = Depends(current_identity)):
        owner = _owner(identity)
        async with lock_for(owner):
            save(owner, None)
            return reply(None)

    return router


__all__ = ["create_liars_dice_router"]
