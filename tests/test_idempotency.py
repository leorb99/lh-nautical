"""
CP2 - Testes de Validação de Idempotência da Camada Bronze

Objetivo
--------
Validar se execuções repetidas do processo de ingestão não
geram registos duplicados nas tabelas da camada Bronze.

Importância
-----------
A idempotência é uma característica fundamental em pipelines
de dados, garantindo que múltiplas execuções do mesmo processo
produzam resultados consistentes sem introduzir duplicidade
de informação.

A ausência desta propriedade pode provocar:

- Duplicação de registos
- Métricas incorretas
- Inconsistências analíticas
- Problemas de rastreabilidade
- Crescimento indevido do volume de dados

Escopo deste teste
------------------
Esta validação verifica a existência de chaves primárias
duplicadas após a ingestão dos dados na Bronze.

O teste não valida estratégias de deduplicação nem mecanismos
de CDC (Change Data Capture), limitando-se à identificação
de possíveis duplicidades na camada ingerida.

Disclaimer Técnico
------------------
As tabelas 'product_suppliers', 'stock_levels' e
'variant_attribute_values' foram excluídas desta validação
por potencialmente utilizarem chaves compostas em vez de uma
chave primária simples ('id').

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

# Tabelas com chave primária simples (coluna "id")
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
def test_pipeline_idempotency_by_primary_key(spark, table_name):
    """Valida a existência de chaves primárias duplicadas."""

    df = spark.table(f"{CATALOG_SCHEMA}.{table_name}")

    duplicate_count = (
        df.groupBy("id")
        .count()
        .filter("count > 1")
        .count()
    )

    assert duplicate_count == 0, (
        f"Falha na validação de idempotência: a tabela "
        f"{table_name} possui {duplicate_count} "
        f"chaves primárias duplicadas."
    )

    
