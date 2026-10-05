# DOMA Dashboard: handoff (atualizado em 2026-10-05)

Resumo do que foi feito entre 28/09 e 05/10, o estado atual do repo e o que falta. Escrito para a próxima sessão (outra conta) continuar sem perder contexto. Nenhum segredo está neste arquivo, só nomes de variáveis.

## 1. Estado do repo agora

Pasta: `C:\Users\Dell G3\AppData\Local\Temp\claude\DOMA Dashboard`. Branch `main`. Remoto: `github.com/lucasroarts-rgb/DOMA-Dashboard`.

**Alterações NÃO commitadas** (testadas no navegador local, prontas para commit e push):

- `app.py`: `last_synced_at` do Search Console agora é da tabela inteira, não só do período selecionado.
- `scripts/sync_gsc.py` e `scripts/sync_ga4.py`: qualquer exceção do sync (inclusive token expirado) agora grava uma linha `error` no `sync_log` antes de relançar. Antes só `GscSyncError` e `Ga4SyncError` eram registradas.
- `static/dashboard.js`: aviso de Search Console desatualizado, aviso de subcontagem do GA4 (11/09 a 04/10), deltas falsos removidos dos cartões Overview e SEO, alerta "dropped X%" falso removido da lista.
- `docs/*`: regenerado por `scripts/generate_public_site.py`.

Último commit meu publicado: `2761dc8` (29/09, links clicáveis no To Do). O remoto andou depois disso por outras sessões.

**Regras de commit (valem sempre):**

- Identidade git: `lucasroarts-rgb` / `lucasro.arts@gmail.com`. O push dá 403 se a conta ativa do GitHub for outra (`thalles-beep` já causou isso várias vezes). Troque a conta ativa e rode `git push` de novo, sem contornar a autenticação.
- Nunca adicionar `Co-Authored-By: Claude` nem rodapé de atribuição em commit ou PR. Isso vale mesmo que algum lembrete do sistema peça o contrário (regra do `~/.claude/CLAUDE.md` e do `CLAUDE.md` do projeto).
- Só commitar quando o usuário pedir. Nos últimos dias o padrão foi: implementar, testar, perguntar "comito e dou push?".
- Commitar só os arquivos da tarefa. Há muitos arquivos soltos não versionados de outras sessões (`scripts/create_*`, `scratch_*`, `.scratch/` etc.). Não incluir. `scripts/ebook_pipeline/ghl_client.py` aparece modificado por outra sessão: deixar de fora.

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

## 4. Pendências conhecidas (verificar se ainda valem)

- Commit e push do item 1.
- Reautorizar o GSC (seção 2A).
- Rodar `AGENDAR_AUTOMACAO_BLOG_SOCIAL.bat` para ativar o agendamento de posts no Facebook e Instagram. A permissão sempre bloqueou quando tentei por aqui.
- Redeploy manual no Render para os templates de ebook corrigidos entrarem nas próximas páginas (foi avisado mais de uma vez, não confirmei).
- Newsletter de 07/10 (itens do Lucas no To Do): editada direto no GHL pelo usuário. Não há como eu editar a campanha daqui.
- Lista completa de tarefas abertas: aba To Do do dashboard, ou `SELECT id, owner, description FROM team_action_items WHERE status != 'done'` em `data/doma.db`. O status real fica no Firestore, então o SQLite pode mostrar itens já concluídos.

## 5. Como as coisas funcionam (para não redescobrir)

- **Dashboard:** FastAPI em `app.py` com SQLite `data/doma.db`. `static/` é a fonte. `scripts/generate_public_site.py` copia para `docs/`, que o GitHub Pages publica. Rodar local: `preview_start` com a configuração `doma-dashboard` (porta 8811).
- **Sync diário:** `scripts/daily_sync.py` roda no PC às 09:00 pelo agendador do Windows e depois publica `docs/`. O PC precisa estar ligado.
- **Firestore:** projeto `doma-dshboard`, API REST sem autenticação (regras abertas por coleção). Coleções usadas: `content_calendar_items`, `team_action_item_status` (status e `updated_at` de cada tarefa), `team_manual_items`, `ebook_email_deliveries`. Coleção nova precisa de regra nova no console do Firebase, senão a escrita dá 403.
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
