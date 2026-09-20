"""SQL executor boundary for the structured-data layer — Phase 6B.1.

:class:`SqlExecutor` is the only thing :class:`~src.structured.service.
StructuredQueryService` depends on, so unit tests inject a fake and never
need a real SQL Warehouse. :class:`DatabricksSqlExecutor` is the one real
implementation, built on the Databricks SDK's Statement Execution API and
the same ``WorkspaceClient`` (service-principal identity inside the App)
that the rest of the codebase already uses — no new auth path, no SQL
framework. It is intentionally not exercised against a live Warehouse in
Phase 6B.1.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    Disposition,
    ExecuteStatementRequestOnWaitTimeout,
    Format,
    StatementParameterListItem,
    StatementState,
)

__all__ = ["SqlExecutor", "DatabricksSqlExecutor", "SqlExecutionError"]


class SqlExecutionError(Exception):
    """A statement did not complete successfully."""


class SqlExecutor(Protocol):
    def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Run one read-only statement with named parameters and return
        rows as ``{column_name: value}`` mappings."""
        ...


def _parameter(name: str, value: Any) -> StatementParameterListItem:
    # Only the two value kinds the allowlisted queries use. Anything else
    # is a programming error, not something to guess a SQL type for.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SqlExecutionError(f"unsupported parameter type for {name!r}: {type(value).__name__}")
    return StatementParameterListItem(name=name, value=str(value), type="INT" if isinstance(value, int) else "STRING")


class DatabricksSqlExecutor:
    """Executes statements on one existing SQL Warehouse."""

    def __init__(self, client: WorkspaceClient, warehouse_id: str, *, wait_timeout: str = "30s") -> None:
        self._client = client
        self._warehouse_id = warehouse_id
        # The API accepts 5s-50s. On timeout the statement is cancelled
        # rather than left running against the warehouse.
        self._wait_timeout = wait_timeout

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        response = self._client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self._warehouse_id,
            parameters=[_parameter(name, value) for name, value in (parameters or {}).items()] or None,
            wait_timeout=self._wait_timeout,
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
            disposition=Disposition.INLINE,
            format=Format.JSON_ARRAY,
        )

        status = response.status
        if status is None or status.state is not StatementState.SUCCEEDED:
            state = status.state.value if status is not None and status.state is not None else "UNKNOWN"
            detail = status.error.message if status is not None and status.error is not None else ""
            raise SqlExecutionError(f"statement did not succeed (state={state}) {detail}".strip())

        manifest = response.manifest
        if manifest is None or manifest.schema is None or manifest.schema.columns is None:
            raise SqlExecutionError("statement succeeded but returned no result schema")
        if manifest.truncated:
            # Never silently return a partial ranking.
            raise SqlExecutionError("result was truncated by the SQL API")

        columns = [column.name for column in manifest.schema.columns]
        data = response.result.data_array if response.result is not None and response.result.data_array else []
        return [dict(zip(columns, row)) for row in data]
