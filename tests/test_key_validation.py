"""
CP2 - Testes de Validação de Chaves da Camada Bronze

Objetivo
--------
Validar a integridade mínima das chaves primárias simples
nas tabelas da camada Bronze.

Importância
-----------
A qualidade das chaves é um dos requisitos fundamentais
para garantir a consistência dos dados ao longo do ciclo
de vida da plataforma analítica.

A presença de valores nulos ou duplicados pode provocar:

- Perda de integridade referencial
- Erros em processos de transformação
- Duplicação de métricas
- Problemas de modelação dimensional
- Resultados incorretos em análises e relatórios

Escopo deste teste
------------------
Esta validação verifica:

- Ausência de valores nulos na coluna "id"
- Ausência de valores duplicados na coluna "id"

O teste aplica-se apenas às tabelas que utilizam uma chave
primária simples baseada na coluna "id".

Disclaimer Técnico
------------------
As tabelas "product_suppliers", "stock_levels" e
"variant_attribute_values" foram excluídas desta validação
por potencialmente utilizarem chaves compostas em vez de uma
chave primária simples ("id").

O mapeamento definitivo das chaves de negócio e dos
relacionamentos será realizado durante a modelação da
camada Silver (CP3).

A eventual implementação de validações de unicidade para
essas entidades deverá considerar a combinação correta
dos atributos que compõem a chave de negócio.
"""

import pytest

CATALOG = "dev_natalia_correia_lh_nautical"
SCHEMA = "bronze"
CATALOG_SCHEMA = f"{CATALOG}.{SCHEMA}"

# Tabelas com chave primária simples na coluna "id"
TABLES_WITH_ID = [
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
    "product_variants",
    "products",
    "purchase_order_items",
    "purchase_orders",
    "return_items",
    "returns",
    "stock_movements",
    "suppliers",
]


@pytest.mark.parametrize("table_name", TABLES_WITH_ID)
def test_id_not_null(spark, table_name):
    """Valida a inexistência de valores nulos na coluna 'id'."""

    df = spark.table(f"{CATALOG_SCHEMA}.{table_name}")

    null_count = df.filter("id IS NULL").count()

    assert null_count == 0, (
        f"Falha na validação de chave primária: a tabela "
        f"{table_name} possui {null_count} valores nulos na coluna 'id'."
    )


@pytest.mark.parametrize("table_name", TABLES_WITH_ID)
def test_id_unique(spark, table_name):
    """Valida a inexistência de valores duplicados na coluna 'id'."""

    df = spark.table(f"{CATALOG_SCHEMA}.{table_name}")

    duplicate_count = (
        df.groupBy("id")
        .count()
        .filter("count > 1")
        .count()
    )

    assert duplicate_count == 0, (
        f"Falha na validação de unicidade: a tabela "
        f"{table_name} possui {duplicate_count} valores duplicados "
        f"na coluna 'id'."
    )

