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

Escopo deste teste
------------------
Esta validação verifica apenas se as tabelas contêm pelo
menos um registo após a execução da ingestão.

O teste não realiza reconciliação entre a origem e a Bronze,
nem valida a completude dos dados carregados.

Disclaimer Técnico
------------------
Esta validação atua como uma verificação inicial de
disponibilidade dos dados na camada Bronze.

Uma reconciliação completa entre os sistemas de origem e
a camada Bronze deverá ser implementada futuramente quando
existir acesso automatizado às métricas e contagens da
fonte de dados.

A existência de registos não garante, por si só, que a
carga esteja completa ou que todos os dados esperados
tenham sido processados.
"""
import pytest

CATALOG = "dev_natalia_correia_lh_nautical"
SCHEMA = "bronze"
CATALOG_SCHEMA = f"{CATALOG}.{SCHEMA}"

# Tabelas esperadas na camada Bronze
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


@pytest.mark.parametrize("nome_tabela", BRONZE_TABLES)
def test_tabela_bronze_tem_registos(spark, nome_tabela):
    """
    Valida se a tabela contém pelo menos um registo.
    """

    df = spark.table(f"{CATALOG_SCHEMA}.{nome_tabela}")

    quantidade_registos = df.count()

    assert quantidade_registos > 0, (
        f"Falha na validação de disponibilidade: "
        f"a tabela {nome_tabela} não contém registos."
    )