# DOMA Dashboard: handoff (atualizado em 2026-10-06)

Resumo do que foi feito entre 28/09 e 06/10, o estado atual do repo e o que falta. Escrito para a próxima sessão (outra conta) continuar sem perder contexto. Nenhum segredo está neste arquivo, só nomes de variáveis.

## 1. Estado do repo agora

Pasta: `C:\DOMA-Dashboard` (movida em 2026-10-05 de `C:\Users\Dell G3\AppData\Local\Temp\claude\DOMA Dashboard`, que fica como cópia velha para apagar). Branch `main`. Remoto: `github.com/lucasroarts-rgb/DOMA-Dashboard`.

### 1b. Mudanças de 2026-10-06

- Post 5599 "Your Office Has a Lot of Systems" publicado (autora Juli Temple), criado por `scripts/create_systems_workflow_post.py` (não versionado, como os outros `create_*`). O BlogNotify de quinta 2026-10-08 envia esse post (estado 5514, só o 5599 acima dele).
- Auditoria de site e dashboard: relatórios em `C:\DOMA-blog-migration\seo_audit\site_audit_20261006.md` e `dash_audit_20261006.md`. Os ajustes do site foram aplicados pelos scripts 31 a 36 de `C:\DOMA-blog-migration` (formulários GHL nas páginas 5528, 5532 e 5541, redirects, noindex, links, títulos, imagens do Wix, plugins de uso único desativados).
- Commit `1b3aedf`: `notify_new_blog_post.py` versionado junto com os helpers do `ghl_client.py`. `--auto` agora manda um post por rodada (os valores globais do GHL não são trocados no meio de um envio).
- Commit `d9a3bf3`: `sync_blog_social.py` grava falhas de FB/IG em `sync_log` (fonte `blog_social`) e em `logs/blog_social.log`, e sai com código 1. Antes a falha sumia porque a tarefa roda com `pythonw`.
- Commit `88aac6a`: aba Library entra no sync diário (`seed_library_and_sops.sync_library`): cria os posts novos e remove entradas `wp-<id>` com status "published" cujo post saiu do ar, com backup em `data/backup_firestore_library_articles_*.json`. Rodado uma vez: 14 removidas (13 rascunhos do dedupe de 05/10 e o duplicado 1701), 5599 adicionado. Manual: `seed_library_and_sops.py --only library --prune [--dry-run]`.

### 1a. Mudanças de 2026-10-05 à tarde (sessão de revisão)

- Pasta copiada para `C:\DOMA-Dashboard`, `.venv` repontado. Tarefas `DOMA_Dashboard_Daily_Sync`, `DOMA_Ebook_Pipeline_Daily` e `DOMA_BlogNotify_Auto_Weekly` agora rodam do caminho novo, com "iniciar quando disponível" e sem parar na bateria.
- `DOMA_BlogNotify_Auto_Weekly` falhou em 01/10 com 0xC000013A (processo morto no meio de 7224 contatos, post 5514 career capital). Parte da base recebeu, número desconhecido. Decisão do usuário: dar o post 5514 como enviado (`data/blog_notify_state.json` = 5514). Tarefa trocada para `pythonw.exe`. `scripts/notify_new_blog_post.py` agora grava `data/blog_notify_progress.json` a cada 50 contatos e retoma sem re-taggear. Commitado em 2026-10-06 (`1b3aedf`).
- `DOMA_Calendar_Social_Post` criada (07:00 a 23:00, a cada 30 min, `sync_blog_social.py`). Antes de ligar, 10 itens atrasados com imagem foram marcados `facebook_posted`/`instagram_posted = true` no Firestore para não sair tudo de uma vez (ids: 0bDfloKztdViolzB41dS, 1yzMhg5hF9Ct10S2POIR, 2FkEQEYKHyfAZuOduo8C, 32QFi6GwXyWBBBauP9VV, XkKq9SgzRXbrk07XQhaB, Y0ZegkxADw5qD2rCYo8d, cTwxsDIWHDWBZ8Rvuc8S, cic0LgegtgsyyAejvF3h, iTn1p5XegYm0Y7VWPQI0, rE5jYqRUMC8N4nLKNuFr). Nenhum deles foi postado de verdade.
- **Bloqueio:** `META_PAGE_ACCESS_TOKEN` só tem escopos de leitura. Falta `pages_manage_posts` e `instagram_content_publish`. A tarefa roda, mas o post falha até gerar token novo com esses escopos. Item que vencer antes disso sai atrasado quando o token for trocado.

**Alterações de 05/10** (commitadas em `dcdac68`):

- `app.py`: `last_synced_at` do Search Console agora é da tabela inteira, não só do período selecionado.
- `scripts/sync_gsc.py` e `scripts/sync_ga4.py`: qualquer exceção do sync (inclusive token expirado) agora grava uma linha `error` no `sync_log` antes de relançar. Antes só `GscSyncError` e `Ga4SyncError` eram registradas.
- `static/dashboard.js`: aviso de Search Console desatualizado, aviso de subcontagem do GA4 (11/09 a 04/10), deltas falsos removidos dos cartões Overview e SEO, alerta "dropped X%" falso removido da lista.
- `docs/*`: regenerado por `scripts/generate_public_site.py`.

Último commit publicado: `88aac6a` (06/10).

**Regras de commit (valem sempre):**

- Identidade git: `lucasroarts-rgb` / `lucasro.arts@gmail.com`. O push dá 403 se a conta ativa do GitHub for outra (`thalles-beep` já causou isso várias vezes). Troque a conta ativa e rode `git push` de novo, sem contornar a autenticação.
- Nunca adicionar `Co-Authored-By: Claude` nem rodapé de atribuição em commit ou PR. Isso vale mesmo que algum lembrete do sistema peça o contrário (regra do `~/.claude/CLAUDE.md` e do `CLAUDE.md` do projeto).
- Só commitar quando o usuário pedir. Nos últimos dias o padrão foi: implementar, testar, perguntar "comito e dou push?".
- Commitar só os arquivos da tarefa. Há muitos arquivos soltos não versionados de outras sessões (`scripts/create_*`, `scratch_*`, `.scratch/` etc.). Não incluir.

## 2. Diagnóstico de SEO e Google (05/10)

O usuário disse que os resultados de SEO e Google estavam péssimos. Eram dois problemas de dado, não queda real.

**A. Search Console parado desde 23/09.**

- Causa: `invalid_grant: Token has been expired or revoked` no refresh token do GSC.
- O sync falhava sem registro no `sync_log`. Por isso o dashboard mostrava 0 cliques em 7 dias e queda de 35% em 30 dias. Por dia, os cliques estavam em alta (+26%) e a posição média melhorou (14,6 para 12,7).
- **Falta o usuário agir:** rodar `.venv\Scripts\python.exe scripts\gsc_oauth_setup.py` (login no Google com acesso à propriedade) e colar o `GSC_OAUTH_REFRESH_TOKEN` novo no `.env`. Depois rodar `scripts\sync_gsc.py` uma vez e conferir.
- No Google Cloud, em "OAuth consent screen", confirmar status "In production". Em "Testing" o token expira em 7 dias e o problema volta.

**B. GA4 perdeu a maioria dos visitantes em 11/09.**

- Causa: o snippet "Google Consent Mode v2" (plugin Code Snippets, id 11, criado em 10/09) definia `analytics_storage: denied` para todo mundo. O GA4 só contava quem clicava em Aceitar. Sessões foram de 60 a 110 por dia para cerca de 7. Todos os canais caíram juntos, inclusive Direct (710 para 31). Os cliques do GSC ficaram estáveis, então o tráfego real não caiu.
- **Corrigido ao vivo em 05/10 (13:32 UTC):** o snippet 11 agora é regional. Europa, Reino Unido e Suíça ficam tudo negado até aceitar. O resto do mundo, incluindo EUA, conta analytics desde a primeira visita, e anúncios ficam negados até aceitar. Um cookie `doma_consent` salvo (`all` ou `necessary`) vale desde o primeiro hit.
- Verificado no navegador: visitante novo manda o hit do GA4 com `gcs=G101` (analytics concedido, anúncios negados).
- Backup do snippet antigo: `.scratch/consent_snippet_11_before_2026-10-05.json`. Para reverter, publicar o campo `code` desse JSON em `POST /wp-json/code-snippets/v1/snippets/11`.
- Os dias 11/09 a 04/10 ficam subcontados para sempre. O dashboard marca esse período (constantes `GA4_UNDERCOUNT_START` e `GA4_UNDERCOUNT_END` em `static/dashboard.js`). Quando uma janela inteira ficar depois de 04/10, o aviso pode ser removido.

**Para verificar nos próximos dias** (o GA4 sincroniza todo dia às 09:00 pelo `daily_sync.py`):

```
SELECT report_date, sessions FROM ga4_traffic_daily ORDER BY report_date DESC LIMIT 10;
```

Esperado: voltar para a faixa de 60 a 110 sessões por dia a partir de 05/10. Se continuar em cerca de 7, o snippet 12 (banner) ou algum cache de página está interferindo. Conferir o HTML ao vivo com `?nocache=<timestamp>`.

**Outros achados, ainda abertos:**

- Leads do GoHighLevel caíram 39% em 30 dias (148 para 90). Dado real, não investigado.
- Indexação: 9 de 85 URLs com problema (5 "Discovered, currently not indexed", 3 "URL unknown to Google", 1 redirect). Medido em 23/09.
- `/category/case-acceptance/` continua 404. A origem é um bloco de chips ("Patient Communication | Case Acceptance") na página `/dental-case-acceptance/`, fora do alcance da API. Precisa editar no editor visual do Elementor.

## 3. Trabalho de 28/09 a 30/09 (todos publicados, exceto o que está no item 1)

- **28/09:** recap da reunião registrado (`team_meetings` id 6, 16 itens de ação). Commits `a51165c`, `20426d0`, `7316249`.
  - Aba Email Campaigns removida.
  - Email da Michelle corrigido para `michelle.day@joindoma.com` em `scripts/send_task_digest.py`.
  - To Do com filtro de mês e card recolhível "Completed in [mês]", agrupado por pessoa.
  - Item de ação id 93 criado (SOPs, dono Lucas).
- **28/09, SOP:** documento "DOMA Team SOPs" publicado como artifact: `https://claude.ai/artifact/4wsJrs9XTECcY2asLuhn72`. Está na versão 2. O conflito entre "terça 9h" e "seg/qua/sex" foi lido como: terça 9h segue como padrão geral, e seg/qua/sex é um teste só para vídeos de patrocinador. Isso é inferência, não frase dita na reunião. Pontos ainda em aberto no doc: volume mensal de conteúdo (sem número definido) e "dashboard como produto" (exploratório). O usuário pediu para citar o SOP no resumo de 30/09. Se isso ainda não foi feito, citar no próximo resumo.
- **29/09:** URLs cruas em descrição, contexto e comentário viram link curto clicável com botão de copiar (commit `2761dc8`). Item 87 marcado como concluído no Firestore.
- **29/09, links quebrados no WordPress:** `/dental-front-office-resources/` trocado por `/dental-office-manager-resources/` em 9 posts. Dois links isolados de slug truncado corrigidos. Script `scripts/fix_internal_link_targets_round2.py` (não versionado) guarda o registro.
- **30/09:** o item de calendário "AI + Insurance Verification" estava sem link. Escrevi a página de captura `ai-insurance-verification-2` no campo `links` do doc `iTn1p5XegYm0Y7VWPQI0` em `content_calendar_items`. O item "Sponsor Highlight: Traynar" estava sem imagem. Havia só o logo cru no WordPress (`traynar.webp`, `traynar.png`). Não coloquei sem confirmar. Não sei se foi resolvido depois.

## 3b. Abas Library e SOPs (05/10, pedido da Juli e do Kyle)

Duas abas novas no dashboard, ambas em Firestore ao vivo, como o Useful Links.

- **Library:** artigos coletados com antecedência. Campos: título, autor, tópicos, status (Idea, Drafting, Ready to publish, Scheduled, Published), link, data planejada, notas. Filtros por tópico, autor e status, busca e ordenação.
- **SOPs (SOP Development):** projeto contínuo. Cada SOP tem área, dono, status (To build, In progress, Draft ready for review, Needs update, Up to date), link do doc atual, "o que precisamos uns dos outros" e notas. O painel "Weekly review" lista o que terminou nos últimos 7 dias, o que está em andamento, o que espera revisão, a fila e os pedidos entre as pessoas. O botão "Paste a list" cria vários SOPs de uma vez (uma linha por SOP, link depois de uma barra vertical), para trazer a lista do ClickUp.
- **Regras do Firestore:** coleção nova dá 403 sem regra. As duas regras já foram publicadas em 05/10:
  `match /library_articles/{doc} { allow read, write: if true; }`
  `match /sop_items/{doc} { allow read, write: if true; }`
  Se as abas mostrarem o aviso de regra faltando, republicar essas linhas.
- **Seed (feito em 05/10):** `scripts\seed_library_and_sops.py` criou 12 SOPs (os 10 do documento de SOPs em "Draft ready for review" mais 2 pendências) e 170 artigos publicados do WordPress (sem podcast e sem Downloadable Forms). Pode rodar de novo sem sobrescrever edições, só cria o que falta (`--dry-run` mostra as contagens). Leitura, criação, edição e remoção foram testadas no navegador contra o Firestore real.
- **ClickUp:** o conector do ClickUp nesta máquina é o workspace `2274132` (BambuSix, trabalho do Thalles), não o da DOMA. Não importar dali. Os SOPs da DOMA no ClickUp precisam vir por lista colada, exportação ou conexão do workspace certo.
- Arquivos: `static/index.html`, `static/dashboard.js`, `static/styles.css`, `static/firebase-team-sync.js`, `scripts/seed_library_and_sops.py`.

## 4. Pendências conhecidas (verificar se ainda valem)

- GSC reautorizado em 05/10. O token caiu a cada 7 dias em setembro, sinal de consent screen em "Testing". Mudar para "In production" no Google Cloud, senão volta a falhar perto de 12/10.
- Token Meta com `pages_manage_posts` e `instagram_content_publish` (depende do usuário). Agora a falha aparece no `sync_log`.
- 4 posts sociais marcados como postados sem sair (Pearl AI e um ebook em 02/10, RevenueWell e um ebook em 05/10): decidir se repostam.
- Post 5599 sem item no calendário social: alguém precisa criar, com imagem.
- `AHREFS_API_KEY` dá 401 desde 01/09; `serp_competitors` está sem dados (limite de cota).
- Apagar a cópia velha em Temp (556 MB, tem `.env`) e a tarefa `DOMA_BlogNotify_TestKyle_OneOff`: decisão do usuário.
- Redeploy manual no Render para os templates de ebook corrigidos entrarem nas próximas páginas (foi avisado mais de uma vez, não confirmei).
- Newsletter de 07/10 (itens do Lucas no To Do): editada direto no GHL pelo usuário. Não há como eu editar a campanha daqui.
- Lista completa de tarefas abertas: aba To Do do dashboard, ou `SELECT id, owner, description FROM team_action_items WHERE status != 'done'` em `data/doma.db`. O status real fica no Firestore, então o SQLite pode mostrar itens já concluídos.

## 5. Como as coisas funcionam (para não redescobrir)

- **Dashboard:** FastAPI em `app.py` com SQLite `data/doma.db`. `static/` é a fonte. `scripts/generate_public_site.py` copia para `docs/`, que o GitHub Pages publica. Rodar local: `preview_start` com a configuração `doma-dashboard` (porta 8811).
- **Sync diário:** `scripts/daily_sync.py` roda no PC às 06:00 (hora local, 09:00 UTC) pelo agendador do Windows e depois publica `docs/`. O PC precisa estar ligado.
- **Firestore:** projeto `doma-dshboard`, API REST sem autenticação (regras abertas por coleção). Coleções usadas: `content_calendar_items`, `team_action_item_status` (status e `updated_at` de cada tarefa), `team_manual_items`, `ebook_email_deliveries`, `library_articles`, `sop_items`. Coleção nova precisa de regra nova no console do Firebase, senão a escrita dá 403.
- **WordPress:** `scripts/env_utils.py` carrega `WP_URL`, `WP_USERNAME`, `WP_APP_PASSWORD`. Toda chamada precisa de User-Agent de navegador, senão o ModSecurity devolve 406. Páginas Elementor guardam o conteúdo em `meta._elementor_data`, não em `content.raw`. Depois de editar, limpar o cache do Elementor.
- **Snippets do WordPress:** `GET/POST /wp-json/code-snippets/v1/snippets/<id>`. Id 7 é o Meta Pixel, id 11 é o Consent Mode, id 12 é o banner de cookies (custom, não é o Complianz).
- **Armadilhas de ferramenta:**
  - `pandas` não está no venv do projeto, usar o módulo `csv`.
  - Python em background precisa de `-u`, senão a saída só aparece no fim.
  - `str.find()` devolvendo -1 e depois fatiado imprime o começo do arquivo e parece um achado. Checar `-1` antes.
  - Há um filtro de texto que bloqueia a gravação de arquivos com travessão longo, aspas curvas e palavras como "hoje" ou "amanhã". Em arquivos de texto, escrever datas absolutas e frases curtas.

## 6. Preferências do usuário (valem em toda sessão)

- Responder em português, resposta curta, sem enrolação (modo caveman ultra do `~/.claude/CLAUDE.md`).
- Texto gerado passa pela skill `no-ai-text` em silêncio: sem travessão, sem abertura nem fechamento de garganta, sem "não é X, é Y".
- Quando o usuário diz "você decide a melhor forma", decidir e avisar o que foi decidido, em vez de perguntar de novo.
- Não marcar pendência como resolvida sem verificar ao vivo. Dizer claramente quando algo foi inferência.
