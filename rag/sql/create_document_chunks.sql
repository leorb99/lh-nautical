-- ============================================================================
-- Contrato de ingestão do MVP de RAG — tabela document_chunks
-- ============================================================================
--
-- Esta tabela é a INTERFACE entre a knowledge base canônica em
-- rag/knowledge_base/ e o futuro mecanismo de indexação/retrieval
-- (Databricks AI Search). Ela armazena texto e metadata de proveniência —
-- NÃO armazena embeddings/vetores.
--
-- Fontes canônicas autorizadas para esta tabela no MVP (allowlist):
--   - gold_business_rules.md
--   - inventory_and_stockout.md
--   - demand_forecasting.md
--   - metrics_dictionary.md
--   - gold_data_architecture.md
-- powerbi_semantic_rules.md está DEFERRED e não deve gerar chunks nesta fase.
--
-- NAMESPACE: schema físico FECHADO como `rag` (ver rag/sql/create_schema.sql
-- e rag/README.md — Delta Persistence Design). O catálogo permanece
-- environment-specific, resolvido em runtime via named parameter marker
-- `:catalog` combinado com a cláusula IDENTIFIER() — o mecanismo
-- oficialmente suportado e seguro contra SQL injection para parametrizar
-- identifiers (catalog/schema/table) em DDL (ver
-- https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-names-identifier-clause,
-- confirmado suportar `IDENTIFIER(:param || '.literal.literal')`). Não se
-- assume reuso do catálogo dev_<usuario>_lh_nautical do projeto
-- dbt_lh_nautical, que pertence a outra aplicação. Nenhuma interpolação de
-- string manual é usada — o valor de :catalog é vinculado em tempo de
-- execução (ex.: `spark.sql(sql_text, args={"catalog": "..."})` a partir de
-- um bootstrap Python ainda não implementado, ou `EXECUTE IMMEDIATE ...
-- USING <valor> AS catalog`); o arquivo em si nunca é editado para
-- fornecer um catálogo.
--
-- Unidade de recuperação: CHUNK. Cada linha desta tabela representa
-- exatamente um chunk recuperável, pertencente a um único documento.
-- ============================================================================

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.rag.document_chunks') (

    -- ------------------------------------------------------------------
    -- Identidade do chunk
    -- ------------------------------------------------------------------

    chunk_id STRING NOT NULL
        COMMENT 'Logicamente obrigatório. Identidade lógica única do chunk (não é UUID aleatório) — será usada futuramente como primary_key do Databricks AI Search Delta Sync Index (configuração futura, não implementada nesta etapa). Deve ser recalculável a partir de document_id + section_path + chunk_index + content_hash, de forma que uma reingestão idêntica produza o mesmo chunk_id, e uma alteração de conteúdo relevante produza um chunk_id diferente. O algoritmo exato de derivação ainda não foi implementado. Unicidade é responsabilidade lógica do pipeline de ingestão, não uma constraint física desta tabela.',

    chunk_index INT NOT NULL
        COMMENT 'Logicamente obrigatório. Índice determinístico e não-negativo (0-based: o primeiro chunk de um documento é 0) do chunk dentro do documento, na ordem em que o chunk aparece no texto fonte.',

    -- ------------------------------------------------------------------
    -- Identidade e proveniência do documento de origem
    -- ------------------------------------------------------------------

    document_id STRING NOT NULL
        COMMENT 'Logicamente obrigatório. Identificador estável do documento canônico, independente do caminho físico do arquivo. Valores do MVP: gold_business_rules, inventory_and_stockout, demand_forecasting, metrics_dictionary, gold_data_architecture (um por arquivo em rag/knowledge_base/, derivado do nome do arquivo sem extensão — escolha documentada, não o path completo).',

    document_title STRING
        COMMENT 'Logicamente obrigatório (nullability física preservada conforme estratégia atual do DDL; a invariante lógica é documentada no README, não imposta aqui como NOT NULL). Título humano do documento, extraído do heading H1 (ex.: "# Regras de Negócio da Camada Gold"). Usado para apresentação/citação, não para identidade.',

    source_path STRING NOT NULL
        COMMENT 'Logicamente obrigatório. Caminho relativo ao repositório (ex.: rag/knowledge_base/gold_business_rules.md). Nunca um path absoluto de máquina local.',

    canonical_domain STRING
        COMMENT 'Logicamente obrigatório (nullability física preservada; invariante lógica documentada no README). Domínio semântico do documento de origem. Valores do MVP: business_rules, inventory_stockout, demand_forecasting, metrics, data_architecture. Não inclui domínio Power BI nesta fase (powerbi_semantic_rules.md está deferred).',

    document_status STRING
        COMMENT 'Logicamente obrigatório (nullability física preservada; invariante lógica documentada no README). Convenção única e final do MVP: valor "CLOSED", único status válido para os cinco documentos canônicos autorizados. Não usar "FINAL", "approved", "canonical", "complete" ou qualquer outra variante.',

    document_hash STRING
        COMMENT 'Logicamente obrigatório (nullability física preservada; invariante lógica documentada no README). Hash determinístico da versão textual integral do documento fonte considerada na ingestão. Todos os chunks derivados da mesma versão do documento devem compartilhar o mesmo document_hash. Função de hash e normalização de texto ainda não definidas (decisão pendente).',

    source_git_commit STRING
        COMMENT 'Campo operacionalmente opcional: pode permanecer NULL independentemente da estrutura do documento, quando a versão efetivamente ingerida não puder ser validamente associada a um commit Git específico. SHA do commit Git do repositório, preenchido SOMENTE quando a versão efetivamente ingerida do documento estiver contida naquele commit. Deve permanecer NULL quando o arquivo estiver untracked, possuir alterações não commitadas, ou quando o conteúdo atual divergir da versão armazenada no commit — não deve ser preenchido automaticamente com o HEAD do repositório (git rev-parse HEAD) sem essa verificação. Relevante desde já: rag/ pode estar untracked nesta fase do projeto.',

    -- ------------------------------------------------------------------
    -- Localização hierárquica dentro do documento
    -- ------------------------------------------------------------------

    section_path STRING
        COMMENT 'Contextual. O heading H1 do documento representa document_title e NÃO integra section_path. section_path é preenchido quando o chunk pertence a uma seção estrutural Markdown H2 ou inferior, representando essa hierarquia (ex.: "## Regras de reposição" > "### Pedidos de compra em aberto" produz "Regras de reposição > Pedidos de compra em aberto"). Pode permanecer NULL somente para conteúdo válido que não esteja sob nenhum heading estrutural H2+, por exemplo texto introdutório localizado após o H1 e antes do primeiro H2 — nunca preencher com um heading artificial apenas para evitar NULL. Separador oficial: " > " (espaço, maior-que, espaço).',

    section_title STRING
        COMMENT 'Contextual, mesma condição de nullability de section_path. O heading estrutural imediato H2 ou inferior ao qual o chunk pertence — o último segmento de section_path. O H1 do documento (document_title) nunca é usado como section_title.',

    -- ------------------------------------------------------------------
    -- Conteúdo recuperável
    -- ------------------------------------------------------------------

    chunk_text STRING NOT NULL
        COMMENT 'Logicamente obrigatório e nunca vazio. Texto do chunk efetivamente enviado ao mecanismo de retrieval/indexação. Não contém embedding — apenas texto.',

    rule_ids ARRAY<STRING> NOT NULL
        COMMENT 'Logicamente obrigatório (o array em si nunca é NULL). Rule IDs (ex.: GOLD-020) diretamente sustentadas pelo conteúdo deste chunk. Array vazio [] representa ausência de Rule ID associada — NULL nunca é usado com esse significado. Zero, uma ou múltiplas Rule IDs são permitidas. Nunca armazenado como string delimitada por vírgula ou JSON string. Um chunk não deve carregar uma Rule ID que não esteja clara e diretamente coberta por seu próprio conteúdo.',

    -- ------------------------------------------------------------------
    -- Rastreabilidade para citação/auditoria
    -- ------------------------------------------------------------------

    line_start INT
        COMMENT 'Logicamente obrigatório (nullability física preservada; invariante lógica documentada no README). Primeira linha (1-indexed) do arquivo fonte coberta pelo chunk. Junto com line_end, permite citar e auditar a origem exata do texto.',

    line_end INT
        COMMENT 'Logicamente obrigatório (nullability física preservada; invariante lógica documentada no README). Última linha (1-indexed) do arquivo fonte coberta pelo chunk. Deve satisfazer line_start <= line_end — invariante lógica exigida do pipeline de ingestão, não uma constraint física desta tabela.',

    content_hash STRING
        COMMENT 'Logicamente obrigatório (nullability física preservada; invariante lógica documentada no README). Hash determinístico do conteúdo normalizado deste chunk especificamente (não do documento inteiro). Usado para detecção de mudança, participação na identidade de chunk_id, idempotência e depuração. Função de hash e normalização de texto ainda não definidas (decisão pendente).',

    -- ------------------------------------------------------------------
    -- Metadata operacional (não faz parte da identidade semântica do chunk)
    -- ------------------------------------------------------------------

    ingested_at TIMESTAMP
        COMMENT 'Logicamente obrigatório (nullability física preservada; invariante lógica documentada no README). Timestamp da execução de ingestão que gravou esta linha. Metadata operacional — não participa de chunk_id, content_hash ou document_hash, e não deve interferir na idempotência semântica do chunk.'
)
USING DELTA
COMMENT 'Contrato de interface entre a knowledge base canônica (rag/knowledge_base/) e o futuro mecanismo de retrieval (Databricks AI Search). Uma linha = um chunk recuperável. Não armazena embeddings/vetores. Fontes autorizadas no MVP: gold_business_rules, inventory_and_stockout, demand_forecasting, metrics_dictionary, gold_data_architecture. powerbi_semantic_rules.md está deferred.'
TBLPROPERTIES (
    'delta.enableChangeDataFeed' = 'true'
);

-- ============================================================================
-- Notas explícitas de escopo (não implementadas nesta etapa):
--
-- - Nenhuma cláusula PARTITION BY, CLUSTER BY ou Liquid Clustering foi
--   configurada. Não há evidência de necessidade concreta para o volume de
--   documentos do MVP (5 documentos canônicos); configurar particionamento
--   agora seria uma otimização prematura. Revisar quando o volume real de
--   documentos/chunks justificar a decisão.
--
-- - Nenhuma lógica de MERGE/upsert é definida NESTE ARQUIVO DDL — a
--   implementação vive em rag/src/ingestion/persistence.py (scoped Delta
--   MERGE por chunk_id, stale-delete restrito a processed_document_ids),
--   não é gerada por esta definição de schema. O contrato de idempotência
--   (mesmo documento + mesmo conteúdo + mesma estratégia de chunking ->
--   mesmo conjunto lógico de chunks) está documentado em rag/README.md.
--   O MERGE ainda não foi executado/validado contra uma tabela real.
--
-- - Nenhuma coluna de embedding/vetor foi incluída. Esta tabela é a fonte
--   textual e de metadata; a indexação vetorial é responsabilidade do
--   Databricks AI Search, gerenciada separadamente.
--
-- - Change Data Feed foi habilitado (delta.enableChangeDataFeed = 'true')
--   para preparar esta tabela como source table de um futuro Databricks AI
--   Search Delta Sync Index, permitindo sincronização incremental. Nenhum
--   endpoint ou índice de AI Search é criado nesta etapa. chunk_id está
--   documentado como a futura primary_key desse índice (configuração
--   futura do lado do AI Search, não uma PRIMARY KEY física desta tabela).
-- ============================================================================
