# OmniRoute — setup

OmniRoute (https://github.com/diegosouzapw/OmniRoute) é um proxy/gateway local que
roteia chamadas de IA entre 291 provedores (90+ com camada grátis), com endpoint
único compatível com OpenAI/Anthropic/Gemini. Roda como servidor na sua máquina em
`http://localhost:20128` — não é um skill/plugin do Claude Code, é um processo
separado que precisa estar de pé para os comandos abaixo funcionarem.

## 1. Instalar e rodar

```bash
npm install -g omniroute
omniroute
```

Sobe em `http://localhost:20128`. Dashboard em `http://localhost:20128/dashboard`.

Alternativas: Docker, pnpm, Electron (desktop) — ver README do projeto,
seção "More install methods".

## 2. Pegar a chave virtual

No dashboard (`http://localhost:20128/dashboard/cli-code`), gere uma chave
`sk-...` do OmniRoute (não é chave de nenhum provedor — é a chave interna que
o proxy usa pra autenticar as ferramentas locais).

## 3. Apontar o Claude Code para o OmniRoute

**Isso é opcional e só deve ser ativado depois que o servidor estiver rodando** —
sem ele no ar, o Claude Code para de conseguir falar com a Anthropic.

Crie (ou edite) `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:20128",
    "ANTHROPIC_AUTH_TOKEN": "sk-sua-chave-omniroute"
  }
}
```

Note: raiz do gateway, sem `/v1` no final, para o Claude Code especificamente.

Teste: `claude "diga oi"`.

Para voltar ao normal (falar direto com a Anthropic), remova o bloco `env` ou
delete `~/.claude/settings.json`.

## 4. Outras ferramentas neste projeto

Se quiser rotear Codex, OpenCode, Cursor CLI etc. pelo mesmo endpoint, o guia
completo por ferramenta está em `docs/reference/CLI-TOOLS.md` no repositório do
OmniRoute (variáveis `OPENAI_BASE_URL`, `GEMINI_BASE_URL`, etc.).
