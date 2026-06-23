"""
CP2 - Testes de Prontidão para Processamento Incremental
========================================================

Objetivo
--------
Validar se as tabelas da camada Bronze possuem colunas temporais
que permitam a implementação futura de estratégias de carga incremental.

Importância
-----------
A existência de colunas temporais é um pré-requisito comum para:

- Incremental Loads
- Change Data Capture (CDC)
- Processamento baseado em janelas temporais
- Reprocessamento parcial de dados
- Monitorização de alterações ao longo do tempo

Escopo deste teste
------------------
Este teste NÃO valida se a ingestão já é incremental.

O teste verifica apenas se existem colunas temporais candidatas
que poderão ser utilizadas futuramente para suportar estratégias
de incrementalidade.

Disclaimer Técnico
------------------
Durante a análise exploratória da camada Bronze foram identificadas
algumas tabelas que não possuem colunas temporais no sistema de origem.

Essas tabelas foram excluídas desta validação porque:

1. Representam tabelas de apoio ou relacionamento.
2. Possuem natureza estática ou baixa frequência de alteração.
3. Não disponibilizam informação temporal no banco transacional.
4. Não impedem a implementação futura de incrementalidade nas
   entidades principais do negócio.

Tabelas atualmente sem colunas temporais identificadas:

- addresses
- attributes
- goods_receipt_items
- order_items
- purchase_order_items
- return_items
- variant_attribute_values

A decisão de mantê-las fora deste teste evita falsos negativos
durante a validação do CP2.

Qualquer necessidade futura de incrementalidade nestas entidades
deverá ser tratada durante as fases de modelação e engenharia
das camadas Silver e Gold (CP3+), podendo envolver:

- Chaves compostas
- CDC na origem
- Tabelas de auditoria
- Estratégias de snapshot
- Derivação temporal a partir de entidades relacionadas

Este comportamento é esperado e não representa defeito da ingestão.
"""

import pytest

CATALOG = "dev_natalia_correia_lh_nautical"
SCHEMA = "bronze"
CATALOG_SCHEMA = f"{CATALOG}.{SCHEMA}"

# Tabelas que possuem pelo menos uma coluna temporal
# candidata para futuras estratégias de carga incremental.
INCREMENTAL_TABLES = [
    "brands",
    "categories",
    "customers",
    "employees",
    "fiscal_invoices",
    "goods_receipts",
    "locations",
    "orders",
    "payments",
    "product_suppliers",
    "product_variants",
    "products",
    "purchase_orders",
    "returns",
    "stock_levels",
    "stock_movements",
    "suppliers",
]

# Tabelas identificadas durante a exploração da Bronze
# que não possuem colunas temporais disponíveis na origem.
# Mantidas apenas para documentação técnica.
TABELAS_SEM_COLUNA_TEMPORAL = [
    "addresses",
    "attributes",
    "goods_receipt_items",
    "order_items",
    "purchase_order_items",
    "return_items",
    "variant_attribute_values",
]

# Colunas temporais candidatas para controle incremental.
TEMPORAL_COLUMNS = [
    "created_at",
    "updated_at",
    "placed_at",
    "paid_at",
    "issued_at",
    "received_at",
    "returned_at",
    "movement_at",
    "hire_date",
    "termination_date",
    "expected_delivery_at",
]


@pytest.mark.parametrize("nome_tabela", INCREMENTAL_TABLES)
def test_tabela_possui_coluna_temporal_para_incrementalidade(
    spark,
    nome_tabela,
):
    """
    Valida se a tabela possui pelo menos uma coluna temporal
    candidata para suportar futuras estratégias de carga incremental.
    """

    df = spark.table(f"{CATALOG_SCHEMA}.{nome_tabela}")

    colunas = [coluna.lower() for coluna in df.columns]

    colunas_encontradas = [
        coluna
        for coluna in TEMPORAL_COLUMNS
        if coluna in colunas
    ]

    assert len(colunas_encontradas) > 0, (
        f"Falha na validação de incrementalidade: "
        f"a tabela '{nome_tabela}' não possui nenhuma "
        f"coluna temporal candidata. "
        f"Colunas esperadas: {', '.join(TEMPORAL_COLUMNS)}."
    )