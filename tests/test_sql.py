"""Las consultas compilan para MariaDB/MySQL y PostgreSQL."""

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql, postgresql


def test_sql_compiles_for_mysql_and_postgres() -> None:
    """Las consultas ORM compilan para MariaDB/MySQL y PostgreSQL."""
    from sqlalchemy import func

    from homeassistant.components.recorder.db_schema import States

    stmt = select(
        States.metadata_id, func.count(), func.coalesce(func.sum(func.length(States.state)), 0)
    ).group_by(States.metadata_id)
    for dialect in (mysql.dialect(), postgresql.dialect()):
        sql = str(stmt.compile(dialect=dialect))
        assert "length" in sql.lower()


class _Result(list):
    def one(self):
        return (0, 0)

    def scalar(self):
        return None


class _FakeSession:
    """Compila cada sentencia para MySQL y PostgreSQL en lugar de ejecutarla."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.compiled: list[str] = []

    def get_bind(self):
        class _B:
            pass

        b = _B()
        b.dialect = mysql.dialect() if self.name == "mysql" else postgresql.dialect()
        b.dialect.name = self.name
        return b

    def execute(self, stmt):
        dialect = mysql.dialect() if self.name == "mysql" else postgresql.dialect()
        self.compiled.append(str(stmt.compile(dialect=dialect)))
        return _Result()


@pytest.mark.parametrize("name", ["mysql", "postgresql"])
def test_all_queries_compile(name: str) -> None:
    from custom_components.db_size_ranking.db import query_calibration, query_raw_usage

    session = _FakeSession(name)
    query_raw_usage(session, None)
    query_raw_usage(session, 1_700_000_000.0)
    query_calibration(session)
    assert len(session.compiled) > 10
    for sql in session.compiled:
        print(sql.replace("\n", " "))
