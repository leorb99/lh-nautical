# Demanda para Forecasting

## Objetivo

Este documento registra o escopo atualmente implementado e versionado de preparação histórica e feature engineering para forecasting de demanda na camada Gold do projeto. Ele cobre a série histórica de demanda utilizada como entrada, a construção do histórico mensal por produto e as features derivadas (lags, janelas móveis e momentum).

O modelo preditivo final, seu horizonte de previsão e seus outputs ainda estão em desenvolvimento por outros membros da equipe e não estão presentes nesta versão do repositório. Por isso, este documento não descreve um método de previsão nem um forecast — apenas a etapa de preparação de dados e features já implementada.

Este documento **não** cobre:

- a regra geral de venda efetiva, documentada em [`gold_business_rules.md`](./gold_business_rules.md) (GOLD-001);
- a demanda operacional usada em decisões de estoque e reposição, documentada em [`inventory_and_stockout.md`](./inventory_and_stockout.md) (GOLD-019);
- as fórmulas formais das métricas de janela móvel e momentum, a serem documentadas em [`metrics_dictionary.md`](./metrics_dictionary.md);
- arquitetura, granularidade e mecanismos técnicos de implementação, documentados em [`gold_data_architecture.md`](./gold_data_architecture.md).

## Série de demanda utilizada para forecasting

A série histórica de demanda utilizada como entrada para a tabela de features parte de `fct_sales`, considerando somente registros com `is_effective_sale = true` (regra de venda efetiva definida centralmente e documentada em [`gold_business_rules.md`](./gold_business_rules.md)). A quantidade de demanda utilizada é `sum(order_item_quantity)` — uma métrica em unidades, não em receita, e sem dedução explícita de devoluções nesta tabela.

A série é agregada por `product_name` e `month_start`, não por `product_id` ou `product_variant_id`. Entradas de catálogo que compartilham o mesmo `product_name` são agregadas juntas nesta tabela.

Não existe dimensão de localização no grão desta tabela: a série é agregada nacionalmente, somando todas as localizações.

`month_start` representa o mês de `order_date`, truncado para o início do mês.

Fonte de implementação:
`models/marts/fact/agg_monthly_demand_features.sql`

Rule ID:
`GOLD-035`

## Construção do histórico mensal

Cada produto possui sua própria malha mensal (month spine), construída a partir do calendário de `dim_dates`, que começa no primeiro mês com venda efetiva observada para aquele produto e termina no último mês com venda efetiva observada.

Meses sem venda dentro desse intervalo permanecem na série com `quantity_sold = 0`. Meses anteriores à primeira venda ou posteriores à última venda observada não são gerados nesta tabela — não existe uma linha para eles, nem zero nem qualquer outro valor. Não há meses futuros gerados nesta implementação.

Fonte de implementação:
`models/marts/fact/agg_monthly_demand_features.sql`

Rule ID:
`GOLD-036`

## Features históricas

### Lags

A tabela expõe os valores de `quantity_sold` defasados em 1, 2, 3, 6 e 12 meses (`lag_1m`, `lag_2m`, `lag_3m`, `lag_6m`, `lag_12m`), calculados sobre a malha mensal descrita acima.

### Rolling features

As features de janela móvel (`rolling_avg_3m`, `rolling_avg_6m`, `rolling_std_6m`, `rolling_max_6m`, `rolling_max_12m`) excluem o mês corrente da janela de cálculo, usando `ROWS BETWEEN N PRECEDING AND 1 PRECEDING`. São features históricas derivadas da série de demanda, não uma previsão. Não há, no repositório versionado, nenhum modelo consumidor identificado para essas features.

Fonte de implementação:
`models/marts/fact/agg_monthly_demand_features.sql`

Rule ID:
`GOLD-037`

## Momentum

`momentum` mede a diferença entre a média móvel de 3 meses e a média móvel de 6 meses:

```
momentum = rolling_avg_3m - rolling_avg_6m
```

Valores positivos indicam que a média de 3 meses está acima da média de 6 meses; valores negativos indicam o contrário. É uma feature derivada, calculada a partir das rolling features acima — não é um forecast nem uma extrapolação para períodos futuros.

Fonte de implementação:
`models/marts/fact/agg_monthly_demand_features.sql`

Rule ID:
`GOLD-038`

## Tratamento de meses sem demanda

O tratamento de meses sem venda dentro do intervalo ativo de cada produto (`quantity_sold = 0`) e a ausência de linhas fora desse intervalo já estão descritos em "Construção do histórico mensal" (GOLD-036) e não são repetidos aqui.

## Estado atual do pipeline de forecasting

No estado atual desta versão do repositório, a camada Gold materializa a tabela histórica de features `agg_monthly_demand_features`, destinada a apoiar o pipeline de forecasting. O modelo preditivo final, seu horizonte e seus outputs ainda não estão presentes nesta versão do repositório e, portanto, não são documentados como regras canônicas nesta etapa.

Fatos observáveis nesta versão do repositório:

- a tabela de features (`agg_monthly_demand_features`) está implementada e materializada;
- nenhum modelo consumidor desta tabela foi encontrado no repositório versionado;
- o código-fonte do modelo faz referência a modelos de forecasting associados a um "Checkpoint 5", sem que a implementação desses modelos esteja presente neste repositório;
- não é possível confirmar, a partir do código atual, qual método final de previsão será utilizado, nem qual horizonte será adotado.

## Validações de qualidade

A tabela de features possui os seguintes testes automatizados:

- teste de que `product_name` não é nulo;
- teste de que `month_start` não é nulo;
- teste de que `quantity_sold` não é nulo;
- teste de que `quantity_sold` é maior ou igual a zero.

Não existe, nesta versão, teste específico de unicidade da combinação `product_name` + `month_start`, nem testes sobre `lag_*`, `rolling_*` ou `momentum`.

## Limitações e comportamento nas bordas do histórico

Nos primeiros meses da malha de um produto, quando não há observações históricas suficientes:

- os campos de `lag_*` podem resultar em `NULL` (por exemplo, `lag_12m` nos primeiros 12 meses de histórico do produto);
- as rolling features usam as linhas anteriores disponíveis até o limite definido por cada janela; o código não exige uma quantidade mínima de observações anteriores nem aplica fallback explícito, portanto valores nas bordas iniciais do histórico podem permanecer nulos dependendo da feature.

Não há, nesta implementação, um threshold mínimo explícito de histórico para inclusão de um produto na tabela, nem um mecanismo de fallback para preencher lags ou rolling features ausentes.

Esse comportamento descreve a implementação técnica atual e não representa uma regra de negócio sobre histórico mínimo.

## Relação com demanda operacional de estoque

A série de demanda usada nesta tabela de features de forecasting é distinta da demanda operacional usada em decisões de estoque e reposição, documentada em [`inventory_and_stockout.md`](./inventory_and_stockout.md) (GOLD-019):

| Aspecto | Forecasting (esta tabela) | Estoque/Reposição |
|---|---|---|
| Fonte | `fct_sales` | movimentações de estoque |
| Filtro | `is_effective_sale = true` | `movement_business_category = 'sale'` |
| Métrica | `sum(order_item_quantity)` | quantidade movimentada |
| Grão de produto | `product_name` | `product_variant_id` |
| Localização | agregada nacionalmente (ausente do grão) | `location_id` |
| Temporalidade | mensal | janelas de 30/60/90 dias |

As duas séries não são reconciliadas nem comparadas quanto à correção entre si — atendem a propósitos e escopos diferentes.

## Relações com outros documentos canônicos

- Regra geral de venda efetiva: [`gold_business_rules.md`](./gold_business_rules.md) (GOLD-001)
- Demanda operacional de estoque e reposição: [`inventory_and_stockout.md`](./inventory_and_stockout.md) (GOLD-019)
- Fórmulas formais das rolling metrics e do momentum: [`metrics_dictionary.md`](./metrics_dictionary.md)
- Granularidade e mecanismos técnicos de materialização: [`gold_data_architecture.md`](./gold_data_architecture.md)
