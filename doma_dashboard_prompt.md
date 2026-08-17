Cole este briefing no Claude Code (numa pasta de projeto nova, vazia) para começar o dashboard da DOMA:

---

Quero construir um dashboard de marketing local (Python/FastAPI + SQLite + HTML/JS estático, sem framework front-end pesado) para a DOMA, seguindo o mesmo padrão de um projeto irmão que já existe (PreSubs/Peasy Anglais): app local rodando em 127.0.0.1, automação diária que sincroniza dados via API, site público estático gerado e publicado no GitHub Pages.

**Diferença importante em relação ao projeto irmão**: a DOMA NÃO está rodando Meta Ads nem Google Ads no momento — não construa nada de tráfego pago (sem aba de campanhas/ad sets/ads, sem CAC via ads, sem pixel/CAPI). O foco da aquisição da DOMA hoje é **orgânico**: SEO e blog. Isso deve ser o centro do dashboard, não um extra.

**Fontes de dados a integrar:**
1. **Google Search Console** — ranking (posição média), cliques, impressões, CTR, top queries de busca, evolução de posição por dia. Isso é prioridade alta.
2. **Blog / conteúdo orgânico** — se houver Google Analytics (GA4) ligado ao blog/site, trazer tráfego, páginas mais visitadas, origem do tráfego (orgânico vs direto vs social). Perguntar se há GA4 configurado antes de assumir.
3. **GoHighLevel (internamente chamamos de "Twilead")** — API de leads e contatos:
   - Novos leads captados (contagem por dia/semana, por fonte/canal se o campo existir)
   - Emails (campanhas enviadas, taxa de abertura/clique, se a API expuser isso — perguntar quais métricas de email fazem sentido)
   - Autenticação: GoHighLevel tem uma **Private Integration API key** (Settings → Private Integrations na sub-conta/location), mais simples que OAuth — não precisa fluxo interativo.

**O que NÃO construir (por enquanto):**
- Nenhuma integração com Meta Ads ou Google Ads.
- Nenhuma métrica de CPL/CAC baseada em spend de anúncio (não existe spend pago pra medir).

**Princípios de arquitetura a seguir (aprendidos no projeto irmão, aplicar aqui também):**
- **Privacidade**: nunca armazenar ou expor email/telefone/nome individual de lead — só contagens agregadas por dia/semana. Se precisar de geografia por telefone, usar só o prefixo DDI (nunca o número completo).
- **Single source of truth**: toda automação/relatório deve ler das mesmas funções de resumo que o dashboard usa — nunca recalcular métrica em dois lugares.
- **Automação diária**: um script único que sincroniza tudo (GSC, GA4 se houver, GoHighLevel), roda via tarefa agendada, publica o site estático automaticamente via git.
- **i18n**: perguntar se precisa (o projeto irmão suporta EN/FR/PT) antes de construir — pode não ser necessário pra DOMA.
- **.env para segredos**: nunca commitar chave de API, sempre `.gitignore` antes de qualquer coisa.
- Cada integração nova = 1 script de sync isolado (`sync_gsc.py`, `sync_ghl.py`, etc), 1 tabela SQLite nova, 1 função de resumo em `app.py`, 1 aba no dashboard — sem misturar lógica entre fontes.

**Primeiro passo real**: antes de escrever qualquer código, me faça perguntas — nome exato do domínio/site (pra Search Console, confirmar `.com` vs subdomínio etc, isso já me pegou uma vez), se existe GA4 configurado, qual é a location/sub-conta certa no GoHighLevel, e o que exatamente "novos leads" e "emails" devem contar (todo contato criado? só os que viraram lead qualificado? campanhas de quais listas?).

---
