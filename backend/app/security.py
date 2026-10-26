"""Senhas (PBKDF2-SHA256, stdlib), JWT (HS256) e dependências de autenticação.

O JWT leva a claim "tv" (users.token_version): trocar a senha incrementa a versão e todos os
tokens emitidos antes deixam de valer (REST e WebSocket). Tokens sem "tv" valem como tv=0.

Falhas de autenticação (REST 401 detail.code; WebSocket fecha com 4401/4403):
- UNAUTHORIZED: token ausente, inválido ou vencido (WS 4401);
- SESSION_REVOKED: token de uma versão anterior, ex.: a senha foi trocada (WS 4401);
- ACCOUNT_DISABLED: usuário desativado ou removido (WS 4403).
"""

from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import User, UserRole

_ALGO = "HS256"
_MAX_USER_ID = 2**31 - 1  # users.id é INTEGER
_bearer = HTTPBearer(auto_error=False)


class AuthFailure(enum.StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    SESSION_REVOKED = "SESSION_REVOKED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"


AUTH_MESSAGES: dict[AuthFailure, str] = {
    AuthFailure.UNAUTHORIZED: "Sessão expirada ou inválida. Entre novamente.",
    AuthFailure.SESSION_REVOKED: (
        "Sua sessão foi encerrada porque a senha foi alterada. Entre novamente."
    ),
    AuthFailure.ACCOUNT_DISABLED: "Seu acesso foi desativado. Fale com o administrador.",
}
FORBIDDEN_MESSAGE = "Apenas administradores podem fazer isso."


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    token_version: int
    expires_at: datetime | None


@dataclass(frozen=True)
class AuthResult:
    """Usuário autenticado (failure None) ou o motivo da recusa (user None)."""

    user: User | None
    failure: AuthFailure | None = None
    claims: TokenClaims | None = None


def hash_password(password: str, iterations: int | None = None) -> str:
    iterations = iterations or settings.password_hash_iterations
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"pbkdf2_sha256${iterations}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iters)
        )
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def create_access_token(user: User) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user.id),
        "name": user.full_name,
        "role": str(user.role),
        "tv": int(user.token_version or 0),
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO), expires_at


def decode_token_claims(token: str) -> TokenClaims | None:
    """Claims de um token com assinatura e validade conferidas; None se inválido/vencido."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
    if not 0 < user_id <= _MAX_USER_ID:
        return None
    version = payload.get("tv", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(exp, timezone.utc) if isinstance(exp, (int, float)) else None
    )
    return TokenClaims(user_id=user_id, token_version=version, expires_at=expires_at)


def decode_access_token(token: str) -> int | None:
    """Retorna o id do usuário ou None se o token for inválido/vencido."""
    claims = decode_token_claims(token)
    return claims.user_id if claims is not None else None


async def check_token(token: str | None, session: AsyncSession) -> AuthResult:
    """Valida o token e diz por que foi recusado. Usado pelo REST, WebSocket e guarda de upload."""
    if not token:
        return AuthResult(None, AuthFailure.UNAUTHORIZED)
    claims = decode_token_claims(token)
    if claims is None:
        return AuthResult(None, AuthFailure.UNAUTHORIZED)
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        return AuthResult(None, AuthFailure.ACCOUNT_DISABLED, claims)
    if claims.token_version != (user.token_version or 0):
        return AuthResult(None, AuthFailure.SESSION_REVOKED, claims)
    return AuthResult(user, None, claims)


async def authenticate_token(token: str | None, session: AsyncSession) -> User | None:
    """Usuário do token, ou None (qualquer motivo de recusa)."""
    return (await check_token(token, session)).user


def auth_error_detail(failure: AuthFailure) -> dict[str, str]:
    return {"code": str(failure), "message": AUTH_MESSAGES[failure]}


def forbidden_detail() -> dict[str, str]:
    return {"code": "FORBIDDEN", "message": FORBIDDEN_MESSAGE}


def _unauthorized(failure: AuthFailure = AuthFailure.UNAUTHORIZED) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=auth_error_detail(failure),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    result = await check_token(creds.credentials if creds else None, session)
    if result.user is None:
        raise _unauthorized(result.failure or AuthFailure.UNAUTHORIZED)
    return result.user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=forbidden_detail())
    return user
