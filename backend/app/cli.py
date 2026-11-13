"""Linha de comando administrativa. Execute de dentro de backend/:

python -m app.cli init-db
python -m app.cli create-user --username ana --name "Ana Souza" --role RECEPTIONIST
python -m app.cli reset-password --username ana   (pede a nova senha; encerra as sessões abertas)
python -m app.cli seed-demo                       (somente AGENDA_ENV=development)
python -m app.cli import-xlsx planilha.xlsx [--commit] [--include-past] [--user admin]
python -m app.cli export-xlsx --date 2026-10-26 [--week] [-o agenda.xlsx]

Com AGENDA_REDIS_URL definido, `import-xlsx --commit` avisa as instâncias do servidor pelo Redis
(evento BOOKINGS_IMPORTED) e os navegadores abertos atualizam a grade sozinhos.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, make_url, select
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal, engine
from app.models import User, UserRole
from app.schemas import ImportReport, UserCreate, UserUpdate
from app.services import users as users_service
from app.services.errors import BusinessError
from app.timeutil import today_local, week_monday


class CommandError(Exception):
    """Erro esperado: a mensagem (pt-BR) é exibida sem traceback."""


# ---------------------------------------------------------------- infraestrutura


def _configure_console() -> None:
    # Console/pipe do Windows pode estar em cp1252/cp850: força UTF-8 para acentos não quebrarem.
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def _error(message: str) -> None:
    print(f"Erro: {message}", file=sys.stderr)


def _database_error(exc: BaseException) -> None:
    url = make_url(settings.database_url).render_as_string(hide_password=True)
    _error(f"não foi possível acessar o banco de dados ({url}): {exc}")


def _os_error(exc: OSError) -> None:
    """OSError com nome de arquivo é do disco local (permissão, pasta inexistente...); sem nome,
    é de rede/conexão (recusada, DNS, tempo esgotado), ou seja, do banco de dados."""
    if exc.filename is not None:
        _error(f"não foi possível acessar o arquivo {exc.filename}: {exc.strerror or exc}.")
    else:
        _database_error(exc)


async def _with_session(fn: Callable[..., Awaitable[int]], *args: Any) -> int:
    try:
        async with SessionLocal() as session:
            return await fn(session, *args)
    finally:
        await engine.dispose()


def _run(fn: Callable[..., Awaitable[int]], *args: Any) -> int:
    """Executa um comando assíncrono com uma sessão e converte falhas esperadas em mensagens."""
    try:
        return asyncio.run(_with_session(fn, *args))
    except (CommandError, BusinessError) as exc:
        _error(exc.message if isinstance(exc, BusinessError) else str(exc))
    except ProgrammingError as exc:
        if "does not exist" in str(exc.orig):
            _error("as tabelas não existem. Execute primeiro: python -m app.cli init-db")
        else:
            raise
    except DBAPIError as exc:
        _database_error(exc)
    except OSError as exc:
        _os_error(exc)
    return 1


def _validation_message(exc: ValidationError) -> str:
    hints = {
        "username": "usuário: de 2 a 50 caracteres (letras, números, ponto, hífen ou sublinhado)",
        "full_name": "nome: de 2 a 150 caracteres",
        "password": "senha: no mínimo 6 caracteres",
        "role": "papel: ADMIN ou RECEPTIONIST",
    }
    fields = dict.fromkeys(str(err["loc"][0]) for err in exc.errors() if err.get("loc"))
    return "; ".join(hints.get(f, f) for f in fields) or str(exc)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"data inválida: {value!r} (use AAAA-MM-DD)") from None


# ---------------------------------------------------------------- init-db / create-user

# Admin inicial em produção: senha longa e que não seja de exemplo/demonstração (o .env de modelo e
# o README são públicos; quem os leu não pode entrar como administrador).
MIN_BOOTSTRAP_PASSWORD_LENGTH = 12
KNOWN_PASSWORDS = frozenset(
    {
        "admin",
        "admin123",
        "admin1234",
        "administrador",
        "recepcao123",
        "senha",
        "senha123",
        "senha1234",
        "123456",
        "1234567",
        "12345678",
        "123456789",
        "1234567890",
        "password",
        "password123",
        "changeme",
        "mudar123",
        "troque-no-primeiro-acesso",
        "troque-esta-senha",
    }
)


def bootstrap_password_problem(username: str, password: str) -> str | None:
    """Motivo (pt-BR) para recusar a senha do administrador inicial em produção, ou None."""
    normalized = password.strip().lower()
    if normalized in KNOWN_PASSWORDS or normalized.startswith("troque"):
        return (
            "AGENDA_BOOTSTRAP_ADMIN_PASSWORD é uma senha de exemplo ou de demonstração conhecida. "
            "Defina uma senha forte e exclusiva no .env e rode de novo"
        )
    if normalized == username.strip().lower():
        return "AGENDA_BOOTSTRAP_ADMIN_PASSWORD não pode ser igual ao nome de usuário"
    if len(password) < MIN_BOOTSTRAP_PASSWORD_LENGTH:
        return (
            f"AGENDA_BOOTSTRAP_ADMIN_PASSWORD precisa ter pelo menos "
            f"{MIN_BOOTSTRAP_PASSWORD_LENGTH} caracteres em produção (tem {len(password)})"
        )
    return None


def cmd_init_db(args: argparse.Namespace) -> int:
    from app.migrate import upgrade_head

    print("Aplicando migrations do banco de dados...")
    try:
        upgrade_head()
    except DBAPIError as exc:
        _database_error(exc)
        return 1
    except OSError as exc:
        _os_error(exc)
        return 1
    print("Banco de dados atualizado.")
    code = _run(_bootstrap_admin)
    if code == 0 and settings.demo_seed:
        code = _run(_seed_demo_server)
    return code


async def _seed_demo_server(session: AsyncSession) -> int:
    from app.seed import seed_demo

    summary = await seed_demo(session, include_admin=False)
    print(
        "AGENDA_DEMO_SEED ativo: "
        f"{len(summary.rooms_created)} sala(s), {len(summary.users_created)} usuário(s) e "
        f"{summary.bookings_created} reserva(s) de demonstração criados."
    )
    return 0


async def _bootstrap_admin(session: AsyncSession) -> int:
    count = await session.scalar(select(func.count()).select_from(User))
    if count:
        print(f"{count} usuário(s) já cadastrado(s): administrador inicial não é necessário.")
        return 0
    username = settings.bootstrap_admin_username
    password = settings.bootstrap_admin_password
    if not username or not password:
        print(
            "Nenhum usuário cadastrado. Defina AGENDA_BOOTSTRAP_ADMIN_USERNAME e "
            "AGENDA_BOOTSTRAP_ADMIN_PASSWORD e rode init-db novamente, ou use:\n"
            '  python -m app.cli create-user --username admin --name "Administrador" --role ADMIN'
        )
        return 0
    if settings.env == "production":
        problem = bootstrap_password_problem(username, password)
        if problem is not None:
            raise CommandError(
                f"administrador inicial não criado: {problem}. Outra opção: deixe "
                "AGENDA_BOOTSTRAP_ADMIN_PASSWORD vazio e crie o administrador com "
                '`python -m app.cli create-user --username admin --name "Administrador" '
                "--role ADMIN` (a senha é pedida no terminal)."
            )
    try:
        data = UserCreate(
            username=username,
            full_name=settings.bootstrap_admin_name,
            password=password,
            role=UserRole.ADMIN,
        )
    except ValidationError as exc:
        raise CommandError(f"administrador inicial inválido — {_validation_message(exc)}") from None
    await users_service.create_user(session, data)
    print(f'Administrador inicial "{username}" criado. Troque a senha após o primeiro acesso.')
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    password = args.password
    if password is None:
        password = getpass.getpass("Senha: ")
        if getpass.getpass("Confirme a senha: ") != password:
            _error("as senhas não conferem.")
            return 1
    try:
        data = UserCreate(
            username=args.username, full_name=args.name, password=password, role=UserRole(args.role)
        )
    except ValidationError as exc:
        _error(_validation_message(exc))
        return 1
    return _run(_create_user, data)


async def _create_user(session: AsyncSession, data: UserCreate) -> int:
    user = await users_service.create_user(session, data)
    role = "administrador" if user.role == UserRole.ADMIN else "recepcionista"
    print(f'Usuário "{user.username}" ({user.full_name}, {role}) criado.')
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    password = args.password
    if password is None:
        password = getpass.getpass("Nova senha: ")
        if getpass.getpass("Confirme a nova senha: ") != password:
            _error("as senhas não conferem.")
            return 1
    try:
        UserUpdate(password=password)
    except ValidationError as exc:
        _error(_validation_message(exc))
        return 1
    return _run(_reset_password, args.username, password)


async def _reset_password(session: AsyncSession, username: str, password: str) -> int:
    user = await users_service.get_by_username(session, username)
    if user is None:
        raise CommandError(f'usuário "{username}" não encontrado.')
    await users_service.set_password(user, password)
    await session.commit()
    print(
        f'Senha de "{user.username}" alterada. As sessões abertas desse usuário foram encerradas '
        "(será preciso entrar de novo)."
    )
    if not user.is_active:
        print("Atenção: a conta está desativada; reative-a na tela de usuários para poder entrar.")
    return 0


# ---------------------------------------------------------------- seed-demo


def cmd_seed_demo(args: argparse.Namespace) -> int:
    if settings.env != "development":
        _error(
            "seed-demo só pode ser executado com AGENDA_ENV=development "
            f"(ambiente atual: {settings.env})."
        )
        return 1
    return _run(_seed_demo)


async def _seed_demo(session: AsyncSession) -> int:
    from app.seed import DEMO_USERS, seed_demo

    summary = await seed_demo(session)
    print("Dados de demonstração:")
    print(f"  salas criadas: {len(summary.rooms_created)}")
    print(f"  usuários criados: {', '.join(summary.users_created) or 'nenhum (já existiam)'}")
    week = f"{summary.week_start:%d/%m/%Y}" if summary.week_start else "-"
    print(
        f"  reservas na semana de {week}: {summary.bookings_created} criadas, "
        f"{summary.bookings_skipped} já existiam/horário ocupado"
    )
    print("Credenciais de demonstração (somente desenvolvimento):")
    for username, _name, password, role in DEMO_USERS:
        print(f"  {username} / {password} ({role})")
    return 0


# ---------------------------------------------------------------- Excel


_SHEET_STATUS = {
    "IMPORTED": "importada",
    "DRY_RUN": "simulada",
    "SKIPPED_PAST": "ignorada (data passada)",
    "SKIPPED_UNRECOGNIZED": "ignorada (data não reconhecida)",
    "ERROR": "erro",
}


def format_import_report(report: ImportReport) -> str:
    title = (
        "SIMULAÇÃO DA IMPORTAÇÃO (nada foi gravado)" if report.dry_run else "IMPORTAÇÃO CONCLUÍDA"
    )
    done = "importáveis" if report.dry_run else "importadas"
    lines = [title, "=" * len(title)]
    for sheet in report.sheets:
        when = f" ({sheet.date:%d/%m/%Y})" if sheet.date else ""
        status = _SHEET_STATUS.get(sheet.status, sheet.status)
        lines.append(f'Aba "{sheet.sheet_name}"{when}: {status}')
        if sheet.bookings_found or sheet.status in ("IMPORTED", "DRY_RUN"):
            lines.append(
                f"    reservas: {sheet.bookings_found} encontradas, "
                f"{sheet.bookings_imported} {done}, {sheet.bookings_skipped} puladas"
            )
        lines.extend(f"    aviso: {warning}" for warning in sheet.warnings)
    if not report.sheets:
        lines.append("Nenhuma aba encontrada na planilha.")
    lines.append("")
    lines.append(
        f"Total: {report.total_found} encontradas, {report.total_imported} {done}, "
        f"{report.total_skipped} puladas"
    )
    if report.unknown_rooms:
        lines.append("Salas não encontradas no cadastro: " + ", ".join(report.unknown_rooms))
    if report.errors:
        lines.append("Erros:")
        lines.extend(f"  - {error}" for error in report.errors)
    if report.dry_run:
        lines.append("Para gravar de verdade, execute novamente com --commit.")
    return "\n".join(lines)


def cmd_import_xlsx(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        _error(f"arquivo não encontrado: {path}")
        return 1
    try:
        from app.excel.importer import import_workbook
    except ImportError:
        _error("o módulo de importação Excel (app.excel.importer) não está disponível.")
        return 1
    try:
        source = path.read_bytes()
    except OSError as exc:
        _error(f"não foi possível ler o arquivo {path}: {exc.strerror or exc}.")
        return 1
    return _run(_import_xlsx, import_workbook, source, args)


async def _import_xlsx(
    session: AsyncSession,
    import_workbook: Callable[..., Awaitable[ImportReport]],
    source: bytes,
    args: argparse.Namespace,
) -> int:
    from app.excel.locks import external_lock_store
    from app.realtime.external import collect_events

    user = await _import_user(session, args.user)
    # Sem servidor neste processo: os eventos publicados pela importação são guardados e
    # repassados às instâncias no fim (BOOKINGS_IMPORTED, igual à importação pela tela).
    # Com Redis, as travas de seleção das telas abertas também são conferidas (reserva que
    # alguém está selecionando na grade é pulada com aviso).
    async with external_lock_store(settings.redis_url) as lock_store:
        with collect_events() as collected:
            report = await import_workbook(
                session,
                source,
                user=user,
                dry_run=not args.commit,
                include_past=args.include_past,
                lock_store=lock_store,
            )
    print(format_import_report(report))
    if collected.envelopes:
        await _notify_open_browsers(collected.envelopes)
    return 1 if report.errors else 0


async def _notify_open_browsers(envelopes: list[dict[str, Any]]) -> None:
    from app.realtime.external import RelayError, relay_to_instances

    if not settings.redis_url:
        print(
            "\nObservação: sem Redis configurado (AGENDA_REDIS_URL), a linha de comando não "
            "consegue avisar o servidor. Quem estiver com a agenda aberta precisa recarregar a "
            "página (F5) para ver as reservas importadas. A importação pela tela "
            "(Administração › Importar planilha) atualiza todos na hora."
        )
        return
    try:
        receivers = await relay_to_instances(settings.redis_url, envelopes)
    except RelayError as exc:
        print(
            f"Aviso: as reservas foram gravadas, mas não foi possível avisar os navegadores "
            f"abertos: {exc}. Peça para recarregarem a página (F5).",
            file=sys.stderr,
        )
        return
    if receivers:
        print(
            f"\nServidor avisado ({receivers} instância(s)): os navegadores abertos atualizam a "
            "grade sozinhos."
        )
    else:
        print(
            "\nNenhuma instância do servidor está ouvindo o Redis agora. Se alguém estiver com a "
            "agenda aberta, peça para recarregar a página (F5)."
        )


async def _import_user(session: AsyncSession, username: str | None) -> User:
    if username:
        user = await users_service.get_by_username(session, username)
        if user is None or not user.is_active:
            raise CommandError(f'usuário "{username}" não encontrado ou inativo.')
        return user
    user = await session.scalar(
        select(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        .order_by(User.id)
        .limit(1)
    )
    if user is None:
        raise CommandError("nenhum administrador ativo encontrado; informe --user.")
    return user


def cmd_export_xlsx(args: argparse.Namespace) -> int:
    try:
        from app.excel.exporter import export_day, export_week
    except ImportError:
        _error("o módulo de exportação Excel (app.excel.exporter) não está disponível.")
        return 1
    d: date = args.date or today_local()
    if args.week:
        monday = week_monday(d)
        output = _output_path(args.output, f"agenda-semana-{monday.isoformat()}.xlsx")
        return _run(_export, export_week, monday, output)
    output = _output_path(args.output, f"agenda-{d.isoformat()}.xlsx")
    return _run(_export, export_day, d, output)


def _writable_dir(path: Path) -> bool:
    return os.access(path, os.W_OK)


def _output_path(requested: str | None, default_name: str) -> Path:
    """Arquivo pedido com -o; sem -o, na pasta atual ou, se ela não aceita gravação (ex.:
    /app/backend na imagem Docker, que é do root), na pasta temporária do sistema."""
    if requested:
        return Path(requested)
    if _writable_dir(Path.cwd()):
        return Path(default_name)
    return Path(tempfile.gettempdir()) / default_name


async def _export(
    session: AsyncSession, exporter: Callable[..., Awaitable[bytes]], d: date, output: Path
) -> int:
    content = await exporter(session, d)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    except OSError as exc:
        raise CommandError(
            f"não foi possível gravar a planilha em {output}: {exc.strerror or exc}. Informe com "
            "-o um arquivo numa pasta em que você possa gravar (no Docker: -o /tmp/agenda.xlsx e "
            "depois docker compose cp app:/tmp/agenda.xlsx .)."
        ) from None
    print(f"Planilha gerada: {output} ({len(content):,} bytes)".replace(",", "."))
    return 0


# ---------------------------------------------------------------- argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Administração da Agenda de Salas."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="comando")

    p = sub.add_parser("init-db", help="aplica as migrations e cria o administrador inicial")
    p.set_defaults(handler=cmd_init_db)

    p = sub.add_parser("create-user", help="cria um usuário")
    p.add_argument("--username", required=True, help="login (ex.: ana.souza)")
    p.add_argument("--name", required=True, help="nome completo")
    p.add_argument("--role", required=True, choices=[r.value for r in UserRole], help="papel")
    p.add_argument("--password", help="senha (se omitida, será pedida no terminal)")
    p.set_defaults(handler=cmd_create_user)

    p = sub.add_parser(
        "reset-password", help="define uma nova senha (encerra as sessões abertas do usuário)"
    )
    p.add_argument("--username", required=True, help="login do usuário")
    p.add_argument("--password", help="nova senha (se omitida, será pedida no terminal)")
    p.set_defaults(handler=cmd_reset_password)

    p = sub.add_parser("seed-demo", help="cria salas, usuários e reservas de demonstração (dev)")
    p.set_defaults(handler=cmd_seed_demo)

    p = sub.add_parser("import-xlsx", help="importa a planilha legada (padrão: simulação)")
    p.add_argument("path", help="caminho do arquivo .xlsx")
    p.add_argument("--commit", action="store_true", help="grava de verdade (sem isso só simula)")
    p.add_argument("--include-past", action="store_true", help="inclui abas com datas passadas")
    p.add_argument("--user", help="usuário registrado como autor (padrão: primeiro admin ativo)")
    p.set_defaults(handler=cmd_import_xlsx)

    p = sub.add_parser("export-xlsx", help="exporta a grade do dia ou da semana para .xlsx")
    p.add_argument("--date", type=_parse_date, help="dia AAAA-MM-DD (padrão: hoje)")
    p.add_argument("--week", action="store_true", help="exporta a semana (seg–sex) do dia")
    p.add_argument(
        "-o",
        "--output",
        help="arquivo de saída (padrão: agenda-AAAA-MM-DD.xlsx na pasta atual ou, se ela não "
        "aceitar gravação, na pasta temporária)",
    )
    p.set_defaults(handler=cmd_export_xlsx)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("\nInterrompido.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
