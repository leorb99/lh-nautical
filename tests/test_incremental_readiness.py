"""
CP2 - Testes de Prontidão para Processamento Incremental

Objetivo
--------
Validar se as tabelas da camada Bronze possuem colunas temporais
da origem que permitam a implementação futura de estratégias de
carga incremental.

Importância
-----------
A existência de colunas temporais de origem é um pré-requisito
comum para:

- Incremental Loads
- Change Data Capture (CDC)
- Processamento baseado em janelas temporais
- Reprocessamento parcial de dados
- Monitorização de alterações ao longo do tempo

Escopo
------
Este teste NÃO valida se a ingestão já é incremental.

O teste verifica apenas se existem colunas temporais candidatas,
provenientes da origem, que poderão ser utilizadas futuramente
para suportar estratégias de incrementalidade.

As tabelas sem colunas temporais permanecem incluídas na suíte,
mas são marcadas como xfail porque não disponibilizam informação
temporal compatível com os critérios adotados nesta validação.

A coluna técnica "_ingested_at", quando implementada, deverá ser
validada em teste separado de metadados de ingestão, pois ela
representa rastreabilidade da carga e não timestamp de negócio
da origem.
"""

import pytest

# Define o catálogo utilizado durante a execução dos testes.
CATALOG = "dev_natalia_correia_lh_nautical"

# Define o schema correspondente à camada Bronze.
SCHEMA = "bronze"

# Constrói o caminho completo utilizado nas consultas Spark SQL.
CATALOG_SCHEMA = f"{CATALOG}.{SCHEMA}"

# Tabelas esperadas na camada Bronze e respetiva estratégia de validação.
BRONZE_TABLES = [
    pytest.param(
        "addresses",
        marks=pytest.mark.xfail(
            reason="Tabela sem coluna temporal de origem identificada."
        ),
    ),
    pytest.param(
        "attributes",
        marks=pytest.mark.xfail(
            reason="Tabela sem coluna temporal de origem identificada."
        ),
    ),
    pytest.param("brands"),
    pytest.param("categories"),
    pytest.param("customers"),
    pytest.param("employees"),
    pytest.param("fiscal_invoices"),
    pytest.param(
        "goods_receipt_items",
        marks=pytest.mark.xfail(
            reason="Tabela sem coluna temporal de origem identificada."
        ),
    ),
    pytest.param("goods_receipts"),
    pytest.param("locations"),
    pytest.param(
        "order_items",
        marks=pytest.mark.xfail(
            reason="Tabela sem coluna temporal de origem identificada."
        ),
    ),
    pytest.param("orders"),
    pytest.param("payments"),
    pytest.param("product_suppliers"),
    pytest.param("product_variants"),
    pytest.param("products"),
    pytest.param(
        "purchase_order_items",
        marks=pytest.mark.xfail(
            reason="Tabela sem coluna temporal de origem identificada."
        ),
    ),
    pytest.param("purchase_orders"),
    pytest.param(
        "return_items",
        marks=pytest.mark.xfail(
            reason="Tabela sem coluna temporal de origem identificada."
        ),
    ),
    pytest.param("returns"),
    pytest.param("stock_levels"),
    pytest.param("stock_movements"),
    pytest.param("suppliers"),
    pytest.param(
        "variant_attribute_values",
        marks=pytest.mark.xfail(
            reason="Tabela sem coluna temporal de origem identificada."
        ),
    ),
]

# Colunas temporais de origem candidatas para controle incremental.
TEMPORAL_COLUMNS = [
    "created_at",
    "updated_at",
    "placed_at",
    "paid_at",
    "issued_at",
    "received_at",
    "returned_at",
    "movement_at",
    "occurred_at",
    "hire_date",
    "termination_date",
    "expected_delivery_at",
]


@pytest.mark.parametrize("nome_tabela", BRONZE_TABLES)
def test_tabela_possui_coluna_temporal_para_incrementalidade(
    spark,
    nome_tabela,
):
    """
    Valida se a tabela possui pelo menos uma coluna temporal
    de origem candidata para futuras estratégias incrementais.
    """

    # Lê a tabela correspondente na camada Bronze.
    df = spark.table(f"{CATALOG_SCHEMA}.{nome_tabela}")

    # Normaliza os nomes das colunas para evitar diferenças de capitalização.
    colunas = [coluna.lower() for coluna in df.columns]

    # Identifica quais colunas temporais candidatas existem na tabela.
    colunas_encontradas = [
        coluna
        for coluna in TEMPORAL_COLUMNS
        if coluna in colunas
    ]

    # Falha o teste quando a tabela não possui coluna temporal de origem.
    assert len(colunas_encontradas) > 0, (
        f"Falha na validação de incrementalidade: "
        f"a tabela '{nome_tabela}' não possui nenhuma "
        f"coluna temporal de origem candidata. "
        f"Colunas esperadas: {', '.join(TEMPORAL_COLUMNS)}."
    )