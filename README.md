# DOMA Dashboard

Dashboard local de marketing orgânico para a DOMA (dentalofficemanagers.com).
Sem tráfego pago: foco em SEO (Google Search Console), blog/site (GA4), leads
(GoHighLevel) e redes sociais orgânicas (Facebook + Instagram). Segue o mesmo
padrão arquitetural do projeto irmão (PreSubs): app FastAPI + SQLite local,
scripts de sync isolados por fonte, automação diária que publica um site
estático no GitHub Pages.

Site publicado: https://lucasroarts-rgb.github.io/DOMA-Dashboard/

## Estrutura

```
app.py                     FastAPI + SQLite + funções de resumo (fonte única de verdade)
scripts/
  env_utils.py              leitor de .env compartilhado
  sitemap_utils.py           leitor do sitemap XML (Yoast/WordPress) compartilhado
  sync_gsc.py                Search Console -> performance, países, dispositivo, gap de conteúdo, indexação
  sync_ga4.py                GA4 -> tráfego, países, dispositivo, gênero/idade, posts recentes
  sync_ghl.py                GoHighLevel -> leads por dia/fonte
  sync_meta_organic.py       Facebook Page + Instagram Business -> seguidores, posts, demografia
  sync_seo_audit.py          Auditoria on-page (título, meta, H1, alt text, schema) via crawl direto
  import_email_stats.py      Importador manual de CSV (campanhas de email do GHL)
  generate_public_site.py    gera docs/ (site estático) a partir das mesmas funções do app.py
  daily_sync.py              orquestra os 5 syncs + gera site + publica no git
static/                     HTML/CSS/JS do dashboard (sem framework pesado)
docs/                       saída estática publicada no GitHub Pages (gerada, não editar à mão)
data/                       doma.db (SQLite), git-ignorado
```

## Setup

1. `python -m venv .venv` e ative o ambiente.
2. `pip install -r requirements.txt`
3. `copy .env.example .env` e preencha (ver seções abaixo).
4. Coloque o JSON da service account do Google Cloud na raiz do projeto
   (nome padrão: `google_service_account.json` — git-ignorado).
5. `python app.py` (ou `RUN_DASHBOARD.bat`) sobe o dashboard local em
   `http://127.0.0.1:8000`.

## Sincronizar dados manualmente

```
python scripts/sync_gsc.py
python scripts/sync_ga4.py
python scripts/sync_ghl.py
python scripts/sync_meta_organic.py
python scripts/sync_seo_audit.py
```

Cada script é isolado: se uma fonte falhar (credencial errada, API fora do
ar), as outras continuam funcionando. Falhas ficam registradas na tabela
`sync_log` e aparecem no dashboard, aba "Overview" > "Things to look at".

## Automação diária

`python scripts/daily_sync.py` roda os 4 syncs, gera o site estático em
`docs/` (com os períodos de 30/90/180 dias já pré-calculados, pro seletor de
período funcionar mesmo no site publicado sem back-end) e faz commit + push
automático (se `AUTO_PUBLISH=true` no `.env` e o repositório já estiver
conectado a um remoto no GitHub).

Agendado no Windows via Task Scheduler (`DOMA_Dashboard_Daily_Sync`, todo dia
06:00). Pra recriar: `AGENDAR_AUTOMACAO_DIARIA.bat`. Pra remover:
`REMOVER_AUTOMACAO_DIARIA.bat`.

## GA4 + Search Console setup (Google Cloud)

1. Google Cloud Console → novo projeto → habilitar **Google Analytics Data
   API** e **Search Console API**.
   - Se a conta Google for de uma organização/Workspace com a política
     `iam.managed.disableServiceAccountKeyCreation` ativa, criação de chave
     de service account é bloqueada — use uma conta **Gmail pessoal** (sem
     organização) pra esse projeto. Não precisa ser a mesma conta que
     administra o GA4/site.
2. IAM & Admin > Service Accounts → criar → gerar chave **JSON** → salvar
   como `google_service_account.json` na raiz do projeto.
3. Adicionar o email da service account como **Viewer** no GA4 (Admin >
   Property Access Management) e como usuário **Restricted** no Search
   Console (Settings > Users and permissions).
   - Se a conta que administra o Search Console não for **Owner
     verificado** da propriedade (só "usuária"), ela não consegue adicionar
     ninguém — nesse caso, adicionar a propriedade de novo (Add property >
     URL prefix) com o mesmo domínio verifica automaticamente se o site já
     tiver Google Analytics ou Tag Manager instalado, te tornando Owner.
4. `GA4_PROPERTY_ID` fica em GA4 > Admin > Property Settings.

`sync_gsc.py` também roda uma checagem de **cobertura de indexação**
(redirects, páginas não indexadas, etc) via URL Inspection API - lê o
sitemap XML do WordPress/Yoast (`/sitemap_index.xml`), inspeciona todas as
páginas estáticas + os posts mais recentes (limite de ~85 URLs por sync,
pra não estourar cota nem deixar o sync lento), e mostra o resultado na aba
SEO > "Index coverage". Estados considerados saudáveis: "Submitted and
indexed", "Indexed, not submitted in sitemap", "Duplicate, Google chose
different canonical" - qualquer outro (redirect, not indexed, unknown to
Google, noindex) aparece como pendência.

## GoHighLevel setup

Settings > Private Integrations > Create new integration, escopo mínimo
**View Contacts**, na sub-conta certa da DOMA. `GHL_LOCATION_ID` aparece na
URL da sub-conta ou em Settings > Business Profile.

**Campanhas de email** (`scripts/sync_ghl.py:fetch_email_campaigns`) chama
`GET /marketing/campaigns`, que **não está disponível** para esta sub-conta
(confirmado - 404). O sync de leads não é afetado; a aba "Leads" mostra
"dados de email indisponíveis" em vez de zero. Precisaria de um endpoint
diferente do GoHighLevel pra resolver, ainda não identificado.

## Meta organic setup (Facebook Page + Instagram)

Sem API de anúncios, sem gasto pago — só Page Insights e Instagram Insights
orgânicos. Passo a passo (via Graph API Explorer, mais rápido que revisão
completa de app):

1. `developers.facebook.com/apps` → Create App → tipo **Business** → nome
   `doma-dashboard`. Na tela de "Casos de uso", marcar **só** "Gerenciar
   tudo na sua Página" (o resto exige verificação de empresa e trava o
   processo à toa).
2. `developers.facebook.com/tools/explorer` → seleciona o app → **User or
   Page** → **Obter token de acesso da Página** (não o fluxo de "User
   Token" comum — para Páginas dentro de um Business Portfolio, só esse
   fluxo mostra a tela de seleção de Página corretamente).
3. Marca as permissões: `pages_show_list`, `pages_read_engagement`,
   `read_insights`, `instagram_basic`, `instagram_manage_insights`.
   - Se a Página pertence a um Business Portfolio, o app também precisa
     estar conectado a ela lá: `business.facebook.com/settings` > Contas >
     Apps > adicionar o app (pelo ID) > Conectar ativos > marcar a Página +
     conta Instagram.
4. Com o token gerado, pegar `META_PAGE_ID` e `META_IG_ACCOUNT_ID` via
   `debug_token` (`granular_scopes[].target_ids`) ou direto:
   `GET /{page-id}?fields=access_token,instagram_business_account`.
5. O token da Página gerado assim é **de curta duração** (poucas horas).
   Trocar por um de longa duração (não expira, na prática):
   ```
   GET https://graph.facebook.com/v21.0/oauth/access_token
     ?grant_type=fb_exchange_token
     &client_id={APP_ID}
     &client_secret={APP_SECRET}
     &fb_exchange_token={USER_TOKEN_CURTO}
   ```
   Isso estende o **User Token** pra ~60 dias. Com esse User Token
   estendido, buscar de novo `GET /{page-id}?fields=access_token` — o Page
   Token resultante não expira (`expires_at: 0`), mesmo o User Token
   original expirando em 60 dias.
6. `META_PAGE_ACCESS_TOKEN` = esse Page Token de longa duração.

**Limitação da API (não é bug deste projeto)**: a Meta descontinuou quase
todas as métricas de alcance/impressão em nível de Página do Facebook
(`page_impressions*`, `page_fan_adds*` etc não existem mais). Só sobrou
`page_post_engagements` e `page_views_total` no nível de Página, e
curtidas/comentários/compartilhamentos por post. O Instagram continua com
alcance (`reach`) completo, tanto por conta quanto por post. Crescimento de
seguidores (Facebook e Instagram) é construído com snapshot diário próprio
— não existe mais endpoint de histórico de seguidores na API, então o
gráfico começa vazio e cresce a cada sync diário.

## Audiência: país, gênero

- **GA4 (Blog)** e **Search Console (SEO)**: país já vem de graça na mesma
  API, sem configuração extra.
- **GA4 gênero/idade**: só existe se **Google Signals** estiver habilitado
  na propriedade (Admin > Data Settings > Data Collection). Sem isso, a
  dimensão volta vazia (não é erro).
- **Instagram**: gênero e país dos seguidores funcionam via
  `follower_demographics` na Graph API, sem configuração extra.
- **Facebook**: a Meta descontinuou a demografia de Página
  (`page_fans_gender_age`) junto com o resto das métricas de
  alcance/impressão - não existe mais forma de trazer isso via API.

## Email do GoHighLevel: limitação confirmada

`GET /emails/campaigns` e `GET /emails/schedule/{id}/stats` retornam
**401 "token not authorized for this scope"** mesmo com **todos os 159
escopos** marcados na Private Integration e um token novo gerado
(2026-08-17). Esse é o formato de erro de guarda de autenticação, não de
rota inexistente (`/emails/stats` sem ID, por comparação, dá 404 de rota
não encontrada) - ou seja, o endpoint existe mas é reservado pra apps de
Marketplace revisados pela GoHighLevel, não pra Private Integration Token.
Virar um app de Marketplace é desproporcional pra um dashboard interno.

Alternativa adotada: `scripts/import_email_stats.py` importa um CSV manual
(campanha, data, destinatários, aberturas, cliques) pra dentro da mesma
tabela que o dashboard já lê. Copia os números da tela de
Reporting/Marketing > Emails do GoHighLevel de vez em quando e roda:

```
python scripts/import_email_stats.py caminho\para\email_stats.csv
python scripts/generate_public_site.py
```

## Ferramentas de SEO (aba SEO do dashboard)

- **Index coverage**: já documentado acima na seção do Search Console setup.
- **Content gap opportunities**: cruza `query` x `page` no Search Console -
  achando queries com impressão real (≥15) onde a melhor página da DOMA
  ainda não chega no top 15 de posição. É oportunidade de conteúdo: ou não
  existe página dedicada pro assunto, ou a página atual não fala claro o
  suficiente sobre aquilo pro Google confiar. Ver `sync_gsc.py:fetch_content_gaps`.
- **On-page SEO audit** (`sync_seo_audit.py`): faz um crawl direto (HTTP GET,
  sem API) de cada página/post do site (via sitemap do Yoast SEO) e confere:
  - Título: presente, 30-60 caracteres
  - Meta description: presente, 70-160 caracteres
  - Exatamente 1 H1 (nem zero, nem mais de um)
  - Imagens sem `alt` text
  - Conteúdo raso (<300 palavras)
  - Tag `canonical` presente
  - Dado estruturado (JSON-LD) presente
  Funciona com WordPress + Elementor + código customizado normalmente,
  porque lê o HTML final renderizado pelo servidor - não depende de
  nenhuma API específica do WordPress.
- **Recent posts** (aba Blog): junta os posts mais recentes (do sitemap) com
  sessão/views do GA4 e cliques/impressões do Search Console - sem precisar
  que o post já seja um dos top-30/top-50 em algum outro lugar.

## Proteção do site publicado

O site é público no GitHub Pages (dado agregado, sem PII). Foi adicionado um
**gate de senha em JavaScript** (`static/index.html` + `dashboard.js`) por
decisão explícita - **isso não é segurança de verdade**: o HTML/JSON com
todos os dados continua baixável por qualquer um que conheça a URL exata
(`data.js`) ou abra o "view-source". O gate só afasta olhar casual.

Senha padrão: `doma2026`. Pra trocar: gera o hash SHA-256 da senha nova
(ex: no console do navegador, `crypto.subtle.digest(...)`, ou qualquer
ferramenta de hash SHA-256) e substitui a constante `PASSWORD_HASH` no topo
de `static/dashboard.js`.

Se quiser proteção de verdade depois: GitHub Pro ($4/mês) + repositório
privado, ou Cloudflare Access apontando um subdomínio.

## Publicar no GitHub Pages

1. Crie um repositório vazio no GitHub (pode ser via GitHub Desktop:
   File > Add local repository > Publish repository).
2. Settings > Pages > Source = **Deploy from a branch**, branch **main**,
   pasta **/docs**.
3. Se o repositório pertencer a uma organização/Workspace com política
   bloqueando `gh` CLI de outra conta, publicar/ativar Pages precisa ser
   feito manualmente pelo dono do repositório no navegador.

O `docs/data.js` só contém contagens agregadas (cliques, sessões, leads por
dia/fonte, seguidores/engajamento social) — nunca email, telefone ou nome de
lead individual. Ver "Privacidade" abaixo.

## Privacidade

- Nunca armazenamos ou expomos email, telefone ou nome individual de lead —
  só contagens agregadas por dia/fonte (`ghl_leads_daily`).
- O sync do GoHighLevel (`sync_ghl.py`) descarta o payload do contato logo
  após contar; só `dateAdded` e `source` são usados, nunca persistidos.
- Posts de Facebook/Instagram são conteúdo público da própria DOMA (não são
  dados pessoais de lead) — legendas, curtidas, comentários e alcance por
  post são armazenados normalmente.
- Nenhum framework de terceiros roda no navegador — os gráficos são SVG
  gerados à mão em `static/dashboard.js`.

## O que não foi construído (de propósito)

Sem Meta Ads, sem Google Ads, sem API de anúncios de forma alguma — nenhum
gasto pago é medido neste dashboard. O que existe de Facebook/Instagram
(`sync_meta_organic.py`) é só Page/Instagram Insights **orgânicos**: posts,
seguidores, engajamento — nenhum dado de campanha, ad set, ad ou CAC/CPL
baseado em spend. Se tráfego pago entrar no futuro, adicionar como
integração isolada nova (`sync_meta_ads.py` / `sync_google_ads.py`, tabela
nova, função de resumo nova, aba nova), sem misturar com o código orgânico
existente.
