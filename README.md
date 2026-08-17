# DOMA Dashboard

Dashboard local de marketing orgânico para a DOMA (dentalofficemanagers.com).
Sem tráfego pago: foco em SEO (Google Search Console), blog/site (GA4) e leads
(GoHighLevel). Segue o mesmo padrão arquitetural do projeto irmão (PreSubs):
app FastAPI + SQLite local, scripts de sync isolados por fonte, automação
diária que publica um site estático no GitHub Pages.

## Estrutura

```
app.py                     FastAPI + SQLite + funções de resumo (fonte única de verdade)
scripts/
  env_utils.py              leitor de .env compartilhado
  sync_gsc.py                Google Search Console -> search_console_daily / _queries
  sync_ga4.py                GA4 -> ga4_traffic_daily / _channel_daily / _top_pages
  sync_ghl.py                GoHighLevel -> ghl_leads_daily / ghl_email_campaigns
  generate_public_site.py    gera docs/ (site estático) a partir das mesmas funções do app.py
  daily_sync.py              orquestra os 3 syncs + gera site + publica no git
static/                     HTML/CSS/JS do dashboard (sem framework pesado)
docs/                       saída estática publicada no GitHub Pages (gerada, não editar à mão)
data/                       doma.db (SQLite), git-ignorado
```

## Setup

1. `python -m venv .venv` e ative o ambiente.
2. `pip install -r requirements.txt`
3. `copy .env.example .env` e preencha (ver "Pendências" abaixo).
4. Coloque o JSON da service account do Google Cloud na raiz do projeto
   (nome padrão: `google_service_account.json` — git-ignorado).
5. `python app.py` (ou `RUN_DASHBOARD.bat`) sobe o dashboard local em
   `http://127.0.0.1:8000`.

## Sincronizar dados manualmente

```
python scripts/sync_gsc.py
python scripts/sync_ga4.py
python scripts/sync_ghl.py
```

Cada script é isolado: se uma fonte falhar (credencial errada, API fora do
ar), as outras continuam funcionando. Falhas ficam registradas na tabela
`sync_log` e aparecem no dashboard.

## Automação diária

`python scripts/daily_sync.py` roda os 3 syncs, gera o site estático em
`docs/` e faz commit + push automático (se `AUTO_PUBLISH=true` no `.env` e
o repositório já estiver conectado a um remoto no GitHub).

Para agendar no Windows (roda todo dia às 06:00): `AGENDAR_AUTOMACAO_DIARIA.bat`.

## Publicar no GitHub Pages

1. Crie um repositório vazio no GitHub.
2. `git init` (se ainda não for um repo), `git remote add origin <url>`.
3. Rode `python scripts/daily_sync.py` uma vez (ou `git add docs && git commit && git push` manual).
4. No GitHub: Settings > Pages > Source = branch principal, pasta `/docs`.

O `docs/data.js` só contém contagens agregadas (cliques, sessões, leads por
dia/fonte) — nunca email, telefone ou nome de lead individual. Ver
"Privacidade" abaixo.

## Privacidade

- Nunca armazenamos ou expomos email, telefone ou nome individual de lead —
  só contagens agregadas por dia/fonte (`ghl_leads_daily`).
- O sync do GoHighLevel (`sync_ghl.py`) descarta o payload do contato logo
  após contar; só `dateAdded` e `source` são usados, nunca persistidos.
- Nenhum framework de terceiros roda no navegador — os gráficos são SVG
  gerados à mão em `static/dashboard.js`.

## Pendências antes de rodar em produção

Estas são as informações que faltam preencher no `.env` / confirmar:

1. **`GA4_PROPERTY_ID`** — Admin > Property details, no GA4 do site da DOMA.
2. **`GA4_SERVICE_ACCOUNT_FILE`** — criar uma service account no Google
   Cloud, baixar o JSON, adicionar como Viewer no GA4 (Admin > Property
   Access Management) e como usuário Restricted no Search Console
   (Settings > Users and permissions). A mesma conta serve para as duas
   integrações.
3. **`GHL_API_KEY`** — Settings > Private Integrations > Create new
   integration, escopo mínimo "View Contacts", na sub-conta certa da DOMA.
4. **`GHL_LOCATION_ID`** — ID da sub-conta/location da DOMA no GoHighLevel.
5. **Campanhas de email (GoHighLevel)** — `scripts/sync_ghl.py:fetch_email_campaigns`
   chama `GET /marketing/campaigns`, que **não é um endpoint confirmado**
   para todas as sub-contas de GoHighLevel via Private Integration. Se
   falhar, o sync de leads continua funcionando normalmente e a aba
   "Leads" mostra "dados de email indisponíveis" em vez de zero. Precisa
   validar com o GoHighLevel (ou com a documentação da conta) qual
   endpoint/escopo expõe taxa de abertura/clique de campanha antes de
   confiar nesse número.
6. **GitHub remoto** — repositório ainda não conectado; `daily_sync.py`
   avisa e não publica até que exista `git remote origin`.

## O que não foi construído (de propósito)

Sem integração de Meta Ads ou Google Ads, sem aba de campanhas/ad
sets/ads, sem CAC baseado em spend — a DOMA não roda tráfego pago hoje.
Se isso mudar no futuro, adicionar como uma integração isolada nova
(`sync_meta.py` / `sync_google_ads.py`, uma tabela nova, uma função de
resumo nova, uma aba nova), sem misturar com o código orgânico existente.
