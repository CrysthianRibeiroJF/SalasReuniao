"""Arquivos de implantação do repositório (raiz): modelos sem segredo utilizável e build reproduzível.

- .env.docker.example é público: um valor preenchido nele vira credencial conhecida em toda
  instalação que só "copia e sobe" (o admin "troque-no-primeiro-acesso" entrava como ADMIN).
- A imagem instala as dependências com backend/constraints.txt: reconstruir uma versão antiga
  (retorno de versão, docs/OPERACAO.md 5.2) traz as mesmas bibliotecas que foram testadas.
"""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _NAME.match(line)
        assert match, f"linha inesperada em {path.name}: {line!r}"
        names.add(_canonical(match.group(1)))
    return names


def _pins() -> dict[str, str]:
    pins = {}
    for line in (BACKEND / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, sep, version = line.partition("==")
            assert sep and version, f"constraints.txt deve ter só versões exatas: {line!r}"
            pins[_canonical(name)] = version
    return pins


def test_docker_env_template_ships_no_usable_secret():
    values = dotenv_values(ROOT / ".env.docker.example")

    assert values["POSTGRES_PASSWORD"] == ""
    assert values["AGENDA_JWT_SECRET"] == ""
    # Vazio: o init-db não cria ninguém e orienta o create-user (senha digitada no terminal).
    assert values["AGENDA_BOOTSTRAP_ADMIN_PASSWORD"] == ""
    assert values["AGENDA_BOOTSTRAP_ADMIN_USERNAME"] == "admin"


def test_compose_refuses_to_start_without_the_secrets():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    db_env = compose["services"]["db"]["environment"]
    app_env = compose["services"]["app"]["environment"]

    assert db_env["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    assert app_env["AGENDA_JWT_SECRET"].startswith("${AGENDA_JWT_SECRET:?")
    assert "${POSTGRES_PASSWORD:?" in app_env["AGENDA_DATABASE_URL"]
    assert app_env["AGENDA_ENV"] == "production"


def test_every_backend_dependency_has_an_exact_pin():
    pins = _pins()
    wanted = _requirement_names(BACKEND / "requirements.txt") | _requirement_names(
        BACKEND / "requirements-dev.txt"
    )

    assert sorted(wanted - pins.keys()) == []
    # uvicorn[standard] no Linux (imagem) puxa o uvloop, que o pip freeze do Windows não lista.
    assert "uvloop" in pins


def test_pins_are_the_versions_under_test():
    pins = _pins()
    installed = {}
    for name in _requirement_names(BACKEND / "requirements.txt"):
        try:
            installed[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue

    mismatched = {n: (v, pins.get(n)) for n, v in installed.items() if pins.get(n) != v}
    assert mismatched == {}, "atualize backend/constraints.txt (veja o cabeçalho do arquivo)"


def test_image_and_dev_setup_install_with_the_constraints():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dev_script = (ROOT / "scripts" / "dev.ps1").read_text(encoding="utf-8-sig")

    assert "COPY backend/requirements.txt backend/constraints.txt ./" in dockerfile
    assert "RUN pip install -r requirements.txt -c constraints.txt" in dockerfile
    assert "-r $requirements -c $constraints" in dev_script
