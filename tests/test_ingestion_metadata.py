"""
CP2 - Testes de Validação de Metadados de Ingestão da Camada Bronze

Objetivo
--------
Validar se todas as tabelas da camada Bronze possuem o
metadado técnico "_ingested_at", utilizado para registar
o momento em que cada registo foi ingerido na camada Bronze.

Importância
-----------
A existência deste metadado permite:

- Auditoria das cargas
- Rastreabilidade da ingestão
- Monitorização operacional
- Suporte a reprocessamentos
- Análise temporal das execuções do pipeline

Escopo
------
Este teste valida:

- Existência da coluna "_ingested_at"
- Ausência de valores nulos nesta coluna

O teste não valida a estratégia de carga nem a
incrementalidade do pipeline, limitando-se aos
metadados técnicos da ingestão.
"""

import pytest

# Lista das tabelas esperadas na camada Bronze.
BRONZE_TABLES = [
    "addresses",
    "attributes",
    "brands",
    "categories",
    "customers",
    "employees",
    "fiscal_invoices",
    "goods_receipt_items",
    "goods_receipts",
    "locations",
    "order_items",
    "orders",
    "payments",
    "product_suppliers",
    "product_variants",
    "products",
    "purchase_order_items",
    "purchase_orders",
    "return_items",
    "returns",
    "stock_levels",
    "stock_movements",
    "suppliers",
    "variant_attribute_values",
]


# Executa o mesmo teste para todas as tabelas da camada Bronze.
@pytest.mark.parametrize("nome_tabela", BRONZE_TABLES)
def test_tabela_possui_coluna_ingested_at(
    spark,
    catalog_schema,
    nome_tabela,
):
    """
    Valida a existência da coluna técnica "_ingested_at".
    """

    # Lê a tabela correspondente na camada Bronze.
    df = spark.table(f"{catalog_schema}.{nome_tabela}")

    # Normaliza os nomes das colunas para evitar diferenças de capitalização.
    colunas = [coluna.lower() for coluna in df.columns]

    # Falha o teste caso a coluna "_ingested_at" não exista.
    assert "_ingested_at" in colunas, (
        f"Falha na validação de metadados: "
        f"a tabela '{nome_tabela}' não possui a coluna "
        f"'_ingested_at'."
    )


# Executa o mesmo teste para todas as tabelas da camada Bronze.
@pytest.mark.parametrize("nome_tabela", BRONZE_TABLES)
def test_coluna_ingested_at_nao_nula(
    spark,
    catalog_schema,
    nome_tabela,
):
    """
    Valida se todos os registos possuem valor na coluna "_ingested_at".
    """

    # Lê a tabela correspondente na camada Bronze.
    df = spark.table(f"{catalog_schema}.{nome_tabela}")

    # Conta quantos registos possuem "_ingested_at" nulo.
    quantidade_nulos = (
        df.filter("_ingested_at IS NULL")
        .count()
    )

    # Falha o teste caso exista qualquer valor nulo na coluna "_ingested_at".
    assert quantidade_nulos == 0, (
        f"Falha na validação de metadados: "
        f"a tabela '{nome_tabela}' possui "
        f"{quantidade_nulos} registos com "
        f"'_ingested_at' nulo."
    )