# Feature Specification: Aba de Ingestão de Livros

**Feature Branch**: `001-book-ingestion-tab`

**Created**: 2026-09-27

**Status**: Draft

**Input**: User description: "Faça uma nova aba no frontend para ingestão de novos livros. Essa ingestão deverá persistir entre as execuções, sendo os novos livros colocados na pasta data/ já no embedding, igual já está atualmente. Os formatos devem ser variados, desde pdf, txt, docx, epub, md, etc."

## Clarifications

### Session 2026-09-27

- Q: Quando o acervo for reconstruído do zero pelo pipeline de linha de comando, os livros adicionados pela aba devem ser reprocessados junto? → A: Sim. O arquivo original enviado vai para a pasta de livros de origem (`books/`), a mesma que o pipeline lê, e entra em qualquer rebuild como os demais.
- Q: A aba deve permitir remover do acervo um livro que foi adicionado por engano? → A: Não. Remoção fica fora do escopo. Um livro enviado por engano só sai do acervo por intervenção manual (apagar o original em `books/` e reconstruir o acervo).
- Q: O usuário deve poder informar o título do livro no momento do envio, ou o título vem sempre da detecção automática? → A: Sempre automático, como no acervo atual. Não há campo de título no envio.

### Session 2026-09-27 (durante a implementação)

- Pedido do usuário: enviar todos os arquivos de uma pasta pelo frontend → FR-028.
- Pedido do usuário: botão de cancelar durante o envio ou a conversão → FR-029, FR-030.
- Pedido do usuário: sem limite de tamanho por arquivo → FR-004 e Assumptions.
- Achado no teste com o Modernist Cuisine vol. 1: o descarte pela regra de "tomo de referência" era mostrado como "não parece ser de gastronomia" → motivo próprio `reference_tome` (FR-007).
- Relato do usuário ("receita completa de entremet" sem resposta, com as fontes presentes): fora do escopo original, corrigido na busca (híbrida: semântica + termos raros) e no prompt do agente (montar a resposta a partir de vários trechos).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Adicionar um livro à biblioteca e consultá-lo (Priority: P1)

O dono da biblioteca tem um livro de gastronomia novo (por exemplo, um PDF ou EPUB) e quer que ele passe a fazer parte do acervo consultável. Ele abre a nova aba "Adicionar livros", envia o arquivo, acompanha o processamento e, ao final, faz uma pergunta na aba de consulta e recebe trechos desse livro citados como fonte, exatamente como acontece com os livros que já estão no acervo.

**Why this priority**: É o valor central da funcionalidade. Hoje, para acrescentar um livro, é preciso converter o arquivo manualmente para Markdown e rodar o pipeline de indexação na linha de comando. Sem este fluxo, nada mais na feature tem utilidade.

**Independent Test**: Enviar um único livro de culinária em um formato suportado, esperar o status "concluído" e fazer uma pergunta cujo assunto só existe nesse livro. A resposta deve citar o livro novo como fonte.

**Acceptance Scenarios**:

1. **Given** a biblioteca está carregada e o livro ainda não está no acervo, **When** o usuário envia um arquivo PDF com texto selecionável de um livro de receitas, **Then** o sistema mostra o progresso do processamento e, ao terminar, marca o livro como "adicionado" com título e número de trechos indexados.
2. **Given** um livro acabou de ser adicionado, **When** o usuário faz na aba de consulta uma pergunta sobre uma receita que só existe nesse livro, **Then** a resposta cita esse livro como fonte.
3. **Given** um livro acabou de ser adicionado, **When** o usuário consulta os indicadores e filtros da biblioteca (total de livros, regiões, tipos de prato), **Then** os números e as opções de filtro já refletem o livro novo.
4. **Given** um livro está sendo processado, **When** outro usuário (ou outra aba do navegador) faz perguntas na aba de consulta, **Then** as perguntas continuam sendo respondidas normalmente com o acervo existente.

---

### User Story 2 - Os livros adicionados sobrevivem a reinícios (Priority: P1)

Depois de adicionar livros, o usuário desliga a aplicação (ou o container é reiniciado ou reconstruído). Quando a aplicação sobe de novo, os livros adicionados continuam no acervo, sem precisar enviá-los ou processá-los outra vez.

**Why this priority**: O usuário pediu explicitamente que a ingestão persista entre as execuções. Um livro que some no próximo reinício não entrega o valor da História 1.

**Independent Test**: Adicionar um livro, reiniciar a aplicação por completo e confirmar que o livro aparece no acervo e continua sendo encontrado nas consultas.

**Acceptance Scenarios**:

1. **Given** um livro foi adicionado com sucesso, **When** a aplicação é parada e iniciada de novo, **Then** o livro continua consultável e aparece na contagem de livros.
2. **Given** um livro foi adicionado com sucesso, **When** a imagem da aplicação é reconstruída do zero, **Then** o livro continua consultável, porque os dados ficam na pasta de dados da biblioteca e não dentro da imagem.
3. **Given** a aplicação foi interrompida no meio do processamento de um livro, **When** ela sobe de novo, **Then** o acervo que já existia está íntegro e consultável, e aquele livro aparece como "falhou/interrompido", podendo ser enviado outra vez.

---

### User Story 3 - Enviar livros em vários formatos (Priority: P2)

O usuário tem livros em formatos diferentes: PDF, EPUB, DOCX, TXT, Markdown e outros formatos comuns de documento. Ele envia qualquer um deles pela mesma aba, sem converter nada antes.

**Why this priority**: A variedade de formatos foi pedida explicitamente e é o que elimina a conversão manual. Ainda assim, a História 1 já entrega valor com um único formato, então esta história vem depois.

**Independent Test**: Enviar um livro curto em cada formato suportado e confirmar que todos chegam a "adicionado" e são encontrados nas consultas.

**Acceptance Scenarios**:

1. **Given** o usuário tem arquivos em PDF, EPUB, DOCX, TXT e MD, **When** envia cada um, **Then** todos são processados e ficam consultáveis.
2. **Given** o usuário tenta enviar um formato não suportado (por exemplo, um arquivo de imagem ou de planilha), **When** seleciona o arquivo, **Then** o sistema recusa o arquivo antes do envio, com uma mensagem que lista os formatos aceitos.
3. **Given** o seletor de arquivos está aberto, **When** o usuário olha as opções, **Then** os formatos aceitos estão visíveis na aba.

---

### User Story 4 - Enviar vários livros de uma vez e entender o resultado de cada um (Priority: P2)

O usuário seleciona ou arrasta vários arquivos de uma vez. A aba mostra uma lista com o estado de cada livro (na fila, processando, adicionado, descartado ou com erro) e o motivo sempre que um livro não entra no acervo.

**Why this priority**: Quem tem uma biblioteca grande vai querer adicionar em lote. Saber por que um livro foi recusado evita tentativas repetidas às cegas.

**Independent Test**: Enviar um lote misto (um livro válido, uma duplicata de um livro já existente, um livro que não é de gastronomia e um arquivo corrompido) e verificar que cada um termina no estado certo, com motivo legível.

**Acceptance Scenarios**:

1. **Given** o usuário envia 5 arquivos de uma vez, **When** o processamento começa, **Then** cada arquivo aparece na lista com seu próprio estado, atualizado sem precisar recarregar a página.
2. **Given** um dos arquivos tem o mesmo conteúdo de um livro já presente no acervo, **When** ele é processado, **Then** é marcado como "já existe no acervo" e nada é duplicado.
3. **Given** um dos arquivos não é um livro de gastronomia, **When** ele é processado, **Then** é marcado como "descartado: conteúdo não culinário", pelo mesmo critério já usado para o acervo atual.
4. **Given** um dos arquivos está corrompido ou não tem texto extraível, **When** ele é processado, **Then** é marcado como "erro", com uma mensagem compreensível, e os demais arquivos do lote seguem normalmente.
5. **Given** um lote está sendo processado, **When** o usuário sai da aba e volta mais tarde, **Then** vê o estado atualizado de cada arquivo; o processamento não depende da aba estar aberta.

---

### User Story 5 - Ver o histórico dos livros adicionados (Priority: P3)

O usuário abre a aba de ingestão e vê uma lista dos livros que já adicionou por ela, com data, título, formato original, número de trechos e resultado, para saber o que já entrou no acervo.

**Why this priority**: É útil para conferência, mas a funcionalidade entrega valor mesmo sem essa lista.

**Independent Test**: Adicionar dois livros, reiniciar a aplicação e confirmar que ambos aparecem no histórico com os dados corretos.

**Acceptance Scenarios**:

1. **Given** livros foram adicionados em sessões anteriores, **When** o usuário abre a aba de ingestão, **Then** vê o histórico com data, título, formato e resultado de cada envio.

---

### Edge Cases

- **Livro duplicado**: um arquivo com conteúdo idêntico a um livro já existente, inclusive em outro formato do mesmo conteúdo textual, não gera trechos duplicados e é reportado como "já existe".
- **Mesmo nome, conteúdo diferente**: dois livros diferentes com o mesmo nome de arquivo convivem no acervo, sem que um sobrescreva o outro.
- **PDF escaneado sem camada de texto**: por padrão, o arquivo é recusado com a mensagem "sem texto extraível". A mensagem vem acompanhada da ação "tentar com reconhecimento de texto (OCR)", que avisa que o processamento será bem mais lento e que a qualidade depende da digitalização.
- **PDF parcialmente escaneado**: se uma parte relevante das páginas (20% ou mais) não tiver texto selecionável, o livro para em "sem texto extraível" antes de ser indexado. O usuário escolhe entre "tentar com OCR", que processa só as páginas sem texto, e "continuar sem OCR". Abaixo de 20%, o livro é processado direto e o resultado informa quantas páginas ficaram sem texto. A decisão acontece antes de indexar porque oferecer OCR depois exigiria substituir trechos, e remover ou editar livros está fora do escopo.
- **Texto extraído com muito lixo**: como nos livros atuais, trechos ilegíveis (OCR ruim, caracteres embaralhados) são descartados individualmente; se sobrar pouco ou nada, o livro é reportado como descartado, com o motivo.
- **Arquivo muito grande**: não há limite de tamanho. O envio mostra o progresso e pode ser cancelado a qualquer momento.
- **Pasta com arquivos de outros tipos**: arquivos ocultos são ignorados sem aviso; arquivos de formato não suportado são ignorados e contados numa mensagem única ("N arquivos da pasta ignorados"), sem uma linha de erro por arquivo.
- **EPUB/DOCX protegido por DRM ou senha**: o arquivo é recusado com a mensagem "arquivo protegido; não é possível extrair o texto".
- **Livro em outro idioma**: livros em inglês, francês, espanhol e italiano são aceitos e marcados com o idioma detectado, como já acontece no acervo atual.
- **Interrupção no meio do processamento**: o acervo existente nunca fica corrompido nem pela metade; o livro interrompido é marcado como falho e pode ser reenviado.
- **Dois lotes ao mesmo tempo**: envios simultâneos entram numa única fila e são processados sem se atrapalhar e sem corromper o acervo.
- **Serviço externo indisponível**: se o serviço usado para classificar ou gerar embeddings estiver fora do ar ou sem crédito, o livro fica em "erro", com a mensagem correspondente, e pode ser reprocessado depois sem ser reenviado.

## Requirements *(mandatory)*

### Functional Requirements

**Aba e envio**

- **FR-001**: O frontend MUST ter uma nova aba, "Adicionar livros", separada da aba de consulta atual, com navegação entre as duas.
- **FR-002**: Usuários MUST poder enviar um ou mais arquivos por seleção de arquivo e por arrastar-e-soltar.
- **FR-003**: O sistema MUST aceitar, no mínimo, os formatos PDF (com texto selecionável), EPUB, DOCX, TXT e MD, e SHOULD aceitar também HTML, RTF e ODT.
- **FR-004**: O sistema MUST recusar, antes do envio, arquivos de formato não suportado, informando o motivo e os formatos aceitos. Não há limite de tamanho por arquivo.
- **FR-005**: A aba de ingestão MUST ficar disponível a qualquer pessoa que acesse a aplicação, sem login, senha ou chave. A proteção vem de a aplicação rodar localmente (ver Assumptions).

**Processamento**

- **FR-006**: O sistema MUST extrair o texto de cada formato suportado e submetê-lo ao mesmo tratamento dos livros atuais: limpeza, classificação culinário/não culinário, divisão em trechos, extração de metadados (título, região, tipos de prato, tags, idioma) e geração de embeddings.
- **FR-007**: O sistema MUST descartar livros não culinários, obras de referência extensas (estilo enciclopédia) e trechos ilegíveis, pelos mesmos critérios já usados no acervo atual, informando o motivo específico ao usuário.
- **FR-008**: O sistema MUST detectar livros cujo conteúdo já esteja no acervo e não duplicá-los.
- **FR-009**: O sistema MUST acrescentar o livro novo ao acervo existente sem reprocessar os livros que já estão lá.
- **FR-010**: O processamento MUST rodar em segundo plano: independe da aba estar aberta e não bloqueia as consultas ao acervo.
- **FR-011**: Um livro recém-adicionado MUST ficar consultável, e refletido nos indicadores e filtros da biblioteca, sem reiniciar a aplicação.
- **FR-012**: Envios simultâneos MUST ser enfileirados e processados de forma que o acervo nunca fique inconsistente.

**Estado e feedback**

- **FR-013**: O sistema MUST mostrar, para cada arquivo enviado, um destes estados: na fila, processando, adicionado, já existe, descartado, sem texto extraível, erro ou cancelado. Para os quatro últimos, MUST mostrar também o motivo em linguagem simples.
- **FR-014**: Ao concluir, o sistema MUST mostrar o título detectado, o idioma e o número de trechos indexados do livro.
- **FR-028**: Usuários MUST poder enviar todos os arquivos de uma pasta, incluindo subpastas, por seleção de pasta e por arrastar-e-soltar.
- **FR-029**: Durante o envio, o sistema MUST mostrar o progresso do arquivo atual (N de M, percentual) e oferecer "Cancelar envio", que interrompe o arquivo em curso e não envia os restantes.
- **FR-030**: Livros na fila, em processamento ou parados em "sem texto extraível" MUST poder ser cancelados. Um livro em processamento para na próxima etapa. A partir da gravação no acervo o cancelamento não é mais aceito, para não deixar o acervo pela metade. Um livro cancelado não deixa nada no acervo e tem o arquivo temporário apagado.
- **FR-027**: O título do livro MUST ser sempre detectado automaticamente, pelo mesmo critério do acervo atual. O envio MUST NOT pedir nem aceitar um título informado pelo usuário.
- **FR-015**: Livros que terminaram em erro por falha temporária (por exemplo, serviço externo indisponível) MUST poder ser reprocessados sem novo envio.

**PDFs escaneados**

- **FR-021**: O sistema MUST detectar PDFs sem texto extraível e recusá-los por padrão, com o estado "sem texto extraível".
- **FR-022**: Para esses PDFs, o usuário MUST poder pedir "tentar com OCR" sem reenviar o arquivo. O livro volta então para a fila e é processado com reconhecimento de texto.
- **FR-023**: Antes de confirmar o OCR, o sistema MUST avisar que o processamento é mais lento e que a qualidade depende da digitalização. Durante o OCR, MUST mostrar o progresso por página.
- **FR-024**: O texto obtido por OCR MUST passar pelos mesmos filtros de qualidade dos demais livros (FR-007): trechos ilegíveis são descartados e, se sobrar pouco, o livro é reportado como descartado.

**Persistência**

- **FR-016**: Os livros adicionados, seus trechos, embeddings e metadados MUST ser gravados na pasta de dados da biblioteca (`data/`), junto do acervo atual e no mesmo formato, e continuar disponíveis após reinício ou reconstrução da aplicação.
- **FR-017**: O sistema MUST guardar o arquivo original enviado, no formato em que chegou, na pasta de livros de origem (`books/`), a mesma que o pipeline de indexação lê. Essa pasta MUST persistir entre reinícios e reconstruções da aplicação, como `data/`.
- **FR-025**: Uma reconstrução completa do acervo pelo pipeline de linha de comando MUST reprocessar também os livros enviados pela aba, em qualquer formato suportado, com o mesmo resultado da ingestão pela aba. Nenhum livro adicionado pode sumir do acervo por causa de um rebuild.
- **FR-026**: Arquivos colocados diretamente em `books/`, sem passar pela aba, MUST ser tratados pelo pipeline de linha de comando da mesma forma que os enviados pela aba, sem exigir conversão prévia para Markdown.
- **FR-018**: Uma interrupção durante o processamento MUST NOT corromper nem remover o acervo existente.
- **FR-019**: O sistema MUST manter um histórico persistente dos envios, com data, nome do arquivo, formato, título detectado, resultado e número de trechos.

**Idioma da interface**

- **FR-020**: Os textos da nova aba MUST seguir o seletor de idioma PT/EN que a interface já tem.

### Key Entities

- **Livro enviado (Envio)**: um arquivo mandado pelo usuário. Atributos: nome original, formato, tamanho, data do envio, identificador de conteúdo (para detectar duplicatas), estado atual, motivo (quando não adicionado) e o livro resultante (quando adicionado).
- **Livro do acervo**: um livro consultável. Atributos já existentes: título, idioma, região, tipos de prato, tags, perfil (receitas, técnica, história etc.) e número de trechos. Passa a registrar também a origem: acervo original ou adicionado pela aba, com a data.
- **Trecho**: parte de um livro, com seu embedding, usada na busca. Mesma estrutura dos trechos atuais.
- **Fila de processamento**: a ordem dos envios aguardando ou em processamento. O estado de cada envio sobrevive a reinícios.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Um livro de receitas típico (cerca de 300 páginas, com texto selecionável) fica consultável em até 5 minutos após o envio.
- **SC-002**: O tempo para adicionar um livro não depende do tamanho do acervo existente; adicionar 1 livro a um acervo de ~750 livros leva o mesmo tempo que adicioná-lo a um acervo vazio, com variação de no máximo 20%.
- **SC-003**: Em 100% dos casos testados, livros adicionados continuam consultáveis depois de reiniciar ou reconstruir a aplicação.
- **SC-004**: Durante a ingestão, as consultas ao acervo continuam sendo respondidas, e nenhuma falha por causa da ingestão em andamento.
- **SC-005**: Em 100% dos casos, arquivos com conteúdo idêntico a um livro existente são identificados como duplicatas e não geram trechos repetidos.
- **SC-006**: O usuário consegue adicionar um livro sem usar linha de comando nem converter o arquivo antes, em até 3 interações (abrir a aba, escolher o arquivo, confirmar).
- **SC-007**: Todo arquivo que não entra no acervo tem um motivo exibido que o usuário consegue entender sem consultar logs.
- **SC-008**: Os formatos PDF, EPUB, DOCX, TXT e MD são aceitos e resultam em livros consultáveis, sem conversão prévia.
- **SC-009**: Um livro escaneado de boa qualidade, com cerca de 300 páginas, fica consultável via OCR em até 30 minutos após o usuário pedir o reprocessamento. As receitas aparecem nas consultas com título e ingredientes legíveis.

## Assumptions

- A aplicação continua sendo um acervo pessoal de um único dono. Não há contas de usuário nem acervos separados por pessoa.
- A aplicação roda localmente (ou em rede de confiança), então a aba de ingestão fica aberta sem autenticação. Publicar a aplicação na internet exigiria rever esta decisão, porque qualquer visitante poderia alterar o acervo e consumir os créditos dos serviços de linguagem.
- A pasta `data/` continua montada como volume fora da imagem, como hoje. A pasta `books/` passa a ser montada da mesma forma, para que os originais enviados sobrevivam a reinícios e reconstruções.
- Livros adicionados passam pelas mesmas regras de qualidade e classificação do acervo atual. O usuário não pode forçar a entrada de um livro descartado como não culinário.
- A extração de metadados pode usar o mesmo serviço de linguagem já configurado para o acervo. Se nenhum estiver disponível, os metadados vêm só de heurística, como já acontece no modo sem LLM do pipeline atual.
- Não há limite de tamanho por arquivo, por decisão do usuário (2026-09-27). O valor inicial de 200 MB não bastou para livros ilustrados reais: o Modernist Cuisine vol. 1 tem 314 MB.
- Remover ou editar livros já presentes no acervo, inclusive os adicionados pela aba, está fora do escopo desta feature. Um envio feito por engano é desfeito manualmente, apagando o original em `books/` e reconstruindo o acervo pelo pipeline de linha de comando.
- Os livros do acervo original (os ~750 atuais) não precisam aparecer no histórico da aba. O histórico cobre só os envios feitos por ela.
- A interface segue o design visual já existente na aplicação.
