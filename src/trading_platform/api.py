"""Authenticated REST/WebSocket operations API for the dashboard."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .auth import AuthenticationError, Principal, authenticate, issue_token, verify_token
from .database import make_engine, session_factory
from .models import (
    AuditEvent,
    Backtest,
    BotSetting,
    Position,
    Signal,
    Strategy,
    Symbol,
    SystemEvent,
    Trade,
    User,
)
from .settings import load_settings

load_dotenv(override=False)
settings = load_settings()
engine = make_engine(settings)
SessionFactory = session_factory(engine)
app = FastAPI(title="Jervis Trading Platform API", version="0.2.0")
cors_origins = [
    origin.strip()
    for origin in os.environ.get("TRADING_PLATFORM_CORS_ORIGINS", "http://localhost:5173").split(
        ","
    )
    if origin.strip()
]
if "*" in cors_origins:
    raise RuntimeError("wildcard CORS is forbidden for the authenticated API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)
bearer = HTTPBearer(auto_error=False)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024)


class GlobalControlBody(BaseModel):
    enabled: bool


class SymbolControlBody(BaseModel):
    enabled: bool
    disable_policy: str = "disable_new"
    confirm_close: bool = False


class StrategyControlBody(BaseModel):
    enabled: bool


class AssetClassControlBody(BaseModel):
    enabled: bool


class EmergencyResetBody(BaseModel):
    confirm: bool


def db_session() -> Generator[Session, None, None]:
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(db_session)]


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: DbSession,
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="bearer token required")
    try:
        principal = verify_token(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    record = session.get(User, principal.user_id)
    if record is None or not record.enabled:
        raise HTTPException(status_code=401, detail="account disabled")
    return Principal(record.id, record.username, record.role)


def require_operator(principal: Annotated[Principal, Depends(current_user)]) -> Principal:
    if principal.role not in {"admin", "operator"}:
        raise HTTPException(status_code=403, detail="operator role required")
    return principal


def require_admin(principal: Annotated[Principal, Depends(current_user)]) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="administrator role required")
    return principal


CurrentUser = Annotated[Principal, Depends(current_user)]
Operator = Annotated[Principal, Depends(require_operator)]
Admin = Annotated[Principal, Depends(require_admin)]


@app.get("/api/v1/health")
def health(session: DbSession) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "ok", "mode": settings.mode}


@app.post("/api/v1/auth/login")
def login(body: LoginBody, session: DbSession) -> dict[str, Any]:
    try:
        principal = authenticate(session, body.username, body.password)
        token = issue_token(principal)
    except AuthenticationError as exc:
        if "signing key" in str(exc):
            raise HTTPException(status_code=503, detail="authentication is not configured") from exc
        raise HTTPException(status_code=401, detail="invalid username or password") from exc
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 28800,
        "user": {"username": principal.username, "role": principal.role},
    }


@app.get("/api/v1/me")
def me(principal: CurrentUser) -> dict[str, str]:
    return {"username": principal.username, "role": principal.role}


@app.get("/api/v1/overview")
def overview(_: CurrentUser, session: DbSession) -> dict[str, Any]:
    return _overview(session)


@app.get("/api/v1/symbols")
def symbols(_: CurrentUser, session: DbSession) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "canonical": item.canonical,
            "asset_class": item.asset_class,
            "enabled": item.enabled,
            "disable_policy": item.disable_policy,
        }
        for item in session.scalars(select(Symbol).order_by(Symbol.asset_class, Symbol.canonical))
    ]


@app.get("/api/v1/strategies")
def strategies(_: CurrentUser, session: DbSession) -> list[dict[str, Any]]:
    return [
        {"id": item.id, "key": item.key, "display_name": item.display_name, "enabled": item.enabled}
        for item in session.scalars(select(Strategy).order_by(Strategy.key))
    ]


@app.get("/api/v1/asset-classes")
def asset_classes(_: CurrentUser, session: DbSession) -> list[dict[str, Any]]:
    names = session.scalars(select(Symbol.asset_class).distinct().order_by(Symbol.asset_class))
    return [
        {"name": name, "enabled": bool(_setting(session, f"asset_class_enabled:{name}", True))}
        for name in names
    ]


@app.put("/api/v1/asset-classes/{asset_class}/control")
def set_asset_class_control(
    asset_class: str,
    body: AssetClassControlBody,
    principal: Operator,
    session: DbSession,
) -> dict[str, Any]:
    normalized = asset_class.strip().lower()
    known = session.scalar(select(Symbol.id).where(Symbol.asset_class == normalized).limit(1))
    if not normalized or known is None:
        raise HTTPException(status_code=404, detail="asset class not found")
    key = f"asset_class_enabled:{normalized}"
    before = _setting(session, key, True)
    _write_setting(session, key, body.enabled, principal, before)
    session.commit()
    return {"name": normalized, "enabled": body.enabled}


@app.get("/api/v1/positions")
def positions(_: CurrentUser, session: DbSession) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(Position).where(Position.state == "open").order_by(Position.opened_at.desc())
    )
    return [
        {
            "id": row.id,
            "symbol_id": row.symbol_id,
            "direction": row.direction,
            "volume": str(row.volume),
            "entry_price": str(row.entry_price),
            "stop_loss": str(row.stop_loss) if row.stop_loss is not None else None,
            "take_profit": str(row.take_profit) if row.take_profit is not None else None,
            "opened_at": row.opened_at.isoformat(),
            "state": row.state,
        }
        for row in rows
    ]


@app.get("/api/v1/signals")
def signals(_: CurrentUser, session: DbSession, limit: int = 50) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 200)
    rows = session.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(limit))
    return [
        {
            "id": row.id,
            "signal_key": row.signal_key,
            "symbol_id": row.symbol_id,
            "direction": row.direction,
            "decision": row.decision,
            "reason": row.reason,
            "rr": str(row.rr) if row.rr is not None else None,
            "market_time": row.market_time.isoformat(),
        }
        for row in rows
    ]


@app.get("/api/v1/backtests")
def backtests(_: CurrentUser, session: DbSession) -> list[dict[str, Any]]:
    rows = session.scalars(select(Backtest).order_by(Backtest.created_at.desc()).limit(100))
    return [
        {
            "id": row.id,
            "data_hash": row.data_hash,
            "status": row.status,
            "metrics": row.metrics,
            "split_definition": row.split_definition,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.put("/api/v1/controls/global")
def set_global_control(
    body: GlobalControlBody,
    principal: Operator,
    session: DbSession,
) -> dict[str, Any]:
    before = _setting(session, "global_enabled", settings.global_enabled)
    _write_setting(session, "global_enabled", body.enabled, principal, before)
    session.commit()
    return {"global_enabled": body.enabled}


@app.post("/api/v1/controls/emergency-stop")
def emergency_stop(principal: Operator, session: DbSession) -> dict[str, bool]:
    before = _setting(session, "emergency_stop", False)
    _write_setting(session, "emergency_stop", True, principal, before)
    session.commit()
    return {"emergency_stop": True}


@app.post("/api/v1/controls/emergency-stop/reset")
def reset_emergency_stop(
    body: EmergencyResetBody,
    principal: Admin,
    session: DbSession,
) -> dict[str, bool]:
    if not body.confirm:
        raise HTTPException(
            status_code=422, detail="explicit emergency-stop reset confirmation required"
        )
    before = _setting(session, "emergency_stop", False)
    _write_setting(session, "emergency_stop", False, principal, before)
    session.commit()
    return {"emergency_stop": False}


@app.put("/api/v1/symbols/{symbol_id}/control")
def set_symbol_control(
    symbol_id: str,
    body: SymbolControlBody,
    principal: Operator,
    session: DbSession,
) -> dict[str, Any]:
    symbol = session.get(Symbol, symbol_id)
    if symbol is None:
        raise HTTPException(status_code=404, detail="symbol not found")
    if body.disable_policy not in {"disable_new", "disable_after_position", "disable_and_close"}:
        raise HTTPException(status_code=422, detail="unknown disable policy")
    if body.disable_policy == "disable_and_close":
        if not body.confirm_close:
            raise HTTPException(status_code=422, detail="explicit close confirmation required")
        raise HTTPException(
            status_code=409, detail="position close command requires a validated execution adapter"
        )
    before = {"enabled": symbol.enabled, "disable_policy": symbol.disable_policy}
    symbol.enabled = body.enabled
    symbol.disable_policy = body.disable_policy
    _audit(
        session,
        principal,
        "symbol_control_changed",
        "symbol",
        symbol.id,
        before,
        {"enabled": symbol.enabled, "disable_policy": symbol.disable_policy},
    )
    session.commit()
    return {"id": symbol.id, "enabled": symbol.enabled, "disable_policy": symbol.disable_policy}


@app.put("/api/v1/strategies/{strategy_id}/control")
def set_strategy_control(
    strategy_id: str,
    body: StrategyControlBody,
    principal: Operator,
    session: DbSession,
) -> dict[str, Any]:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    before = {"enabled": strategy.enabled}
    strategy.enabled = body.enabled
    _audit(
        session,
        principal,
        "strategy_control_changed",
        "strategy",
        strategy.id,
        before,
        {"enabled": strategy.enabled},
    )
    session.commit()
    return {"id": strategy.id, "enabled": strategy.enabled}


@app.websocket("/api/v1/live")
async def live_updates(websocket: WebSocket) -> None:
    protocols = websocket.scope.get("subprotocols", [])
    if len(protocols) != 2 or protocols[0] != "bearer":
        await websocket.close(code=4401)
        return
    try:
        principal = verify_token(protocols[1])
    except AuthenticationError:
        await websocket.close(code=4401)
        return
    with SessionFactory() as session:
        user = session.get(User, principal.user_id)
        if user is None or not user.enabled:
            await websocket.close(code=4401)
            return
    await websocket.accept(subprotocol="bearer")
    try:
        while True:
            with SessionFactory() as session:
                await websocket.send_json(_overview(session))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return


def _overview(session: Session) -> dict[str, Any]:
    open_positions = (
        session.scalar(select(func.count()).select_from(Position).where(Position.state == "open"))
        or 0
    )
    signals_count = session.scalar(select(func.count()).select_from(Signal)) or 0
    trades_count = session.scalar(select(func.count()).select_from(Trade)) or 0
    recent_events = session.scalars(
        select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(10)
    )
    return {
        "mode": settings.mode,
        "global_enabled": bool(_setting(session, "global_enabled", settings.global_enabled)),
        "emergency_stop": bool(_setting(session, "emergency_stop", False)),
        "open_positions": open_positions,
        "signals": signals_count,
        "completed_trades": trades_count,
        "last_events": [
            {
                "severity": event.severity,
                "event_type": event.event_type,
                "message": event.message,
                "created_at": event.created_at.isoformat(),
            }
            for event in recent_events
        ],
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _setting(session: Session, key: str, default: Any) -> Any:
    row = session.scalar(
        select(BotSetting)
        .where(BotSetting.key == key)
        .order_by(BotSetting.revision.desc())
        .limit(1)
    )
    return row.value.get("value", default) if row else default


def _write_setting(
    session: Session, key: str, value: Any, principal: Principal, before: Any
) -> None:
    latest = session.scalar(select(func.max(BotSetting.revision)).where(BotSetting.key == key)) or 0
    session.add(BotSetting(key=key, revision=latest + 1, value={"value": value}))
    _audit(
        session,
        principal,
        f"{key}_changed",
        "bot_setting",
        key,
        {"value": before},
        {"value": value},
    )


def _audit(
    session: Session,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    session.add(
        AuditEvent(
            actor=principal.username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
        )
    )
