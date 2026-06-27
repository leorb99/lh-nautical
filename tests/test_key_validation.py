"""
CP2 - Testes de Validação de Chaves da Camada Bronze

Objetivo
--------
Validar a integridade mínima das chaves primárias nas tabelas
da camada Bronze.

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

Escopo
------
Esta validação verifica:

- Ausência de valores nulos na coluna "id"
- Ausência de valores duplicados na coluna "id"

As tabelas com chave primária composta permanecem incluídas
na suíte, mas são marcadas como xfail porque não possuem uma
chave primária simples baseada na coluna "id".

Essa abordagem evita maquiar a cobertura do teste e documenta
explicitamente quais tabelas exigem validações específicas
durante a modelação da camada Silver (CP3).
"""

import pytest

# Tabelas esperadas na camada Bronze e respetiva estratégia de validação.
BRONZE_TABLES = [
    pytest.param("addresses"),
    pytest.param("attributes"),
    pytest.param("brands"),
    pytest.param("categories"),
    pytest.param("customers"),
    pytest.param("employees"),
    pytest.param("fiscal_invoices"),
    pytest.param("goods_receipt_items"),
    pytest.param("goods_receipts"),
    pytest.param("locations"),
    pytest.param("order_items"),
    pytest.param("orders"),
    pytest.param("payments"),
    pytest.param(
        "product_suppliers",
        marks=pytest.mark.xfail(
            reason="Tabela possui chave primária composta: product_variant_id + supplier_id"
        ),
    ),
    pytest.param("product_variants"),
    pytest.param("products"),
    pytest.param("purchase_order_items"),
    pytest.param("purchase_orders"),
    pytest.param("return_items"),
    pytest.param("returns"),
    pytest.param(
        "stock_levels",
        marks=pytest.mark.xfail(
            reason="Tabela possui chave primária composta: location_id + product_variant_id"
        ),
    ),
    pytest.param("stock_movements"),
    pytest.param("suppliers"),
    pytest.param(
        "variant_attribute_values",
        marks=pytest.mark.xfail(
            reason="Tabela possui chave primária composta: attribute_id + product_variant_id"
        ),
    ),
]


@pytest.mark.parametrize("nome_tabela", BRONZE_TABLES)
def test_id_not_null(
    spark,
    catalog_schema,
    nome_tabela,
):
    """Valida a inexistência de valores nulos na coluna 'id'."""

    # Lê a tabela correspondente na camada Bronze.
    df = spark.table(f"{catalog_schema}.{nome_tabela}")

    # Conta quantos registos possuem valor nulo na coluna "id".
    quantidade_nulos = df.filter("id IS NULL").count()

    # Falha o teste caso exista qualquer valor nulo na chave primária simples.
    assert quantidade_nulos == 0, (
        f"Falha na validação de chave primária: a tabela "
        f"{nome_tabela} possui {quantidade_nulos} valores nulos na coluna 'id'."
    )


@pytest.mark.parametrize("nome_tabela", BRONZE_TABLES)
def test_id_unique(
    spark,
    catalog_schema,
    nome_tabela,
):
    """Valida a inexistência de valores duplicados na coluna 'id'."""

    # Lê a tabela correspondente na camada Bronze.
    df = spark.table(f"{catalog_schema}.{nome_tabela}")

    # Conta quantos valores de "id" aparecem mais de uma vez na tabela.
    quantidade_duplicados = (
        df.groupBy("id")
        .count()
        .filter("count > 1")
        .count()
    )

    # Falha o teste caso exista qualquer chave primária simples duplicada.
    assert quantidade_duplicados == 0, (
        f"Falha na validação de unicidade: a tabela "
        f"{nome_tabela} possui {quantidade_duplicados} valores duplicados "
        f"na coluna 'id'."
    )