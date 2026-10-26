"""Login e usuário atual."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, SessionDep
from app.schemas import LoginIn, TokenOut, UserOut
from app.security import create_access_token
from app.serializers import user_out
from app.services import users as users_service
from app.services.errors import BusinessError
from app.services.login_throttle import LoginThrottle, MemoryThrottleStore, too_many_attempts

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenOut,
    responses={
        401: {"description": "INVALID_CREDENTIALS"},
        429: {"description": "TOO_MANY_ATTEMPTS (com Retry-After e retry_after_seconds)"},
    },
)
async def login(data: LoginIn, request: Request, session: SessionDep) -> TokenOut:
    throttle = login_throttle(request)
    ip = client_ip(request)
    # Bloqueado: recusa sem conferir a senha (nem gastar o PBKDF2) e sem contar nova falha.
    retry_after = await throttle.retry_after(data.username, ip)
    if retry_after is not None:
        raise too_many_attempts(retry_after)
    try:
        user = await users_service.authenticate(session, data.username, data.password)
    except BusinessError as exc:
        if exc.code == "INVALID_CREDENTIALS":
            retry_after = await throttle.failure(data.username, ip)
            if retry_after is not None:
                raise too_many_attempts(retry_after) from None
        raise
    await throttle.success(data.username, ip)
    token, expires_at = create_access_token(user)
    return TokenOut(access_token=token, expires_at=expires_at, user=user_out(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return user_out(user)


def login_throttle(request: Request) -> LoginThrottle:
    throttle = getattr(request.app.state, "login_throttle", None)
    if throttle is None:  # aplicação montada sem create_app()
        throttle = request.app.state.login_throttle = LoginThrottle(MemoryThrottleStore())
    return throttle


def client_ip(request: Request) -> str:
    """IP do cliente (atrás de proxy, o uvicorn --proxy-headers já aplica o X-Forwarded-For)."""
    return request.client.host if request.client else "desconhecido"
