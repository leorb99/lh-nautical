"""
CP2 - Testes de Validação de Idempotência da Camada Bronze

Objetivo
--------
Validar se execuções repetidas do processo de ingestão não
geram registos COMPLETAMENTE duplicados nas tabelas da camada Bronze.

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

Escopo
------
Esta validação compara a contagem total de registros com a contagem
de registros distintos, excluindo as colunas de auditoria (_ingested_at,
_action, _hash) que podem mudar a cada execução.

Esta abordagem cobre todas as tabelas independentemente de possuírem
chave primária simples ou composta, tornando o teste agnóstico ao schema.
"""

import pytest

AUDIT_COLUMNS = {"_ingested_at", "_action", "_hash"}

# Tabelas esperadas na camada Bronze e respetiva estratégia de validação.
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


# Executa o mesmo teste para cada tabela esperada da camada Bronze.
@pytest.mark.parametrize("nome_tabela", BRONZE_TABLES)
def test_pipeline_idempotente(
    spark,
    catalog_schema,
    nome_tabela,
):
    """
    Valida que não existem linhas completamente duplicadas na tabela.
    """

    # Lê a tabela correspondente na camada Bronze.
    df = spark.table(f"{catalog_schema}.{nome_tabela}")

    # exclui colunas de auditoria que mudam a cada run
    colunas_dados = [c for c in df.columns if c not in AUDIT_COLUMNS]

    df_dados = df.select(colunas_dados)

    total = df_dados.count()
    distintos = df_dados.distinct().count()

    assert total == distintos, (
        f"Falha na validação de idempotência: a tabela '{nome_tabela}' "
        f"possui {total - distintos} linhas completamente duplicadas. "
        f"Isso indica que o pipeline appendou os mesmos registros mais de uma vez."
    )