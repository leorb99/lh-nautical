# Estoque, Reposição e Risco de Ruptura

## Objetivo

Este documento registra as regras de negócio canônicas que determinam a posição de estoque, a demanda utilizada em decisões de estoque, o lead time aplicado à reposição, o estoque de segurança, a classificação de saúde de estoque, o risco de ruptura e a quantidade sugerida para reposição na camada Gold do projeto.

Este documento **não** cobre:

- regras de negócio gerais sobre vendas, devoluções, clientes e fornecedores, documentadas em [`gold_business_rules.md`](./gold_business_rules.md);
- a série de demanda usada pelo pipeline de previsão, documentada em [`demand_forecasting.md`](./demand_forecasting.md);
- as definições formais de fórmulas de métricas, documentadas em [`metrics_dictionary.md`](./metrics_dictionary.md);
- arquitetura, granularidade e mecanismos técnicos de implementação (incluindo o mecanismo de snapshot dbt), documentados em [`gold_data_architecture.md`](./gold_data_architecture.md).

## Conceitos e posição atual de estoque

A posição de estoque de um produto-variante em um local é descrita por `quantity_on_hand` (quantidade física disponível) e por duas classificações derivadas dela:

`is_out_of_stock` é verdadeiro quando `quantity_on_hand = 0`.

`is_below_reorder_point` é verdadeiro somente quando o `reorder_point` da origem não é nulo e `quantity_on_hand` é menor ou igual a esse valor. Quando `reorder_point` é nulo, `is_below_reorder_point` é sempre falso, mesmo que o estoque esteja baixo.

O `reorder_point` referido aqui é um valor fornecido pela posição/origem de estoque — não é calculado por nenhum modelo Gold. Ele é distinto do `recommended_stock_target` (ver seção "Alvo recomendado de estoque").

Fonte de implementação:
`models/intermediate/int_inventory_snapshots.sql`

Rule ID:
`GOLD-030`

### Snapshot atual único

Para cada combinação de `product_variant_id` e `location_id`, deve existir no máximo um snapshot marcado como atual (`is_current_snapshot = true`). Essa regra de integridade evita que mais de um snapshot seja marcado como atual para a mesma combinação de produto-variante e local. O mecanismo técnico por trás da versão histórica do snapshot (SCD/dbt snapshot) está documentado em [`gold_data_architecture.md`](./gold_data_architecture.md).

Fonte de implementação:
`tests/assert_single_current_inventory_snapshot.sql`

Rule ID:
`GOLD-031`

## Demanda utilizada nas decisões de estoque

A demanda operacional usada nas decisões de estoque e reposição é calculada a partir de movimentações de estoque classificadas como `movement_business_category = 'sale'`, considerando apenas movimentações com `occurred_at` não posterior ao momento de processamento.

Existem três janelas: 30, 60 e 90 dias. As médias diárias (`average_daily_sales_30d`, `average_daily_sales_60d`, `average_daily_sales_90d`) dividem a quantidade total vendida na janela pelo número de dias corridos da janela completa (30, 60 ou 90), não apenas pelos dias em que houve venda. Como a média utiliza todos os dias da janela, dias sem venda também participam implicitamente do denominador; essa média pode ser menor do que uma média calculada exclusivamente sobre os dias em que ocorreram vendas.

Fonte de implementação:
`models/intermediate/int_inventory_demand_metrics.sql`

Rule ID:
`GOLD-019`

**Nota sobre duas séries de demanda:** a demanda utilizada nas decisões operacionais de estoque e reposição (produto-variante + local, janelas de 30/60/90 dias, a partir de movimentações de estoque) não é a mesma série de demanda utilizada pelo pipeline de forecasting. Consulte [`demand_forecasting.md`](./demand_forecasting.md) para a definição da demanda usada em previsão.

## Lead time utilizado na reposição

### Hierarquia de fallback

O lead time selecionado para reposição (`selected_lead_time_days`) segue uma hierarquia de fallback implementada por `COALESCE`, na seguinte ordem:

1. histórico de lead time do produto + local;
2. histórico de lead time do produto (todas as localizações);
3. histórico de lead time global (todos os produtos e locais);
4. valor padrão fixo de 7 dias.

Todo produto/local recebe um `selected_lead_time_days`, pois o último nível do fallback é um valor fixo.

Fonte de implementação:
`models/intermediate/int_inventory_replenishment_inputs.sql`

Rule ID:
`GOLD-020`

### Confiança da estimativa

A classificação de confiança do lead time (`lead_time_confidence`) é operacional, não estatística:

- `high`: histórico produto+local com 10 ou mais observações;
- `medium`: histórico produto+local com 3 a 9 observações;
- `low`: histórico produto+local com 1 ou 2 observações, ou quando o valor selecionado veio do fallback por histórico de produto ou por histórico global;
- `assumed`: quando o valor selecionado veio do fallback padrão de 7 dias.

Fonte de implementação:
`models/intermediate/int_inventory_replenishment_inputs.sql`

Rule ID:
`GOLD-021`

### Validação das observações

Uma observação de lead time só é considerada válida quando `placed_at` não é nulo, `first_received_at` não é nulo, e `first_received_at` é maior ou igual a `placed_at`. Caso qualquer uma dessas condições não seja satisfeita, `lead_time_days` é nulo para essa observação, e ela não entra em nenhuma das métricas agregadas de lead time (produto+local, produto, ou global).

Fonte de implementação:
`models/intermediate/int_purchase_order_receipts.sql`

Rule ID:
`GOLD-022`

## Demanda durante o lead time

`demand_during_lead_time` representa a demanda esperada durante o período de reabastecimento, calculada como:

```
demand_during_lead_time = average_daily_sales_90d × selected_lead_time_days
```

Essa medida combina a demanda média diária de 90 dias (ver "Demanda utilizada nas decisões de estoque") com o lead time selecionado pela hierarquia de fallback (ver "Lead time utilizado na reposição"). A definição formal da fórmula, incluindo tipo e precisão de armazenamento, está documentada em [`metrics_dictionary.md`](./metrics_dictionary.md); este documento registra apenas o significado de negócio.

Fonte de implementação:
`models/marts/fact/fct_inventory_replenishment.sql`

Rule ID:
`GOLD-023`

## Estoque de segurança

`safety_stock_quantity` é um buffer fixo equivalente a sete dias de demanda média, calculado como:

```
safety_stock_quantity = average_daily_sales_90d × 7
```

O valor de sete dias é fixo no código. Não há documentação no repositório que explique por que esse número específico foi escolhido.

Fonte de implementação:
`models/marts/fact/fct_inventory_replenishment.sql`

Rule ID:
`GOLD-024`

## Ponto de reposição da origem

`reorder_point` é um valor definido pela posição/origem de estoque (não calculado por nenhum modelo Gold). Ele é usado em dois lugares:

- na classificação `is_below_reorder_point` (ver "Conceitos e posição atual de estoque", GOLD-030);
- como um dos critérios de acionamento de reposição em `is_replenishment_required` (ver "Regras de reposição", GOLD-028).

`reorder_point` **não é o mesmo conceito** que `recommended_stock_target`: o primeiro vem da origem de dados de estoque, enquanto o segundo é derivado no modelo a partir de `demand_during_lead_time` e `safety_stock_quantity` (ver seção seguinte).

## Alvo recomendado de estoque

`recommended_stock_target` é calculado como:

```
recommended_stock_target = demand_during_lead_time + safety_stock_quantity
```

Este valor é usado como alvo operacional na determinação de `suggested_reorder_quantity` (ver "Regras de reposição") e como um dos critérios de `is_replenishment_required`. Ele é distinto de `reorder_point`, que é um valor de origem, não derivado.

Fonte de implementação:
`models/marts/fact/fct_inventory_replenishment.sql`

Rule ID:
`GOLD-025`

## Classificação de saúde e risco de ruptura

### Inventory health status

A classificação `inventory_health_status` segue esta ordem de prioridade:

1. `out_of_stock` — quando `quantity_on_hand = 0`;
2. `no_recent_sales` — quando `average_daily_sales_90d` é zero ou nula, sendo valores nulos tratados como zero nessa classificação;
3. `critical` — quando a cobertura estimada é menor ou igual a 7 dias;
4. `attention` — quando a cobertura estimada é maior que 7 e menor ou igual a 15 dias;
5. `healthy` — nos demais casos.

Essa lógica é centralizada na macro `classify_inventory_health` e compartilhada, com os mesmos parâmetros, entre `fct_current_inventory` e `fct_inventory_replenishment`. Não é uma probabilidade estatística de ruptura, apenas uma classificação por faixas de cobertura.

Fonte de implementação:
`macros/classify_inventory_health.sql`

Rule ID:
`GOLD-017`

### Stockout risk

`is_stockout_risk`, exposto em `fct_current_inventory`, é verdadeiro quando:

- o estoque é zero; OU
- existe demanda positiva e a cobertura estimada (90 dias) é menor ou igual a 7 dias.

Essa flag é distinta de `inventory_health_status`, embora utilize o mesmo limite crítico de sete dias.

Fonte de implementação:
`models/marts/fact/fct_current_inventory.sql`

Rule ID:
`GOLD-018`

### Risco de ruptura antes da reposição

`will_stockout_before_replenishment`, exposto em `fct_inventory_replenishment`, é verdadeiro quando `average_daily_sales_90d > 0` e `quantity_on_hand < demand_during_lead_time`.

Quantidades de pedidos de compra em aberto (`open_purchase_order_quantity`) não participam desse cálculo.

Fonte de implementação:
`models/marts/fact/fct_inventory_replenishment.sql`

Rule ID:
`GOLD-027`

## Regras de reposição

### Quantidade sugerida

`suggested_reorder_quantity` é calculada como:

```
suggested_reorder_quantity = MAX(
    recommended_stock_target - quantity_on_hand - open_purchase_order_quantity,
    0
)
```

Pedidos de compra em aberto são descontados da recomendação. O resultado é limitado a zero por baixo (nunca negativo).

Fonte de implementação:
`models/marts/fact/fct_inventory_replenishment.sql`

Rule ID:
`GOLD-026`

### Critério para reposição

`is_replenishment_required` é verdadeiro somente quando existe demanda positiva (`average_daily_sales_90d > 0`) e pelo menos uma das condições a seguir ocorre:

1. o estoque atual é menor ou igual ao `reorder_point` da origem; OU
2. o estoque atual somado aos pedidos de compra em aberto é menor que o `recommended_stock_target`.

Sem demanda positiva nos últimos 90 dias, a reposição não é acionada: `suggested_reorder_quantity = 0` e `is_replenishment_required = false`.

`reorder_point` (critério 1) e `recommended_stock_target` (critério 2) são conceitos distintos — ver "Ponto de reposição da origem" e "Alvo recomendado de estoque".

Fonte de implementação:
`models/marts/fact/fct_inventory_replenishment.sql`

Rule ID:
`GOLD-028`

### Pedidos de compra em aberto

`open_purchase_order_quantity`, usado nos cálculos de reposição acima, considera somente pedidos de compra com status `sent` ou `partially_received`. O saldo aberto por item é calculado como `MAX(quantidade pedida - quantidade recebida, 0)`. Pedidos em `draft`, `received` ou `cancelled` não contribuem para a quantidade em trânsito utilizada pela reposição.

A definição geral de pedido de compra em aberto está documentada em [`gold_business_rules.md`](./gold_business_rules.md) (GOLD-032); esta seção trata especificamente do uso dessa quantidade nos cálculos de reposição de estoque.

Fonte de implementação:
`models/intermediate/int_open_purchase_order_quantities.sql`

Rule ID:
`GOLD-029`

## Validações de qualidade

As regras acima são verificadas por testes automatizados dedicados, existentes em `tests/inventory/` e `tests/`:

- validação de que todo produto/local possui um `selected_lead_time_days` válido (associada à GOLD-020);
- validação de consistência entre a fonte do lead time, a contagem de observações e o nível de confiança atribuído (associada à GOLD-021);
- validação de consistência entre a fonte do lead time e a flag `is_lead_time_estimated` (associada à GOLD-020/GOLD-021);
- validação de que a ausência de demanda não aciona reposição (associada à GOLD-028);
- validação de que pedidos de compra em aberto reduzem corretamente a quantidade sugerida (associada à GOLD-026);
- validação de que todo registro de estoque atual está presente nos insumos de reposição;
- validação de consistência do risco de ruptura antes da reposição (associada à GOLD-027);
- validação de que a quantidade sugerida de reposição nunca é negativa (associada à GOLD-026);
- validação de que existe no máximo um snapshot atual por produto/local (associada à GOLD-031).

## Relações com outros documentos canônicos

- Definição geral de pedido de compra em aberto (GOLD-032) e de lead time por fornecedor (GOLD-039): [`gold_business_rules.md`](./gold_business_rules.md)
- Categorização de movimentações de estoque em categorias de negócio (GOLD-034), usada como base da demanda operacional: [`gold_business_rules.md`](./gold_business_rules.md)
- Definições formais de fórmulas e sua precisão de armazenamento: [`metrics_dictionary.md`](./metrics_dictionary.md)
- Mecanismo técnico de snapshot (SCD/dbt snapshot) e granularidade dos modelos: [`gold_data_architecture.md`](./gold_data_architecture.md)
- Série de demanda utilizada no pipeline de previsão: [`demand_forecasting.md`](./demand_forecasting.md)
