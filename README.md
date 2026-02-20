# Agente de Compras: pesquisa, comparação e alertas no WhatsApp

Este projeto cria um agente que:
- pesquisa produtos em sites específicos (configuráveis);
- compara fornecedores por preço;
- monta planilhas Excel com comparativo + histórico;
- monitora preços e dispara alertas no WhatsApp (Twilio).

## 1) Requisitos

- Python 3.11+
- Conta Twilio (para envio WhatsApp)

## 2) Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3) Configuração

1. Copie o arquivo de fontes:

```bash
cp config/sources.example.json config/sources.json
```

2. Edite `config/sources.json` e ajuste URLs/seletores CSS de cada site.

3. (Opcional, para alertas WhatsApp) Copie variáveis de ambiente:

```bash
cp .env.example .env
```

Preencha os campos Twilio no `.env`.

## 4) Executar

### Rodar uma vez (bom para cron)

```bash
python -m src.agent --once
```

### Rodar continuamente (a cada 60 min)

```bash
python -m src.agent --interval-minutes 60
```

## 5) Saídas

- `data/offers_latest.xlsx`
  - Aba `comparativo_atual`: ranking de ofertas por preço
  - Aba `resumo_fornecedores`: média/mínimo por fornecedor
  - Aba `historico`: todas as coletas
- `data/price_history.csv`: histórico bruto incremental

## 6) Agendamento sugerido (Linux cron)

```bash
crontab -e
```

Exemplo para coletar a cada 30 minutos:

```cron
*/30 * * * * cd /workspace/An-lise-HEC && /usr/bin/python -m src.agent --once >> data/agent.log 2>&1
```

## 7) Observações importantes

- Cada site tem HTML diferente: seletores CSS devem ser ajustados para cada fonte.
- Alguns sites bloqueiam scraping pesado; use intervalos razoáveis e respeite termos de uso.
- Para produção, você pode evoluir para Playwright/Selenium em sites dinâmicos.
