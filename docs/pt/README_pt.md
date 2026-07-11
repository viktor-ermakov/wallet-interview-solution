# Decisões de Design

## Como Começar

Configure o ambiente virtual:
```bash
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
cp .env.example .env        # preencha suas chaves de API
docker compose up --build   # inicia a API + PostgreSQL
```

A API estará disponível em `http://localhost:8000`.
Documentação interativa em `http://localhost:8000/docs`.

---

## Visão Geral

Este documento descreve como a Carteira Digital especificada em `BUSINESS_SPEC.md` e
`TECHNICAL_SPEC.md` foi de fato construída e — tão importante quanto isso — onde ela foi
deliberadamente deixada aquém do "pronto para produção", dado o orçamento de tempo do projeto.
Nada abaixo é omitido: cada trade-off é apontado no momento em que ocorre, em vez de reunido
em um único aviso ao final.

**O que está totalmente funcionando:** depósitos (de ponta a ponta, incluindo webhooks reais
do Stripe), transferências, travamento de saldo/concorrência, autenticação, histórico de
transações e o agente de consultas de IA.
**O que está parcial:** saques (totalmente funcionais contra o `StubGateway`, bloqueados pelo
Stripe Connect para repasses reais — ver §1.5). Tudo mais fora de escopo está listado na §6.

Se você só quer a versão resumida: o **Stripe** foi o gateway, o **travamento de linhas do
PostgreSQL** cuida da concorrência, **LangChain + Claude** alimentam o agente, e as três
maiores partes intencionalmente incompletas são os saques via Stripe Connect, sessões
persistentes do agente e migrações Alembic. Continue lendo para entender o raciocínio por
trás de cada uma.

### Sumário

0. [Como Começar](#como-começar)
1. [Gateway de Pagamento: Stripe](#1-gateway-de-pagamento-stripe)
2. [Lógica de Domínio da Carteira](#2-lógica-de-domínio-da-carteira)
3. [Autenticação](#3-autenticação)
4. [Agente de IA](#4-agente-de-ia)
5. [Estratégia de Testes](#5-estratégia-de-testes)
6. [Explicitamente Fora de Escopo](#6-explicitamente-fora-de-escopo)

---

## 1. Gateway de Pagamento: Stripe

O **Stripe** foi escolhido em vez do Mercado Pago por dois motivos práticos: possui um modo de
teste maduro e compatível com operações assíncronas (cartões de teste, uma CLI para
encaminhamento local de webhooks) que tornou possível um teste real de ponta a ponta sem
precisar de uma conta bancária, e os padrões do `config.py` já apontavam para o Stripe
(`GATEWAY_PROVIDER=stripe`), sugerindo que essa era a opção pretendida como padrão.

Tudo relacionado ao gateway fica atrás do protocolo `PaymentGateway` em `gateway/base.py`, de
modo que uma implementação do Mercado Pago poderia ser adicionada depois como uma classe irmã
sem tocar em `wallet/service.py` — a camada de serviço depende sempre apenas do protocolo,
nunca do Stripe diretamente.

A partir daí, quatro subdecisões moldaram a integração:

### 1.1 Mantendo o event loop livre: `asyncio.to_thread`

O SDK Python do Stripe é síncrono, mas toda a aplicação é assíncrona. Chamá-lo diretamente de
dentro de uma `async def` bloquearia todo o event loop a cada chamada ao Stripe, travando
todas as outras requisições em andamento. Toda chamada em `gateway/stripe_gateway.py` é
envolvida em `asyncio.to_thread(...)`, empurrando-a para uma thread em segundo plano.

### 1.2 Um bug no primeiro design da chave de idempotência — e a correção

A primeira versão construía a chave de idempotência do depósito a partir de
`wallet_id + amount + description`. Isso quebrou imediatamente na prática: dois depósitos de
$20 genuinamente distintos com a mesma descrição (padrão) geravam a *mesma* chave, então o
Stripe tratava o segundo como duplicado e o rejeitava com um `IdempotencyError`. A correção foi
usar um `uuid4()` novo a cada chamada.

**Trade-off aceito:** isso significa que uma verdadeira nova tentativa em nível de rede (o
cliente reenviando exatamente a mesma requisição HTTP após um timeout) não é mais deduplicada
pelo Stripe do nosso lado — isso exigiria que o *chamador* fornecesse seu próprio cabeçalho
`Idempotency-Key` estável, o que não está no escopo atual do `BUSINESS_SPEC.md`. Vale a pena
adicionar se este projeto continuasse.

### 1.3 `StubGateway` e o fallback silencioso em `get_gateway()`

Antes de existir a integração real com o Stripe, `gateway/stub_gateway.py` foi construído como
um `PaymentGateway` deliberadamente falso, em memória — todo depósito/repasse "tem sucesso"
instantaneamente, sem chamadas HTTP. O `get_gateway()` de `gateway/factory.py` recorre
automaticamente ao `StubGateway` sempre que `STRIPE_SECRET_KEY` não está configurada,
independentemente de `GATEWAY_PROVIDER`.

**Trade-off aceito:** isso é uma conveniência de desenvolvimento, não como você gostaria que
isso fosse tratado em produção. Lá, uma credencial ausente deveria causar uma falha rígida na
inicialização, não um rebaixamento silencioso para um gateway falso. Isso está sendo sinalizado
explicitamente, em vez de apresentado como um comportamento pronto para produção.

### 1.4 Verificação de assinatura do webhook

`StripeGateway.verify_webhook` usa a verificação oficial de payload assinado do Stripe
(`stripe.Webhook.construct_event`) — é isso que impede que alguém envie via POST um falso
evento de "pagamento bem-sucedido" diretamente ao endpoint do webhook para se creditar dinheiro.
Ele também filtra os eventos de confirmação de depósito (`payment_intent.succeeded`,
`payment_intent.payment_failed`/`canceled`) entre os muitos outros tipos de evento que uma
conta Stripe real envia (`customer.created`, etc.) — qualquer outro é reconhecido com `200` e
silenciosamente ignorado, em vez de gerar erro, já que um erro faria o Stripe repetir para
sempre um evento sobre o qual intencionalmente não agimos.

Já `StubGateway.verify_webhook` não faz **nenhuma** verificação de assinatura real — apenas
interpreta o corpo JSON. Isso está documentado em seu próprio docstring e não deve ser
confundido com uma barreira de segurança.

### 1.5 Limitação conhecida: saques precisam do Stripe Connect (não implementado)

Repasses reais por usuário no Stripe exigem que o destinatário tenha uma conta conectada e
integrada via **Stripe Connect** — um fluxo separado que envolve verificação de identidade e
vinculação de conta bancária. Esse fluxo de integração ficou fora do escopo dado o orçamento de
tempo. `StripeGateway.create_payout` assume que o dicionário `destination` da carteira já
carrega um `stripe_connected_account_id` válido, e gera um `ValueError` claro se ele estiver
ausente, em vez de falhar silenciosamente ou simular sucesso.

**Resumindo:** depósitos são a parte que está genuinamente no formato de produção de ponta a
ponta; saques funcionam totalmente apenas contra o `StubGateway`.

---

## 2. Lógica de Domínio da Carteira

### 2.1 Evitando deadlocks de transferência: uma ordem fixa de travamento

Todas as mutações de saldo adquirem um lock `SELECT ... FOR UPDATE` via
`WalletRepository.get_for_update` antes de tocar em `balance`. Operações em uma única carteira
(depósito, saque) são diretas — o caso interessante são as transferências, que travam **duas**
linhas de carteira na mesma transação.

Se as carteiras fossem sempre travadas na ordem "remetente primeiro, depois destinatário",
duas transferências simultâneas em direções opostas (A→B e B→A) poderiam cada uma pegar um
lock e esperar para sempre pelo outro — um clássico deadlock. A correção: **sempre travar as
carteiras na mesma ordem fixa, independentemente do papel de remetente/destinatário**,
ordenadas pela string do UUID. Ambas as transferências então concordam com a ordem de
travamento, então uma simplesmente espera brevemente pela outra em vez de gerar um deadlock.

### 2.2 Por que depósitos e saques são assimétricos

Isso é fácil de entender errado, então vale a pena declarar explicitamente:

- **Depósito** — o saldo *não* é alterado quando `/wallet/deposit` é chamado. Uma transação
  `PENDING` é criada e o gateway é solicitado a criar uma payment intent; o saldo só é
  creditado depois, em `confirm_deposit`, quando um webhook confirma que o pagamento de fato
  foi bem-sucedido (D-02/D-03). Isso modela a realidade de que os fundos do depósito ainda não
  chegaram no momento da requisição.
- **Saque** — o saldo é debitado *imediatamente* ("reservado") quando `/wallet/withdraw` é
  chamado, antes mesmo de o repasse do gateway rodar (S-03). Se o repasse falhar depois, a
  reserva é revertida e a transação é marcada como `REVERSED` (S-04). Isso modela a realidade
  de que, uma vez que um saque é solicitado, esse dinheiro não deveria poder ser gasto
  novamente até que se confirme que o repasse falhou.
- **Transferência** — totalmente síncrona e atômica. Tanto o débito quanto o crédito acontecem
  na mesma transação de banco de dados, sem envolver nenhum gateway (T-04), já que é
  puramente interna.

### 2.3 Como os depósitos permanecem idempotentes sob webhooks repetidos

`Transaction.gateway_reference` possui uma restrição `UNIQUE` no banco de dados, e
`confirm_deposit` também verifica se a transação já está em um estado terminal (`COMPLETED` /
`FAILED`) antes de alterar qualquer coisa. Juntas, essas medidas garantem que um evento de
webhook repetido — o que os gateways de pagamento costumam enviar mais de uma vez — nunca
possa creditar uma carteira em dobro. Isso é verificado em dois níveis: um teste unitário
(`test_confirm_deposit_is_idempotent`) e um teste de integração que contorna a API para provar
que a própria restrição do banco de dados se sustenta
(`test_duplicate_gateway_reference_is_rejected`).

### 2.4 Histórico de transações: resolução de contraparte e seu custo

Conforme H-03, cada transação expõe um `counterparty_email` legível para transferências (não
apenas um `counterpart_transaction_id` opaco). Resolver isso atualmente gera uma consulta
extra por transação em uma resposta de lista — um padrão N+1, aceitável nos tamanhos de página
que esta API permite (≤100), mas não algo que escalaria bem para páginas muito grandes. Uma
versão de produção resolveria em lote todos os ids de contraparte em uma única consulta.

### 2.5 Por que a camada de serviço é testável sem um banco de dados

`WalletService.__init__` aceita um parâmetro opcional `repo`, recorrendo à construção de um
`WalletRepository(session)` real caso nenhum seja fornecido. Essa única mudança é o que torna
toda a camada de serviço testável com um repositório falso em memória, sem nenhuma dependência
de banco de dados ou de rede — atendendo diretamente ao requisito não funcional de que "a
lógica de domínio deve ser testável sem dependências de infraestrutura".

---

## 3. Autenticação

### 3.1 O subject do JWT é o ID do usuário, não seu e-mail

A claim `sub` de `create_access_token` guarda o UUID do usuário. Isso significa que, se um
usuário mudar seu e-mail depois, seus tokens existentes não são invalidados — a claim de
identidade do token nunca muda sob ele.

### 3.2 A criação da carteira é implícita no registro

Nada na superfície da API cria uma carteira explicitamente, então `register_user` cria uma
automaticamente junto com a linha do `User`, na mesma transação. Essa foi uma leitura
deliberada de W-01 ("cada usuário pode ter exatamente uma carteira ativa"): sem isso, todo
endpoint de carteira ficaria permanentemente quebrado para qualquer usuário recém-registrado.

### 3.3 Por que as falhas de autenticação são deliberadamente genéricas

Tanto "senha errada" quanto "usuário inexistente" retornam o mesmo `401 Incorrect email or
password`. Isso é deliberado — um erro mais específico permitiria que um atacante enumerasse
quais e-mails estão cadastrados.

### 3.4 Uma fixação de dependência conhecida: `bcrypt<4.1`

O backend bcrypt do `passlib` tem um bug de compatibilidade com o `bcrypt` 4.1+ (ele reporta
incorretamente "password cannot be longer than 72 bytes" mesmo em senhas curtas, devido a uma
API interna removida que o `passlib` ainda espera). `bcrypt<4.1` está fixado em
`pyproject.toml` para evitar isso — vale revisitar se o `passlib` lançar uma correção, já que
fixar abaixo da versão major mais recente de um pacote é, em si, uma pequena dívida de
manutenção.

---

## 4. Agente de IA

### 4.1 O escopo dos dados é estrutural, não uma instrução de prompt

`build_tools(repo, user_id)` retorna ferramentas que cada uma **encapsula** (closure sobre) o
`user_id` a partir dos argumentos da função externa. O LLM nunca vê nem fornece um id de
usuário como parâmetro de ferramenta — a decisão de a quais dados de qual usuário uma
conversa pode acessar é tomada uma única vez, antes mesmo de o LLM ser invocado, e não é algo
que se confia ao modelo respeitar via uma instrução no system prompt. É isso que de fato
impõe o A-04, e isso é verificado diretamente em `test_agent_tools.py`, semeando dados de dois
usuários diferentes no mesmo repositório falso e verificando que as ferramentas de um usuário
nunca retornam as linhas do outro.

### 4.2 O armazenamento de sessão é apenas em memória

O `InMemorySessionStore` de `agent/session.py` mantém as conversas em um dicionário Python
simples.

**Trade-off aceito:** todo o histórico de conversas é perdido ao reiniciar o processo, e isso
não funcionaria corretamente se a API algum dia fosse escalada para múltiplas instâncias (uma
requisição de acompanhamento roteada para uma instância diferente não encontraria a sessão).
Uma versão de produção precisaria disso apoiado em Redis ou no banco de dados. Deixado como
está dado o escopo do projeto, mas explicitamente não pronto para produção como está escrito.

### 4.3 Agnóstico de provedor por construção

O `get_llm()` de `agent/agent.py` alterna entre `ChatAnthropic` e `ChatOpenAI` puramente com
base em `settings.llm_provider`, sem nenhum outro caminho de código dependendo de qual está
ativo — atendendo à exigência de que o provedor de LLM seja intercambiável apenas via
configuração.

---

## 5. Estratégia de Testes

Três camadas foram construídas, seguindo a tabela de testes do `TECHNICAL_SPEC.md`:

- **Testes unitários** (`tests/unit/`) — `WalletService`, as funções puras de autenticação
  (`hash_password`/`verify_password`/`create_access_token`) e as ferramentas do agente, todos
  testados contra fakes em memória, sem nenhuma dependência de banco de dados ou rede. Rodam
  em bem menos de um segundo.
- **Testes de integração** (`tests/integration/`) — o fluxo depósito → webhook → saldo, testado
  contra uma instância real do PostgreSQL e chamadas HTTP reais através da aplicação FastAPI,
  com o gateway forçado para `StubGateway` via um override de dependência (para que esses
  testes não dependam de quaisquer credenciais do Stripe que porventura estejam configuradas
  no `.env` de um dado desenvolvedor).
- **Verificação manual/ao vivo** — o fluxo completo, incluindo um depósito real em modo de
  teste do Stripe (via a CLI do Stripe encaminhando webhooks reais) e uma conversa real de
  múltiplos turnos com o agente de fato apoiado no Claude.

### 5.1 Um bug real encontrado e corrigido na infraestrutura de testes

O engine de banco de dados dos testes de integração era originalmente criado uma única vez, no
momento de importação do módulo. O `pytest-asyncio` cria um novo event loop por função de teste
por padrão, mas as conexões do `asyncpg` ficam permanentemente vinculadas ao event loop em que
foram abertas — então uma conexão retirada do pool em um teste seria reutilizada pelo próximo
teste rodando em um event loop já fechado, corrompendo-se de formas que se manifestavam como
`'NoneType' object has no attribute 'send'` e `cannot perform operation: another operation is
in progress`. A correção foi usar `poolclass=NullPool` no engine de teste, forçando uma conexão
nova a cada checkout em vez de reutilizar conexões do pool entre os testes.

### 5.2 Lacuna conhecida: nenhum teste de uma *assinatura* de webhook genuinamente inválida

`StubGateway.verify_webhook` não faz nenhuma verificação real de assinatura, então
`test_webhook_rejects_invalid_signature` só consegue provar que um corpo malformado (não-JSON)
é rejeitado — não que um evento do Stripe incorretamente *assinado* seja rejeitado. Um teste
real disso teria que simular (mock) `stripe.Webhook.construct_event` para lançar
`stripe.error.SignatureVerificationError` diretamente contra o `StripeGateway`, e não foi
construído dadas as restrições de tempo.

### 5.3 Lacuna conhecida: `test_agent_chat.py` (integração) não implementado

O comportamento real do agente é coberto no nível unitário (`test_agent_tools.py`, escopo e
correção de cada ferramenta) e foi verificado manualmente de ponta a ponta contra a API real do
Claude. Um teste de integração completo — atingindo `/agent/chat` via HTTP real com um LLM
simulado (stub) retornando chamadas de ferramenta determinísticas — foi deixado não
implementado; o esqueleto em `tests/integration/test_agent_chat.py` documenta exatamente o que
ele deveria cobrir.

---

## 6. Explicitamente Fora de Escopo

Mantido sem alterações a partir de `BUSINESS_SPEC.md` §10: conversão multimoeda, fluxos de
chargeback/disputa, verificação de identidade (KYC), notificações push, um painel
administrativo e implantação em produção.

Dadas as restrições reais de tempo do projeto, os seguintes itens também não foram concluídos:

- Implementação do gateway Mercado Pago (apenas Stripe)
- Integração com o Stripe Connect (bloqueia saques reais de ponta a ponta — ver §1.5)
- Migrações Alembic (o schema é criado via `Base.metadata.create_all()` na inicialização da
  aplicação — o que funciona para desenvolvimento, mas, diferente de uma migração, isso não
  consegue expressar mudanças em tabelas *existentes*, apenas criar as que ainda não existem)
- Armazenamento de sessão do agente persistente (não apenas em memória) (ver §4.2)