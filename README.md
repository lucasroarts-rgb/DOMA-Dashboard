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
2b. `playwright install chromium` (uma vez só - baixa o browser que o
    `sync_seo_audit.py` usa pra re-checar H1/thin-content em páginas com
    template JS antes de marcar como problema real).
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
python scripts/sync_pagespeed.py
python scripts/sync_ahrefs.py
python scripts/sync_competitors_content.py
python scripts/sync_serp_competitors.py
python scripts/send_seo_digest.py
```

Cada script é isolado: se uma fonte falhar (credencial errada, API fora do
ar), as outras continuam funcionando. Falhas ficam registradas na tabela
`sync_log` e aparecem no dashboard, aba "Overview" > "Things to look at".

## Automação de eBooks (Drive -> WordPress + GoHighLevel)

Marianeel manda pra Lucas a imagem de capa + PDF de um novo eBook (sem
horário fixo, normalmente seg/qua/sex). Lucas sobe os 2 arquivos na pasta
Google Drive compartilhada (`DRIVE_EBOOKS_FOLDER_ID` no `.env`). A partir
daí, `scripts/sync_ebook_pipeline.py` automatiza o que a API permite:

```
scripts/
  ebook_pipeline/
    drive_client.py    lista/baixa o par PDF+imagem novo da pasta Drive
    pdf_extract.py      extrai título/subtítulo/bullets do PDF (pdfplumber)
    wp_client.py        upload de mídia + criação de página draft no WordPress
    ghl_client.py        upload de mídia no GoHighLevel + leitura de forms/workflows
    copy_generator.py   monta slug/tag/excerpt/email/HTML das páginas
  sync_ebook_pipeline.py   orquestra tudo, grava estado em data/ebook_pipeline_state.json
```

Já roda sozinho: `AGENDAR_AUTOMACAO_EBOOKS.bat` cria uma tarefa que checa a
pasta seg/qua/sex, das 10h às 12h, a cada 30 minutos (janela pensada pra
cobrir o horário que Marianeel costuma mandar, sem ficar rodando o dia
inteiro). Usa `pythonw.exe` - roda sem abrir janela de console.
`RODAR_EBOOK_AGORA.bat` ainda existe se quiser forçar uma rodada imediata
fora da janela agendada.

Cada eBook processado gera `ebook_packages/{slug}/` (git-ignorado, só
local) com `capture_page.html`, `thank_you_page.html`, `email_delivery.md`,
`package_details.md` e `package.json` — e já sobe a capa pro WP Media
Library, o PDF pro GHL Media Storage, e cria as 2 páginas no WordPress como
**draft** (nunca publica sozinho).

Cada marco importante dispara um aviso na tela (janela do Windows, via
`scripts/ebook_pipeline/notify.py`) sem precisar checar nada manualmente:
página nova criada (com o link de edição do draft) e form conectado (pronto
pra revisar/publicar).

`package_details.md` traz o nome exato que o form/workflow precisam ter no
GHL (muda por ebook, sempre gerado a partir do título real do PDF - nunca
precisa adivinhar) e a URL futura da thank-you page pra usar como redirect
do form. Depois que o form é duplicado+renomeado no GHL, **não precisa
avisar ninguém**: toda rodada de 30min também rechecka todo ebook pendente
(`recheck_pending_forms` em `sync_ebook_pipeline.py`) e, assim que acha o
form pelo nome, cola o embed real na página draft sozinha
(`scripts/ebook_pipeline/attach.py`). Pra forçar isso na hora sem esperar o
próximo ciclo: `python scripts/attach_ebook_form.py {slug}`.

**Teto real da API do GHL** (confirmado ao vivo, não é falta de escopo da
key): não existe endpoint público pra criar form nem workflow do zero — só
listar. A conta já tem 1 form + 1 workflow publicados por eBook, nomeados
`Ebook - {Título}` (confirmado via API contra os eBooks já existentes,
ex.: "Ebook - Burnout at the Front Desk"). Pra cada eBook novo, isso
continua manual: duplicar um form/workflow existente no GHL, renomear, e
colar os valores que já estão prontos em `package_details.md` (tag, URL do
PDF, embed do form). Publicar as páginas do WP e rodar o teste real de
lead (checklist do CLAUDE.md, regra 48) também continuam manuais.

Setup necessário uma vez: habilitar a Drive API no mesmo projeto Google
Cloud usado por GA4/GSC (`planar-maxim-305714`) e compartilhar a pasta do
Drive como Viewer com `doma-dashboard-sync@planar-maxim-305714.iam.gserviceaccount.com`.

## Automação diária

`python scripts/daily_sync.py` roda os 9 syncs + o digest de e-mail, gera o
site estático em `docs/` (com os períodos de 30/90/180 dias já
pré-calculados, pro seletor de período funcionar mesmo no site publicado sem
back-end) e faz commit + push automático (se `AUTO_PUBLISH=true` no `.env` e
o repositório já estiver conectado a um remoto no GitHub).

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

## Editar SEO do WordPress via API (fora do escopo deste repo, mas documentado aqui)

DOMA roda em WordPress + Elementor + código customizado (dentalofficemanagers.com,
hospedado na Bluehost/Newfold). Numa sessão em 2026-08-19, usei a REST API do
WordPress com uma **Application Password** (não a senha de login) pra
corrigir título/meta description/alt text apontados pela auditoria SEO
deste dashboard, direto no site. Isso não faz parte do código deste
projeto (não roda automaticamente), mas fica registrado aqui porque outra
sessão pode precisar repetir o processo:

1. wp-admin > Users > Profile > Application Passwords > gera uma nova.
   Autentica via HTTP Basic (`usuário:application-password`) em
   `https://dentalofficemanagers.com/wp-json/wp/v2/...`.
2. **Bloqueio 1**: o WAF da Bluehost (Mod_Security) retorna 406 pro
   User-Agent padrão da lib `requests` do Python — passa um header
   `User-Agent` de navegador normal.
3. **Bloqueio 2**: os campos `_yoast_wpseo_title` e `_yoast_wpseo_metadesc`
   **não aparecem** no objeto `meta` da API por padrão nesse Yoast — mudar
   `title`/`excerpt` do WordPress não muda o `<title>`/meta real da página
   (o Yoast tem um valor próprio, travado, que sobrepõe tudo). Resolvido
   registrando os dois campos pra REST via um snippet PHP no plugin
   **Code Snippets** (já instalado e ativo no site):
   ```php
   add_action('init', function () {
       foreach (array('post', 'page') as $post_type) {
           register_post_meta($post_type, '_yoast_wpseo_title', array(
               'show_in_rest' => true, 'single' => true, 'type' => 'string',
               'auth_callback' => function () { return current_user_can('edit_posts'); },
           ));
           register_post_meta($post_type, '_yoast_wpseo_metadesc', array(
               'show_in_rest' => true, 'single' => true, 'type' => 'string',
               'auth_callback' => function () { return current_user_can('edit_posts'); },
           ));
       }
   });
   ```
   Criado via `POST /wp-json/code-snippets/v1/snippets` (a conta precisa
   ser Administrator, não Editor, pra essa rota). Ativação do snippet foi
   feita manualmente pelo usuário (ativar execução de código em site ao
   vivo é bloqueado pra automação, por design).
4. Conteúdo de imagem/vídeo/hero no site não usa a Biblioteca de Mídia do
   WordPress — é tudo HTML customizado dentro de widgets "HTML" do
   Elementor (`meta._elementor_data`, um JSON serializado como string). Pra
   editar alt text de imagem, editei essa estrutura JSON diretamente
   (`json.loads` > editar o campo `settings.html` de cada widget > `json.dumps`
   de volta), não a API de Media.
5. **Cuidado real, não teórico**: o template de post único (Elementor
   Library #318, "Elementor Single Post") monta o hero (H1, imagem, corpo
   do artigo) via **JavaScript client-side** — um crawler sem JS (como o
   `sync_seo_audit.py` deste projeto, ou `curl`) vê `'+title+'` literal e
   ~259 palavras, enquanto um navegador de verdade mostra o título certo,
   H1 correto e o artigo completo (2000+ palavras). Isso gerou dois falsos
   alarmes na auditoria (já corrigidos no texto do achado, ver
   `app.py:_onpage_findings`) - **sempre confirmar com navegador renderizado
   antes de tratar como bug real** nesse site especificamente.
6. **Plugin Redirection (regras de redirect)**: achado real em 2026-08-19 —
   38 posts publicados de verdade estavam sendo redirecionados pro `/blog/`
   genérico por uma regra antiga de limpeza da migração Wix que nunca foi
   removida quando o post foi republicado com o mesmo slug (>2000 acessos
   reais desviados no total). Rotas úteis da API do plugin (descobertas via
   `GET /wp-json/` — não documentado em lugar óbvio):
   - `GET /redirection/v1/redirect?per_page=200` lista as regras (200 é o
     máximo da própria API, pagina além disso)
   - `POST /redirection/v1/bulk/redirect/disable` com corpo
     `{"items": [id1, id2, ...]}` — **essa é a que realmente funciona** pra
     ativar/desativar em massa. `POST .../redirect/{id}/disable` dá 404
     (rota não existe), e `POST .../redirect/{id}` com o objeto completo e
     `"enabled": false` retorna 200 mas **ignora silenciosamente** o campo
   - Reimportação de CSV (Tools > Import) **não respeita a coluna
     `status`** mesmo ela existindo no CSV exportado — atualiza
     url/target/code/hits/title casando pela URL de origem (confirmado:
     "127 updated, 0 created"), mas descarta o estado ativo/desativado.
     Não confiar em CSV pra mudar status — usar o endpoint bulk acima.

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
- **Page speed (mobile)** (`sync_pagespeed.py`): roda o Google PageSpeed
  Insights (Lighthouse) na homepage + nas páginas de maior tráfego (top 7 do
  GA4, `ga4_top_pages`), sempre estratégia mobile - é o critério mais duro e
  o que o Google usa pra mobile-first indexing. Guarda performance,
  acessibilidade, best practices, SEO score, LCP, CLS, TBT, FCP, speed index,
  erros de console reais (via browser headless do Lighthouse, então pega
  problemas que o `sync_seo_audit.py` não pega por não executar JS) e as
  principais oportunidades de otimização por página. Requer
  `PAGESPEED_API_KEY` no `.env` - o limite público sem chave é compartilhado
  globalmente e pode zerar no dia; crie uma API key no mesmo projeto Google
  Cloud do GA4/GSC (ativar "PageSpeed Insights API" > Credentials > Create
  Credentials > API key, sem OAuth/service-account). Se uma página falhar
  (timeout, erro do próprio Lighthouse), o script pula ela e segue com as
  demais - não derruba o sync inteiro.

## Resumo diário de SEO por e-mail

`scripts/send_seo_digest.py` manda um e-mail (texto simples) todo dia com:
Search Console (cliques/impressões/CTR/posição dos últimos 7 dias), index
coverage, on-page audit (páginas com problema + os achados de cada uma),
PageSpeed (score médio + páginas abaixo de 50) e falhas de sync do dia. Usa
as mesmas funções de resumo do `app.py` que o dashboard usa - nunca vai
divergir do que aparece lá.

Requer no `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`EMAIL_FROM`, `EMAIL_TO`. Pra Gmail, `SMTP_PASSWORD` **tem que ser** uma
Senha de App (myaccount.google.com/apppasswords, exige verificação em 2
etapas ativada) - a senha normal da conta não funciona (Google bloqueia
login via SMTP com senha comum) e não deve ser usada em arquivo de
automação de qualquer forma.

## Ahrefs (aba "Competitors" do dash) - código pronto, bloqueado do lado da conta

`AHREFS_API_KEY` + `AHREFS_PROJECT_ID` no `.env`, mas toda chamada à API
retorna `401 Unauthorized` - já testado com 3 chaves diferentes (geradas do
zero pela própria conta), em múltiplos endpoints (inclusive endpoints
públicos básicos, sem `project_id`), com curl puro (não é bug de biblioteca
Python), confirmado que a requisição chega no servidor real da Ahrefs (não
é bloqueio de rede/proxy - tem `x-request-trace-id` no header de resposta).
Conclusão: é um problema do lado da conta Ahrefs (entitlement de API não
ativo de verdade, mesmo aparecendo na tela de gerenciar chaves) - só o
suporte da Ahrefs resolve.

`scripts/sync_ahrefs.py` já existe (tabela `ahrefs_domains` +
`ahrefs_site_audit_issues`, aba "Competitors" no dashboard com tabela de
comparação DOMA x concorrentes e a lista de Site Audit issues), mas está
**não verificado end-to-end** - só dá pra confirmar contra uma API que
autentica. A chamada de `site-audit/issues` usa o formato exato de um
exemplo real que veio da própria UI da Ahrefs (esse pedaço é confiável); os
endpoints de `site-explorer/metrics` (métricas de domínio) e
`site-explorer/competing-domains` (descoberta automática de concorrentes,
sem lista manual) são a melhor suposição da estrutura da API v3 da Ahrefs,
nunca testados contra uma resposta real - **reconferir contra a
documentação atual da Ahrefs (ou por tentativa e erro) assim que a conta
autenticar**, antes de confiar cegamente neles. Enquanto isso, a aba
Competitors mostra um aviso explicando a situação em vez de ficar vazia
sem explicação.

## Ad Spy - inteligência de anúncios pagos de concorrentes (aba "Competitors")

**Exceção deliberada à regra "só orgânico"**: o resto deste dashboard nunca
toca em mídia paga (ver "O que não foi construído (de propósito)" mais
abaixo) - esse painel é a única exceção, autorizada explicitamente pelo
usuário, porque o objetivo aqui não é a mídia paga *da DOMA* (isso
continua fora do escopo), e sim entender o que **concorrentes** estão
testando em anúncio, como sinal estratégico pra copywriting/oferta -
baseado num guia próprio ("Claude Ad Spy") que o usuário trouxe do Notion.

**Não é sync automático.** Meta Ads Library, Google Ads Transparency
Center, TikTok Creative Center e LinkedIn Ads Library não têm API pública
pra coleta em lote/agendada - o processo é manual: navegar numa dessas
bibliotecas, achar um anúncio real de um concorrente, e registrar aqui.
Não tentar contornar isso com scraping - viola os termos das próprias
plataformas.

`scripts/add_ad_spy_entry.py` grava um achado por vez (competidor,
plataforma, hook, oferta, CTA, prova, hipótese estratégica, link, etc -
mesma estrutura da base de dados do Passo 11 do guia original). Rodar
`python scripts/generate_public_site.py` depois pra publicar. Exemplo:

```
python scripts/add_ad_spy_entry.py --competitor "AADOM" --platform Meta --date-found 2026-08-22 --format Video --hook "..." --offer "..." --cta "..." --link "https://www.facebook.com/ads/library/?id=..." --hypothesis "..."
```

Regra do guia original mantida: nunca inventar métrica, orçamento, ROAS
ou conversão - só registrar o que é observável publicamente na própria
biblioteca de anúncios. Campo vazio é melhor que campo inventado.

## Concorrentes - rank real (SERP) e conteúdo novo (aba "Competitors")

Como a Ahrefs tá bloqueada e o Google Custom Search JSON API exige conta de
faturamento vinculada ao projeto Google Cloud (recusado - ver acima),
essas duas partes usam caminhos gratuitos, sem chave paga:

- **`scripts/sync_serp_competitors.py`** - "share of voice" real: pega as
  queries que a DOMA já mira (top por clicks no `search_console_queries`)
  e busca cada uma no **DuckDuckGo** (`html.duckduckgo.com/html/`, endpoint
  HTML público, sem API/chave), vendo quem mais aparece no top 10 e em que
  posição. Não é o índice do Google, é um sinal de concorrência real e
  gratuito, não uma métrica de "autoridade" inventada. **Limitação
  conhecida**: a DuckDuckGo aplica rate-limit por IP depois de um número
  de buscas em sequência (retorna `202` em vez do resultado) - descoberto
  na prática rodando ~20 buscas seguidas nesta sessão. Rodando 1x/dia (uso
  normal do `daily_sync.py`) não deve bater nesse limite, mas se acontecer
  o script já falha graciosamente (não quebra o resto da automação) e os
  dados parciais já coletados até o bloqueio são salvos mesmo assim. Não
  tentar contornar esse bloqueio (seria bypass de bot-detection) - se
  voltar a bloquear com frequência, a alternativa é pagar por uma API tipo
  DataForSEO.
- **`scripts/sync_competitors_content.py`** - lê o sitemap público de cada
  concorrente rastreado (mesma técnica do `sitemap_utils.py` da DOMA,
  generalizada para sites fora do WordPress via `fetch_all_urls_generic`)
  e compara com a leitura anterior - URL nova = conteúdo novo publicado.
  Concorrentes rastreados hoje: **AADOM** (dentalmanagers.com) e
  **Dental A Team** (thedentalateam.com). **The DALE Foundation**
  (dalefoundation.org) foi cogitada mas excluída - o `/sitemap.xml` dela
  serve uma página de "Client Challenge" (bot-detection) em vez do sitemap
  real, e isso não deve ser contornado.

Lista de concorrentes é curada manualmente em `COMPETITORS` no topo do
script - editar lá pra adicionar/remover.

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
gasto pago **da DOMA** é medido neste dashboard. O que existe de
Facebook/Instagram (`sync_meta_organic.py`) é só Page/Instagram Insights
**orgânicos**: posts, seguidores, engajamento — nenhum dado de campanha,
ad set, ad ou CAC/CPL baseado em spend próprio. Se tráfego pago da DOMA
entrar no futuro, adicionar como integração isolada nova
(`sync_meta_ads.py` / `sync_google_ads.py`, tabela nova, função de resumo
nova, aba nova), sem misturar com o código orgânico existente.

**Exceção única e deliberada**: o painel "Ad Spy" (ver seção própria
acima) analisa anúncios pagos **de concorrentes**, não da DOMA - não
mede spend, CAC ou CPL de ninguém, só registra hooks/ofertas/CTAs
observáveis publicamente nas bibliotecas de transparência de anúncio de
cada plataforma. Autorizado explicitamente pelo usuário como exceção à
regra acima, não uma reversão dela.
