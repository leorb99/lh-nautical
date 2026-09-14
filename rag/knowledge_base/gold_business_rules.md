# Regras de Negócio da Camada Gold

## Objetivo

Este documento registra as regras de negócio canônicas que determinam o significado dos dados disponibilizados pela camada Gold do projeto (schema `gold`, modelos em `models/marts/` do dbt), incluindo regras implementadas em modelos intermediários que alimentam os marts Gold. Cada regra aqui descrita foi validada diretamente no código-fonte, na documentação YAML dos modelos ou em testes automatizados, e é rastreável até sua implementação através do identificador `Rule ID`.

Este documento cobre exclusivamente regras de negócio gerais, aplicáveis a pedidos, vendas, devoluções, clientes, fornecedores e regras financeiras. Ele **não** cobre:

- regras específicas de estoque e ruptura, documentadas em [`inventory_and_stockout.md`](./inventory_and_stockout.md);
- regras de previsão de demanda, documentadas em [`demand_forecasting.md`](./demand_forecasting.md);
- definições formais de fórmulas de métricas, documentadas em [`metrics_dictionary.md`](./metrics_dictionary.md);
- arquitetura, granularidade e mecanismos técnicos de implementação, documentados em [`gold_data_architecture.md`](./gold_data_architecture.md).

## Vendas

### Vendas efetivas

Um pedido é reconhecido como venda efetiva quando reúne três condições simultâneas: o pedido está com status pago (`paid`), possui nota fiscal com status autorizada (`authorized`), e sua data de realização (`placed_at`) é uma data válida, ou seja, não futura. Pedidos que não atendem a todas as três condições — por exemplo, com nota fiscal rejeitada ou cancelada, ou com data futura — não são considerados vendas efetivas.

Fonte de implementação:
`models/intermediate/int_orders.sql`

Rule ID:
`GOLD-001`

### Vendas pagas pendentes de regularização fiscal

Pedidos com pagamento recebido (status `paid`) cuja nota fiscal foi rejeitada (`rejected`) são classificados separadamente como pendentes de regularização fiscal e não são considerados vendas efetivas. Notas fiscais canceladas são tratadas separadamente dessa classificação.

Fonte de implementação:
`models/intermediate/int_orders.sql`

Rule ID:
`GOLD-002`

### Rateio proporcional de desconto do pedido

Quando um pedido recebe um desconto, esse valor é distribuído entre os itens do pedido de forma proporcional à participação de cada item no subtotal do pedido — o desconto não é aplicado igualmente a todos os itens, nem concentrado em um único item.

Fonte de implementação:
`models/marts/fact/fct_sales.sql`

Rule ID:
`GOLD-003`

## Devoluções

### Devolução efetiva

Uma devolução só é considerada efetiva quando seu status é `completed` e sua data de registro é uma data válida, ou seja, não futura. Devoluções em aberto (`open`) ou canceladas (`cancelled`), assim como devoluções com data futura, não são consideradas efetivas e não afetam indicadores de receita ou de devolução.

Fonte de implementação:
`models/intermediate/int_returns.sql`

Rule ID:
`GOLD-005`

### Reembolso monetário exclusivo para ação de reembolso

O valor monetário de reembolso é contabilizado somente para itens cuja ação registrada é `refund`. Itens cuja ação registrada é `exchange` não contribuem para o valor monetário de reembolso.

Fonte de implementação:
`models/intermediate/int_completed_returns_by_order_item.sql`

Rule ID:
`GOLD-006`

### Validação dos limites de quantidade devolvida e valor reembolsado

A camada de qualidade exige que a quantidade devolvida de forma efetiva para um item de pedido não exceda a quantidade vendida, e que o valor reembolsado não exceda o valor total da linha do item original. Essas condições são verificadas por testes automatizados dedicados.

Fontes de implementação:
- `tests/assert_return_quantity_not_greater_than_sold.sql`
- `tests/assert_refund_amount_not_greater_than_item_total.sql`

Rule ID:
`GOLD-042`

## Clientes

### Escopo de clientes na segmentação RFM

A segmentação de clientes por Recência, Frequência e Valor Monetário (RFM) considera exclusivamente vendas classificadas como efetivas (ver "Vendas efetivas"). Clientes sem nenhuma venda efetiva não aparecem nesta segmentação.

Fonte de implementação:
`models/marts/fact/agg_customer_rfm.sql`

Rule ID:
`GOLD-013`

### Data de referência da recência do cliente

A recência de um cliente é calculada em relação à data da venda efetiva mais recente observada entre todos os clientes, e não em relação à data em que o modelo é executado. Isso garante que o resultado de recência não mude apenas porque o modelo foi processado em um dia diferente.

Fonte de implementação:
`models/marts/fact/agg_customer_rfm.sql`

Rule ID:
`GOLD-014`

### Prioridade das regras de segmentação de clientes

Cada cliente recebe um segmento — Champions, At Risk, Loyal, New, Hibernating, ou Needs Attention como categoria residual — a partir de um conjunto de condições avaliadas em ordem de prioridade. A primeira condição que o cliente satisfizer determina o segmento final, mesmo que o cliente também satisfaça outras condições mais adiante na lista.

Fonte de implementação:
`models/marts/fact/agg_customer_rfm.sql`

Rule ID:
`GOLD-016`

## Fornecedores

### Definição de pedido de compra em aberto

Um pedido de compra é considerado "em aberto" quando seu status é `sent` ou `partially_received`. Pedidos em `draft`, `received` ou `cancelled` não são incluídos na contagem de pedidos em aberto.

Fonte de implementação:
`models/marts/fact/fct_purchases.yml` (descrição do modelo)

Rule ID:
`GOLD-032`

### Lead time do fornecedor: histórico observado prevalece sobre o valor declarado

O lead time de entrega usado para avaliar um fornecedor prioriza o histórico real de recebimento de mercadorias (mediana observada), sempre que esse histórico existir. Somente na ausência de qualquer histórico de recebimento, o valor declarado no catálogo do fornecedor (cotação de lead time) é utilizado como alternativa.

Esta regra descreve o lead time agregado por fornecedor. A hierarquia de fallback usada especificamente para reposição de estoque por produto e local é distinta e mais detalhada, e está documentada em [`inventory_and_stockout.md`](./inventory_and_stockout.md).

Fonte de implementação:
`models/intermediate/int_supplier_lead_time_metrics.sql`

Rule ID:
`GOLD-039`

## Regras Financeiras

### Valor total do pedido não deve ser somado na grão de item

O valor total do pedido (`order_total_amount`) é um valor único por pedido, mas aparece repetido em todas as linhas de item pertencentes a esse pedido. Por isso, ele nunca deve ser somado diretamente na grão de item: antes de qualquer soma, os dados precisam ser reduzidos a uma linha por pedido. Ignorar esta regra resulta em duplicação de receita para pedidos com mais de um item.

Fontes de implementação:
- `models/marts/fact/fct_sales.sql`
- `models/marts/fact/agg_daily_financial_metrics.sql`

Rule ID:
`GOLD-008`

### Receita pendente de regularização fiscal é reportada separadamente

Pedidos pagos com nota fiscal rejeitada (ver "Vendas pagas pendentes de regularização fiscal") são excluídos dos indicadores de receita, ticket médio e taxa de devolução calculados por `agg_daily_financial_metrics` e são reportados em contagens e valores separados.

Fonte de implementação:
`models/marts/fact/agg_daily_financial_metrics.sql`

Rule ID:
`GOLD-012`

## Critérios de Inclusão e Exclusão

### Classificação de movimentações de estoque por categoria de negócio

Cada movimentação de estoque registrada na origem é reclassificada em uma categoria de negócio: entrada de compra (`purchase_in`) torna-se `purchase`; saída de venda (`sale_out`) torna-se `sale`; entrada de devolução (`return_in`) torna-se `customer_return`; saída de devolução (`return_out`) torna-se `supplier_return`; qualquer outro tipo é classificado como `other`.

O uso dessa categoria para cálculo de indicadores de estoque e ruptura está documentado em [`inventory_and_stockout.md`](./inventory_and_stockout.md).

Fonte de implementação:
`models/intermediate/int_stock_movements.sql`

Rule ID:
`GOLD-034`

## Tratamento de Dados com Impacto de Negócio

### Validação de datas futuras

Nos campos de data ou timestamp aos quais essa validação é aplicada — por exemplo, data do pedido, emissão da nota fiscal, data do pedido de compra ou data de devolução — valores no futuro em relação ao momento de processamento são tratados como inválidos e substituídos por nulo, preservando o restante do registro.

Esse tratamento tem efeito direto sobre outras regras de negócio: por exemplo, é o que torna a validade da data do pedido um dos critérios para uma venda ser considerada efetiva (ver "Vendas efetivas").

Os detalhes de implementação e arquitetura — mecanismo técnico e modelos onde a regra é aplicada — estão documentados em [`gold_data_architecture.md`](./gold_data_architecture.md).

Rule ID:
`GOLD-007`

## Referências para Regras Especializadas

- Regras de estoque, ruptura e reposição: [`inventory_and_stockout.md`](./inventory_and_stockout.md)
- Regras de previsão de demanda: [`demand_forecasting.md`](./demand_forecasting.md)
- Definições formais de fórmulas de métricas: [`metrics_dictionary.md`](./metrics_dictionary.md)
- Arquitetura, granularidade e mecanismos técnicos: [`gold_data_architecture.md`](./gold_data_architecture.md)
