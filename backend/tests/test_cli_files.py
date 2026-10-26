"""CLI: falhas de arquivo local não podem ser relatadas como falha do banco de dados.

Na imagem Docker a pasta de trabalho (/app/backend) é do root e o processo roda como `agenda`:
um `export-xlsx` sem `-o` não consegue gravar ali. Antes, o PermissionError caía no mesmo
tratamento das falhas de conexão e a mensagem dizia "não foi possível acessar o banco de dados".

Os comandos usam asyncio.run(): rodam numa thread para não colidir com o loop do pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import pathlib

from app import cli
from tests.excel_builders import reload

pytest_plugins = ("tests.excel_fixtures",)


async def run_cli(*argv: str) -> int:
    return await asyncio.to_thread(cli.main, list(argv))


async def test_export_to_an_unwritable_path_blames_the_file_not_the_database(
    capsys, rooms, tmp_path
):
    blocker = tmp_path / "isto-e-um-arquivo"
    blocker.write_text("não é uma pasta")
    output = blocker / "agenda.xlsx"  # a "pasta" de destino é um arquivo: mkdir falha

    assert await run_cli("export-xlsx", "--date", "2026-10-26", "-o", str(output)) == 1

    err = capsys.readouterr().err
    assert "não foi possível gravar a planilha em" in err
    assert "-o /tmp/agenda.xlsx" in err  # diz como resolver no Docker
    assert "banco de dados" not in err
    assert not output.exists()


async def test_export_without_output_uses_the_temp_dir_when_cwd_is_read_only(
    capsys, monkeypatch, rooms, tmp_path
):
    read_only = tmp_path / "app-backend"
    temp_dir = tmp_path / "tmp"
    read_only.mkdir()
    temp_dir.mkdir()
    monkeypatch.chdir(read_only)
    monkeypatch.setattr(cli, "_writable_dir", lambda path: path.resolve() != read_only.resolve())
    monkeypatch.setattr(cli.tempfile, "gettempdir", lambda: str(temp_dir))

    assert await run_cli("export-xlsx", "--date", "2026-10-26") == 0
    assert await run_cli("export-xlsx", "--date", "2026-10-26", "--week") == 0

    out = capsys.readouterr().out
    day_file = temp_dir / "agenda-2026-10-26.xlsx"
    week_file = temp_dir / "agenda-semana-2026-10-26.xlsx"
    assert f"Planilha gerada: {day_file}" in out
    assert reload(day_file.read_bytes()).sheetnames == ["Seg 26-10"]
    assert len(reload(week_file.read_bytes()).sheetnames) == 5
    assert list(read_only.iterdir()) == []


async def test_export_without_output_still_writes_to_a_writable_cwd(
    capsys, monkeypatch, rooms, tmp_path
):
    monkeypatch.chdir(tmp_path)

    assert await run_cli("export-xlsx", "--date", "2026-10-26") == 0

    assert (tmp_path / "agenda-2026-10-26.xlsx").is_file()
    assert "Planilha gerada: agenda-2026-10-26.xlsx" in capsys.readouterr().out


async def test_import_of_an_unreadable_file_is_a_file_error(capsys, monkeypatch, tmp_path):
    path = tmp_path / "agenda.xlsx"
    path.write_bytes(b"PK")
    original = pathlib.Path.read_bytes

    def read_bytes(self: pathlib.Path) -> bytes:
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)

    assert await run_cli("import-xlsx", str(path)) == 1

    err = capsys.readouterr().err
    assert f"não foi possível ler o arquivo {path}: Permission denied." in err
    assert "Traceback" not in err


def test_os_errors_with_a_file_name_are_file_errors_and_the_rest_database_errors(capsys):
    async def fails_on_a_file(session) -> int:
        raise PermissionError(13, "Permission denied", "/app/backend/agenda.xlsx")

    async def fails_to_connect(session) -> int:
        raise ConnectionRefusedError(111, "Connect call failed ('127.0.0.1', 5432)")

    assert cli._run(fails_on_a_file) == 1
    err = capsys.readouterr().err
    assert "não foi possível acessar o arquivo /app/backend/agenda.xlsx: Permission denied." in err
    assert "banco de dados" not in err

    assert cli._run(fails_to_connect) == 1
    assert "não foi possível acessar o banco de dados" in capsys.readouterr().err
