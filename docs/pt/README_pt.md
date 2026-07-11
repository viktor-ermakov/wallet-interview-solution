# Wallet Interview Challenge

Projeto técnico de entrevista. Construa uma **Carteira Digital** em Python com:

- API REST (FastAPI + PostgreSQL)
- Integração com gateway de pagamento (Stripe ou Mercado Pago)
- Agente de IA para consultas em linguagem natural (Anthropic Claude com tool use)

---

## Leia antes de começar

| Documento | Conteúdo |
|---|---|
| [docs/BUSINESS_SPEC.md](docs/BUSINESS_SPEC.md) | O que construir — regras de domínio, requisitos funcionais, especificação do agente |
| [docs/TECHNICAL_SPEC.md](docs/TECHNICAL_SPEC.md) | Stack sugerida, estrutura de projeto, padrões de design |

---

## Como começar

```bash
cp .env.example .env        # preencha suas chaves de API
docker compose up --build   # sobe a API + PostgreSQL
```

A API estará disponível em `http://localhost:8000`.  
Documentação interativa em `http://localhost:8000/docs`.

---

## O que já está aqui

O repositório inclui:

- `src/` — skeleton com modelos, schemas e rotas. **Toda a lógica de negócio é `raise NotImplementedError`** — sua missão é implementá-la.
- `tests/` — estrutura de testes com casos nomeados para guiar a cobertura.
- `pyproject.toml`, `Dockerfile`, `docker-compose.yml` — prontos para uso.

---

## Entregáveis

1. **Implementação funcional** que sobe com `docker compose up`
2. **Testes** — unitários para a lógica de domínio (sem DB) e ao menos um teste de integração ponta a ponta
3. **Seção no README** explicando as escolhas de design feitas (gateway escolhido, decisões de arquitetura, trade-offs)

---

## Prazo estimado

4–8 horas. Priorize correção e clareza em vez de completude. Uma implementação parcial bem projetada vale mais do que uma implementação completa e apressada.

---

## Restrições

- Python 3.11+
- Deve rodar via Docker — sem dependências no host
- Use LangChain para o agente — o provider de LLM deve ser trocável via configuração
- Sem frontend — apenas a API
