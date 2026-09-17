# Regras Semânticas do Power BI — Dashboard Gerencial Compass 360 (LH Nautical)

## Objetivo

Este documento descreve a semântica do modelo Power BI (PBIP/TMDL) do
"Dashboard Gerencial Compass 360 - LH Nautical": medidas DAX, relacionamentos,
granularidade, parâmetros e filtros persistidos no relatório. O objetivo é
tornar essa semântica recuperável pelo RAG, com rastreabilidade explícita
para os artefatos reais do Power BI (tabela/coluna/medida de origem).

Este documento **não inventa regras de negócio**. Uma expressão DAX, um
relacionamento ou um filtro persistido é descrito aqui como **semântica do
Power BI** — só é apresentado como regra de negócio equivalente a uma regra
Gold já documentada quando há correspondência literal de nome de
tabela/coluna com o que já está registrado em
[`gold_business_rules.md`](./gold_business_rules.md) ou
[`metrics_dictionary.md`](./metrics_dictionary.md).

**Status deste documento: DEFERRED / NOT INGESTED.** Não faz parte de
`CANONICAL_DOCUMENTS` em `loader.py` e não gera chunks nesta fase — ver
Roadmap do RAG ("Corpus Completion Checkpoint").

## Proveniência e Escopo dos Artefatos

- Artefato inspecionado: um projeto PBIP (Power BI Project) contendo dois
  itens — `Dashboard Gerencial Compass 360 - LH Nautical.SemanticModel`
  (modelo semântico, definido em TMDL) e
  `Dashboard Gerencial Compass 360 - LH Nautical.Report` (definição do
  relatório/visuais, em JSON).
- `compatibilityLevel: 1606`; `culture: pt-BR`; `defaultPowerBIDataSourceVersion: powerBI_V3`.
- **Fonte de dados confirmada:** todas as 16 tabelas de negócio do modelo
  (fatos, dimensões, agregações e a tabela ponte) são importadas via
  `Databricks.Catalogs(...)`, apontando para
  `prod_lh_nautical.gold.<nome_da_tabela>` — o schema `gold` do catálogo
  **`prod_lh_nautical`**. Cada partição de tabela faz apenas uma navegação
  direta até a tabela Gold correspondente, sem transformação adicional no
  Power Query (nenhum `Table.TransformColumns`/filtro/agregação extra foi
  encontrado nas partições `m` das tabelas reais).
- Esta é a mesma camada Gold já documentada em
  [`gold_data_architecture.md`](./gold_data_architecture.md), mas via o
  catálogo de **produção** (`prod_lh_nautical`), não o catálogo de
  desenvolvimento (`dev_natalia_correia_lh_nautical`) usado pelo pipeline de
  ingestão RAG. Os nomes de tabela/coluna Gold, quando citados abaixo, são os
  mesmos observados diretamente nas colunas do modelo Power BI — não foram
  reconferidos linha a linha contra o SQL do dbt nesta tarefa.

### Artefatos internos referenciados

Paths relativos à raiz de cada item PBIP (nunca ao caminho absoluto da
máquina onde o projeto foi inspecionado):

**Source project:** `Dashboard Gerencial Compass 360 - LH Nautical.SemanticModel`
- `definition/model.tmdl` — inventário de tabelas do modelo e ordem de carga.
- `definition/relationships.tmdl` — os 47 relacionamentos (ver "Relacionamentos").
- `definition/tables/_Medidas.tmdl` — as 63 medidas DAX (ver "Medidas DAX").
- `definition/tables/fct_current_inventory.tmdl` — coluna calculada
  `'Ordem Status Saúde'` (ver "Estoque & Supply").
- `definition/tables/Parâmetro.tmdl` — o field parameter (ver abaixo).
- `definition/tables/Waterfall Categorias.tmdl` — tabela de apoio do waterfall.
- `definition/tables/dim_dates.tmdl` — hierarquia de datas.
- `definition/tables/<nome_da_tabela>.tmdl` — uma por tabela de negócio listada
  acima, cada uma contendo a partição `m` que confirma a origem
  `prod_lh_nautical.gold.<nome_da_tabela>`.

**Source project:** `Dashboard Gerencial Compass 360 - LH Nautical.Report`
- `definition/report.json` — confirmou ausência de filtro no nível do relatório.
- `definition/pages/pages.json` — ordem e identificadores das 13 páginas.
- `definition/pages/<page_id>/page.json` — nome/visibilidade de cada página.
- `definition/pages/<page_id>/visuals/<visual_id>/visual.json` — os filtros
  persistidos por visual (ver "Filtros Persistidos Relevantes no Relatório").

Provenance não é repetida por linha/medida abaixo — os artefatos acima
cobrem, por grupo, tudo que é citado no restante deste documento.

### Inventário exato do modelo (metadata factual)

Contagens exatas confirmadas diretamente nos artefatos TMDL/JSON acima —
metadata do modelo, não regra de negócio:

| Item | Contagem |
|---|---|
| Tabelas de negócio vindas de `prod_lh_nautical.gold.*` | 16 |
| Medidas DAX (`_Medidas.tmdl`) | 63 |
| Relacionamentos (`relationships.tmdl`) | 47 (todos ativos; 3 `bothDirections`) |
| Colunas calculadas relevantes (fora das tabelas de calendário automáticas) | 1 (`fct_current_inventory[Ordem Status Saúde]`) |
| Field parameters | 1 (`Parâmetro Estabelecimento ou Região`) |

Além dessas, existem 2 tabelas técnicas sem origem Gold (`Waterfall Categorias`,
`Parâmetro` — ver "Estrutura do Modelo Semântico") e 20 tabelas ocultas de
calendário auto-geradas pelo Power BI, já descritas abaixo como não sendo
conteúdo de negócio.

## Estrutura do Modelo Semântico

### Tabelas de negócio e sua origem

| Tabela Power BI | Tabela Gold de origem | Papel no modelo |
|---|---|---|
| `fct_sales` | `gold.fct_sales` | Fato — vendas/itens de pedido |
| `fct_returns` | `gold.fct_returns` | Fato — devoluções |
| `fct_purchases` | `gold.fct_purchases` | Fato — pedidos de compra a fornecedores |
| `fct_payments` | `gold.fct_payments` | Fato — pagamentos |
| `fct_current_inventory` | `gold.fct_current_inventory` | Fato — posição atual de estoque (snapshot) |
| `fct_inventory_replenishment` | `gold.fct_inventory_replenishment` | Fato — modelo de reabastecimento/ruptura |
| `agg_customer_rfm` | `gold.agg_customer_rfm` | Agregação — segmentação RFM de clientes |
| `agg_daily_financial_metrics` | `gold.agg_daily_financial_metrics` | Agregação — métricas financeiras diárias |
| `bridge_product_suppliers` | `gold.bridge_product_suppliers` | Ponte — produto ↔ fornecedor (N:N) |
| `dim_customers` | `gold.dim_customers` | Dimensão — clientes |
| `dim_products` | `gold.dim_products` | Dimensão — produtos/variantes |
| `dim_suppliers` | `gold.dim_suppliers` | Dimensão — fornecedores |
| `dim_locations` | `gold.dim_locations` | Dimensão — estabelecimentos/locais |
| `dim_employees` | `gold.dim_employees` | Dimensão — funcionários |
| `dim_payment_methods` | `gold.dim_payment_methods` | Dimensão — métodos de pagamento |
| `dim_dates` | `gold.dim_dates` | Dimensão — calendário (usada nas relações explícitas) |

Além dessas, o modelo contém duas tabelas que **não vêm do Gold** — dados
persistidos diretamente no próprio modelo (M/Power Query com dados
literais embutidos, não uma fonte externa):

- **`Waterfall Categorias`** — tabela de apoio com duas colunas
  (`CategoriaOrdem`, `Categoria`), usada apenas para ordenar o eixo do
  gráfico em cascata de composição de receita. Confirmado pela medida
  `Valor Waterfall` (ver abaixo): as categorias, na ordem de exibição, são
  **Receita Bruta, Impostos, Descontos, Devoluções**.
- **`Parâmetro`** ("Parâmetro Estabelecimento ou Região") — um *field
  parameter* nativo do Power BI (não uma tabela de dados), que permite a um
  visual alternar entre três colunas de agrupamento distintas: "Estabelecimento"
  (`dim_locations[location_name]`), "Estado do Estabelecimento"
  (`dim_locations[location_state]`) e "Estado do Cliente"
  (`dim_customers[primary_address_state]`). É um mecanismo de interação do
  relatório, não um dado de negócio.

O modelo também contém **20 tabelas ocultas de calendário auto-geradas pelo
Power BI** (`DateTableTemplate_...`/`LocalDateTable_...`, uma por coluna de
data sem relação explícita com `dim_dates`), cada uma com as colunas
padrão `Date`/`Ano`/`MonthNo`/`Mês`/`QuarterNo`/`Trimestre`/`Dia`. Essas
tabelas são artefato técnico do recurso "Auto Date/Time" do Power BI, não
conteúdo de negócio — não são documentadas individualmente aqui.

### Relacionamentos

**Artifact:** `definition/relationships.tmdl` (47 relacionamentos no total,
todos ativos — nenhum com `isActive: false` — 3 deles com
`crossFilteringBehavior: bothDirections`, ver abaixo).

**Relationship behavior ≠ business rule:** os relacionamentos descritos
nesta seção documentam comportamento do modelo semântico do Power BI —
conectividade e propagação de filtro entre tabelas. Um relacionamento, por
si só, não estabelece nem implica uma regra de negócio além do que já
estiver explicitamente codificado no modelo ou sustentado pela
documentação de negócio canônica (`gold_business_rules.md` e demais).

A maioria das relações fato→dimensão é do tipo padrão do Power BI (cardinalidade
muitos-para-um, filtro em uma única direção, da dimensão para o fato), por
exemplo `fct_sales[product_variant_id] → dim_products[product_variant_id]`,
`fct_sales[customer_id] → dim_customers[customer_id]`,
`fct_sales[salesperson_id] → dim_employees[employee_id]`,
`fct_sales[location_id] → dim_locations[location_id]`,
`fct_purchases[supplier_id] → dim_suppliers[supplier_id]`,
`fct_purchases[buyer_id] → dim_employees[employee_id]`,
`fct_purchases[destination_location_id] → dim_locations[location_id]`,
`fct_current_inventory[product_variant_id] → dim_products[product_variant_id]`,
`fct_current_inventory[location_id] → dim_locations[location_id]`,
`fct_inventory_replenishment[product_variant_id] → dim_products[product_variant_id]`,
`fct_inventory_replenishment[location_id] → dim_locations[location_id]`,
`fct_returns[product_variant_id] → dim_products[product_variant_id]`,
`fct_returns[customer_id] → dim_customers[customer_id]`,
`fct_returns[received_at_location_id] → dim_locations[location_id]`,
`fct_payments[payment_method_sk] → dim_payment_methods[payment_method_sk]`,
`bridge_product_suppliers[supplier_id] → dim_suppliers[supplier_id]`.

Relações com filtro cruzado explicitamente configurado como **bidirecional**
(`crossFilteringBehavior: bothDirections`) — desvio deliberado do padrão,
persistido no TMDL:

- `dim_customers[customer_id] → agg_customer_rfm[customer_id]` (cardinalidade `one` do lado `dim_customers`).
- `bridge_product_suppliers[product_variant_id] → dim_products[product_variant_id]`.
- `dim_dates[date_day] → agg_daily_financial_metrics[metric_date]` (cardinalidade `one` do lado `dim_dates`).

Relações diretas com `dim_dates[date_day]` (sem passar pelas tabelas de
calendário automáticas), confirmadas no TMDL: `fct_sales[order_date]`,
`fct_returns[return_date]`, `fct_payments[payment_paid_at]`.

Todas as demais colunas de data (`placed_at`, `created_at`, `updated_at`,
`inventory_reference_date`, `last_sale_date`, `inventory_analysis_date`,
`employee_hire_date`, `employee_termination_date`,
`purchase_order_expected_delivery_at`, etc.) se relacionam com uma das 20
tabelas de calendário ocultas auto-geradas mencionadas acima — não com
`dim_dates` diretamente. Isso significa que análises de série temporal por
essas colunas específicas usam uma hierarquia de calendário separada da
hierarquia de `dim_dates`.

### Hierarquia de datas

`dim_dates` possui uma hierarquia definida explicitamente no TMDL, chamada
literalmente **`Hierarquia de datass`** (grafia exatamente como persistida
no arquivo, com "ss" duplo — transcrita aqui sem correção), com os níveis,
em ordem: `year_num` → `quarter_name` → `month_short_name` → `date_day`.

### Governança de dados observada

- **PII sem máscara/RLS:** as medidas `Email do Cliente`
  (`SELECTEDVALUE(dim_customers[customer_email])`) e `Telefone do Cliente`
  (`SELECTEDVALUE(dim_customers[customer_phone])`) expõem diretamente
  `customer_email`/`customer_phone` de `dim_customers`, sem qualquer máscara,
  transformação ou regra de segurança aplicada na medida.
- **Nenhuma Row-Level Security (RLS) ou Perspective está definida no
  modelo** — não foi encontrado nenhum arquivo/definição de `role` (segurança
  em nível de linha) nem de `perspective` nos artefatos TMDL inspecionados.
  Combinado com o ponto acima, isso é uma observação factual de estado atual
  do modelo, não uma afirmação sobre se isso é adequado ou não.

## Medidas DAX

**Artifact:** `definition/tables/_Medidas.tmdl`. Todas as **63 medidas** do
modelo estão centralizadas nessa única tabela técnica (sem dados próprios —
apenas medidas). Abaixo, agrupadas pelo `displayFolder` com que cada medida
já está organizada no próprio modelo (agrupamento real do modelo, não uma
taxonomia inventada para este documento): 57 medidas de negócio,
documentadas individualmente, e 6 medidas técnicas/de apresentação (ver
"Medidas técnicas e de apresentação" ao final desta seção) — 63 no total,
nenhuma omitida.

### Receita, vendas e visão geral (`Global (2+ abas)` / `Visão Geral` / `Vendas`)

#### `Receita Líquida`

**Semantic definition:** Receita líquida final apurada sobre vendas
efetivas, já descontados impostos (ICMS/IPI), desconto comercial e valores
devolvidos. É a medida-base referenciada por diversas outras medidas do
modelo (measure "raiz" da árvore de receita).

**Expression:**
```dax
CALCULATE(
    SUM(fct_sales[order_item_net_revenue_after_returns]),
    fct_sales[is_effective_sale] = TRUE()
)
```

**Table:** `fct_sales`. **Format:** moeda (BRL). **Filtro embutido:**
`is_effective_sale = TRUE()`. **Correspondência com regra já documentada:**
a coluna `order_item_net_revenue_after_returns` é exatamente a métrica
`GOLD-004` já descrita em `metrics_dictionary.md` — esta medida DAX soma
essa mesma métrica Gold sobre vendas efetivas, sem recalcular sua fórmula.

#### `Ticket Médio`

**Semantic definition:** Valor médio por pedido efetivo — soma do valor
total de cada pedido efetivo distinto, dividida pela contagem de pedidos
efetivos.

**Expression:**
```dax
DIVIDE(
    CALCULATE(
        SUMX(
            VALUES(fct_sales[order_id])
            , CALCULATE(MAX(fct_sales[order_total_amount]))
        )
        , fct_sales[is_effective_sale] = TRUE()
    )
    , [Pedidos Efetivos]
)
```

**Divisão por zero:** protegida por `DIVIDE` (retorna `BLANK()` em vez de
erro quando `[Pedidos Efetivos] = 0`). **Dependência:** `[Pedidos Efetivos]`
(medida própria, ver abaixo). **Nota de grão:** usa `MAX(order_total_amount)`
por `order_id` — evita a duplicação de `order_total_amount` entre itens do
mesmo pedido, o mesmo cuidado descrito na regra `GOLD-008`
("Valor total do pedido não deve ser somado na grão de item") já
documentada em `gold_business_rules.md`.

#### `Pedidos Efetivos`

**Semantic definition:** Contagem de pedidos distintos considerados vendas
efetivas.

**Expression:**
```dax
CALCULATE(
    DISTINCTCOUNT(fct_sales[order_id])
    , fct_sales[is_effective_sale] = TRUE()
)
```

**Table:** `fct_sales`, displayFolder `Visão Geral`.

#### `Receita Pendente Fiscal`

**Semantic definition:** Valor total de pedidos pagos com nota fiscal
pendente de regularização.

**Expression:**
```dax
CALCULATE(
    SUMX(
        VALUES(fct_sales[order_id])
        , CALCULATE(MAX(fct_sales[order_total_amount]))
    )
    , fct_sales[is_paid_sale_pending_invoice_fix] = TRUE()
)
```

**Correspondência com regra já documentada:** filtra por
`is_paid_sale_pending_invoice_fix`, o mesmo campo usado na regra de
"Vendas pagas pendentes de regularização fiscal" já descrita em
`gold_business_rules.md`.

#### `Pedidos Pendentes Fiscal`

**Semantic definition:** Contagem de pedidos distintos pagos com nota
fiscal pendente de correção (mesma condição da medida anterior, mas
contagem em vez de soma de valor).

**Expression:**
```dax
CALCULATE(
    DISTINCTCOUNT(fct_sales[order_id])
    , fct_sales[is_paid_sale_pending_invoice_fix] = TRUE()
)
```

#### `Receita Acumulada no Ano (YTD)`

**Semantic definition:** Receita líquida acumulada desde o início do ano
civil até a data do contexto selecionado.

**Expression:**
```dax
TOTALYTD(
    [Receita Líquida]
    , dim_dates[date_day]
)
```

**Contexto temporal:** função de time intelligence nativa (`TOTALYTD`) sobre
`dim_dates[date_day]`.

#### `Receita Líquida do Período (%YoY)`

**Semantic definition:** Variação percentual da receita líquida do período
filtrado em relação ao mesmo período do ano anterior. Retorna vazio quando
nenhum filtro de data está aplicado (proteção explícita contra comparação
sem contexto de período).

**Expression:**
```dax
VAR TemFiltroDeData = ISFILTERED(dim_dates[date_day]) || ISCROSSFILTERED(dim_dates[date_day])
VAR ReceitaAtual = [Receita Líquida]
VAR ReceitaAnoAnterior = CALCULATE([Receita Líquida], SAMEPERIODLASTYEAR(dim_dates[date_day]))
RETURN
    IF(TemFiltroDeData, DIVIDE(ReceitaAtual - ReceitaAnoAnterior, ReceitaAnoAnterior))
```

**Divisão por zero:** protegida por `DIVIDE`. **BLANK explícito:** quando
`TemFiltroDeData = FALSE`.

#### `Receita Líquida LTM` / `Receita Líquida LTM Ano Anterior` / `Receita Líquida LTM (%YoY)`

**Semantic definition:** Receita líquida acumulada nos últimos 12 meses
corridos (LTM) a partir da data mais recente do contexto; a mesma janela no
ano anterior; e a variação percentual entre as duas. Todas retornam vazio
quando não há filtro de data aplicado.

**Expression (LTM):**
```dax
VAR TemFiltroDeData = ISFILTERED(dim_dates[date_day]) || ISCROSSFILTERED(dim_dates[date_day])
RETURN
    IF(
        TemFiltroDeData
        , CALCULATE(
            [Receita Líquida]
            , ALL(dim_dates)
            , DATESINPERIOD(dim_dates[date_day], MAX(dim_dates[date_day]), -12, MONTH)
        )
    )
```
`Ano Anterior` usa a mesma estrutura com `DATESINPERIOD(dim_dates[date_day], EDATE(MAX(dim_dates[date_day]), -12), -12, MONTH)`.
`(%YoY)` = `DIVIDE([Receita Líquida LTM] - [Receita Líquida LTM Ano Anterior], [Receita Líquida LTM Ano Anterior])`.

#### `Receita Líquida Média Móvel 30d`

**Semantic definition:** Média móvel diária de receita líquida sobre os
últimos 30 dias corridos. Retorna vazio quando a janela de 30 dias está
incompleta (menos de 30 dias disponíveis) ou quando a data avaliada é
posterior à última venda registrada na base.

**Expression:**
```dax
VAR Janela30Dias = DATESINPERIOD(dim_dates[date_day], MAX(dim_dates[date_day]), -30, DAY)
VAR DiasNaJanela = COUNTROWS(Janela30Dias)
VAR UltimoDiaComDados = CALCULATE(MAX(fct_sales[order_date]), ALL(fct_sales))
VAR DataAtual = MAX(dim_dates[date_day])
RETURN
    IF(
        DiasNaJanela = 30 && DataAtual <= UltimoDiaComDados
        , AVERAGEX(Janela30Dias, CALCULATE([Receita Líquida]))
    )
```

#### `Valor Total Reembolsado`

**Semantic definition:** Soma dos reembolsos concluídos em vendas efetivas.

**Expression:**
```dax
CALCULATE(
    SUM(fct_sales[completed_refund_amount])
    , fct_sales[is_effective_sale] = TRUE()
)
```

#### `Reembolso Acumulado nos Últimos 12 Meses (LTM)`

**Semantic definition:** `[Valor Total Reembolsado]` restrito à janela
móvel dos últimos 12 meses corridos a partir da data mais recente do
filtro.

**Expression:**
```dax
CALCULATE(
    [Valor Total Reembolsado]
    , DATESINPERIOD(dim_dates[date_day], MAX(dim_dates[date_day]), -12, MONTH)
)
```

#### `Motivo Reembolso Top por Quantidade`

**Semantic definition:** Motivo de devolução mais frequente (maior
contagem) no contexto selecionado, considerando apenas devoluções
efetivas.

**Expression:**
```dax
VAR MotivosNoContexto =
    ADDCOLUMNS(
        VALUES(fct_returns[return_reason])
        , "Qtde", CALCULATE(COUNTROWS(fct_returns), fct_returns[is_effective_return] = TRUE())
    )
RETURN
    MAXX(TOPN(1, MotivosNoContexto, [Qtde]), fct_returns[return_reason])
```

#### `Categoria Top Receita`

**Semantic definition:** Nome da categoria de produto com maior receita
líquida no contexto selecionado.

**Expression:**
```dax
VAR CategoriasNoContexto =
    ADDCOLUMNS(
        VALUES(dim_products[category_name])
        , "Receita", CALCULATE([Receita Líquida])
    )
RETURN
    MAXX(TOPN(1, CategoriasNoContexto, [Receita]), dim_products[category_name])
```

#### `Receita da Categoria Top do Vendedor`

**Semantic definition:** Receita líquida da categoria de produtos com maior
receita, dentro do contexto de filtro atual (por exemplo, o vendedor
selecionado) — mesma lógica de `Categoria Top Receita`, mas retornando o
valor de receita da categoria vencedora em vez do nome.

**Expression:**
```dax
VAR CategoriasNoContexto =
    ADDCOLUMNS(
        VALUES(dim_products[category_name])
        , "Receita", CALCULATE([Receita Líquida])
    )
RETURN
    MAXX(TOPN(1, CategoriasNoContexto, [Receita]), [Receita])
```

#### `Ranking de Vendedores por Receita`

**Semantic definition:** Posição do vendedor no ranking de receita líquida
(1 = maior receita), calculada apenas entre os vendedores efetivamente
exibidos no visual (`ALLSELECTED`).

**Expression:**
```dax
IF(
    ISINSCOPE(dim_employees[employee_full_name]),
    RANKX(
        ALLSELECTED(dim_employees[employee_full_name])
        , CALCULATE([Receita Líquida])
        ,
        , DESC
    )
)
```

**Nota de filtro do relatório (não da medida):** os visuais desta página
("Vendas") que usam `dim_employees[employee_role]` como coluna aplicam um
filtro persistido `employee_role = 'Salesperson'` — ou seja, na prática o
ranking de vendedores exibido no relatório é restrito a funcionários com
esse `role`. Esse filtro está no visual, não dentro da medida DAX.

#### `Quantidade de Pagamentos`

**Semantic definition:** Contagem de pagamentos efetivos.

**Expression:**
```dax
CALCULATE(
    COUNTROWS(fct_payments)
    , fct_payments[is_effective_paid] = TRUE()
)
```

#### `Pedidos Pagos`

**Semantic definition:** Quantidade de pedidos distintos com pagamento
efetivo, a partir de `fct_payments`. O próprio modelo documenta, em
comentário explícito, que isto **não deve ser confundido** com
`[Pedidos Efetivos]` (que vem de `fct_sales`) — um pedido pode ter mais de
um pagamento (parcelado/dividido entre métodos), então a soma por método de
pagamento pode ultrapassar o total de pedidos.

**Expression:**
```dax
CALCULATE(
    DISTINCTCOUNT(fct_payments[order_id]),
    fct_payments[is_effective_paid] = TRUE()
)
```

#### `Média Itens por Pedido`

**Semantic definition:** Quantidade média de itens por pedido efetivo.

**Expression:**
```dax
DIVIDE([Quantidade Total Vendida], [Pedidos Efetivos])
```

#### `Itens em Risco de Ruptura`

**Semantic definition:** Percentual de itens de estoque com previsão de
esgotar antes da chegada do próximo reabastecimento, sobre o total de itens
monitorados pelo modelo de reabastecimento.

**Expression:**
```dax
DIVIDE(
    CALCULATE(
        COUNTROWS(fct_inventory_replenishment),
        fct_inventory_replenishment[will_stockout_before_replenishment] = TRUE
    ),
    COUNTROWS(fct_inventory_replenishment)
)
```

**Correspondência com regra já documentada:** `will_stockout_before_replenishment`
é exatamente o campo já descrito em `inventory_and_stockout.md`
("Risco de ruptura antes da reposição").

### Clientes & RFM

#### `Total Clientes`

**Expression:** `COUNTROWS(agg_customer_rfm)` — contagem de clientes
únicos na base segmentada por RFM.

#### `Recência Média` / `Frequência Média` / `Monetário Médio`

**Semantic definition:** Médias simples, no contexto selecionado, de
`recency_days`, `frequency_orders` e `monetary_amount` de `agg_customer_rfm`
(os três componentes RFM). Nomes e grão idênticos aos já documentados em
`metrics_dictionary.md` — nenhum novo cálculo é introduzido; são apenas
`AVERAGE()` diretos sobre as colunas Gold já existentes.

**Expression:**
```dax
AVERAGE(agg_customer_rfm[recency_days])
AVERAGE(agg_customer_rfm[frequency_orders])
AVERAGE(agg_customer_rfm[monetary_amount])
```

#### `Receita por Segmento RFM`

**Expression:** `SUM(agg_customer_rfm[monetary_amount])` — tipicamente
usada com um visual detalhado por `customer_segment`.

#### `Ranking Particionado no Segmento`

**Semantic definition:** Posição de um cliente dentro do seu próprio
segmento RFM, ordenada por valor monetário (maior primeiro) — só retorna
valor quando exatamente um cliente está em foco no contexto (`HASONEVALUE`).

**Expression:**
```dax
IF(
    HASONEVALUE(agg_customer_rfm[customer_id])
    , RANKX(
        FILTER(
            ALL(agg_customer_rfm)
            , agg_customer_rfm[customer_segment] = SELECTEDVALUE(agg_customer_rfm[customer_segment])
        )
        , agg_customer_rfm[monetary_amount]
        , MAX(agg_customer_rfm[monetary_amount])
        , DESC
    )
)
```

**Nota de filtro do relatório:** um visual que usa esta medida filtra
explicitamente `[Ranking Particionado no Segmento] <= 10` — ou seja, essa
visualização específica exibe apenas o Top 10 de cada segmento, mas isso é
um filtro de visual, não parte da definição da medida.

#### `Receita em Risco (Churn)`

**Semantic definition:** Valor monetário (LTV) somado apenas dos clientes
classificados no segmento RFM `"at_risk"` — interpretado no modelo como a
receita potencialmente perdida em caso de churn desses clientes.

**Expression:**
```dax
CALCULATE(
    SUM(agg_customer_rfm[monetary_amount])
    , agg_customer_rfm[customer_segment] = "at_risk"
)
```

#### `Clientes em Risco`

**Expression:** `CALCULATE(COUNTROWS(agg_customer_rfm), agg_customer_rfm[customer_segment] = "at_risk")`.

#### `Email do Cliente` / `Telefone do Cliente`

**Semantic definition:** Retornam `customer_email`/`customer_phone` apenas
quando um único cliente está em foco (`SELECTEDVALUE`), tipicamente em um
cartão de detalhes.

**Expression:**
```dax
SELECTEDVALUE(dim_customers[customer_email])
SELECTEDVALUE(dim_customers[customer_phone])
```

**Nota de governança:** ver "Governança de dados observada" acima — dado
classificado como PII, exposto sem máscara e sem RLS configurada no modelo,
no momento desta inspeção.

### Produtos

#### `Produtos Vendidos`

**Semantic definition:** Quantidade de produtos distintos (agrupando
variantes por `product_id`) com pelo menos uma venda efetiva no contexto
selecionado. Usa `CROSSFILTER(..., BOTH)` para forçar filtro bidirecional
entre `dim_products` e `fct_sales` apenas dentro desta medida (a relação
padrão do modelo, nesse sentido, é de mão única).

**Expression:**
```dax
CALCULATE(
    DISTINCTCOUNT(dim_products[product_id])
    ,  fct_sales[is_effective_sale] = TRUE()
    , CROSSFILTER(
        dim_products[product_variant_id]
        , fct_sales[product_variant_id]
        , BOTH
    )
)
```

#### `Quantidade Total Vendida`

**Expression:** `CALCULATE(SUM(fct_sales[order_item_quantity]), fct_sales[is_effective_sale] = TRUE())`.

#### `Ranking por Quantidade` / `Ranking por Receita`

**Semantic definition:** Posição do produto no ranking geral (1º =
melhor), calculada sobre **todo o catálogo** (`ALL`), não apenas os
produtos exibidos no visual — ranking denso (`Dense`, sem saltos de
posição quando há empates).

**Expression (Receita):**
```dax
IF(
    ISBLANK([Receita Líquida]),
    BLANK(),
    RANKX(
        ALL(dim_products[product_id], dim_products[product_name]),
        [Receita Líquida],
        ,
        DESC,
        Dense
    )
)
```
(`Ranking por Quantidade` é estruturalmente idêntico, usando
`[Quantidade Total Vendida]` no lugar de `[Receita Líquida]`.)

#### `Taxa de Devolução`

**Semantic definition:** Percentual de itens devolvidos em relação à
quantidade total vendida, ambos restritos a vendas efetivas.

**Expression:**
```dax
DIVIDE(
    CALCULATE(SUM(fct_sales[completed_returned_quantity]), fct_sales[is_effective_sale] = TRUE())
    , CALCULATE(SUM(fct_sales[order_item_quantity]), fct_sales[is_effective_sale] = TRUE())
)
```

#### `Quantidade Devolvida`

**Expression:** `SUM(fct_sales[completed_returned_quantity])`.

#### `Receita do Produto Campeão` / `Nome do Produto Campeão` / `Volume de Vendas do Produto Campeão`

**Semantic definition:** Identificam e retornam atributos do produto com
maior receita líquida no contexto atual — o "produto campeão" — via
`TOPN(1, ALLSELECTED(dim_products[product_id]), [Receita Líquida], DESC)`.

**Expression (Receita do Produto Campeão):**
```dax
CALCULATE(
    [Receita Líquida],
    TOPN(1, ALLSELECTED(dim_products[product_id]), [Receita Líquida], DESC)
)
```
`Nome do Produto Campeão` usa a mesma lógica de `TOPN`, retornando
`MAX(dim_products[product_name])`. `Volume de Vendas do Produto Campeão`
usa a mesma lógica de `TOPN`, mas ordenando por
`CALCULATE(SUM(fct_sales[order_item_net_revenue_after_returns]))` em vez
de `[Receita Líquida]` diretamente, e retorna `[Quantidade Total Vendida]`
do produto campeão identificado.

#### `Participação Produto Campeão`

**Semantic definition:** Percentual que a receita do produto campeão
representa sobre a receita líquida total do contexto.

**Expression:**
```dax
VAR ReceitaTop1 = [Receita do Produto Campeão]
VAR ReceitaTotal = [Receita Líquida]
RETURN
    DIVIDE(ReceitaTop1, ReceitaTotal, 0)
```

**Divisão por zero:** `DIVIDE(..., 0)` — retorna `0` explicitamente em vez
de `BLANK()`, diferente da maioria das outras medidas do modelo que usam a
forma de dois argumentos.

#### `Receita Líquida Indexada (Base 100)`

**Semantic definition:** Receita líquida indexada a 100 no primeiro mês da
janela filtrada, para comparar evolução relativa entre séries de
magnitudes muito diferentes.

**Expression:**
```dax
VAR PrimeiroDiaJanela = CALCULATE(MIN(dim_dates[date_day]), ALLSELECTED(dim_dates))
VAR FimDoMesBase = EOMONTH(PrimeiroDiaJanela, 0)
VAR ReceitaBase =
    CALCULATE(
        [Receita Líquida]
        , ALLSELECTED(dim_dates)
        , dim_dates[date_day] >= PrimeiroDiaJanela
        , dim_dates[date_day] <= FimDoMesBase
    )
RETURN
    DIVIDE([Receita Líquida], ReceitaBase) * 100
```

### Estoque & Supply

#### `Estoque Atual`

**Expression:** `SUM(fct_inventory_replenishment[quantity_on_hand])`.

#### `Dias até Ruptura`

**Semantic definition:** Estimativa de dias restantes até a ruptura de
estoque do item, lida diretamente da coluna já calculada no modelo de
reabastecimento Gold (`estimated_days_until_stockout`) — a medida não
recalcula essa estimativa, apenas soma o valor já vindo da fonte.

**Expression:** `SUM(fct_inventory_replenishment[estimated_days_until_stockout])`.

#### `Quantidade de Restoque Sugerida`

**Semantic definition:** Quantidade sugerida de reposição, sempre
arredondada para cima (nunca subestima a compra).

**Expression:**
```dax
ROUNDUP(SUM(fct_inventory_replenishment[suggested_reorder_quantity]), 0)
```

#### `Confiança Restoque Sugerido`

**Expression:** `SELECTEDVALUE(fct_inventory_replenishment[lead_time_confidence])`.

#### `Itens em Reposição (%)`

**Semantic definition:** Percentual de itens de estoque que precisam de
reposição, sobre o total monitorado pelo modelo de reabastecimento.

**Expression:**
```dax
DIVIDE(
    CALCULATE(
        COUNTROWS(fct_inventory_replenishment),
        fct_inventory_replenishment[is_replenishment_required] = TRUE
    ),
    COUNTROWS(fct_inventory_replenishment)
)
```

**Nota de filtro do relatório:** existe também um visual (tabela) na
página "Estoque & Supply" com filtro persistido
`fct_inventory_replenishment[is_replenishment_required] = TRUE` aplicado
diretamente sobre o visual — ou seja, uma tabela de detalhe mostra apenas
os itens que efetivamente precisam de reposição, distinta desta medida de
percentual.

#### `Itens por Status de Saúde` / `Itens com Status Fora de Estoque` / `Itens com Status Crítico` / `Itens com Status Atenção` / `Itens com Status Saudável`

**Semantic definition:** `Itens por Status de Saúde` é a contagem total de
itens monitorados (`COUNTROWS(fct_current_inventory)`), pensada para ser
detalhada por `inventory_health_status` em um visual agrupado. As quatro
medidas seguintes são a mesma contagem, cada uma filtrada por um valor
específico de `inventory_health_status`: `"out_of_stock"`, `"critical"`,
`"attention"`, `"healthy"`.

**Expression (exemplo — Crítico):**
```dax
CALCULATE(
    COUNTROWS(fct_current_inventory)
    , fct_current_inventory[inventory_health_status] = "critical"
)
```

**Observação sobre `inventory_health_status` — coluna calculada
`'Ordem Status Saúde'`:** a coluna calculada auxiliar do modelo (usada só
para ordenar o eixo/legenda de um visual) mapeia:
```dax
SWITCH(
    fct_current_inventory[inventory_health_status]
    , "out_of_stock", 1
    , "critical", 2
    , "attention", 3
    , "healthy", 4
    , "no_recent_sales", 5
)
```
Isso confirma, diretamente na fonte Gold consumida pelo Power BI, o quinto
valor de `inventory_health_status` — **`"no_recent_sales"`** — que não
possui uma medida de contagem dedicada no modelo Power BI (diferente dos
outros quatro status, que têm). **Este não é um valor novo ou não
documentado:** os 5 estados (`out_of_stock`, `no_recent_sales`, `critical`,
`attention`, `healthy`), nessa mesma ordem de prioridade, já estão
descritos em `inventory_and_stockout.md` ("Inventory health status",
`GOLD-017`), com fonte de implementação na macro dbt
`classify_inventory_health.sql`. O Power BI apenas consome o valor
já existente na coluna Gold — a coluna calculada `'Ordem Status Saúde'`
apenas atribui um ordinal (1–5) para ordenação/apresentação, não cria nem
redefine a classificação. Este documento não redefine nem reinterpreta a
taxonomia de status já descrita em `inventory_and_stockout.md` (`GOLD-017`).

#### `Lead Time Médio`

**Semantic definition:** Média do lead time (em dias) dos fornecedores
ativos, usando o campo `selected_lead_time_days` já calculado no Gold — a
mesma coluna que resolve o fallback "observado prevalece sobre declarado"
já documentado em `gold_business_rules.md`. A medida não reimplementa esse
fallback, apenas consome o resultado já resolvido.

**Expression:**
```dax
CALCULATE(
    AVERAGE(dim_suppliers[selected_lead_time_days]),
    dim_suppliers[is_active] = TRUE
)
```

#### `Fornecedores no Prazo`

**Semantic definition:** Percentual de fornecedores ativos cujo prazo de
entrega observado está dentro ou abaixo do prazo combinado
(`lead_time_variance_days <= 0`), sobre o total de fornecedores ativos.

**Expression:**
```dax
DIVIDE(
    CALCULATE(COUNTROWS(dim_suppliers), dim_suppliers[lead_time_variance_days] <= 0, dim_suppliers[is_active] = TRUE),
    CALCULATE(COUNTROWS(dim_suppliers), dim_suppliers[is_active] = TRUE)
)
```

#### `Fornecedor Principal`

**Semantic definition:** Nome do fornecedor com o menor `lead_time_days`
cadastrado para o produto/variante no contexto selecionado, via a tabela
ponte `bridge_product_suppliers` — identifica o fornecedor "prioritário"
por menor prazo de entrega.

**Expression:**
```dax
CALCULATE(
    SELECTEDVALUE(dim_suppliers[supplier_display_name])
    , TOPN(1, bridge_product_suppliers, bridge_product_suppliers[lead_time_days], ASC)
)
```

#### `Produtos Cobertos pelo Fornecedor`

**Expression:** `DISTINCTCOUNT(bridge_product_suppliers[product_variant_id])`.

#### `Pedidos Abertos`

**Semantic definition:** Contagem de pedidos de compra a fornecedores
ainda em aberto.

**Expression:**
```dax
CALCULATE(
    DISTINCTCOUNT(fct_purchases[purchase_order_id]),
    fct_purchases[purchase_order_status] IN {"sent", "partially_received"}
)
```

**Correspondência com regra já documentada:** os status `"sent"` e
`"partially_received"` são exatamente os já descritos como "pedido de
compra em aberto" em `gold_business_rules.md`
(`GOLD-032`) e reforçados em `inventory_and_stockout.md` (`GOLD-029`) —
esta medida usa a mesma condição, não uma nova.

### Medidas técnicas e de apresentação

As 6 medidas abaixo existem para suportar apresentação visual (texto
formatado, cores condicionais, tooltips) e não introduzem novo cálculo de
negócio além de referenciar medidas já descritas acima. Cada uma tem sua
própria subseção para que uma busca por um desses conceitos (waterfall,
YoY, tooltip, tendência de produto, atualização, cor de categoria) não
dependa de recuperar as outras cinco junto.

#### `Variação YoY (texto)`

**Semantic definition:** Concatena `[Receita Líquida do Período (%YoY)]` e
`[Receita Líquida LTM (%YoY)]`, cada uma formatada com seta (▲/▼), em um
único texto para exibição em cards.

**Table:** `_Medidas`. **Dependências:** `[Receita Líquida do Período (%YoY)]`,
`[Receita Líquida LTM (%YoY)]`.

#### `Valor Waterfall`

**Semantic definition:** Valor de cada etapa do gráfico em cascata de
composição da receita, conforme a categoria selecionada no eixo (via a
tabela de apoio `Waterfall Categorias`) — considera apenas vendas efetivas.

**Expression:**
```dax
CALCULATE(
    SWITCH(
        SELECTEDVALUE('Waterfall Categorias'[Categoria])
        , "Receita Bruta", SUM(fct_sales[order_item_line_total])
        , "Impostos", -SUM(fct_sales[order_item_icms_amount]) - SUM(fct_sales[order_item_ipi_amount])
        , "Descontos", -(SUM(fct_sales[order_item_net_revenue]) - SUM(fct_sales[order_item_net_revenue_after_discount]))
        , "Devoluções", -SUM(fct_sales[completed_refund_amount])
        , BLANK()
    )
    , fct_sales[is_effective_sale] = TRUE()
)
```

**Correspondência com conteúdo já documentado:** reconstrói visualmente o
mesmo waterfall financeiro já descrito textualmente em
`metrics_dictionary.md` ("Waterfall financeiro (item-level)"), sem alterar
nenhuma das fórmulas-base — COMPLEMENTARY, não duplica.

#### `Tendência Produto Campeão`

**Semantic definition:** Série temporal da receita do produto identificado
como campeão no filtro geral da página — granularidade diária quando a
janela filtrada tem até 31 dias, ou agregada por mês (dia 1) quando maior.

**Table:** `_Medidas`. **Dependência:** `[Receita Líquida]`, identificação
do produto campeão via `TOPN`/`ALLSELECTED` sobre `dim_products[product_id]`.

#### `Produtos Vendidos Tooltip`

**Semantic definition:** Texto comparando `[Produtos Vendidos]` do período
atual contra o mês anterior (quando há filtro de data) ou o mesmo período
do ano anterior (quando não há), com seta de alta/queda. Retorna
`"Sem histórico para comparação"` quando não há período anterior
disponível — `BLANK` tratado explicitamente, não propagado como erro.

**Table:** `_Medidas`. **Dependência:** `[Produtos Vendidos]`.

#### `Última Atualização (Texto)`

**Semantic definition:** Texto de referência indicando a data da venda mais
recente registrada na base, usado como indicação de até quando os dados
exibidos estão atualizados.

**Expression:**
```dax
"Dados até " & FORMAT(MAX(fct_sales[order_date]), "dd/MM/yyyy")
```

#### `Cor Linha Categoria`

**Semantic definition:** Atribui uma cor hexadecimal fixa às 5 primeiras
categorias de produto por ranking de receita líquida (`RANKX`), com cor de
fallback (`#CCCCCC`) para categorias além da 5ª posição — formatação
condicional, não uma classificação de negócio.

**Table:** `_Medidas`. **Dependência:** `[Receita Líquida]` (via `RANKX`
sobre `dim_products[category_name]`).

## Filtros Persistidos Relevantes no Relatório

Não há filtros configurados no nível do relatório (`report.json`) nem no
nível de página (`filterConfig` de todas as 5 páginas de conteúdo está
vazio). Os filtros abaixo estão persistidos em **visuais individuais**
(nível mais baixo da hierarquia de confiança de evidência) e são citados
aqui apenas como convenções de exibição do relatório, não como regras de
negócio:

- **Janela móvel padrão de 12 meses:** vários visuais de série temporal
  (páginas "Vendas" e "Produtos") filtram `dim_dates[date_day]` para o
  intervalo `[hoje - 12 meses, hoje]` por padrão.
  **Artifact (exemplo):** `Dashboard Gerencial Compass 360 - LH Nautical.Report/definition/pages/977d378bc913040446ed/visuals/8c8801a44508ef9b4407/visual.json`
  (página "Vendas").
- **Vendedores = `role` "Salesperson":** visuais de ranking/detalhe de
  vendedores, na página "Vendas", filtram
  `dim_employees[employee_role] = 'Salesperson'` (e, em um caso, também
  excluem `employee_full_name` nulo).
  **Artifact:** `.../pages/977d378bc913040446ed/visuals/c06010f9b2640e3468a4/visual.json`.
- **Top 10 por segmento RFM:** o visual de ranking de clientes por
  segmento filtra `[Ranking Particionado no Segmento] <= 10`.
  **Artifact:** `.../pages/fc0674fa291316487872/visuals/a5d4a469cea0e2706179/visual.json`
  (página "Clientes & RFM").
- **Itens que precisam de reposição:** uma tabela de detalhe na página
  "Estoque & Supply" filtra `fct_inventory_replenishment[is_replenishment_required] = TRUE`.
  **Artifact:** `.../pages/fed4f5a8c0dc65084325/visuals/80c01a425162a98b3e6e/visual.json`.

`...` acima abrevia o prefixo já dado na primeira ocorrência:
`Dashboard Gerencial Compass 360 - LH Nautical.Report/definition`.

## Páginas do Relatório (Mapa de Cobertura)

13 páginas ao todo: 1 capa, 5 páginas de conteúdo e 7 páginas de tooltip
(usadas apenas como tooltip customizado de outros visuais, sem navegação
direta do usuário):

| Página | Papel |
|---|---|
| Capa | Página de abertura do dashboard |
| Visão Geral (Executivo) | KPIs consolidados — receita, YTD, YoY, LTM |
| Vendas | Receita, ticket médio, vendedores, waterfall financeiro |
| Produtos | Ranking de produtos, produto campeão, categorias, devoluções |
| Estoque & Supply | Saúde de estoque, ruptura, reposição, fornecedores/lead time |
| Clientes & RFM | Segmentação RFM, clientes em risco, receita por segmento |
| Tooltip_Vendas [Métodos Pagamento] | Tooltip customizado |
| Tooltip_Vendas [Tendência de Receita UDM] | Tooltip customizado |
| Tooltip_Vendas [Especialidade Vendedor] | Tooltip customizado |
| Tooltip_Produtos [Produtos Vendidos MoM ou YoY] | Tooltip customizado |
| Tooltip_Produtos [Produto Campeão] | Tooltip customizado |
| Tooltip_Produtos [Top Categorias] | Tooltip customizado |
| Tooltip_Produtos [Devolucoes] | Tooltip customizado |

Esta tabela reflete apenas nomes/papel geral de página (evidência de
menor confiança, nível "metadata de visuals/pages" na hierarquia da
Seção 5) — não foi feito um levantamento visual-a-visual dos 167 visuais
do relatório; o conteúdo semântico de maior confiança (medidas, colunas,
relacionamentos) está integralmente nas seções acima.

## Relação com o Corpus Canônico Existente

Este documento **complementa**, e não substitui, os documentos canônicos
já ingeridos:

- `gold_business_rules.md` — regras de negócio Gold (GOLD-XXX). As
  correspondências citadas acima (`GOLD-004`, `GOLD-008`, `GOLD-029`,
  `GOLD-032`) confirmam que o modelo Power BI consome os mesmos campos Gold
  já cobertos por essas regras — este documento não redefine nenhuma delas.
- `metrics_dictionary.md` — definições quantitativas de métricas Gold
  (`order_item_net_revenue_after_returns`, RFM, etc.), reutilizadas
  diretamente por várias medidas DAX acima.
- `inventory_and_stockout.md` — campos de reposição/ruptura
  (`will_stockout_before_replenishment`, `is_replenishment_required`,
  `inventory_health_status`) consumidos as-is pelas medidas de Estoque &
  Supply, incluindo a taxonomia de 5 estados já fechada sob `GOLD-017`
  (`out_of_stock`, `no_recent_sales`, `critical`, `attention`, `healthy` —
  ver "Estoque & Supply" acima).

Nenhuma Rule ID no formato `GOLD-\d{3}` é criada por este documento — o
namespace `GOLD-XXX` pertence exclusivamente às regras dbt/Gold já
fechadas. Referências a `GOLD-XXX` acima são citações a regras
pré-existentes, não novas atribuições.
