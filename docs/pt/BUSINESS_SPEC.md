# Especificação de Negócio — Carteira Digital com Gateway de Pagamento e Agente de IA

## 1. Problema

Projete e implemente um sistema de **Carteira Digital** que permita aos usuários gerenciar seu saldo,
realizar transações financeiras por meio de um gateway de pagamento externo e consultar seu histórico
de transações em linguagem natural por meio de um agente de IA.

Este projeto avalia a capacidade do candidato de integrar sistemas externos, modelar domínios limpos,
construir APIs REST e trabalhar com agentes baseados em LLM em um contexto real.

---

## 2. Contexto de Negócio

Uma empresa de fintech precisa oferecer uma funcionalidade de carteira digital em seu produto. Os
usuários devem poder depositar fundos via cartão de crédito ou PIX (ou qualquer método do gateway),
transferir fundos para outros usuários e realizar saques para sua conta bancária cadastrada.

Para reduzir a carga no suporte ao cliente, a empresa deseja um agente de IA capaz de responder
perguntas sobre transações em linguagem natural — substituindo um painel de BI manual para os
usuários finais.

---

## 3. Atores

| Ator             | Descrição                                                                   |
|------------------|-----------------------------------------------------------------------------|
| `Usuário`        | Usuário autenticado que possui uma ou mais carteiras                        |
| `Admin`          | Operador interno que pode visualizar todas as carteiras e congelar contas   |
| `Gateway`        | Provedor de pagamento externo (Stripe ou Mercado Pago) que processa E/S     |
| `Agente de IA`   | Agente LLM que responde perguntas em linguagem natural sobre transações      |

---

## 4. Requisitos Funcionais

### 4.1 Gestão de Carteira

| ID    | Requisito                                                                               |
|-------|-----------------------------------------------------------------------------------------|
| W-01  | Cada usuário pode ter exatamente uma carteira ativa                                     |
| W-02  | A carteira armazena um saldo atual (sempre não-negativo)                                |
| W-03  | A carteira pode estar em um destes estados: `ACTIVE`, `FROZEN`, `CLOSED`               |
| W-04  | Atualizações de saldo devem ser atômicas — sem condições de corrida em requisições simultâneas |

### 4.2 Depósito (Entrada)

| ID    | Requisito                                                                               |
|-------|-----------------------------------------------------------------------------------------|
| D-01  | O usuário inicia um depósito especificando valor e método de pagamento                  |
| D-02  | O sistema cria uma transação `PENDING` e chama o gateway de pagamento                  |
| D-03  | Após confirmação do gateway, o saldo é creditado e a transação marcada como `COMPLETED` |
| D-04  | Em caso de falha do gateway, a transação é marcada como `FAILED` — o saldo nunca é alterado |
| D-05  | O webhook do gateway deve ser verificado (validação de assinatura) e idempotente        |

### 4.3 Saque (Saída)

| ID    | Requisito                                                                               |
|-------|-----------------------------------------------------------------------------------------|
| S-01  | O usuário inicia um saque especificando valor e destino (conta bancária/chave PIX)     |
| S-02  | O saque só é permitido se `carteira.saldo >= valor`                                    |
| S-03  | O saldo é debitado imediatamente (reservado) enquanto o gateway processa o pagamento   |
| S-04  | Se o pagamento falhar, o saldo reservado é devolvido à carteira                        |

### 4.4 Transferência entre Usuários

| ID    | Requisito                                                                               |
|-------|-----------------------------------------------------------------------------------------|
| T-01  | O usuário pode transferir fundos para outro usuário identificado por e-mail ou ID      |
| T-02  | A carteira de origem deve ter saldo suficiente                                          |
| T-03  | O débito e o crédito devem ocorrer em uma única operação atômica                       |
| T-04  | Transferências são internas — não há chamada ao gateway                                 |
| T-05  | Uma transferência cria dois registros de transação vinculados (débito + crédito)       |

### 4.5 Histórico de Transações

| ID    | Requisito                                                                               |
|-------|-----------------------------------------------------------------------------------------|
| H-01  | Usuários podem listar suas próprias transações com paginação                           |
| H-02  | Filtros: intervalo de datas, tipo de transação, status, intervalo de valor             |
| H-03  | Cada registro expõe: id, tipo, valor, moeda, status, created_at, descrição,            |
|       | contraparte (em transferências), gateway_reference (em transações com gateway)          |

### 4.6 Agente de Consultas

| ID    | Requisito                                                                               |
|-------|-----------------------------------------------------------------------------------------|
| A-01  | O usuário envia uma pergunta em linguagem natural sobre suas próprias transações        |
| A-02  | O agente tem acesso a ferramentas estruturadas para consultar os dados de transação     |
| A-03  | O agente retorna uma resposta clara e concisa em linguagem natural                     |
| A-04  | O agente não deve expor dados de outros usuários — todas as consultas são escopadas ao chamador |
| A-05  | O agente suporta perguntas de acompanhamento dentro da mesma sessão (multi-turn)       |
| A-06  | O agente pode gerar agregações simples: totais, médias, contagens, top-N               |

---

## 5. Requisitos Não-Funcionais

| Categoria        | Requisito                                                                         |
|------------------|-----------------------------------------------------------------------------------|
| Atomicidade      | Mutações de saldo devem usar bloqueio em nível de banco ou concorrência otimista   |
| Idempotência     | Webhooks e operações de depósito/saque devem ser idempotentes                     |
| Segurança        | Todos os endpoints requerem autenticação JWT; webhooks requerem validação HMAC    |
| Observabilidade  | Logs estruturados em cada mudança de estado de transação                          |
| Testabilidade    | A lógica de domínio deve ser testável sem dependências de infraestrutura          |
| Portabilidade    | A aplicação roda via Docker; sem dependências no host                             |

---

## 6. Modelo de Domínio

```
Usuário
 ├── id: UUID
 ├── email: str (único)
 ├── nome: str
 └── created_at: datetime

Carteira
 ├── id: UUID
 ├── user_id: UUID (FK → Usuário)
 ├── saldo: Decimal (precisão=18, escala=2)
 ├── moeda: str (ISO 4217, padrão "BRL")
 ├── status: WalletStatus (ACTIVE | FROZEN | CLOSED)
 └── updated_at: datetime

Transação
 ├── id: UUID
 ├── wallet_id: UUID (FK → Carteira)
 ├── tipo: TransactionType (DEPOSIT | WITHDRAWAL | TRANSFER_DEBIT | TRANSFER_CREDIT)
 ├── valor: Decimal (sempre positivo)
 ├── saldo_antes: Decimal
 ├── saldo_depois: Decimal
 ├── status: TransactionStatus (PENDING | COMPLETED | FAILED | REVERSED)
 ├── descricao: str (opcional)
 ├── counterpart_transaction_id: UUID (nullable, FK → Transação)
 ├── gateway_reference: str (nullable — ID externo do pagamento)
 ├── metadata: JSONB (payload do gateway, chave PIX, dados bancários, etc.)
 └── created_at: datetime
```

---

## 7. Integração com Gateway de Pagamento

O candidato pode escolher **um** dos gateways abaixo. O módulo de gateway deve ser abstraído
por uma interface `PaymentGateway` para que a implementação possa ser substituída.

| Opção          | Caso de Uso                           | Docs                                          |
|----------------|---------------------------------------|-----------------------------------------------|
| Stripe         | Depósito com cartão, payouts          | https://docs.stripe.com                       |
| Mercado Pago   | PIX, boleto, cartão de crédito (BR)   | https://www.mercadopago.com.br/developers     |

**Requisitos mínimos de integração:**

- Criar uma intenção de pagamento / preferência para depósitos
- Receber e verificar um webhook para confirmar o pagamento
- Iniciar um payout/transferência para saques
- Tratar erros do gateway de forma adequada (chaves de idempotência, retentativas)

---

## 8. Agente de Consultas — Especificação

### 8.1 Objetivo

O agente substitui uma interface manual de consultas BI para o usuário final. Ele recebe uma
pergunta em linguagem natural e retorna uma resposta baseada em dados reais de transação.

### 8.2 Arquitetura

```
Pergunta do Usuário
        │
        ▼
┌─────────────┐       chamadas de ferramenta     ┌──────────────────────┐
│  Agente LLM │ ──────────────────────────────► │  Ferramentas de       │
│ (Claude API)│ ◄────────────────────────────── │  Transação            │
└─────────────┘       resultados das ferramentas └──────────────────────┘
        │
        ▼
Resposta em Linguagem Natural
```

### 8.3 Ferramentas do Agente

O agente deve ser equipado com as seguintes ferramentas (implementadas como funções Python):

| Ferramenta               | Descrição                                                          |
|--------------------------|--------------------------------------------------------------------|
| `get_wallet_summary`     | Retorna saldo atual, status e metadados da carteira                |
| `list_transactions`      | Lista paginada com filtros (data, tipo, status, intervalo de valor)|
| `aggregate_transactions` | SUM / AVG / COUNT / MAX / MIN agrupado por tipo, período ou status |
| `get_top_transactions`   | Retorna N maiores / menores transações em um período               |
| `get_transaction_detail` | Retorna detalhes completos de uma transação pelo ID                |

### 8.4 Exemplos de Interação

```
Usuário: "Quanto gastei no total este mês?"
Agente: [chama aggregate_transactions(type=WITHDRAWAL, period=current_month)]
Agente: "Você gastou R$ 1.250,00 este mês em 8 transações."

Usuário: "Qual foi meu maior gasto?"
Agente: [chama get_top_transactions(type=WITHDRAWAL, limit=1)]
Agente: "Seu maior saque foi de R$ 500,00 em 3 de junho de 2026."

Usuário: "Mostre meus últimos 5 depósitos."
Agente: [chama list_transactions(type=DEPOSIT, limit=5)]
Agente: "Aqui estão seus últimos 5 depósitos: [lista formatada]"
```

### 8.5 Restrições

- O agente deve ser implementado com LangChain, permitindo trocar o provider de LLM via configuração
- O provider padrão configurado no repositório é Anthropic Claude
- Todas as chamadas de ferramenta devem ser escopadas à carteira do usuário autenticado
- O agente deve tratar casos onde nenhum dado corresponde à consulta de forma adequada
- O histórico de conversa deve ser mantido entre turnos dentro de uma sessão

---

## 9. Superfície da API (Alto Nível)

```
POST   /auth/register              — criar conta de usuário
POST   /auth/token                 — obter JWT

GET    /wallet                     — obter carteira do usuário atual
POST   /wallet/deposit             — iniciar depósito via gateway
POST   /wallet/withdraw            — iniciar saque via gateway
POST   /wallet/transfer            — transferência entre usuários

GET    /transactions               — listar transações (paginado + filtrado)
GET    /transactions/{id}          — detalhe de transação

POST   /webhooks/gateway           — receber evento do gateway (sem auth, verificação HMAC)

POST   /agent/chat                 — enviar mensagem ao agente
GET    /agent/sessions/{id}        — recuperar histórico da conversa
```

---

## 10. Fora do Escopo

Os itens abaixo estão explicitamente excluídos do escopo do desafio:

- Conversão entre múltiplas moedas
- Fluxos de chargeback / disputa
- KYC / verificação de identidade
- Notificações push
- Dashboard administrativo
- Deploy em produção / Kubernetes
