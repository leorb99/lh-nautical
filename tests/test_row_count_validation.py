"""
CP2 - Testes de Validação de Registos da Camada Bronze

Objetivo
--------
Validar se todas as tabelas esperadas da camada Bronze
contêm registos após a conclusão do processo de ingestão.

Importância
-----------
A presença de dados nas tabelas ingeridas representa uma
verificação fundamental da disponibilidade da informação
na camada Bronze.

A ausência de registos pode indicar:

- Falhas durante a ingestão
- Problemas de conectividade com a origem
- Erros de configuração do pipeline
- Falhas de permissões
- Problemas na escrita para a camada Bronze

Escopo
-------
Este teste valida que todas as tabelas esperadas da camada
Bronze foram carregadas com pelo menos um registo após a
execução da ingestão.

Esta verificação assegura a disponibilidade inicial dos
dados e complementa as demais validações de qualidade,
como integridade, idempotência e consistência estrutural.
"""

import pytest

# Lista das tabelas que devem conter dados após a ingestão.
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
def test_tabela_bronze_tem_registos(
    spark,
    catalog_schema,
    nome_tabela,
):
    """
    Valida se a tabela contém pelo menos um registo.
    """

    # Lê a tabela correspondente na camada Bronze.
    df = spark.table(f"{catalog_schema}.{nome_tabela}")

    # Obtém a quantidade total de registos presentes na tabela.
    quantidade_registos = df.count()

    # Falha o teste caso a tabela esteja vazia.
    assert quantidade_registos > 0, (
        f"Falha na validação de disponibilidade: "
        f"a tabela {nome_tabela} não contém registos."
    )