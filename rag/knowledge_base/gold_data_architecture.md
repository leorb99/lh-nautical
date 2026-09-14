# Arquitetura de Dados Gold

## 1. Objetivo e escopo

Este documento descreve a estrutura técnica da camada Gold do projeto: organização dos modelos, materialização, grão, chaves e unicidade, lineage principal, mecanismos de snapshot/estado corrente, preservação de registros, tratamento técnico de datas futuras, tratamento estrutural de observações inválidas de lead time, integridade referencial e testes estruturais.

Este documento não é um catálogo de regras de negócio. Regras gerais de classificação e inclusão/exclusão estão em [`gold_business_rules.md`](./gold_business_rules.md); regras operacionais de estoque e reposição estão em [`inventory_and_stockout.md`](./inventory_and_stockout.md); escopo e limitações do pipeline de forecasting estão em [`demand_forecasting.md`](./demand_forecasting.md); fórmulas e definições quantitativas de métricas estão em [`metrics_dictionary.md`](./metrics_dictionary.md).

## 2. Organização da camada Gold

A camada Gold é composta por **20 modelos SQL**, distribuídos em três diretórios:

- **7 dimensions** (`models/marts/dimension/`)
- **12 facts/aggregates/current-state models** (`models/marts/fact/`)
- **1 bridge** (`models/marts/bridge/`)

O prefixo de nome (`fct_`, `dim_`, `agg_`) não determina sozinho a natureza semântica do modelo. Exemplos observados:

- `fct_current_inventory` — não é um fato de evento; representa o **estado corrente** de estoque (CURRENT_STATE).
- `fct_inventory_replenishment` — recomendação derivada sobre o estado corrente, não um evento transacional (CURRENT_STATE / DERIVED).
- `fct_inventory_snapshot` — expõe o **histórico versionado completo** de uma entidade, não apenas seu estado atual.
- `agg_monthly_demand_features` — tabela de features agregadas (AGGREGATE / FEATURE TABLE), não uma dimensão nem um fato transacional.

| Model | Architectural type | Grain | Grain confidence |
|---|---|---|---|
| dim_customers | DIMENSION | customer_id | SUPPORTED |
| dim_dates | DIMENSION | date_day | CONFIRMED |
| dim_employees | DIMENSION | employee_id | SUPPORTED |
| dim_locations | DIMENSION | location_id | SUPPORTED |
| dim_payment_methods | DIMENSION | payment_method_name | SUPPORTED |
| dim_products | DIMENSION | product_variant_id | CONFIRMED |
| dim_suppliers | DIMENSION | supplier_id | SUPPORTED |
| bridge_product_suppliers | BRIDGE | product_variant_id + supplier_id | CONFIRMED |
| fct_sales | FACT | order_item_id | SUPPORTED |
| fct_returns | FACT | return_item_id | SUPPORTED |
| fct_payments | FACT | payment_id | SUPPORTED |
| fct_purchases | FACT | purchase_order_item_id | SUPPORTED |
| fct_goods_receipts | FACT | goods_receipt_item_id | SUPPORTED |
| fct_stock_movements | FACT | stock_movement_id | SUPPORTED |
| fct_current_inventory | CURRENT_STATE | product_variant_id + location_id | CONFIRMED |
| fct_inventory_snapshot | SNAPSHOT (histórico) | product_variant_id + location_id + versão | SUPPORTED |
| fct_inventory_replenishment | CURRENT_STATE / DERIVED | product_variant_id + location_id | CONFIRMED |
| agg_daily_financial_metrics | AGGREGATE | metric_date | CONFIRMED |
| agg_customer_rfm | AGGREGATE | customer_id | CONFIRMED |
| agg_monthly_demand_features | AGGREGATE / FEATURE TABLE | product_name + month_start | SUPPORTED |

## 3. Materialização

| Camada | Materialização | Schema |
|---|---|---|
| staging | view | silver |
| intermediate | view | silver |
| marts/dimension | table | gold |
| marts/fact | table | gold |
| marts/bridge | table | gold |

Nenhum modelo Gold ou intermediate com configuração `materialized: incremental` foi identificado. Nenhuma configuração de `partition_by`, `cluster_by`, `liquid_clustering` ou equivalente foi encontrada em nenhum modelo ou no `dbt_project.yml`.

A exceção estrutural é o snapshot de estoque (`snapshots/snp_stock_levels.sql`), que materializa no **schema silver** (não gold) — detalhado na Seção 8.

A macro `generate_schema_name` resolve o nome físico do schema usando diretamente o valor `+schema` declarado por diretório (`silver` ou `gold`), sem concatenar o schema do profile de conexão.

## 4. Grão, chaves e unicidade

### Surrogate key

**16 dos 20 modelos Gold** possuem uma coluna de surrogate key (`*_sk`). Nos 16 casos, essa coluna tem testes `unique` e `not_null`. Isso **não significa automaticamente que a chave natural correspondente também é testada como única** — na maioria desses 16 modelos, a chave natural (ex.: `order_item_id`, `payment_id`, `employee_id`) tem apenas teste `not_null`, não `unique`.

### Modelos sem surrogate key

Os outros **4 modelos** não possuem coluna `*_sk`:

- `dim_dates`
- `agg_daily_financial_metrics`
- `agg_customer_rfm`
- `agg_monthly_demand_features`

Nos três primeiros, o grão é validado **diretamente** por teste `unique`+`not_null` na própria chave natural (`date_day`, `metric_date`, `customer_id`, respectivamente).

**`agg_monthly_demand_features`:**

- **Grain:** `product_name + month_start`
- **Status:** SUPPORTED
- **Uniqueness:** UNIQUENESS_ASSUMED
- **Reason:** o SQL (`GROUP BY product_name, month_start`) e o YAML ("Grain: one row per product_name and month_start") sustentam o grão, mas não existe teste `unique` nem `unique_combination_of_columns` sobre essa combinação — apenas `not_null` em cada coluna isoladamente.

**Known limitation:** a unicidade do grão de `agg_monthly_demand_features` não é validada por nenhum teste dbt.

## 5. Lineage principal

### 5.1 Vendas e financeiro

```
stg_orders + stg_fiscal_invoices → int_orders
stg_order_items → int_order_items
int_returns → int_completed_returns_by_order_item
int_order_items + int_orders + int_completed_returns_by_order_item → fct_sales
fct_sales → agg_daily_financial_metrics
fct_sales → agg_customer_rfm
fct_sales + dim_products + dim_dates → agg_monthly_demand_features
```

Fórmulas financeiras não são repetidas aqui — ver [`metrics_dictionary.md`](./metrics_dictionary.md).

### 5.2 Estoque

```
stg_stock_levels → snp_stock_levels → int_inventory_snapshots
int_inventory_snapshots → int_current_inventory → fct_current_inventory
int_current_inventory + demanda + lead time + open PO → int_inventory_replenishment_inputs → fct_inventory_replenishment
int_inventory_snapshots → fct_inventory_snapshot
stg_stock_movements → int_stock_movements → int_inventory_demand_metrics / fct_stock_movements
```

### 5.3 Lead time

```
int_purchase_orders + int_purchase_order_items + int_goods_receipts + stg_goods_receipt_items → int_purchase_order_receipts
        ↓
int_inventory_lead_time_metrics
int_product_lead_time_metrics
int_global_lead_time_metrics
int_supplier_lead_time_metrics
```

A hierarquia de fallback entre esses agregados (GOLD-020) e a classificação de confiança (GOLD-021) são regras operacionais já canonizadas em [`inventory_and_stockout.md`](./inventory_and_stockout.md) — não redesenvolvidas aqui.

### 5.4 Forecasting

```
fct_sales + dim_products + dim_dates → agg_monthly_demand_features
```

Não foi encontrado, no repositório versionado, nenhum modelo consumidor desta feature table. Isso é registrado como um fato observável sobre o estado atual do repositório — não como uma afirmação de que a tabela é inútil ou abandonada.

## 6. Tratamento técnico de datas futuras

Na saída da execução do modelo, para os campos em que essa validação é aplicada, valores superiores ao `current_timestamp()` da execução são substituídos por `NULL`, preservando a linha. O valor original não é preservado nesse campo da saída tratada.

**Rule ID:** `GOLD-007`

Tecnicamente, duas formas de implementação coexistem:

- a macro compartilhada `nullify_future_date.sql`, usada em `int_locations`, `int_employees` e `int_goods_receipts`;
- o mesmo padrão de `CASE WHEN` reescrito inline (sem a macro), usado em `int_orders`, `int_purchase_orders`, `int_returns`, `int_customers` e `int_products_enriched`.

`fct_payments` é o único caso observado em que esse tratamento ocorre diretamente na camada Gold, em vez da camada intermediate — nos demais casos identificados, o tratamento ocorre na camada intermediate.

A cobertura documentada corresponde aos campos explicitamente comprovados pela implementação:

| Campo(s) | Modelo |
|---|---|
| `placed_at`, `created_at`, `updated_at` (pedido) | int_orders |
| `issued_at`, `created_at`, `updated_at` (nota fiscal) | int_orders |
| `placed_at`, `created_at`, `updated_at` (pedido de compra) | int_purchase_orders |
| `created_at` (base de `returned_at`/`return_date`, com flag `is_future_return_date`) | int_returns |
| `created_at`, `updated_at` (cliente) | int_customers |
| `created_at`, `updated_at` (local) | int_locations |
| `created_at`, `updated_at` (funcionário) | int_employees |
| `product_created_at`, `product_updated_at` | int_products_enriched |
| `brand_created_at`, `brand_updated_at` | int_products_enriched |
| `category_created_at`, `category_updated_at` | int_products_enriched |
| `received_at`, `created_at` (recebimento, com flag `is_future_receipt_date`) | int_goods_receipts |
| `payment_paid_at` (com flag `is_effective_paid`) | fct_payments |

Não se afirma que todos os campos temporais do projeto passam por essa validação, que o valor é irrecuperável em todo o sistema, ou que a origem/staging perde o valor original — apenas que, nos campos e modelos listados acima, o campo específico na saída tratada é substituído por `NULL`.

## 7. Observações de lead time

`lead_time_days` (em `int_purchase_order_receipts`) só recebe um valor calculado quando:

- `placed_at` não é `NULL`;
- `first_received_at` não é `NULL`;
- `first_received_at >= placed_at`.

Caso contrário, `lead_time_days = NULL`. A linha correspondente em `int_purchase_order_receipts` **permanece** na tabela — não é descartada.

Nos quatro modelos agregadores downstream, cada um com sua própria cláusula `WHERE lead_time_days IS NOT NULL`, essas observações inválidas são excluídas do histórico agregado:

- `int_inventory_lead_time_metrics` (grão: product_variant + location)
- `int_product_lead_time_metrics` (grão: product_variant)
- `int_global_lead_time_metrics` (grão: global)
- `int_supplier_lead_time_metrics` (grão: supplier)

**Rule ID:** `GOLD-022`

**Cross-reference:** [`inventory_and_stockout.md`](./inventory_and_stockout.md) (hierarquia de fallback GOLD-020, não redesenvolvida aqui).

## 8. Snapshots e estado corrente

`snp_stock_levels` é um **dbt snapshot real** (não uma lógica SQL equivalente construída manualmente):

- `target_schema`: silver
- `strategy`: timestamp
- `updated_at`: updated_at
- `unique_key`: concatenação de `product_variant_id` e `location_id`
- `invalidate_hard_deletes`: true

`int_inventory_snapshots` consome `snp_stock_levels` e deriva `is_current_snapshot = (dbt_valid_to is null)`.

A partir daí, a Gold expõe três visões distintas:

- `fct_inventory_snapshot` → histórico completo de versões;
- `fct_current_inventory` → apenas o estado atual (filtro `is_current_snapshot = true`);
- `fct_inventory_replenishment` → recomendação derivada a partir do estado atual.

Para cada combinação de `product_variant_id` e `location_id`, no máximo um registro pode estar marcado como `is_current_snapshot = true`; não há evidência de que a existência de um registro atual seja garantida para toda combinação.

**Rule ID:** `GOLD-031`

- **AT_MOST_ONE_CURRENT:** CONFIRMED (validado por `tests/assert_single_current_inventory_snapshot.sql`)
- **EXACTLY_ONE_CURRENT:** NOT VALIDATED (nenhum teste de completude encontrado)

A ausência dessa garantia não é tratada como um defeito confirmado — apenas como uma propriedade não validada pela suíte de testes atual.

## 9. Moeda em pedidos de compra

O campo `purchase_order_currency` é propagado sem alteração desde `stg_purchase_orders` até `fct_purchases`, sem que nenhuma camada aplique conversão de câmbio. Os valores monetários associados a um pedido de compra permanecem na moeda original do pedido. Isso é documentado no repositório como uma limitação conhecida para qualquer KPI futuro que precise agregar valores entre pedidos de moedas diferentes.

**Rule ID:** `GOLD-033`

**Documented examples:** BRL, USD, EUR (valores documentados/exemplificados na descrição YAML dos modelos — não valores observados nos dados; nenhuma consulta aos dados foi realizada para determinar distribuição ou frequência real de moedas).

## 10. Preservação de registros

Joins arquiteturalmente relevantes (cobertura, cardinalidade ou preservação de população):

- `int_orders`: pedidos `LEFT JOIN` notas fiscais — preserva todo pedido mesmo sem nota fiscal associada.
- `fct_sales`: itens de pedido `LEFT JOIN` pedidos e `LEFT JOIN` devoluções efetivas agregadas por item — preserva todo item de pedido mesmo sem devolução associada.
- `int_inventory_replenishment_inputs`: estoque atual `LEFT JOIN` demanda, lead time e pedidos de compra abertos — o estoque atual é a população dirigente (driving dataset); produtos/locais sem demanda ou lead time histórico são preservados com valores de fallback/`COALESCE`.
- `int_supplier_lead_time_metrics`: `FULL OUTER JOIN` entre histórico observado e lead time declarado — preserva fornecedores com apenas um dos dois lados presente.
- `agg_monthly_demand_features`: expansão de `month_bounds` × `all_months` (malha mensal), restrita ao intervalo ativo de cada produto — não é uma expansão cartesiana irrestrita.

Joins estruturalmente triviais (sem efeito sobre cobertura/cardinalidade/grão) não são documentados aqui.

## 11. Proteção estrutural contra double-counting

`order_total_amount` existe repetido no grão de item em `fct_sales`. Nos dois modelos identificados que agregam esse valor — `agg_daily_financial_metrics` e `agg_customer_rfm` — ocorre uma redução explícita para uma linha por `order_id` antes da soma, por meio de `GROUP BY` incluindo `order_id` e seleção de `max(order_total_amount)`.

Esse padrão foi observado, implementado de forma independente e repetida (sem abstração/macro compartilhada), nos dois modelos citados.

**Cross-reference:** GOLD-008, [`metrics_dictionary.md`](./metrics_dictionary.md) — fórmula completa não repetida aqui.

## 12. Deduplicação e seleção de registros

**Endereço primário do cliente (`int_customers`):** `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY address_is_primary DESC, _ingested_at DESC, address_id DESC) = 1`. Existe desempate explícito: quando múltiplos endereços empatam em `address_is_primary` e `_ingested_at`, `address_id DESC` é usado como critério determinístico final.

**Devoluções efetivas agregadas (`int_completed_returns_by_order_item`):** o modelo agrega registros por `order_item_id` utilizando funções como `MAX` e `MAX_BY`. `MAX_BY` recupera o valor associado ao maior `returned_at`; não há tratamento explícito identificado para empate exato em `returned_at`.

**Snapshot de estoque:** o versionamento é inteiramente gerenciado pelo mecanismo de dbt snapshot (Seção 8), não por lógica de deduplicação manual.

`GROUP BY` usado para agregação por grão de negócio (ex.: soma de vendas por dia) não é classificado como deduplicação nesta seção.

## 13. Integridade referencial

- **VALIDATED_REFERENCE** → existe teste dbt `relationships`.
- **IMPLEMENTED_JOIN_ONLY** → existe join SQL real no modelo, sem teste `relationships`.
- **ASSUMED_REFERENCE** → relação FK-like esperada/documentada, sem join SQL direto e sem teste `relationships`.

| Relation | Direct SQL Join | relationships test | Classification |
|---|---|---|---|
| `bridge_product_suppliers.product_variant_id → stg_product_variants` | NO | YES | VALIDATED_REFERENCE |
| `bridge_product_suppliers.supplier_id → stg_suppliers` | NO | YES | VALIDATED_REFERENCE |
| `int_completed_returns_by_order_item.order_item_id → int_order_items` | NO | YES | VALIDATED_REFERENCE |
| `agg_monthly_demand_features ↔ dim_products` (product_variant_id) | YES | NO | IMPLEMENTED_JOIN_ONLY |
| `fct_payments ↔ dim_payment_methods` (payment_method) | YES | NO | IMPLEMENTED_JOIN_ONLY |
| `fct_sales ↔ dim_customers` / `dim_products` / `dim_locations` | NO | NO | ASSUMED_REFERENCE |
| `fct_inventory_replenishment ↔ dim_products` / `dim_locations` | NO | NO | ASSUMED_REFERENCE |
| `fct_purchases ↔ dim_suppliers` | NO | NO | ASSUMED_REFERENCE |
| Demais colunas `*_id` em fatos Gold apontando para dimensões | Não verificado join real adicional | NO | ASSUMED_REFERENCE |

As três relações `VALIDATED_REFERENCE` não possuem join SQL direto entre os modelos — sua validação decorre exclusivamente do teste `relationships` declarado no YAML. Nenhuma dessas relações constitui uma constraint física de chave estrangeira.

## 14. Testes estruturais e garantias

Todos os testes abaixo são verificações executadas no momento do `dbt build`/`dbt test` — **nenhum constitui uma constraint física de banco de dados**.

Tipos de teste estrutural encontrados: `unique`, `not_null`, `unique_combination_of_columns`, `relationships`, `dbt_utils.equal_rowcount`, `assert_single_current_inventory_snapshot.sql`, `assert_replenishment_inputs_preserve_current_inventory.sql`.

A única associação direta entre um teste estrutural e uma Rule ID de negócio é:

`assert_single_current_inventory_snapshot.sql` → **GOLD-031**

As seguintes validações estruturais não possuem associação direta com uma Rule ID de negócio — um teste de unicidade de grão valida a estrutura do modelo, não a lógica de negócio calculada nele:

- `unique_combination_of_columns` em `fct_current_inventory` (não valida a classificação de saúde/risco de estoque)
- `unique_combination_of_columns` em `fct_inventory_replenishment` (não valida as fórmulas de demanda/alvo de reposição)
- `dbt_utils.equal_rowcount` (fct_sales vs. int_order_items) (valida ausência de fan-out nos joins, não a regra de venda efetiva)
- `relationships` de `int_completed_returns_by_order_item` (valida integridade de chave, não a regra de reembolso por ação)

## 15. Observações arquiteturais

**Duplicação da lógica de data futura:** a mesma lógica de nulificação existe tanto via macro compartilhada quanto via `CASE` inline reescrito em múltiplos modelos (Seção 6). *Classificação: architectural observation.*

**`fct_payments` na camada Gold:** único caso de tratamento de data futura implementado fora da camada intermediate. *Classificação: architectural observation.*

**Duplicação da proteção contra double-counting:** o mesmo padrão de redução a uma linha por pedido é implementado de forma independente em dois modelos, sem abstração compartilhada (Seção 11). *Classificação: architectural observation.*

**Localização do mecanismo SCD:** o único mecanismo SCD identificado no repositório reside no schema Silver (`snp_stock_levels`). O histórico versionado resultante é materializado na Gold por `fct_inventory_snapshot`, enquanto o estado corrente é exposto por `fct_current_inventory` e utilizado pelo pipeline de reposição. *Classificação: architectural observation.*

Nenhum destes itens recebe Rule ID.

## 16. Limitações conhecidas

### 16.1 `agg_monthly_demand_features`

O grão `product_name + month_start` é sustentado por SQL e YAML, mas não possui teste formal de unicidade (nem `unique`, nem `unique_combination_of_columns`).

**Status:** SUPPORTED / UNIQUENESS_ASSUMED

Isso não invalida as regras já canonizadas GOLD-035 a GOLD-038 em [`demand_forecasting.md`](./demand_forecasting.md).

### 16.2 Existência de snapshot atual

GOLD-031 garante apenas AT_MOST_ONE_CURRENT. Não existe teste de completude que estabeleça EXACTLY_ONE_CURRENT para toda combinação de `product_variant_id` e `location_id`. Isso não é tratado como um defeito confirmado, apenas como uma propriedade não validada pela suíte de testes atual.

## 17. Relação com os demais documentos canônicos

| Topic | Canonical owner |
|---|---|
| General business semantics | [`gold_business_rules.md`](./gold_business_rules.md) |
| Inventory/replenishment rules | [`inventory_and_stockout.md`](./inventory_and_stockout.md) |
| Demand/forecasting semantics | [`demand_forecasting.md`](./demand_forecasting.md) |
| Metric formulas | [`metrics_dictionary.md`](./metrics_dictionary.md) |
| Gold technical structure | gold_data_architecture.md (este documento) |
