"""Login, usuário atual e cadastro de usuários (5.1 e 5.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import settings
from app.models import User, UserRole
from app.services import users as users_service
from app.services.errors import BusinessError
from tests.core_support import error_code


async def _login(client, username: str, password: str):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


async def test_login_success_updates_last_login(client, receptionist, session):
    assert receptionist.last_login_at is None
    resp = await _login(client, "ANA", "senha123")  # login sem diferenciar maiúsculas
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer" and body["access_token"]
    assert body["user"]["id"] == receptionist.id and body["user"]["role"] == "RECEPTIONIST"
    assert body["user"]["last_login_at"] is not None
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc)

    await session.refresh(receptionist)
    assert receptionist.last_login_at is not None

    resp = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert resp.status_code == 200 and resp.json()["username"] == "ana"


@pytest.mark.parametrize(("username", "password"), [("ana", "errada"), ("ninguem", "senha123")])
async def test_login_invalid_credentials(client, receptionist, username, password):
    resp = await _login(client, username, password)
    assert resp.status_code == 401
    assert resp.json()["detail"] == {
        "code": "INVALID_CREDENTIALS",
        "message": "Usuário ou senha inválidos.",
    }


async def test_login_inactive_user(client, make_user):
    await make_user("inativa", "Inativa", is_active=False)
    resp = await _login(client, "inativa", "senha123")
    assert resp.status_code == 401 and error_code(resp) == "INVALID_CREDENTIALS"


async def test_login_validation(client):
    resp = await client.post("/api/auth/login", json={"username": "", "password": "x"})
    assert resp.status_code == 422


async def test_unknown_user_still_hashes(monkeypatch, session):
    """Usuário inexistente também passa pelo PBKDF2 (tempo de resposta não revela quem existe)."""
    calls: list[str] = []
    original = users_service.verify_password

    def spy(password: str, stored: str) -> bool:
        calls.append(stored)
        return original(password, stored)

    monkeypatch.setattr(users_service, "verify_password", spy)
    with pytest.raises(BusinessError) as exc:
        await users_service.authenticate(session, "fantasma", "qualquer")
    assert exc.value.status_code == 401
    assert len(calls) == 1 and calls[0].startswith("pbkdf2_sha256$")


async def test_me_requires_valid_token(client, auth, make_user, session):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401 and error_code(resp) == "UNAUTHORIZED"
    resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer nao-e-um-jwt"})
    assert resp.status_code == 401
    expired = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    ghost = User(
        id=999, username="ghost", full_name="Fantasma", password_hash="x", role=UserRole.ADMIN
    )
    resp = await client.get("/api/auth/me", headers=auth(ghost))
    assert resp.status_code == 401

    user = await make_user("vai-sair", "Vai Sair")
    headers = auth(user)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    user.is_active = False
    await session.commit()
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401


async def test_users_endpoints_are_admin_only(client, auth, receptionist):
    h = auth(receptionist)
    assert (await client.get("/api/users", headers=h)).status_code == 403
    resp = await client.post(
        "/api/users", json={"username": "x1", "full_name": "Xis", "password": "123456"}, headers=h
    )
    assert resp.status_code == 403
    resp = await client.patch(f"/api/users/{receptionist.id}", json={"role": "ADMIN"}, headers=h)
    assert resp.status_code == 403 and error_code(resp) == "FORBIDDEN"
    assert (await client.get("/api/users")).status_code == 401


async def test_create_and_list_users(client, auth, admin, receptionist):
    h = auth(admin)
    resp = await client.post(
        "/api/users",
        json={"username": "carla.m", "full_name": "  Carla   Mendes ", "password": "segredo1"},
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["username"] == "carla.m" and created["full_name"] == "Carla Mendes"
    assert created["role"] == "RECEPTIONIST" and created["is_active"] is True
    assert "password" not in created and "password_hash" not in created
    assert (await _login(client, "carla.m", "segredo1")).status_code == 200

    resp = await client.post(
        "/api/users",
        json={"username": "CARLA.M", "full_name": "Outra", "password": "segredo1"},
        headers=h,
    )
    assert resp.status_code == 409 and error_code(resp) == "USERNAME_TAKEN"
    resp = await client.post(
        "/api/users",
        json={"username": "com espaço", "full_name": "Ok", "password": "segredo1"},
        headers=h,
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/users", json={"username": "curta", "full_name": "Ok", "password": "123"}, headers=h
    )
    assert resp.status_code == 422

    resp = await client.get("/api/users", headers=h)
    assert [u["username"] for u in resp.json()] == ["admin", "ana", "carla.m"]


async def test_update_user(client, auth, admin, receptionist):
    h = auth(admin)
    resp = await client.patch(
        f"/api/users/{receptionist.id}",
        json={"full_name": "Ana Paula", "role": "ADMIN", "password": "nova-senha"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["full_name"], resp.json()["role"]) == ("Ana Paula", "ADMIN")
    assert (await _login(client, "ana", "senha123")).status_code == 401
    assert (await _login(client, "ana", "nova-senha")).status_code == 200

    # um admin pode rebaixar/desativar OUTRO admin
    resp = await client.patch(
        f"/api/users/{receptionist.id}",
        json={"role": "RECEPTIONIST", "is_active": False},
        headers=h,
    )
    assert resp.json()["is_active"] is False and resp.json()["role"] == "RECEPTIONIST"
    assert (await _login(client, "ana", "nova-senha")).status_code == 401

    resp = await client.patch("/api/users/999", json={"full_name": "Ninguém"}, headers=h)
    assert resp.status_code == 404 and error_code(resp) == "USER_NOT_FOUND"


@pytest.mark.parametrize("change", [{"role": "RECEPTIONIST"}, {"is_active": False}])
async def test_admin_cannot_demote_self(client, auth, admin, change):
    resp = await client.patch(f"/api/users/{admin.id}", json=change, headers=auth(admin))
    assert resp.status_code == 409 and error_code(resp) == "CANNOT_DEMOTE_SELF"


async def test_admin_can_edit_own_name(client, auth, admin):
    resp = await client.patch(
        f"/api/users/{admin.id}",
        json={"full_name": "Admin TI", "role": "ADMIN", "is_active": True},
        headers=auth(admin),
    )
    assert resp.status_code == 200 and resp.json()["full_name"] == "Admin TI"


# ---------------------------------------------------------------- revogação de tokens (D2/D3)

REVOKED = {
    "code": "SESSION_REVOKED",
    "message": "Sua sessão foi encerrada porque a senha foi alterada. Entre novamente.",
}
DISABLED = {
    "code": "ACCOUNT_DISABLED",
    "message": "Seu acesso foi desativado. Fale com o administrador.",
}


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


async def _me(client, headers):
    return await client.get("/api/auth/me", headers=headers)


async def test_password_change_revokes_tokens_issued_before(client, auth, admin, receptionist):
    old = auth(receptionist)
    assert _claims(old["Authorization"].removeprefix("Bearer "))["tv"] == 0
    resp = await client.patch(
        f"/api/users/{receptionist.id}", json={"full_name": "Ana Paula"}, headers=auth(admin)
    )
    assert resp.status_code == 200
    assert (await _me(client, old)).status_code == 200  # editar dados não derruba ninguém

    resp = await client.patch(
        f"/api/users/{receptionist.id}", json={"password": "outra-senha"}, headers=auth(admin)
    )
    assert resp.status_code == 200
    resp = await _me(client, old)
    assert resp.status_code == 401 and resp.json()["detail"] == REVOKED
    assert resp.headers["www-authenticate"] == "Bearer"
    # o token antigo também não grava nada
    resp = await client.post("/api/rooms", json={"name": "X", "room_type": "NORMAL"}, headers=old)
    assert resp.status_code == 401

    login = await _login(client, "ana", "outra-senha")
    token = login.json()["access_token"]
    assert _claims(token)["tv"] == 1
    assert (await _me(client, {"Authorization": f"Bearer {token}"})).status_code == 200


async def test_deactivation_disables_and_reactivation_does_not_revive_old_tokens(
    client, auth, admin, receptionist
):
    old = auth(receptionist)
    h = auth(admin)
    resp = await client.patch(f"/api/users/{receptionist.id}", json={"is_active": False}, headers=h)
    assert resp.status_code == 200
    resp = await _me(client, old)
    assert resp.status_code == 401 and resp.json()["detail"] == DISABLED
    # login de usuário inativo continua sendo INVALID_CREDENTIALS (não revela a situação da conta)
    resp = await _login(client, "ana", "senha123")
    assert resp.status_code == 401 and error_code(resp) == "INVALID_CREDENTIALS"

    resp = await client.patch(f"/api/users/{receptionist.id}", json={"is_active": True}, headers=h)
    assert resp.status_code == 200
    resp = await _me(client, old)  # token de antes da desativação (vazado?) não volta a valer
    assert resp.status_code == 401 and resp.json()["detail"] == REVOKED
    assert (await _login(client, "ana", "senha123")).status_code == 200


async def test_deleted_user_gets_account_disabled(client, auth, make_user, session):
    from sqlalchemy import delete

    user = await make_user("sumiu", "Usuária Removida")
    headers = auth(user)
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()
    resp = await _me(client, headers)
    assert resp.status_code == 401 and resp.json()["detail"] == DISABLED


async def test_version_claim_rules(client, receptionist, session):
    def token(**claims) -> dict[str, str]:
        payload = {
            "sub": str(receptionist.id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            **claims,
        }
        return {"Authorization": f"Bearer {jwt.encode(payload, settings.jwt_secret, 'HS256')}"}

    # tokens emitidos antes da claim "tv" existir valem como versão 0
    assert (await _me(client, token())).status_code == 200
    for bad in ("0", True, 1.5, None):
        resp = await _me(client, token(tv=bad))
        assert resp.status_code == 401 and error_code(resp) == "UNAUTHORIZED", bad
    await users_service.set_password(receptionist, "senha-trocada")
    await session.commit()
    assert error_code(await _me(client, token())) == "SESSION_REVOKED"
    assert (await _me(client, token(tv=1))).status_code == 200
