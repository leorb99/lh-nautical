"""
CP2 - Testes de Existência de Tabelas da Camada Bronze

Valida se todas as tabelas esperadas foram criadas e estão
disponíveis na camada Bronze após a execução do processo
de ingestão.

Objetivo
--------
Garantir que a ingestão carregou todas as entidades
previstas para o projeto.

Importância
-----------
A ausência de qualquer tabela pode indicar:

- Falha na ingestão
- Problemas de permissões
- Alterações na origem de dados
- Problemas de configuração do pipeline

Este teste atua como uma validação estrutural da camada Bronze.
"""

# Lista das tabelas que devem existir após a ingestão completa.
EXPECTED_TABLES = [
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


def test_todas_as_tabelas_esperadas_existem_na_bronze(
    spark,
    catalog_schema,
):
    """
    Valida se todas as tabelas esperadas existem na camada Bronze.
    """

    # Obtém todas as tabelas atualmente disponíveis na camada Bronze.
    tabelas_existentes = [
        row.tableName
        for row in spark.sql(
            f"SHOW TABLES IN {catalog_schema}"
        ).collect()
    ]

    # Identifica quais tabelas esperadas não foram encontradas.
    tabelas_ausentes = [
        tabela
        for tabela in EXPECTED_TABLES
        if tabela not in tabelas_existentes
    ]

    # Falha o teste caso exista qualquer tabela ausente.
    assert len(tabelas_ausentes) == 0, (
        "As seguintes tabelas não foram encontradas na camada Bronze: "
        f"{', '.join(tabelas_ausentes)}"
    )