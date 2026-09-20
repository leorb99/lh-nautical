// Frontend do chat LH Nautical.
//
// Sem lógica de conta e sem histórico persistente, de propósito: o
// /api/ask é stateless (nenhum conversation_id, cada chamada é
// independente — ver API_CONTRACT.md), então o histórico aqui vive só em
// memória, nesta aba. Recarregou a página, começou do zero — combinando
// com o próprio backend.
(function () {
  const chatEl = document.getElementById('chat');
  const formEl = document.getElementById('ask-form');
  const inputEl = document.getElementById('query-input');
  const sendBtn = document.getElementById('send-btn');
  const statusIndicatorEl = document.getElementById('status-indicator');
  const statusTextEl = document.getElementById('status-text');
  const stopBtn = document.getElementById('stop-btn');

  let controller = null; // AbortController da chamada em andamento
  let isLoading = false;

  formEl.addEventListener('submit', onSubmit);
  stopBtn.addEventListener('click', cancelRequest);

  // Delegação de clique: qualquer chip [S1]/[S2] rola até a referência
  // correspondente, esteja ela em qual mensagem estiver.
  chatEl.addEventListener('click', (event) => {
    const chip = event.target.closest('.citation-chip');
    if (!chip) return;
    const target = document.getElementById(`source-${chip.dataset.sourceLabel}`);
    if (!target) return; // label citado que não existe em sources[] — sem alvo pra rolar
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target.classList.add('reference-highlight');
    setTimeout(() => target.classList.remove('reference-highlight'), 1500);
  });

  function onSubmit(event) {
    event.preventDefault();
    if (isLoading) return; // guarda contra reenvio duplicado

    const query = inputEl.value.trim();
    if (!query) {
      inputEl.focus();
      return;
    }

    addUserMessage(query);
    inputEl.value = '';
    askBackend(query);
  }

  function addUserMessage(text) {
    const el = document.createElement('div');
    el.className = 'message message-user';
    el.textContent = text;
    chatEl.appendChild(el);
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  function askBackend(query) {
    setLoading(true);
    const assistantEl = createLoadingMessage();

    controller = new AbortController();

    fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
      signal: controller.signal,
    })
      .then(async (response) => {
        const contentType = response.headers.get('content-type') || '';
        const data = contentType.includes('application/json') ? await response.json() : null;
        if (!response.ok) {
          console.error('Falha ao chamar /api/ask', {
            status: response.status,
            statusText: response.statusText,
            body: data,
          });
          return { data, httpStatus: response.status };
        }
        return { data, httpStatus: response.status };
      })
      .then(({ data, httpStatus }) => {
        assistantEl.className = 'message message-assistant';
        if (httpStatus === 401 || httpStatus === 403) {
          renderNeutral(assistantEl, 'Sua sessão não tem acesso a este App. Abra-o pelo Databricks e tente novamente.');
          return;
        }
        renderResult(assistantEl, data);
      })
      .catch((error) => {
        assistantEl.className = 'message message-assistant';
        if (error.name === 'AbortError') {
          assistantEl.textContent = 'Pergunta cancelada.';
          return;
        }
        console.error('Falha de rede ao chamar /api/ask', error);
        renderFailure(assistantEl);
      })
      .finally(() => {
        setLoading(false);
        controller = null;
        inputEl.focus();
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
      });
  }

  // Estado de "carregando": reaproveita a animação .wave-container/.wave-dot
  // que já existe no styles.css (não era usada em lugar nenhum ainda). Quando
  // a resposta chega, o mesmo elemento vira a bolha .message-assistant.
  function createLoadingMessage() {
    const el = document.createElement('div');
    el.className = 'wave-container';
    el.innerHTML = '<span class="wave-dot"></span><span class="wave-dot"></span><span class="wave-dot"></span>';
    chatEl.appendChild(el);
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    return el;
  }

  function setLoading(loading) {
    isLoading = loading;
    inputEl.disabled = loading;
    sendBtn.disabled = loading;
    statusIndicatorEl.hidden = !loading;
  }

  function cancelRequest() {
    if (controller) controller.abort();
  }

  // A resposta é sempre um JSON completo (V1 não faz streaming — ver
  // API_CONTRACT.md), então não existe "chegando aos poucos": ou o status
  // conhecido chega certinho, ou cai no genérico de falha.
function renderResult(assistantEl, data) {
    assistantEl.innerHTML = '';

    // Resposta que não deu pra entender como JSON (ex: página de login em
    // HTML de um proxy de auth) — sem 'data' pra checar nada, é falha.
    if (!data) {
      renderFailure(assistantEl);
      return;
    }

    // 1. Se a API devolver 'answer', renderiza o sucesso (funciona para RAG e SQL)
    if (data.answer) {
      renderSuccess(assistantEl, data);
      return;
    }

    // 2. Falha real do backend não é a mesma coisa que "sem evidência" —
    //    RAG_EXECUTION_FAILED também vem com 'message', então esse check
    //    precisa vir antes do genérico abaixo, ou os dois casos ficam
    //    visualmente idênticos.
    if (data.status === 'RAG_EXECUTION_FAILED') {
      renderFailure(assistantEl);
      return;
    }

    // 3. Se não há resposta, mas existe 'message' (ex: sem dados ou não suportado)
    if (data.message) {
      renderNeutral(assistantEl, data.message);
      return;
    }

    // 4. Fallback para qualquer coisa que não bateu em nenhum caso acima
    renderFailure(assistantEl);
  }

  function renderSuccess(assistantEl, data) {
    const answerEl = document.createElement('p');
    answerEl.className = 'answer-text';
    answerEl.innerHTML = linkifyCitations(data.answer || '', data.sources || []);
    assistantEl.appendChild(answerEl);

    const integrityNote = buildIntegrityNote(data.citation_integrity);
    if (integrityNote) assistantEl.appendChild(integrityNote);

    if (data.sources && data.sources.length > 0) {
      assistantEl.appendChild(buildReferences(data.sources));
    }
  }

  // Troca marcadores [S1] / [S1, S4] no texto da resposta por chips
  // clicáveis. Só reconhece o formato Sn (o formato real de source_label);
  // qualquer outro colchete no texto fica como texto normal.
  function linkifyCitations(answerText, sources) {
    const bySourceLabel = {};
    sources.forEach((source) => {
      bySourceLabel[source.source_label] = source;
    });

    let html = escapeHtml(answerText).replace(/\n/g, '<br>');

    html = html.replace(/\[(S\d+(?:\s*,\s*S\d+)*)\]/g, (match, inner) => {
      const labels = inner.split(',').map((label) => label.trim());
      const chips = labels
        .map((label) => {
          const known = Object.prototype.hasOwnProperty.call(bySourceLabel, label);
          const cls = known ? 'citation-chip' : 'citation-chip citation-chip-unknown';
          return `<button type="button" class="${cls}" data-source-label="${label}">${label}</button>`;
        })
        .join(' ');
      return `<span class="citation-group">${chips}</span>`;
    });

    return html;
  }

  // VALID é checagem estrutural só (os labels citados existem em
  // sources[]) — não quer dizer resposta certa, então não vira um selo de
  // "verificado" na UI. NO_CITATIONS é neutro. INVALID é o único caso que
  // merece aviso visível de verdade.
  function buildIntegrityNote(citationIntegrity) {
    if (!citationIntegrity) return null;

    if (citationIntegrity.status === 'INVALID') {
      const note = document.createElement('p');
      note.className = 'integrity-note integrity-note-warning';
      note.textContent = 'Esta resposta referenciou uma fonte que não veio da busca — vale conferir manualmente.';
      return note;
    }

    if (citationIntegrity.status === 'NO_CITATIONS') {
      const note = document.createElement('p');
      note.className = 'integrity-note';
      note.textContent = 'Esta resposta não citou uma fonte específica.';
      return note;
    }

    return null; // VALID — nada a destacar
  }

  function buildReferences(sources) {
    const details = document.createElement('details');
    details.className = 'references';

    const summary = document.createElement('summary');
    summary.textContent = `Referências (${sources.length})`;
    details.appendChild(summary);

    const list = document.createElement('ol');
    sources
      .slice()
      .sort((a, b) => a.rank - b.rank)
      .forEach((source) => {
        const item = document.createElement('li');
        item.id = `source-${source.source_label}`;
        item.className = 'reference-item';

        const title = source.document_title || source.document_id;
        const path = [source.section_path, source.section_title].filter(Boolean).join(' — ');
        const rules = source.rule_ids && source.rule_ids.length ? source.rule_ids.join(', ') : '';

        item.innerHTML = `
          <strong>[${source.source_label}]</strong> ${escapeHtml(title)}
          ${path ? `<br><span class="reference-path">${escapeHtml(path)}</span>` : ''}
          ${rules ? `<br><span class="reference-rules">Regras: ${escapeHtml(rules)}</span>` : ''}
        `;
        list.appendChild(item);
      });
    details.appendChild(list);

    return details;
  }

  function renderNeutral(assistantEl, message) {
    const el = document.createElement('p');
    el.className = 'neutral-note';
    el.textContent = message;
    assistantEl.appendChild(el);
  }

  function renderFailure(assistantEl) {
    const el = document.createElement('p');
    el.className = 'failure-note';
    el.textContent = 'Algo deu errado ao processar essa pergunta. Tente novamente.';
    assistantEl.appendChild(el);
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
})();