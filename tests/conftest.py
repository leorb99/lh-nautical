"""
Configuração central dos testes automatizados.

Este arquivo configura o Pytest, inicializa o Databricks Connect
e disponibiliza fixtures reutilizáveis para os testes do projeto.
"""

import csv
import json
import os
import pathlib
import sys
from contextlib import contextmanager

try:
    import pytest
    from databricks.connect import DatabricksSession
    from databricks.sdk import WorkspaceClient
    from pyspark.sql import SparkSession
except ImportError:
    raise ImportError(
        "Dependências de teste não encontradas.\n\n"
        "Execute os testes usando 'uv run pytest'. "
        "Consulte http://docs.astral.sh/uv para mais informações."
    )


@pytest.fixture()
def spark() -> SparkSession:
    """
    Disponibiliza uma sessão Spark para execução dos testes.
    """

    # Cria ou reutiliza uma sessão Spark através do Databricks Connect.
    return DatabricksSession.builder.getOrCreate()


@pytest.fixture(scope="session")
def catalog_name() -> str:
    """
    Define o catálogo utilizado durante a execução dos testes.
    """

    # Permite sobrescrever o catálogo por variável de ambiente.
    return os.getenv(
        "CATALOG",
        "dev_natalia_correia_lh_nautical",
    )


@pytest.fixture(scope="session")
def bronze_schema() -> str:
    """
    Define o schema correspondente à camada Bronze.
    """

    # Permite sobrescrever o schema por variável de ambiente.
    return os.getenv("SCHEMA", "bronze")


@pytest.fixture(scope="session")
def catalog_schema(catalog_name: str, bronze_schema: str) -> str:
    """
    Constrói o caminho completo utilizado nas consultas Spark SQL.
    """

    # Combina catálogo e schema no formato esperado pelo Unity Catalog.
    return f"{catalog_name}.{bronze_schema}"


@pytest.fixture()
def load_fixture(spark: SparkSession):
    """
    Disponibiliza uma função auxiliar para carregar arquivos de teste.
    """

    def _loader(filename: str):
        # Localiza o arquivo dentro da pasta fixtures.
        path = pathlib.Path(__file__).parent.parent / "fixtures" / filename

        # Identifica o tipo do arquivo a partir da extensão.
        suffix = path.suffix.lower()

        # Carrega arquivos JSON como DataFrame Spark.
        if suffix == ".json":
            rows = json.loads(path.read_text())
            return spark.createDataFrame(rows)

        # Carrega arquivos CSV como DataFrame Spark.
        if suffix == ".csv":
            with path.open(newline="") as f:
                rows = list(csv.DictReader(f))
            return spark.createDataFrame(rows)

        # Impede o carregamento de formatos não suportados.
        raise ValueError(f"Tipo de fixture não suportado: {filename}")

    return _loader


def _enable_fallback_compute():
    """
    Habilita o compute serverless quando nenhum compute é informado.
    """

    # Obtém a configuração atual do cliente Databricks.
    conf = WorkspaceClient().config

    # Mantém a configuração existente quando já houver compute definido.
    if conf.serverless_compute_id or conf.cluster_id or os.environ.get("SPARK_REMOTE"):
        return

    # Informa que será utilizado o compute serverless como fallback.
    url = "https://docs.databricks.com/dev-tools/databricks-connect/cluster-config"
    print("☁️ nenhum compute especificado; usando serverless como fallback", file=sys.stderr)
    print(f"  consulte {url} para configuração manual", file=sys.stdout)

    # Define o compute serverless automático para o Databricks Connect.
    os.environ["DATABRICKS_SERVERLESS_COMPUTE_ID"] = "auto"


@contextmanager
def _allow_stderr_output(config: pytest.Config):
    """
    Permite saída no terminal durante a configuração do Pytest.
    """

    # Obtém o gerenciador de captura de saída do Pytest.
    capman = config.pluginmanager.get_plugin("capturemanager")

    # Desabilita temporariamente a captura para permitir mensagens de configuração.
    if capman:
        with capman.global_and_fixture_disabled():
            yield
    else:
        yield


def pytest_configure(config: pytest.Config):
    """
    Configura a sessão de testes do Pytest.
    """

    # Permite exibir mensagens durante a configuração inicial.
    with _allow_stderr_output(config):
        # Garante um compute disponível para o Databricks Connect.
        _enable_fallback_compute()

        # Inicializa a sessão Spark antes da execução dos testes.
        if hasattr(DatabricksSession.builder, "validateSession"):
            DatabricksSession.builder.validateSession().getOrCreate()
        else:
            DatabricksSession.builder.getOrCreate()