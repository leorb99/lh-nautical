# LH Nautical — Plataforma de Dados (Ingestão + Transformação dbt)

## Visão Geral

Este repositório contém a plataforma de dados da **LH Nautical**:

1. ingestão de dados do SQL Server para a camada Bronze;
2. transformação dos dados em modelos Silver e Gold com dbt;
3. testes, documentação e lineage dos modelos analíticos;
4. orquestração e implantação por Databricks Asset Bundles.

O fluxo principal é:

`SQL Server → Databricks/Delta Bronze → dbt Staging e Intermediate (Silver) → dbt Marts (Gold)`

O Databricks fornece execução, armazenamento e orquestração; o dbt gerencia transformações, dependências, testes e documentação.

## Pipeline de Ingestão

### Visão geral

A ingestão em PySpark extrai dados do SQL Server por JDBC e os persiste
como tabelas Delta registradas no Unity Catalog.

O processo segue o padrão EL:

`SQL Server → extração JDBC → Bronze Delta`

### Descoberta de tabelas

O pipeline consulta `information_schema.tables` e seleciona tabelas
`BASE TABLE` pertencentes ao schema `dbo`.

Isso elimina a necessidade de manter uma lista fixa no código.

### Leitura JDBC

A função `query_jdbc` suporta:

- leitura sequencial quando não existe chave de particionamento adequada;
- consulta de limites com `MIN()` e `MAX()`;
- leitura paralela com `lowerBound`, `upperBound` e `numPartitions`;
- múltiplas consultas concorrentes ao SQL Server.

| Tabelas | Chave de particionamento |
|---|---|
| `variant_attribute_values` | Leitura sequencial |
| `product_suppliers`, `stock_levels` | `updated_at` |
| Demais tabelas | `id` |

### Detecção de alterações

A função `incremental_ingenstion_hash` utiliza hashing e recursos do Delta
Lake para identificar registros novos ou alterados:

1. gera um hash MD5 a partir das colunas da origem;
2. consulta a versão anterior da tabela Delta;
3. lê os hashes da versão de referência;
4. aplica `left_anti` join;
5. grava somente os registros não encontrados na versão anterior.

### Estratégia de gravação

| Cenário | Comportamento |
|---|---|
| Tabela inexistente | Cria a tabela e registra `INITIAL_LOAD` |
| Tabela legada sem `_hash` | Recria a estrutura com os metadados técnicos |
| Registros novos ou alterados | Acrescenta registros com `INSERT_OR_UPDATE` |
| Nenhuma alteração | Encerra sem nova gravação |

### Metadados técnicos

| Coluna | Finalidade |
|---|---|
| `_hash` | Identificação de alterações no conteúdo do registro |
| `_ingested_at` | Data e hora da ingestão |
| `_action` | Classificação como `INITIAL_LOAD` ou `INSERT_OR_UPDATE` |

### Tratamento de erros

Falhas individuais são registradas e, ao final, a presença de tabelas
com erro provoca falha explícita do job e aciona os alertas.

### Configuração

As credenciais `sql_server_user`, `sql_server_password`,
`sql_server_host` e `sql_server_port` são mantidas no Secret Scope
`databases_lh_nautical`. Os notebooks recebem `catalog_name`, `schema` e
`numPartitions` como parâmetros de orquestração.

## Camada de Transformação dbt

### Visão geral

O projeto `dbt_lh_nautical/` transforma a Bronze em modelos Silver e Gold.

Cada desenvolvedor utiliza um catálogo pessoal no padrão:

`dev_<nome>_lh_nautical`

### Arquitetura em camadas

| Camada | Pasta | Materialização | Schema | Propósito |
|---|---|---|---|---|
| **Staging** | `models/staging/` | `view` | `silver` | Limpeza 1:1, casts, normalização e seleção de colunas |
| **Intermediate** | `models/intermediate/` | `view` | `silver` | Joins, classificações, reconciliações e regras reutilizáveis |
| **Marts — Dimension** | `models/marts/dimension/` | `table` | `gold` | Dimensões conformadas |
| **Marts — Fact** | `models/marts/fact/` | `table` | `gold` | Fatos transacionais, snapshots e agregados |
| **Marts — Bridge** | `models/marts/bridge/` | `table` | `gold` | Relacionamentos muitos-para-muitos |

O macro customizado `generate_schema_name` aplica os schemas `silver` e
`gold` configurados no `dbt_project.yml`.

### Modelagem

A camada Gold utiliza:

- dimensões conformadas;
- fatos transacionais;
- fatos de snapshot;
- bridge para relacionamentos muitos-para-muitos;
- chaves substitutas com `dbt_utils.generate_surrogate_key`;
- agregados próprios para indicadores não aditivos;
- granularidade documentada nos YAMLs.

## Regras de Negócio Centrais

### Venda efetiva

A flag `is_effective_sale`, definida em `int_orders`, indica quando um pedido
participa dos indicadores financeiros oficiais.

Uma venda é efetiva quando:

- `order_status = 'paid'`;
- `fiscal_invoice_status = 'authorized'`;
- `placed_at` é válido e não futuro.

Pedidos pagos com nota rejeitada usam `is_paid_sale_pending_invoice_fix`
e não entram na receita oficial.

### Rateio de desconto por item

O desconto do pedido é distribuído proporcionalmente entre os itens:

```text
desconto_alocado =
    order_discount_amount
    * order_item_line_total
    / order_subtotal_amount
```

O teste `assert_order_item_discount_allocation_reconciles.sql` valida o rateio.

### Devolução efetiva

A flag `is_effective_return`, definida em `int_returns`, classifica uma
devolução como efetiva quando:

- `return_status = 'completed'`;
- `returned_at` é válido e não futuro.

`int_completed_returns_by_order_item` consolida devoluções por
`order_item_id` antes do join com `fct_sales`.

### Métricas financeiras

`agg_daily_financial_metrics` possui uma linha por data e expõe os
componentes necessários para calcular:

```text
faturamento_liquido =
    order_item_net_revenue_after_discount
    - completed_refund_amount
```

```text
ticket_medio =
    effective_order_revenue_amount
    / effective_order_count
```

```text
taxa_de_devolucao =
    returned_item_quantity
    / sold_item_quantity
```

Para períodos maiores que um dia, ticket médio e taxa de devolução devem
ser recalculados a partir da soma de seus componentes.

### Reposição de estoque

`fct_inventory_replenishment` possui uma linha por variante e local:

```text
suggested_reorder_quantity =
    demand_during_lead_time
    + safety_stock_quantity
    - quantity_on_hand
    - open_purchase_order_quantity
```

O lead time segue a cascata:

1. histórico da variante no local;
2. histórico geral da variante;
3. histórico global;
4. padrão de sete dias.

## KPIs Principais

| KPI | Definição | Modelo | Campo |
|---|---|---|---|
| Faturamento líquido | Receita das vendas efetivas após descontos alocados e reembolsos concluídos | `agg_daily_financial_metrics` | `net_revenue_amount` |
| Ticket médio | Valor oficial dos pedidos efetivos dividido pela quantidade de pedidos efetivos | `agg_daily_financial_metrics` | `average_ticket_amount` |
| Taxa de devolução | Quantidade devolvida efetivamente dividida pela quantidade vendida efetivamente | `agg_daily_financial_metrics` | `return_quantity_rate` |

Os KPIs são calculados diariamente. Para períodos maiores, ticket médio e
taxa de devolução devem ser recalculados pela soma dos componentes:

```text
ticket_medio =
    sum(effective_order_revenue_amount)
    / sum(effective_order_count)

taxa_de_devolucao =
    sum(returned_item_quantity)
    / sum(sold_item_quantity)
```

## Perguntas Analíticas

As perguntas abaixo explicam os KPIs principais e apoiam decisões comerciais,
financeiras e operacionais.

| Pergunta | Modelo Gold | Medida principal |
|---|---|---|
| Quantos pedidos efetivos foram realizados? | `agg_daily_financial_metrics` | `effective_order_count` |
| Quantas unidades foram vendidas? | `agg_daily_financial_metrics` | `sold_item_quantity` |
| Quais produtos tiveram maior volume de vendas? | `fct_sales`, `dim_products` | `order_item_quantity` |
| Quais produtos mais contribuíram para o faturamento líquido? | `fct_sales`, `dim_products` | `order_item_net_revenue_after_returns` |
| Qual foi o valor total reembolsado? | `agg_daily_financial_metrics` | `completed_refund_amount` |
| Quais produtos apresentaram maior taxa de devolução? | `fct_sales`, `dim_products` | Quantidade devolvida ÷ quantidade vendida |
| Quais itens podem entrar em ruptura? | `fct_inventory_replenishment` | `will_stockout_before_replenishment` |
| Quanto deve ser reposto por variante e local? | `fct_inventory_replenishment` | `suggested_reorder_quantity` |
| Qual lead time é utilizado na reposição? | `fct_inventory_replenishment` | `selected_lead_time_days` |
| Quais fornecedores atendem cada produto? | `bridge_product_suppliers` | `supplier_id`, `last_quoted_cost`, `lead_time_days` |

## Qualidade e Reutilização

### Macros customizadas

O projeto centraliza transformações compartilhadas em macros:

- `clean_and_format_name`;
- `clean_and_format_name_place`;
- `lower_trim`;
- `upper_trim`;
- `initcap_trim`;
- `nullify_garbage`;
- `nullify_future_date`;
- `generate_schema_name`.

### DRY

O princípio DRY é aplicado por meio de:

- macros reutilizáveis;
- modelos intermediários compartilhados;
- regras de negócio calculadas uma única vez;
- dependências declaradas com `ref()`;
- fontes declaradas com `source()`.

### Nomenclatura

Os modelos utilizam `stg_`, `int_`, `dim_`, `fct_`, `bridge_` e `agg_`.
Chaves substitutas terminam em `_sk`, identificadores em `_id` e flags
booleanas começam com `is_` ou `has_`.

### Testes

Os testes são aplicados conforme o risco analítico:

- `unique` e `not_null` para chaves e granularidade;
- `relationships` para integridade referencial;
- `accepted_values` para domínios fechados;
- testes `dbt_utils` para chaves compostas e invariantes;
- testes singulares para reconciliações e regras de negócio.

Os testes singulares cobrem, entre outros:

- reconciliação de subtotais;
- alocação de descontos;
- limites de reembolso e devolução;
- consistência de estoque e reposição;
- seleção e confiança do lead time;
- reconciliação das métricas financeiras.

## Governança de Dados (PII e Mascaramento)

### Classificação de colunas

Colunas com dado de pessoa física (nome, CPF, e-mail, telefone, endereço)
recebem a tag `databricks_tags: data_classification: "pii"` diretamente no
`.yml` do model. Identificadores de empresa (CNPJ, razão social) recebem
`data_classification: "sensitive"` — uma distinção intencional, já que PII se
refere especificamente a dado de pessoa física, não de pessoa jurídica.

Essa tag só é suportada em modelos materializados como `table`; staging e
intermediate são views e não a recebem. Hoje ela está aplicada em
`dim_customers`, `dim_employees` e `dim_suppliers`.

### Mascaramento (ABAC)

A tag sozinha só classifica a coluna — quem de fato mascara o dado é uma
**policy de ABAC** (Attribute-Based Access Control) do Unity Catalog,
configurada uma única vez por catálogo em
[`dbt_lh_nautical/setup/pii_masking_policy.sql`](dbt_lh_nautical/setup/pii_masking_policy.sql):

1. Uma **governed tag** (`data_classification`) é registrada no nível da conta.
2. Uma função (`mask_pii`) retorna um valor fixo (`***MASKED***`) para qualquer
   coluna à qual for aplicada.
3. Uma **policy** aplica essa função automaticamente a toda coluna com a tag
   `pii`, para qualquer usuário, sem exceção.

O script usa widgets do SQL editor (`:catalog_name`) e deve ser executado uma
vez por catálogo (cada dev pessoal, e produção).

## Documentação

As descrições completas de modelos e colunas, os testes aplicados, a
granularidade e o lineage são mantidos nos arquivos YAML e publicados pelo
`dbt docs`.

| Artefato | Responsabilidade |
|---|---|
| `README.md` | Visão geral, arquitetura, execução e regras centrais |
| YAML | Modelos, colunas, granularidade e testes |
| `dbt docs` | Catálogo, lineage, DAG e SQL compilado |
| Matriz funcional | Cobertura completa das perguntas de negócio |

Para gerar e abrir a documentação:

```bash
cd dbt_lh_nautical

dbt docs generate \
  --vars '{"catalog_name": "dev_<seu_nome>_lh_nautical"}'

dbt docs serve
```

O comando `dbt docs generate` cria o catálogo da documentação. O comando
`dbt docs serve` inicia o site local para consulta.

## Orquestração e CI/CD

### Databricks Job

A transformação é executada pela task `dbt_transformacao` dentro do job
`job_ingestao_completa`, após a ingestão e seus testes.

### Resiliência e idempotência

Todas as 5 tasks do job `job_ingestao_completa` — incluindo `dbt_transformacao`
— possuem `max_retries: 3`, `min_retry_interval_millis: 120000` e
`retry_on_timeout: true`, permitindo até três novas tentativas automáticas
em caso de falha.

A idempotência do pipeline é garantida em duas camadas. Na ingestão, a
função `incremental_ingenstion_hash` compara o hash MD5 da linha de origem
contra os hashes já gravados no destino (via `left_anti` join) antes de
gravar qualquer registro: reexecutar a ingestão sobre o mesmo estado da
origem não produz linhas duplicadas, porque os registros já presentes no
destino são descartados da escrita. Na transformação, os modelos
materializados como `view` e `table` são reconstruídos deterministicamente
a partir do estado atual das fontes a cada `dbt build`, sem depender de
execuções anteriores. O snapshot de estoque utiliza a estratégia histórica
do dbt para registrar mudanças sem duplicar versões já processadas.

Falhas de processamento são monitoradas por
`email_notifications.on_failure`, configurado no nível do Databricks Job.

### Bitbucket Pipelines

O pipeline cobre:

1. lint e build dbt em Pull Requests destinados a `develop`;
2. validação do Databricks Bundle entre `develop` e `main`;
3. implantação do bundle após promoção para `main`.

O desenvolvimento utiliza branches de escopo delimitado e revisão por
Pull Request.

## Execução Local

### Pré-requisitos

São necessários Python compatível, Databricks CLI, autenticação OAuth,
perfil em `~/.dbt/profiles.yml` e catálogo pessoal com a Bronze carregada.

### Instalar dependências

```bash
pip install -r requirements.txt

cd dbt_lh_nautical
dbt deps
```

### Validar o projeto

```bash
dbt parse \
  --vars '{"catalog_name": "dev_<seu_nome>_lh_nautical"}'
```

### Executar as transformações e testes

```bash
dbt build \
  --vars '{"catalog_name": "dev_<seu_nome>_lh_nautical"}'
```

### Executar um modelo e suas dependências

```bash
dbt build \
  --select +nome_do_modelo \
  --vars '{"catalog_name": "dev_<seu_nome>_lh_nautical"}'
```

### Validar SQL

```bash
sqlfluff lint models/
```