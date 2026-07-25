# LH Nautical — Plataforma de Dados (Ingestão + Transformação dbt)

## Visão Geral do Projeto
Este repositório contém a plataforma de dados completa do projeto **LH Nautical** (grupo_9): a ingestão de dados brutos do sistema transacional (SQL Server) para a camada **Bronze** do Data Lake, e a transformação desses dados em modelos analíticos (camadas **Silver** e **Gold**) via **dbt**, seguindo modelagem dimensional (Kimball).

O projeto é dividido em duas partes:
1. **Ingestão (PySpark/Databricks)** — descrita em detalhe na seção [Pipeline de Ingestão de Dados via JDBC](#pipeline-de-ingestão-de-dados-via-jdbc-sql-server---delta-lake) logo abaixo.
2. **Transformação (dbt)** — descrita na seção [Camada de Transformação (dbt)](#camada-de-transformação-dbt), cobrindo a modelagem, regras de negócio, testes e o pipeline de CI/CD.

---

## Pipeline de Ingestão de Dados via JDBC (SQL Server - Delta Lake)

### Visão Geral
Este ingestão de dados foi desenvolvido em **PySpark** no Databricks. Ele foi desenhado para automatizar a extração de tabelas de um banco de dados relacional (SQL Server) e carregá-las no **Data Lake** (Delta Lake integrado ao Unity Catalog).

O grande diferencial tecnológico deste script é a implementação de um **CDC Lógico baseado em Hashing** combinado com o **Time Travel do Delta**, garantindo ingestões incrementais de alta performance e baixo consumo de memória (I/O otimizado).

---

### Arquitetura e Fluxo de Dados

O pipeline segue um padrão de **EL (Extract and Load)** para a camada *Bronze/Staging*, atuando de forma genérica para suportar dezenas de tabelas dinamicamente.

#### 1. Descoberta Dinâmica de Tabelas
O código não possui uma lista fixa (hardcoded) de tabelas. Ele inicia consultando o `information_schema.tables` diretamente no SQL Server de origem. 
* Captura dinamicamente apenas tabelas do tipo `Base table` pertencentes ao schema `dbo`.
* Se uma nova tabela for criada no ERP/Banco Transacional, ela será automaticamente capturada na próxima execução, sem necessidade de deploy de código.

#### 2. Leitura Particionada e Paralelizada
Ler grandes volumes de dados via JDBC em uma única thread causa gargalos. O pipeline resolve isso com a função `consulta_tabela_jdbc`.
* Se uma chave de particionamento for definida, o Spark dispara uma query rápida na origem utilizando `MIN()` e `MAX()`.
* Usando esses limites e o parâmetro `numPartitions`, o Spark divide a leitura em múltiplas queries concorrentes, acelerando drasticamente o processo.

#### 3. CDC Hashing e Time Travel
Ao invés de realizar updates lentos ou ler a tabela inteira do destino para comparar, a função `ingestao_incremental_hash` faz o seguinte:
* **Na Origem:** Gera um Hash MD5 da linha inteira. Utiliza a função `coalesce` para garantir que valores nulos não quebrem a geração do Hash, e concatena tudo com o separador `||`.
* **No Destino (Delta):** Descobre a última versão da tabela com `DESCRIBE HISTORY` e carrega para a memória **apenas** a coluna `_hash` dessa versão específica usando a option `versionAsOf`.
* **O Join:** Executa um `left_anti` join. O Spark compara as origens e mantém apenas os Hashes que são novos ou sofreram alterações (I/O mínimo). 

---

### Pré-requisitos e Setup do Ambiente

A infraestrutura do projeto utiliza a abordagem de Declarative Automation Bundles (DABs) para a orquestração via código.

Para os desenvolvedores operando com IDEs locais (como VS Code), as dependências do projeto são gerenciadas de forma estrita para garantir total compatibilidade com os clusters do Databricks.

#### 1. Pré-requisito de Linguagem
Certifique-se de estar utilizando o Python 3.12, versão homologada pela equipe para garantir compatibilidade nativa com os pacotes e bibliotecas em execução no Databricks.

#### 2. Gerenciamento de Dependências 
Em vez de gerenciadores externos, o projeto adota o padrão nativo do Python. Instale as bibliotecas necessárias executando: 

```pip install -r requirements.txt```

#### 3. Databricks CLI
Instale o Databricks CLI versão 1.2, ferramenta obrigatória para a validação e envio dos bundles de infraestrutura. O próprio Databricks oferece [instruções](https://docs.databricks.com/aws/pt/dev-tools/cli/install) completas para instalação em qualquer sistema operacional.

#### 4. Autenticação Segura 
Autentique-se via CLI com o comando `databricks auth login`. Esta é a prática recomendada pela arquitetura para evitar a exposição e o tráfego de tokens fixos em arquivos de configuração local (como .env).

#### 5. Deploy em Produção 
Com o ambiente devidamente configurado, para realizar o envio da infraestrutura para o ambiente produtivo, execute:\
`databricks bundle deploy --target prod`

#### 6. Databricks Secrets (Credenciais)
As credenciais do banco nunca ficam expostas no código. O Databricks deve ter um *Secret Scope* configurado com as seguintes chaves:
* **Escopo:** `databases_lh_nautical`
* **Chaves Necessárias:** `sql_server_user`, `sql_server_password`, `sql_server_host`, `sql_server_port`.

*Nota:* A string de conexão JDBC aponta fixamente para o banco de dados `lh_nautical` e utiliza o driver `com.microsoft.sqlserver.jdbc.SQLServerDriver`.

#### 7. Parâmetros de Orquestração (Widgets)
O código consome variáveis em tempo de execução via `dbutils.widgets.get`. Esses parâmetros devem ser passados pelo orquestrador:
* `catalog_name`: Nome do catálogo no Unity Catalog.
* `schema`: Nome do banco/schema de destino.
* `numPartitions`: Valor determinando a concorrência do JDBC.

---

### Mergulho Técnico nas Funções Principais

#### 1. `consulta_tabela_jdbc`
Responsável por estabelecer a conexão e extrair os dados, aplicando paralelização quando aplicável.

**Como funciona sob o capô:**
1. **Avaliação de Paralelismo:** Checa se uma `coluna_referencia_paralelizacao` foi informada. Se não for (`None`), faz uma leitura JDBC sequencial.
2. **Descoberta de Limites (Bounds):** Se informada, o Spark envia uma subquery leve para a origem: `SELECT MIN(coluna), MAX(coluna)`.
3. **Leitura Distribuída:** Os valores extraídos são passados para `lowerBound` e `upperBound` do conector JDBC.
4. **Fatiamento:** O Spark divide o intervalo e dispara múltiplas queries simultâneas para o SQL Server com base no `numPartitions`.

#### 2. `ingestao_incremental_hash`
O coração lógico do pipeline que decide se os dados devem ser sobrescritos ou apensados.

**Árvore de Decisão de Gravação:**
* **Cenário A (Carga Inicial):** Verifica se a tabela de destino existe. Se não existir, adiciona as colunas `_ingested_at` e `_action` e retorna a instrução `"overwrite"`.
* **Cenário B (Tabela Legada - Sem Hash):** Se a tabela existir mas não possuir a coluna `_hash`, recria a tabela do zero injetando data/hora (`_ingested_at`) e a flag `"CARGA_INICIAL"` (`_action`) e retorna `"overwrite"` para atualização segura do schema.
* **Cenário C (Ingestão Incremental via CDC):** * Busca a última versão via `DESCRIBE HISTORY`.
  * Lê o Data Lake trazendo apenas a coluna `_hash` usando `versionAsOf`.
  * Faz o `left_anti` join para isolar novos registros ou updates.
  * Se houver alterações, injeta data/hora (`_ingested_at`) e a flag `"INSERT_OU_UPDATE"` (`_action`) e retorna `"append"`. Caso contrário, retorna `None, None`, poi não houver alterações.

---

### Regras de Negócio e Estratégia de Tabelas

Durante a iteração, o script aplica regras para decidir **como** cada tabela deve ser lida:

| Tabela(s) | Chave de Partição | Comportamento Explicado |
| :--- | :--- | :--- |
| `variant_attribute_values` | *Nenhuma* | Tabela não mapeada com chave confiável. Roda sequencialmente (sem paralelismo). |
| `product_suppliers`, `stock_levels` | `updated_at` | Usa a coluna de data de atualização para criar as fatias particionadas. |
| **Demais Tabelas** | `id` | Comportamento padrão. Utiliza a chave primária sequencial `id` da tabela. |

---

### Metadados Injetados (Auditoria)

As tabelas salvas no Delta Lake contêm colunas extras fundamentais para rastreabilidade:

* **`_hash`** `[String]`: A assinatura MD5 dos dados. Usada puramente para performance na detecção de mudanças futuras.
* **`_ingested_at`** `[String]`: Registra o momento da inserção no formato `yyyy-MM-dd HH:mm:ss`.
* **`_action`** `[String]`: Flag indicando o motivo da entrada na base. 
  * `CARGA_INICIAL`: Ocorreu porque a tabela era nova ou teve seu schema legado atualizado.
  * `INSERT_OU_UPDATE`: Apenas deltas foram inseridos através do CDC.

---

### Monitoramento e Tratamento de Erros

O script adota uma postura focada em resiliência e propagação de alertas:
1. Ele encapsula o processamento em blocos `try-except` monitorando `Py4JJavaError`, `PySparkException` e erros gerais.
2. Se uma tabela falha (ex: erro de JDBC ou schema), o erro é logado e o nome da tabela é adicionado à lista `tabelas_com_erro`.
3. O laço de repetição continua (`continue`) processando as tabelas restantes para não paralisar toda a carga por causa de um único erro.
4. **Alerta Final:** Ao término, se a lista `tabelas_com_erro` contiver itens, o pipeline aciona um `raise Exception`, falhando intencionalmente a célula final. Isso garante que a ferramenta de orquestração marque o job como "Failed" e a equipe de engenharia seja alertada.

---

## Camada de Transformação (dbt)

### Visão Geral
O projeto **dbt** (`dbt_lh_nautical/`) transforma os dados brutos da camada **Bronze** (ingerida pelo pipeline JDBC/Autoloader acima) em modelos analíticos organizados nas camadas **Silver** (staging + intermediate) e **Gold** (marts), seguindo os princípios de **modelagem dimensional de Kimball**: dimensões conformadas, fatos no grão transacional ou de snapshot periódico, e chaves substitutas (surrogate keys) geradas via `dbt_utils.generate_surrogate_key`.

Cada desenvolvedor trabalha em um catálogo Unity Catalog pessoal (`dev_<nome>_lh_nautical`), totalmente auto-contido (bronze + silver + gold), o que permite testar o pipeline completo de ponta a ponta sem depender de recursos compartilhados.

---

### Arquitetura em Camadas

| Camada | Pasta | Materialização | Schema Unity Catalog | Propósito |
| :--- | :--- | :--- | :--- | :--- |
| **Staging** | `models/staging/` | `view` | `silver` | Limpeza 1:1 das tabelas de origem: normalização de texto, cast de tipos, remoção de valores-lixo. Não aplica lógica de negócio. |
| **Intermediate** | `models/intermediate/` | `view` | `silver` | Junta staging models e aplica regras de negócio (classificações, rateios, métricas derivadas) antes de chegar ao grão final. |
| **Marts — Dimension** | `models/marts/dimension/` | `table` | `gold` | Dimensões conformadas (`dim_customers`, `dim_products`, `dim_locations`, `dim_employees`, `dim_suppliers`, `dim_payment_methods`, `dim_dates`). |
| **Marts — Fact** | `models/marts/fact/` | `table` | `gold` | Fatos transacionais (`fct_sales`, `fct_purchases`, `fct_goods_receipts`, `fct_returns`, `fct_payments`, `fct_stock_movements`), fatos de snapshot periódico (`fct_current_inventory`, `fct_inventory_snapshot`, `fct_inventory_replenishment`) e o agregado diário de KPIs financeiros (`agg_daily_financial_metrics`). |
| **Marts — Bridge** | `models/marts/bridge/` | `table` | `gold` | Resolve relacionamentos N:N (ex.: `bridge_product_suppliers`, já que um produto pode ter múltiplos fornecedores e vice-versa). |

A resolução de schema é feita por um macro customizado (`generate_schema_name.sql`) que usa diretamente o `+schema` configurado por camada no `dbt_project.yml` (silver/gold), ignorando o schema padrão do profile — evitando a concatenação padrão do dbt (`<profile_schema>_<custom_schema>`).

---

### Regras de Negócio Centrais

#### Venda efetiva (`is_effective_sale`)
Regra centralizada em `int_orders.sql`, propagada até `fct_sales`, que define quando um pedido conta como **venda efetiva/concluída** para fins de KPI financeiro: o pedido precisa estar pago (`order_status = 'paid'`), ter nota fiscal autorizada (`fiscal_invoice_status = 'authorized'`) e ter um `placed_at` válido (não-futuro). Pedidos pagos com nota fiscal rejeitada são sinalizados separadamente pela flag `is_paid_sale_pending_invoice_fix` (receita pendente de regularização fiscal, não receita perdida).

#### Rateio de desconto por item (`order_item_net_revenue_after_discount`)
Como o desconto no sistema de origem é aplicado no nível do **pedido**, e não do item, `fct_sales.sql` rateia o desconto proporcionalmente à participação de cada item no subtotal bruto do pedido:
```
desconto_alocado = order_discount_amount * order_item_line_total / order_subtotal_amount
```
Validado por um teste singular de reconciliação (`assert_order_item_discount_allocation_reconciles.sql`) que garante que a soma dos descontos alocados por item bate com o desconto total do pedido.

#### Devoluções efetivas (`is_effective_return`)
Espelhando a lógica de `is_effective_sale`, `int_returns.sql` define uma devolução como efetiva quando está com status `completed` e tem uma data de evento válida (não-futura). `int_completed_returns_by_order_item.sql` agrega essas devoluções ao grão de `order_item_id`, permitindo que `fct_sales` incorpore quantidade devolvida e valor de reembolso sem duplicar linhas (join 1:1 seguro).

#### Reposição de estoque (`fct_inventory_replenishment`)
Calcula a quantidade sugerida de reposição por variante de produto e local, combinando estoque atual, pedidos de compra em aberto e uma **cascata de fallback de lead time** (histórico produto+local → histórico só do produto → histórico global → padrão fixo de 7 dias), com uma classificação de confiança (`lead_time_confidence`) que reflete quantas observações sustentam a estimativa.

#### Métricas financeiras diárias (`agg_daily_financial_metrics`)
Agregado (grão: 1 linha por dia) que responde às 3 perguntas centrais do negócio: receita líquida, ticket médio e taxa de devolução. Consome exclusivamente `fct_sales` já filtrado por `is_effective_sale`, com numeradores e denominadores expostos lado a lado (não apenas a razão), para permitir recálculo correto em janelas maiores que um dia.

---

### Qualidade de Dados: Macros e Convenções

* **`nullify_future_date(coluna)`**: anula (`null`) timestamps no futuro em vez de descartar a linha inteira — segue o princípio de que **dado errado isolado é pior que dado ausente**, mas o resto do registro continua utilizável.
* **`clean_and_format_name(coluna)` / `clean_and_format_name_place(coluna)`**: padronizam nomes de pessoas e de logradouros (capitalização, espaços redundantes, preposições em minúsculo).
* **`lower_trim` / `upper_trim` / `initcap_trim`**: normalização básica de texto reutilizada em praticamente todos os models de staging.
* **`nullify_garbage(coluna)`**: trata strings-lixo conhecidas (ex.: `'[NULL]'` literal vindo da origem) como nulo de verdade.
* **`generate_schema_name.sql`**: ver seção de arquitetura acima.

Padrão de **defesa em profundidade** para cálculos financeiros sensíveis a divisão por zero (ex.: rateio de desconto): um guard `nullif(x, 0)` no SQL evita o crash, **e** um teste dbt (`x > 0`) expõe o problema de qualidade de dados como falha visível — o guard sozinho só protege em runtime, o teste é o que garante que ninguém deixe de perceber o problema.

---

### Estratégia de Testes
* **Chaves primárias / grão**: todo model tem teste `unique` + `not_null` na sua chave de grão — "padrão-ouro", mantido mesmo quando redundante com constraints upstream.
* **Testes de reconciliação (singulares)**: comparam totais agregados contra o somatório de suas partes (ex.: `assert_order_subtotal_matches_items.sql`, `assert_purchase_order_subtotal_matches_items.sql`, `assert_refund_amount_not_greater_than_item_total.sql`).
* **Testes de negócio real, não de "seguro"**: `not_null`/`accepted_values` só são adicionados onde existe risco real de dado inconsistente vindo da origem — flags booleanas derivadas 100% por `case/when` no próprio SQL (ex.: `is_effective_sale`) não recebem teste de `not_null`, já que nunca podem ser nulas por construção do código.
* **Testes por domínio inventário/financeiro**: agrupados em `tests/inventory/` e `tests/financial/`, cobrindo consistência de flags de confiança de lead time, não-negatividade de quantidades sugeridas, etc.

---

### CI/CD (Bitbucket Pipelines)
O `bitbucket-pipelines.yml` na raiz do repositório cobre três cenários:

1. **PR de qualquer branch → `develop`**: roda `sqlfluff lint` + `dbt build` completo contra um catálogo dedicado de CI (`ci_lh_nautical`), autenticado via OAuth M2M (service principal). Como a chave do Bitbucket em `pull-requests` casa pela branch de **origem** (não destino), o gatilho usa `'**'` e filtra o destino em runtime via `$BITBUCKET_PR_DESTINATION_BRANCH`. Ao final (mesmo se o build falhar), um `after-script` dropa os schemas `silver`/`gold` do catálogo de CI, mantendo só a bronze entre execuções.
2. **PR de `develop` → `main`**: valida a sintaxe do bundle Databricks (`databricks bundle validate -t prod`).
3. **Push direto em `main`**: deploy automático do bundle pra produção (`databricks bundle deploy -t prod`).

---

### Como Rodar Localmente
1. Configure `dbt_lh_nautical/.env` com `DBT_CATALOG_NAME=dev_<seu_nome>_lh_nautical` (usado pelas sources via `env_var()`).
2. Configure `~/.dbt/profiles.yml` com seu catálogo pessoal e `auth_type: oauth` (login interativo via `databricks auth login`).
3. Rode `dbt deps` (instala `dbt_utils` e `codegen`), depois `dbt build`.
4. Para lint: `sqlfluff lint models/` (usa o templater `dbt`, resolve `ref()`/`source()` automaticamente).