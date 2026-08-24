# Uber Route & Coffee Recommendation Agent — como a aplicação funciona e como atende aos requisitos

Este artigo documenta a arquitetura da aplicação e mapeia, item a item, como ela atende aos
dois tópicos de desenvolvimento propostos — **PyProd** (interoperabilidade em Python puro) e
**RAG** (Retrieval-Augmented Generation com pesquisa híbrida) — incluindo todos os itens de
bônus de cada tópico.

A documentação técnica completa e o histórico de decisões estão em
[`specs/001-uber-route-coffee-agent/`](specs/001-uber-route-coffee-agent/) (spec, plano,
modelo de dados, contrato de API e `research.md`, com 22 seções de decisões e descobertas
registradas ao longo do desenvolvimento). Este artigo é o resumo voltado a avaliação.

## O que a aplicação faz

O rider informa **origem**, **destino** e **o horário que precisa chegar** (não o horário de
saída — é o prazo). A aplicação devolve três opções de viagem, calculadas de trás para frente
a partir desse prazo:

| Opção | O que é |
|---|---|
| **Sair agora** | Horário de partida "ingênuo" (chegada menos tempo de viagem estimado), sem espera |
| **Sair 30 min mais cedo** | Mesmo trajeto, saindo 30 min antes, com sugestão de um café/padaria/restaurante real por perto para esperar |
| **Sair 60 min mais cedo** | Igual, 60 min antes |

Cada opção tem sua própria tarifa estimada (via IntegratedML) e, para as duas opções com
espera, uma sugestão de lugar real (via busca híbrida RAG sobre lugares reais buscados ao
vivo no OpenStreetMap). O frontend é uma página HTML única, servida pelo próprio IRIS via
WSGI, em `http://<host>/uberapp/`.

---

## Tópico 1 — PyProd: projeto de interoperabilidade

Toda a lógica de negócio roda como uma **Production PyProd** (`production/production.py`),
declarada e carregada no IRIS via `intersystems-pyprod`, com todos os hosts escritos em
Python puro — nenhum host ObjectScript-first.

```
POST /api/uber-route/recommend (WSGI, production/wsgi/app.py)
        │
        ▼
BsUberRouteService   (Business Service, adapterless — validação de payload)
        │  send_request_sync
        ▼
BpRouteOrchestrator  (Business Process — orquestra as 3 opções, geocodificação, persistência)
        │                                   │
        │ SendRequestSync (×3, uma          │ SendRequestSync (×2, só nas opções
        │ por opção)                        │ com espera)
        ▼                                   ▼
BoIntegratedMlPredictor              BoHybridRagEngine
   (tarifa via IntegratedML/PMML)       (busca híbrida RAG sobre lugares reais)
        │                                   │
        ▼                                   ▼
        └──────────── InterSystems IRIS (relacional + JSON + Vetor) ──────────┘
```

### Requisito principal: hosts em Python puro via PyProd

Todos os quatro hosts em [`production/hosts/`](production/hosts/) estendem as classes base
do `intersystems-pyprod` (`BusinessService`, `BusinessProcess`, `BusinessOperation`) e são
carregados no IRIS pela CLI `intersystems_pyprod`, que gera e compila as classes ObjectScript
correspondentes a partir do código Python — sem nenhuma linha de ObjectScript escrita à mão
para lógica de negócio.

### 🎯 Bônus

**"Incluir pelo menos três hosts (Business Service, Business Process e Business Operation)"**
— atendido com folga: são **4 hosts**, cobrindo os três tipos:

| Host | Tipo | Arquivo | Papel |
|---|---|---|---|
| `BsUberRouteService` | Business Service | [`bs_uber_route_service.py`](production/hosts/bs_uber_route_service.py) | Recebe o payload do WSGI, valida (`FR-002`), encaminha ao Process |
| `BpRouteOrchestrator` | Business Process | [`bp_route_orchestrator.py`](production/hosts/bp_route_orchestrator.py) | Orquestra as 3 opções de viagem, geocodificação, persistência |
| `BoIntegratedMlPredictor` | Business Operation | [`bo_integratedml_predictor.py`](production/hosts/bo_integratedml_predictor.py) | Consulta o modelo `FarePredictor` (IntegratedML) por SQL |
| `BoHybridRagEngine` | Business Operation | [`bo_hybrid_rag_engine.py`](production/hosts/bo_hybrid_rag_engine.py) | Busca híbrida RAG sobre lugares para esperar |

**"Incluir um adaptador no host"** — dois adaptadores externos, isolados em módulos próprios
(`production/adapters/`) para que a lógica de orquestração nunca fale diretamente com uma API
externa:

- [`geocoding_adapter.py`](production/adapters/geocoding_adapter.py) — resolve texto livre
  (endereço) em coordenadas via **Nominatim** (OpenStreetMap), usado por `BpRouteOrchestrator`.
- [`overpass_adapter.py`](production/adapters/overpass_adapter.py) — busca lugares reais
  (cafés, padarias, restaurantes, coworkings) perto de uma coordenada via **Overpass API**
  (OpenStreetMap), usado por `BoHybridRagEngine`.

Ambos: sem chave de API, com rate-limiting próprio, e projetados para nunca derrubar a
requisição principal caso a API externa falhe (degradam para "localização não encontrada" ou
"nenhum lugar disponível", nunca para uma exceção não tratada).

**"Usar Business Rules no projeto"** — [`business_rules.py`](production/hosts/business_rules.py)
isola a regra de negócio original do projeto (o gatilho de 30 minutos) como uma função pura,
independentemente testável — desenhada deliberadamente para poder ser trocada depois por uma
regra formal `Ens.Rule.RuleSet` do IRIS sem tocar na orquestração (documentado em
`research.md §8`: autorar uma Rule XML às cegas, sem o Rule Editor interativo disponível no
ambiente, arriscava produzir uma regra sintaticamente válida mas incorreta).

**"Usar o protocolo WSGI para aplicações web"** —
[`production/wsgi/app.py`](production/wsgi/app.py) é uma **aplicação WSGI nativa do IRIS**
(recurso disponível desde IRIS 2024.2), registrada como Web Application (`/uberapp`) na
própria instância — sem servidor de aplicação externo (gunicorn/uwsgi). Ela serve tanto o
frontend (`GET /`) quanto a API (`POST /api/uber-route/recommend`), injetando cada requisição
diretamente na produção via `director.create_business_service(...).process_input(...)`.

**"Usar métricas de monitoramento e/ou telemetria"** —
[`production/observability/telemetry.py`](production/observability/telemetry.py) fornece
`log_event()` (grava cada evento no log de eventos do IRIS via `IRISLog`) e o context manager
`timed_event` (mede duração e captura erros automaticamente). Todo host chama isso nos pontos
-chave: requisição recebida, chamada ao IntegratedML, chamada ao RAG, resultado da regra de
negócio, persistência, erros — com contadores e histogramas em memória (`get_metrics_snapshot()`)
prontos para um exportador compatível com OpenTelemetry. Os eventos ficam consultáveis
diretamente via SQL (`Ens_Util.Log`), o que inclusive foi a ferramenta de diagnóstico usada
ao longo do desenvolvimento para achar bugs reais em produção (ver seção de descobertas
abaixo).

**"Usar InterSystems IntegratedML"** — o modelo `FarePredictor` é definido e consultado via
SQL do IntegratedML (`CREATE MODEL` / `TRAIN MODEL` / `PREDICT`), consultado por
`BoIntegratedMlPredictor` via SQL embutido em Python, sem nenhum serviço externo de
model-serving. Detalhe técnico relevante: a imagem Community Edition testada não tinha
provedor de AutoML disponível (`TRAIN MODEL` falhava), então o modelo foi treinado **fora**
do IRIS (scikit-learn, sobre os mesmos dados históricos) e **importado como PMML** pelo
provedor nativo `%ML.PMML.Provider` do IntegratedML — que não precisa de nenhum AutoML, é
justamente o mecanismo documentado do IntegratedML para trazer um modelo pronto. A consulta
em produção continua 100% IntegratedML padrão (`SELECT PREDICT(FarePredictor) ...`).
Documentado em detalhe em `research.md §16`.

---

## Tópico 2 — RAG: pesquisa híbrida com dados reais

O caso de uso de RAG do projeto é a sugestão de **onde esperar** quando compensa sair mais
cedo. O sistema recupera lugares reais (cafés, padarias, restaurantes) próximos da origem do
rider, ranqueia por relevância híbrida (vetor + palavra-chave) e monta uma resposta
estruturada que **fundamenta a escolha** com uma explicação — por que aquele lugar e não os
outros candidatos (distância, sinal mais forte, avaliação, quantos outros foram superados).

### Componentes do pipeline, de ponta a ponta

| Etapa | Onde | O que faz |
|---|---|---|
| **Ingestão** | [`ingestion/load_waiting_places.py`](ingestion/load_waiting_places.py) + [`overpass_adapter.py`](production/adapters/overpass_adapter.py) | Duas fontes: um dataset semente (`data/waiting_places_seed.json`, carregado offline) e busca **ao vivo** na Overpass API por qualquer coordenada, a cada requisição |
| **Chunking** | `_chunk_description()` em `load_waiting_places.py` | Baseado em sentenças, blocos de 256–512 "tokens" (proxy: palavras separadas por espaço) com overlap de 50 tokens; o cabeçalho (nome + endereço + categoria) é reanexado a **todo** chunk, para nunca perder o contexto de "onde" e "que tipo de lugar" numa correspondência parcial |
| **Indexação vetorial** | `sql/002_vector_index.sql` + `BoHybridRagEngine._embed()` | Embedding local (sem API externa) inserido como `VECTOR(DOUBLE, 384)` nativo do IRIS, com índice `HNSW(Distance='Cosine')` |
| **Recuperação** | `BoHybridRagEngine._search()` | Busca híbrida: `VECTOR_COSINE` (semântica) + `%FIND search_index(...)` do iFind (palavra-chave exata), combinadas com peso 0.6/0.4, filtradas por raio de proximidade da origem |
| **"Prompt" / fundamentação** | `BoHybridRagEngine._rationale()` | Monta a explicação estruturada a partir das evidências recuperadas (distância, sinal vencedor, nota, quantos concorrentes foram superados) — é aqui que a recuperação **fundamenta** a resposta final |
| **Geração da resposta** | `BpRouteOrchestrator` → `production/wsgi/app.py` | Resposta final montada e devolvida em JSON/HTML — determinística e auditável, não uma chamada a um LLM de terceiros (ver nota de transparência abaixo) |

> **Nota de transparência**: a etapa de "geração" deste RAG não é uma chamada a um LLM
> generativo — é a montagem estruturada de uma explicação a partir da evidência recuperada
> (rationale) mais o resultado do modelo preditivo (IntegratedML). Isso foi uma escolha
> deliberada de arquitetura (evitar dependência de uma API de LLM paga/externa numa imagem
> Community Edition), mas mantém integralmente a característica central de RAG pedida no
> enunciado: a resposta é **fundamentada em dados recuperados do banco**, não inventada.

### Requisito principal: RAG com Pesquisa Híbrida

Atendido em [`BoHybridRagEngine._search()`](production/hosts/bo_hybrid_rag_engine.py) —
combina explicitamente busca vetorial (`VECTOR_COSINE`) e busca por palavra-chave (iFind),
nunca uma sozinha, com peso configurável (`_VECTOR_WEIGHT = 0.6`, `_KEYWORD_WEIGHT = 0.4`) —
exigência não-negociável da constituição do projeto (Princípio III).

### 🎯 Bônus

**"Foreign Table"** — [`sql/003_foreign_tables.sql`](sql/003_foreign_tables.sql) mapeia
`UberRoute.TrafficWeatherReference` (fator de congestionamento por hora/dia da semana) a
partir de um CSV externo via `CREATE FOREIGN SERVER ... FOREIGN DATA WRAPPER CSV` +
`CREATE FOREIGN TABLE`, consultado por `BpRouteOrchestrator._congestion_factor()` para
ajustar a estimativa de tempo de viagem. Há um *fallback* nativo
([`003b_foreign_tables_fallback.sql`](sql/003b_foreign_tables_fallback.sql)) para versões do
IRIS onde Foreign Tables não estão disponíveis.

**"Dados multimodelo"** — as mesmas requisições passam por três modelos de dados diferentes,
todos na mesma instância IRIS (Princípio II da constituição — nenhum banco externo):

- **Relacional**: `UberRoute.TripRequest`, `RouteRecommendation`, `TripHistory`,
  `WaitingPlace` (tabelas SQL tradicionais).
- **Documento JSON**: `UberRoute.RequestLog.Payload` — cada requisição e sua resposta
  completa (as 3 opções) gravadas como JSON, consultável via funções JSON do IRIS.
- **Vetor**: `UberRoute.WaitingPlace.Embedding` — `VECTOR(DOUBLE, 384)` nativo, com índice
  HNSW.

**"Aplicar pesquisa híbrida"** — ver seção acima (requisito principal).

**"Incluir acesso a uma API pública"** — duas, ambas gratuitas e sem chave:
**Nominatim** (geocodificação de endereço → coordenadas) e **Overpass API** (busca de lugares
reais do OpenStreetMap perto de qualquer coordenada). A escolha da Overpass em vez da API do
Google Places foi deliberada: o Google exige conta paga com chave de API, indisponível neste
ambiente; a Overpass é gratuita, sem chave, e usa a mesma base de dados (OpenStreetMap) já
usada para geocodificação — mantendo a busca híbrida real (vetor+palavra-chave) exigida pela
constituição, em vez de simplesmente repassar o resultado de uma API de terceiros.

**"Análise e explicação sobre estratégia de chunking e escolha do algoritmo de embedding"**:

- **Chunking**: baseado em sentenças, blocos-alvo de 256–512 tokens (proxy: palavras), com
  50 tokens de sobreposição entre blocos consecutivos. O cabeçalho (nome + endereço +
  categoria) é reanexado a **todo** chunk — decisão deliberada para que uma correspondência
  parcial numa descrição longa nunca perca o contexto de "onde fica" e "que tipo de lugar é".
  Na prática, a maioria das descrições de lugares é curta (um parágrafo) e cabe inteira em um
  único chunk; o particionamento só entra em ação para descrições excepcionalmente longas.
- **Embedding**: `sentence-transformers/all-MiniLM-L6-v2`, rodado **localmente** (sem chamada
  a API externa, sem custo por requisição), 384 dimensões. Escolhido especificamente para
  evitar dependência de rede/chave de API numa demo Community Edition (alternativa
  considerada e descartada: `text-embedding-3-small` da OpenAI). Descoberta técnica real
  encontrada em produção: o backend padrão do `sentence-transformers` (PyTorch) causava
  **segfault** consistente no processo Python embutido do IRIS durante o cálculo do
  embedding — corrigido carregando o modelo com `backend="onnx"` (ONNX Runtime), que usa um
  modelo de threading/extensões-C diferente e não reproduz o problema. Detalhado em
  `research.md §22`.

**"Clareza sobre componentes: ingestão, chunking, indexação vetorial, recuperação, prompt e
geração da resposta"** — ver a tabela do pipeline no início desta seção.

---

## Decisões e descobertas técnicas relevantes

O desenvolvimento deste projeto envolveu depuração extensa contra uma instância real do IRIS
Community Edition rodando em Docker — não apenas testes com mocks. Isso gerou 22 seções de
decisões e descobertas documentadas em
[`research.md`](specs/001-uber-route-coffee-agent/research.md), incluindo vários bugs de
plataforma genuínos encontrados e corrigidos (não hipotéticos):

- Nomes de classe com `_` sendo truncados silenciosamente na compilação (IRIS 2026.1) —
  corrigido renomeando todas as classes para PascalCase sem caracteres especiais.
- `TRAIN MODEL` (AutoML) sofrendo segfault nessa imagem — contornado importando um modelo
  PMML treinado externamente.
- `PREDICT(model USING (...))` não é sintaxe válida do IntegratedML — a forma correta é
  `PREDICT(model)`, casando colunas por nome.
- O objeto retornado por `service.process_input()` usa propriedades PascalCase (não
  snake_case), e `director.create_business_service()` espera o nome do item de configuração,
  não o nome completo da classe.
- `VECTOR_COSINE(...)` retorna uma string numérica ObjectScript, não um `float` Python —
  precisa de conversão explícita antes de usar em aritmética.
- O backend padrão (PyTorch) do `sentence-transformers` derrubava o processo Python embutido
  do IRIS com segfault — corrigido usando o backend ONNX.

Essas descobertas — e como foram diagnosticadas (a maioria via consulta SQL direta ao log de
eventos do IRIS, `Ens_Util.Log`, e ao log bruto do processo, `messages.log`) — estão todas
registradas com o passo a passo de investigação em `research.md`, para reprodutibilidade.

## Como testar

```bash
pip install -r production/requirements.txt
pytest tests/
```

Testes locais (unitários, integração e contrato) rodam sem precisar de uma instância IRIS —
as chamadas ao IRIS/pyprod são mockadas. Para o passo a passo de implantação e validação
contra uma instância real do IRIS, ver
[`specs/001-uber-route-coffee-agent/quickstart.md`](specs/001-uber-route-coffee-agent/quickstart.md).
Frontend acessível em `http://<host>:<porta>/uberapp/` (autenticação HTTP Basic).
