import argparse
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from alphasieve.config import Settings, ensure_storage
from alphasieve.errors import AlphaSieveError
from alphasieve.state import connect

SCHEMA = "alphasieve.response/v1"


@dataclass
class CommandResult:
    data: dict = field(default_factory=dict)
    artifacts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: AlphaSieveError | None = None


@dataclass
class Command:
    name: str
    roles: tuple[str, ...]
    handler: Callable[[argparse.Namespace, "Context"], CommandResult]
    configure: Callable[[argparse.ArgumentParser], None] | None = None
    needs_state: bool = True
    needs_store: bool = False
    help: str = ""


class Context:
    def __init__(self, settings: Settings, command: Command):
        self.settings = settings
        self.command = command
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            ensure_storage(self.settings, need_store=self.command.needs_store)
            self._conn = connect(self.settings.state_db)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


COMMANDS: dict[str, Command] = {}


def command(name: str, roles: tuple[str, ...], configure=None, needs_state=True, needs_store=False, help=""):
    def decorator(fn):
        COMMANDS[name] = Command(name, roles, fn, configure, needs_state, needs_store, help)
        return fn

    return decorator


def envelope(name: str, result: CommandResult | None, error: AlphaSieveError | None) -> dict:
    err = error or (result.error if result else None)
    return {
        "schema": SCHEMA,
        "command": name,
        "status": "error" if err else "ok",
        "data": result.data if result else {},
        "artifacts": result.artifacts if result else [],
        "warnings": result.warnings if result else [],
        "error": None if err is None else {"code": err.code, "message": err.message, "details": err.details},
    }
