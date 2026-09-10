# Pluto

Painel desktop modular para conciliações financeiras e automações RPA.

## Atividades

### Conferência de Recebimentos

Concilia, nos dois sentidos, os relatórios:

- OPERA em XML;
- vendas da Rede em XLSX;
- lançamentos do CMFlex em XLSX.

O vínculo OPERA × CMFlex usa o número da transação ou o fólio associado ao documento. O vínculo OPERA × Rede usa data, meio de pagamento, bandeira, valor e final do cartão quando disponível. Registros sem contrapartida são mantidos como divergências.

### Conferência Booking × OPERA

O RPA usa DrissionPage com navegador visível para:

1. entrar no extranet da Booking;
2. habilitar e extrair todas as colunas das reservas;
3. selecionar no OPERA o hotel/resort informado na interface;
4. consultar as reservas elegíveis no OPERA;
5. comparar o valor final da Booking com o valor do OPERA;
6. gerar CSV e Excel na pasta escolhida.

Ao concluir, a interface permite filtrar os resultados por situação e exportar
uma cópia do relatório Excel para outro local.

As credenciais são utilizadas somente durante a execução e os campos de senha são limpos ao final.

## Execução

Requisitos: Python 3.12, `uv` e navegador Chromium compatível.

```powershell
uv sync
uv run python main.py
```

## Testes

```powershell
uv run python -m unittest discover -s tests -v
```

## Estrutura

```text
automations/
  booking.py          # fachada pública e composição Booking × OPERA
  booking_models.py   # configuração, resultados e contratos de dependências
  booking_domain.py   # parsing e regras puras de conciliação
  booking_browser.py  # integração com Booking, OPERA e Chromium
  booking_reports.py  # persistência CSV e Excel
  booking_service.py  # orquestração do caso de uso
  recebimentos.py   # parsers, conciliação e exportação
pluto_ui/
  app.py            # interface Flet
tests/
main.py
```

O fluxo Booking × OPERA usa injeção de dependências por composição. Assim, as
regras de comparação e a orquestração podem ser testadas sem abrir o navegador,
enquanto `booking.py` preserva uma API única para a interface e integrações.
