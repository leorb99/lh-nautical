"""
Ponto de entrada para execução dos testes via Databricks Job.
Lê o catalog passado pelo resources.yml via parameters como argumento de linha de comando e o injeta
como variável de ambiente antes de invocar o pytest (injeta em os.environ para que o conftest.py consiga
ler via os.environ.get("DATABRICKS_CATALOG"))
"""
import argparse
import os
import sys

# Desabilita geração de __pycache__ — Workspace filesystem não suporta e estava retornando erro na hora de rodar o job
sys.dont_write_bytecode = True

import pytest

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True, help="Catalog ativo do ambiente")
args = parser.parse_args()

os.environ["DATABRICKS_CATALOG"] = args.catalog

exit_code = pytest.main([
    ".",
    "-v",
    "--tb=short"
])

if exit_code != 0:
    raise RuntimeError(f"Testes falharam com exit code: {exit_code}")