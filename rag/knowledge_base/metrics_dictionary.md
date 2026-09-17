# Dicionário de Métricas

## Objetivo

Este documento centraliza a definição quantitativa das métricas relevantes da camada Gold: fórmula, unidade, grão, componentes, comportamento numérico (NULL/zero) e precisão de armazenamento.

As regras operacionais que determinam **quando** ou **por que** uma métrica é utilizada — critérios de inclusão/exclusão, classificações, fallbacks, thresholds de negócio — permanecem nos documentos de domínio correspondentes: [`gold_business_rules.md`](./gold_business_rules.md), [`inventory_and_stockout.md`](./inventory_and_stockout.md) e [`demand_forecasting.md`](./demand_forecasting.md). Este dicionário não as repete além do necessário para tornar a fórmula compreensível.

## Convenções

### Grão

Toda métrica descrita neste documento deve ser interpretada estritamente no grão do modelo em que é publicada. Métricas com nomes conceitualmente semelhantes calculadas em grãos diferentes (por exemplo, demanda operacional de estoque em `product_variant_id + location_id` versus demanda de forecasting em `product_name + month_start`) não devem ser combinadas ou comparadas diretamente — cada uma é documentada em sua própria entrada.

### Unidade

São usadas apenas unidades comprováveis pela implementação: valor monetário da fonte, unidades (quantidade de itens), unidades/dia, dias, razão (ratio), contagem, ou pontuação ordinal.

**Sobre moeda:** nenhuma configuração de código de moeda (ISO ou equivalente) foi encontrada na cadeia de cálculo das métricas financeiras. Para todas as métricas monetárias deste documento, a unidade é registrada como "valor monetário da fonte", e o código de moeda é `UNKNOWN`.

### NULL e zero

Não existe uma regra universal de que "0 significa sem dado" ou "NULL significa sem dado" neste projeto. O comportamento é específico de cada métrica e está documentado individualmente em cada entrada abaixo. Resumo comprovado:

- `average_ticket_amount`: retorna `0` quando `effective_order_count = 0`;
- `return_quantity_rate`: retorna `0` quando `sold_item_quantity = 0`;
- `inventory_coverage_days_30d/60d/90d`: retorna `NULL` quando a respectiva demanda média diária não é positiva;
- `lag_1m`...`lag_12m`: retornam `NULL` quando o deslocamento ultrapassa o histórico anterior existente;
- `rolling_avg_*`, `rolling_max_*`: podem retornar `NULL` quando não há nenhuma linha anterior disponível na janela;
- `momentum`: retorna `NULL` quando algum dos componentes da subtração é `NULL`.

### Precisão e arredondamento

| Métrica | Precisão |
|---|---|
| `net_revenue_amount` | `decimal(14,2)`, cast aplicado após a soma |
| `average_ticket_amount` | `decimal(14,2)`, cast aplicado após a divisão |
| `return_quantity_rate` | `decimal(14,6)` |
| `demand_during_lead_time` | `decimal(14,3)` |
| `safety_stock_quantity` | `decimal(14,3)` |
| `recommended_stock_target` | `decimal(14,3)` |
| `suggested_reorder_quantity` | `decimal(14,3)` |
| `inventory_coverage_days_30d/60d/90d` | `decimal(14,2)` |
| `order_item_net_revenue_after_discount` / `order_item_net_revenue_after_returns` | sem `cast`/`round` explícito na expressão |
| `rolling_avg_*`, `rolling_std_6m`, `rolling_max_*`, `momentum` | sem cast explícito identificado |

Nenhuma precisão foi presumida além do que está explicitamente implementado no código.

---

## Métricas financeiras

### Waterfall financeiro (item-level)

A receita de um item de pedido passa pelas seguintes etapas, cada uma deduzindo um componente adicional:

```
order_item_line_total
        ↓  (- ICMS - IPI)
order_item_net_revenue
        ↓  (- desconto do pedido, alocado proporcionalmente)
order_item_net_revenue_after_discount
        ↓  (- valores reembolsados associados às devoluções consideradas pelo modelo)
order_item_net_revenue_after_returns
```

- **`order_item_line_total`** (SUPPORTING_METRIC) — valor bruto da linha do item, antes da dedução de ICMS, IPI e desconto. Fonte: `int_order_items.sql`.
- **`order_item_net_revenue`** (SUPPORTING_METRIC) — `order_item_line_total - order_item_icms_amount - order_item_ipi_amount`. Fonte: `int_order_items.sql`. Precisão: `decimal(14,2)`.
- **`order_item_net_revenue_after_discount`** (SUPPORTING_METRIC) — receita após alocação proporcional do desconto do pedido (regra de alocação já canonizada como GOLD-003 em `gold_business_rules.md`, não redesenvolvida aqui). Sem `cast`/`round` explícito nesta expressão.

Nenhum componente do waterfall acima possui Rule ID própria, exceto o último degrau:

### `order_item_net_revenue_after_returns`

**Classification:** METRIC

**Significado:** Receita líquida por item de pedido após (1) os impostos considerados no cálculo upstream, (2) a alocação proporcional do desconto do pedido, e (3) a dedução de valores reembolsados associados às devoluções efetivas consideradas pelo modelo. O termo `completed`, presente no nome do componente de reembolso, refere-se ao estado/escopo da devolução considerada pelo pipeline (devoluções com status `completed` e data válida) — não é evidência de que 100% do valor do item foi reembolsado.

**Fórmula:**
```text
order_item_net_revenue_after_discount - completed_refund_amount
```

**Grão:** `order_item_id`

**NULL:** `completed_refund_amount` recebe `COALESCE` para zero quando não existe devolução correspondente ao item — nunca é `NULL` nesta expressão.

**Precisão:** sem `cast`/`round` explícito na expressão final.

**Referências cruzadas:** GOLD-003, GOLD-006 (`gold_business_rules.md`) — não redesenvolvidas aqui.

**Fonte de implementação:** `models/marts/fact/fct_sales.sql`

**Rule ID:** `GOLD-004`

---

### `net_revenue_amount`

**Classification:** METRIC

**Significado:** Receita líquida diária agregada das vendas efetivas.

**Fórmula:**
```text
cast(
    sum(order_item_net_revenue_after_returns)
    as decimal(14,2)
)
```
calculada sobre o subconjunto com `is_effective_sale = true`, agrupado por `metric_date`. O `cast` para `decimal(14,2)` ocorre **após** a agregação (soma), não por linha.

**Grão:** `metric_date`

**Data:** derivada da data do pedido (`placed_at`) utilizada no modelo.

**Precisão:** `decimal(14,2)`

**Fonte de implementação:** `models/marts/fact/agg_daily_financial_metrics.sql`

**Rule ID:** `GOLD-009`

---

### `average_ticket_amount`

**Classification:** METRIC

**Significado:** Ticket médio diário de vendas efetivas.

**Fórmula:**
```text
case
    when effective_order_count > 0
        then effective_order_revenue_amount / effective_order_count
    else 0
end
```

**Numerator:** `effective_order_revenue_amount` — soma de `order_total_amount` **após redução a uma única linha por `order_id`** (proteção explícita contra double-counting; ver seção "Double-counting" abaixo).

**Denominator:** `effective_order_count` — contagem de pedidos efetivos distintos no dia.

**Grão:** `metric_date`

**Zero:** quando `effective_order_count = 0`, o resultado é `0` (não `NULL`).

**Precisão:** `decimal(14,2)`, cast aplicado após a divisão.

**Regra de agregação importante:** `order_total_amount` **não deve ser somado diretamente no grão de item** — esta métrica só é válida porque a implementação reduz para uma linha por pedido antes de somar (GOLD-008, `gold_business_rules.md`).

**Não-aditividade:** `average_ticket_amount` não é aditiva entre dias. Para períodos maiores que um dia, deve ser recalculada a partir do numerador (`effective_order_revenue_amount`) e do denominador (`effective_order_count`) correspondentes somados no período, e não somada ou mediada diretamente entre as linhas diárias.

**Testes:** reconciliação com tolerância `0.01` entre o valor publicado e o recalculado a partir do numerador/denominador expostos.

**Referência cruzada:** GOLD-008 (`gold_business_rules.md`)

**Fonte de implementação:** `models/marts/fact/agg_daily_financial_metrics.sql`

**Rule ID:** `GOLD-010`

---

### `return_quantity_rate`

**Classification:** METRIC (ratio)

**Significado:** Taxa de devolução diária baseada em quantidade de unidades, não em valor monetário.

**Fórmula:**
```text
case
    when sold_item_quantity > 0
        then returned_item_quantity / sold_item_quantity
    else 0
end
```

**Numerator:** `returned_item_quantity`

**Denominator:** `sold_item_quantity`

**Population:** vendas efetivas do dia. A devolução permanece atribuída ao dia da venda original (não ao dia em que a devolução foi processada), conforme a implementação atual.

**Grão:** `metric_date`

**Unit:** ratio, no intervalo [0, 1]

**Zero:** quando `sold_item_quantity = 0`, o resultado é `0`.

**Precisão:** `decimal(14,6)`

**Testes:** reconciliação com tolerância `0.000001`; validação de domínio `0 <= return_quantity_rate <= 1`.

**Restrição:** não é uma taxa monetária, nem uma taxa por pedido — é uma razão de quantidades, armazenada como valor decimal.

**Fonte de implementação:** `models/marts/fact/agg_daily_financial_metrics.sql`

**Rule ID:** `GOLD-011`

---

## Métricas de clientes e RFM

### A. Métricas base RFM

**`recency_days`** (METRIC) — diferença em dias entre a data de referência da base (última venda efetiva observada entre todos os clientes) e a última venda efetiva do cliente, conforme regra de data de referência já canonizada em GOLD-014 (`gold_business_rules.md`, não redesenvolvida aqui).

**`frequency_orders`** (METRIC) — `count(distinct order_id)` de pedidos efetivos do cliente.

**`monetary_amount`** (METRIC) — soma de `order_total_amount` do cliente, após proteção contra double-counting no nível de pedido (mesma proteção descrita para `effective_order_revenue_amount`; ver GOLD-008).

**Grão:** `customer_id`

**Fonte de implementação:** `models/marts/fact/agg_customer_rfm.sql`

### B. Scores RFM

**Classification:** ORDINAL_SCORE (não METRIC simples)

```text
recency_score   = ntile(5) over (order by recency_days desc)
frequency_score = ntile(5) over (order by frequency_orders asc)
monetary_score  = ntile(5) over (order by monetary_amount asc)
```

**Unit:** pontuação ordinal, 1 a 5

**Grão:** `customer_id`

**Comportamento:** os scores são relativos à distribuição de clientes no conjunto calculado no momento da execução — não são valores absolutos universalmente comparáveis entre execuções diferentes se a base de clientes mudar.

**Fonte de implementação:** `models/marts/fact/agg_customer_rfm.sql`

**Rule ID:** `GOLD-015`

---

## Métricas de estoque e reposição

### `demand_during_lead_time`

**Classification:** DERIVED_METRIC

**Fórmula:**
```text
average_daily_sales_90d × selected_lead_time_days
```

**Unit:** units

**Grain:** `product_variant_id + location_id`

**Precisão:** `decimal(14,3)`

**Zero:** se `average_daily_sales_90d = 0`, o resultado é `0`.

**Referências cruzadas:** [`inventory_and_stockout.md`](./inventory_and_stockout.md) — GOLD-019 (definição de `average_daily_sales_90d`) e GOLD-020 (hierarquia de fallback de `selected_lead_time_days`), não redesenvolvidas aqui.

**Fonte de implementação:** `models/marts/fact/fct_inventory_replenishment.sql`

**Rule ID:** `GOLD-023`

### `safety_stock_quantity` (SUPPORTING_METRIC)

**Fórmula:**
```text
average_daily_sales_90d × 7
```

**Unit:** units | **Grain:** `product_variant_id + location_id` | **Precisão:** `decimal(14,3)`

**Referência cruzada:** [`inventory_and_stockout.md`](./inventory_and_stockout.md) — GOLD-024. Não redesenvolvida aqui; não há Rule ID nova.

### `recommended_stock_target`

**Classification:** DERIVED_METRIC

**Fórmula:**
```text
demand_during_lead_time + safety_stock_quantity
```

**Unit:** units | **Grain:** `product_variant_id + location_id` | **Precisão:** `decimal(14,3)`

**Distinção crítica:** `recommended_stock_target` **não é o mesmo conceito** que `reorder_point`. O primeiro é derivado neste cálculo; o segundo vem da origem de dados de estoque, não é calculado por nenhum modelo Gold. A regra operacional completa dessa distinção está em [`inventory_and_stockout.md`](./inventory_and_stockout.md) e não é redesenvolvida aqui.

**Fonte de implementação:** `models/marts/fact/fct_inventory_replenishment.sql`

**Rule ID:** `GOLD-025`

### `suggested_reorder_quantity` (SUPPORTING_METRIC)

**Fórmula:**
```text
greatest(
    recommended_stock_target - quantity_on_hand - open_purchase_order_quantity,
    0
)
```

**Unit:** units | **Grain:** `product_variant_id + location_id` | **Precisão:** `decimal(14,3)`

**Referência cruzada:** [`inventory_and_stockout.md`](./inventory_and_stockout.md) — GOLD-026. Não há Rule ID nova.

### `inventory_coverage_days_30d` / `_60d` / `_90d`

**Classification:** METRIC (sem Rule ID própria)

**Fórmula geral:**
```text
quantity_on_hand / average_daily_sales_Nd    (N = 30, 60 ou 90)
```

**Unit:** days | **Grain:** `product_variant_id + location_id` | **Precisão:** `decimal(14,2)`

**Comportamento quando a demanda não é positiva:** o resultado é `NULL` (comportamento técnico comprovado pela ausência de cláusula `ELSE` no `CASE` correspondente — não é interpretado aqui como uma semântica genérica de "sem dado").

Estas três métricas são materializadas como colunas próprias em `fct_current_inventory`.

**Fonte de implementação:** `models/marts/fact/fct_current_inventory.sql`

### `average_daily_sales_30d` / `_60d` / `_90d` (referência)

Já definidas operacionalmente em [`inventory_and_stockout.md`](./inventory_and_stockout.md) (GOLD-019). Registro formal da fórmula, sem redesenvolver a regra:
```text
sales_quantity_last_Nd / N    (N = 30, 60 ou 90 — janela calendário completa, não COUNT de dias com venda)
```

---

## Métricas de forecasting e feature engineering

Estas métricas têm seu escopo, limitações e estado de integração com o pipeline preditivo documentados em [`demand_forecasting.md`](./demand_forecasting.md). Aqui consta apenas a definição formal da fórmula.

### `rolling_avg_3m`

**Classification:** METRIC | **Grain:** `product_name + month_start` | **Unit:** units

```text
avg(quantity_sold) over (
    partition by product_name
    order by month_start
    rows between 3 preceding and 1 preceding
)
```

Exclui o mês corrente. Se não houver nenhuma linha anterior disponível para a janela, o resultado pode ser `NULL`; quando há linhas anteriores disponíveis (mesmo que menos que três), a função `AVG` calcula sobre as observações disponíveis dentro da janela — não é exigido um número exato de observações para produzir um resultado.

### `rolling_avg_6m`

Mesmo padrão de `rolling_avg_3m`, com janela `rows between 6 preceding and 1 preceding`. Exclui o mês corrente. Não exige seis observações completas para gerar valor.

### `rolling_std_6m`

**Classification:** METRIC | **Grain:** `product_name + month_start` | **Unit:** units

```text
stddev(quantity_sold) over (
    partition by product_name
    order by month_start
    rows between 6 preceding and 1 preceding
)
```

O comportamento de `NULL` das médias móveis não é generalizado para esta métrica: o número mínimo de observações necessário para `stddev` retornar um valor não-nulo depende da semântica da função no engine utilizado. Este documento registra apenas a fórmula e a janela; não descreve isso como regra de negócio.

### `rolling_max_6m` / `rolling_max_12m`

**Classification:** METRIC | **Grain:** `product_name + month_start` | **Unit:** units

```text
max(quantity_sold) over (
    partition by product_name
    order by month_start
    rows between 6 preceding and 1 preceding   -- (12 preceding para rolling_max_12m)
)
```

Excluem o mês corrente.

### Família `lag_1m`, `lag_2m`, `lag_3m`, `lag_6m`, `lag_12m`

**Classification:** SUPPORTING_METRIC / historical feature | **Grain:** `product_name + month_start` | **Unit:** units

```text
lag(quantity_sold, N) over (
    partition by product_name
    order by month_start
)
```

Quando o deslocamento `N` ultrapassa o histórico anterior existente para o produto, o valor resultante é `NULL`.

**Referência cruzada:** [`demand_forecasting.md`](./demand_forecasting.md)

### `momentum`

**Classification:** DERIVED_METRIC | **Grain:** `product_name + month_start` | **Unit:** units

**Fórmula:**
```text
rolling_avg_3m - rolling_avg_6m
```

**Significado:** `momentum` mede a diferença entre a média móvel de 3 meses e a média móvel de 6 meses. Valores positivos ocorrem quando `rolling_avg_3m > rolling_avg_6m`; valores negativos ocorrem quando `rolling_avg_3m < rolling_avg_6m`.

**Zero:** quando as duas médias são iguais, `momentum = 0`.

**NULL:** o resultado é `NULL` quando algum dos componentes da subtração é `NULL`.

**Referência cruzada:** [`demand_forecasting.md`](./demand_forecasting.md)

**Fonte de implementação:** `models/marts/fact/agg_monthly_demand_features.sql`

**Rule ID:** `GOLD-038`

---

## Taxas, razões e médias

| Métrica | Numerador | Denominador | População | Unit |
|---|---|---|---|---|
| `return_quantity_rate` | `returned_item_quantity` | `sold_item_quantity` | vendas efetivas do dia | ratio [0,1] |
| `average_ticket_amount` | `effective_order_revenue_amount` | `effective_order_count` | pedidos efetivos do dia | valor monetário / pedido |
| `average_daily_sales_30/60/90d` | `sales_quantity_last_Nd` | `N` (constante fixa: 30, 60 ou 90) | movimentações de venda na janela | units/day |

O denominador de `average_daily_sales_Nd` é a janela calendário completa (constante fixa), não uma contagem de dias com venda — distinção relevante para não presumir que a média é calculada apenas sobre dias com movimento.

## Validações e tolerâncias

| Teste | Métrica | Comparação |
|---|---|---|
| Reconciliação diária de ticket médio | `average_ticket_amount` | tolerância `0.01` |
| Reconciliação diária de taxa de devolução | `return_quantity_rate` | tolerância `0.000001` |
| Reconciliação da alocação de desconto | `order_item_net_revenue_after_discount` (agregado por pedido) | comparação após `round(...,2)` |
| Reconciliação de subtotal do pedido | `order_subtotal_amount` vs. soma dos itens | comparação após `round(...,2)` |
| Reconciliação de quantidade sugerida de reposição | `suggested_reorder_quantity` | tolerância `0.001` |

A tolerância observada em cada teste possui a mesma ordem decimal da precisão publicada da respectiva métrica. Isso é uma correlação observável no código, não uma justificativa documentada explicitamente pelos autores.

Todos os testes listados validam uma condição no momento da execução do build (dbt test) — nenhum deles constitui uma constraint física de banco de dados, e nenhum impede que um valor inválido exista entre execuções.

## Double-counting

`order_total_amount` aparece repetido em todas as linhas de item do mesmo pedido em `fct_sales`. Métricas que utilizam `order_total_amount` devem reduzir os dados a uma linha por `order_id` antes de somar. Implementações comprovadas dessa proteção: `effective_order_revenue_amount` (`agg_daily_financial_metrics`) e `monetary_amount` (`agg_customer_rfm`).

**Referência cruzada:** GOLD-008 (`gold_business_rules.md`) — regra completa não redesenvolvida aqui.

## Relações com outros documentos canônicos

- [`gold_business_rules.md`](./gold_business_rules.md) → GOLD-001 (venda efetiva, filtro subjacente a várias métricas), GOLD-003 (alocação de desconto), GOLD-006 (reembolso), GOLD-008 (não somar `order_total_amount` no grão de item), GOLD-014 (data de referência RFM)
- [`inventory_and_stockout.md`](./inventory_and_stockout.md) → GOLD-019 (demanda operacional), GOLD-020 (lead time), GOLD-024 (estoque de segurança), GOLD-026 (quantidade sugerida)
- [`demand_forecasting.md`](./demand_forecasting.md) → GOLD-035, GOLD-036, GOLD-037 (escopo e limitações das features de forecasting)
- [`gold_data_architecture.md`](./gold_data_architecture.md) → grão, materialização e mecanismos técnicos, quando não cobertos aqui
