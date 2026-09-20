# Documentação da Interface de Usuário (UI) – Doris
A interface do chat Doris para a LH Nautical foi concebida com uma arquitetura **stateless**, o que significa que não possui lógica de conta de utilizador nem persistência de histórico em base de dados. O histórico de mensagens vive exclusivamente na memória da aba ativa; recarregar a página inicia uma sessão limpa, refletindo o comportamento independente de cada chamada ao backend.  A aplicação é composta por três arquivos principais que gerem a estrutura, o comportamento e a apresentação. 

## Estrutura Base (index.html)
O documento HTML define o esqueleto estrutural da página numa única view.
* **Cabeçalho:** Contém o logotipo da LH Nautical e atua como âncora visual no topo da página.  
* **Contêiner de Chat:** A área principal (chat), encapsulada pela classe *.chat-wrapper*, onde os balões de mensagem do utilizador e da assistente são injetados dinamicamente.  
* **Área de Interação:** Uma barra inferior fixada (*.input-area*) que aloja o formulário principal (*ask-form*). Este formulário contém o campo de entrada de texto (*query-input*), o botão de submissão (*send-btn*) e um botão para cancelar o processamento (*stop-btn*).
*   **Indicador de Status:** Uma estrutura oculta (*status-indicator*) mantida no código (HTML) para compatibilidade com as funções de manipulação de estado do JavaScript. 
  
## Lógica e Comportamento (app.js)
O motor do frontend controla todo o fluxo de requisições, renderização de estados e tratamento de respostas estruturadas.
* **Gestão de Requisições:** A comunicação com o backend é feita através do método POST para a rota /api/ask. A requisição utiliza um *AbortController*, permitindo que o utilizador cancele a geração da resposta a qualquer momento através do botão de paragem. 
*  **Tratamento de Sessão e Autenticação:** O script inspeciona os códigos HTTP de retorno e captura explicitamente os erros 401 e 403. Quando detectados, a interface instrui o utilizador a acessar à aplicação diretamente através do Databricks para validar a sessão.
*    **Motor de Renderização Universal:** A função *renderResult* consolida as respostas baseadas em RAG e em consultas SQL. Se o objeto retornar a propriedade *answer*, a resposta de sucesso é desenhada. Se faltar a resposta mas existir a propriedade *message*, a interface renderiza uma nota neutra. Falhas gerais disparam a renderização de erro.  
*    **Processamento de Referências:** O texto da resposta é analisado por uma expressão regular que identifica identificadores como [S1] e transforma-os em botões clicáveis (*.citation-chip*). Identificadores não reconhecidos no objeto de fontes recebem um estilo distinto de aviso (*.citation-chip-unknown*). 
*    **UX de Navegação:** Através da delegação de eventos, clicar num chip de citação aciona uma rolagem suave da janela inteira até ao item correspondente na seção de referências, aplicando um destaque visual que desaparece após 1,5 segundos.  
*    **Indicadores de Integridade:** Se a resposta tiver o status de integridade *INVALID*, o sistema anexa um aviso alertando que uma das referências citadas não veio diretamente da busca. 

## Estilização e Design (styles.css)
O design do chat foi construído para priorizar a legibilidade e a modernidade, garantindo o funcionamento do scroll e o posicionamento dos elementos.
* **Tema Global:** O fundo utiliza um gradiente linear fixo de cores escuras (*#021526* a *#042e54*), e a rolagem (*overflow-y: auto*) está configurada ao nível da janela principal (*body*), evitando quebras de design ao longo da leitura.  
* **Posicionamento de Mensagens:** O contêiner de mensagens é gerido via *Flexbox*. As mensagens do utilizador são alinhadas à direita com um fundo azul translúcido e as mensagens da assistente à esquerda com um estilo semitransparente mais escuro.  
* **Efeito Glassmorphism:** A barra inferior (*.input-area*) está fixada no rodapé da página (*bottom: 0*) e implementa a propriedade *backdrop-filter: blur(12px)* para desfocar o conteúdo do chat à medida que este desliza por trás da caixa de texto. A chat-wrapper aplica um enchimento inferior de 120px para garantir que a última mensagem nunca fique ocultada por esta barra.  
* **Animação de Carregamento:** O estado de processamento (*wave-container*) exibe três pontos animados (*.wave-dot*) usando a regra CSS *@keyframes wave*, que cria um movimento sequencial no eixo Y.  
* **Destaque Dinâmico:** A classe *.reference-highlight* fornece um fundo amarelado temporário às referências acionadas por clique, facilitando a correlação visual imediata.