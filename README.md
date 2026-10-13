# FacturExtract v2

[Español](README.es.md) · **English**

An agent that reads photos of Chilean electronic invoices, extracts their data with a vision model, validates it with code-based rules, and leaves the final call to a person when something doesn't add up. It records invoices, builds the monthly Excel file, and alerts on due dates.

Built with **LangGraph** and traced with **LangSmith**. It is the second version of [FacturExtract](https://github.com/jarpaivan-wq/FacturExtract), redesigned from scratch.

> Every invoice, tax ID (RUT) and company shown in the screenshots is synthetic. This repository contains no real data.
>
> Note: node names, fields and UI labels are in Spanish because the domain (Chilean invoicing, SII rules) is Chilean. Each one is explained where it first appears.

## The core idea

**Only one node uses a model: `extraer` (extract).** Everything else (validation, decisions, saving, alerts) is deterministic code. The model proposes, the code decides, and a person approves anything doubtful. The model cannot give the system any order: it only returns fields.

## How it works

![Extraction graph](docs/img/grafo_extraccion.png)

*Solid lines: fixed steps. Dashed lines: decisions based on the result. Human pauses: `revision_humana` (human review) and `configurar_pago` (payment setup).*

1. **Register and load the image.** One photo is accepted, or two if they are the front and back of the same invoice.
2. **Extract.** The vision model returns structured fields. Anything it can't read comes back as null; it never fills a gap by guessing.
3. **Validate.** A subgraph of code rules checks the extracted data (table below).
4. **Decide.** If everything passes, the invoice is ready to confirm. If it's a duplicate, it is blocked. If there are findings, the model is asked to read the image again, with what the code found passed back to it (up to 2 retries).
5. **Human review.** If the findings persist, the invoice is flagged red and a person decides: approve as is, correct and revalidate, or reject.
6. **Set payment and save.** Pending (on credit, with an alert) or paid by cash, transfer or check. A check requires a document number. Invoice, line items and follow-up are saved in a single transaction and the month's Excel is regenerated.

### Validation rules

| Rule | What it checks | Type |
|---|---|---|
| V01 | Required data present (number, issuer and receiver RUT, issue date, total, at least one line) | Blocking |
| V02 | RUT with valid format and check digit (modulo 11) | Blocking |
| V03 | Issue date valid and not in the future | Blocking |
| V04 | Due date valid and not earlier than issue date. If it is missing on an invoice that looks like credit, it is only a warning (it will be required when setting payment) | Blocking / Warning |
| V05 | Net + exempt + VAT + other taxes = total (1 peso tolerance) | Blocking |
| V06 | VAT close to 19 % of net on taxable invoices | Blocking |
| V07 | Sum of lines matches net + exempt | Blocking |
| V08 | Per line: quantity × price − discount = line total | Warning |
| V10 | Document already registered (same issuer RUT, number and type) | Duplicate |
| V11 | An additional tax must come with its name printed on the document | Blocking |

![Validation subgraph](docs/img/validacion_subgrafo.png)

## When something doesn't add up

![Invoice review](docs/img/revision_factura_900009.png)

*An invoice with an inconsistent total: net + VAT add up to 778,379 but the printed total is 785,379. The agent re-reads the image with the validation findings and, when the error persists, hands it to a person.*

This screen offers five actions:

- **Approve as is** («Aprobar tal cual»): the reading is accepted without revalidating and marked as manually approved; it then moves on to setting the payment, and is only saved there.
- **Correct and validate** («Corregir y validar»): fields are edited and validated again.
- **Reject** («Rechazar»): the invoice is not saved.
- **Retry reading** («Reintentar lectura»): sends the photo to the model again to read it from scratch. It is only enabled when the invoice is in a "reading error" state (for example, a network or API failure). It does not fix a bad reading, which is why it appears disabled on this invoice: it didn't have a reading error, it had an inconsistent total.
- **Close** («Cerrar»): closes the window without deciding.

## What happens inside

![LangSmith trace](docs/img/traza_900009_arbol.png)

*LangSmith trace of the same invoice: three readings (the initial one and two retries), each followed by validation, and finally the human review pause.*

## A lesson from the project

In one test, faced with a total that didn't add up, the model filled `otros_impuestos` (other taxes) with the difference (7,000 pesos) so that validation would pass, even though that tax did not exist in the image. The sum balanced, but the figure was invented. It was fixed in the design, not just in the prompt: rule V11 requires an additional tax to come with its printed name, and the retry message now asks the model to return what is printed, without adding or adjusting items. With that change, the same invoice ends up in human review with the amounts exactly as they appear in the photo.

Validation checks that the numbers balance, but not that each item exists in the image. That's why rules should demand supporting evidence, not just arithmetic consistency.

## Due dates

![Due-date graph](docs/img/grafo_vencimientos.png)

*Every pending invoice creates a follow-up. The alert is scheduled 3 calendar days before the due date, or for today if it is already overdue or very close.*

The "Vencimientos del día" (due today) window lists pending items. Each one can be **postponed** (to a date after today), **marked as paid** (with a document number if it's a check) or **closed** (it reappears the next day). Decisions are validated inside the graph and, if invalid, the question is asked again.

An already confirmed payment can also be corrected from the Invoices tab with **Edit payment…** («Editar pago…»): switching from pending to paid closes the follow-up, and the reverse recreates it.

## Desktop app

Tkinter interface that stays resident in the system tray (single instance), with optional start with Windows. Tabs: **Procesar** (process), **Lote** (batch: confirm payment for several invoices at once) and **Facturas** (invoices).

## How to run

### Requirements

- **Windows 10 or 11** (the desktop app uses Tkinter, the system tray and the Windows registry).
- **Python 3.10 or newer** (tested with 3.13), installed with the `py` launcher (the "py launcher" option in the python.org installer).
- Internet access and an **Anthropic API key** for the vision model.

### To use it: double-click

1. Download or clone the repository ([FacturExtract-LangGraph-Edition](https://github.com/jarpaivan-wq/FacturExtract-LangGraph-Edition)).
2. Double-click `Iniciar FacturExtract.bat`.
3. The first time it prepares everything on its own: it creates its virtual environment inside the data folder (`FacturExtract_datos`, in your user folder), installs the pinned dependencies, asks for the API key in a small window (it saves it in the `.env` of that folder) and opens the app. On later runs it only checks that everything is in order and opens.
4. When you close the window the app keeps running in the system tray (next to the clock); from there you can reopen it or quit for real.

### To develop

```bat
git clone https://github.com/jarpaivan-wq/FacturExtract-LangGraph-Edition.git
cd FacturExtract-LangGraph-Edition
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -c constraints.txt
.venv\Scripts\python -m pip install -r requirements-dev.txt -c constraints.txt
copy .env.example .env
```

Open `.env` and put your key in `ANTHROPIC_API_KEY=` (`.env` is not versioned; `.env.example` only lists the variable names). `requirements-dev.txt` adds `pytest` and `langgraph-cli`, which are development-only and are not part of what the client installs.

To open the app:

```bat
.venv\Scripts\python FacturExtract.pyw
```

Inside a clone with `.git` the app runs in development mode: it uses the `.env` in the project root and stores its data (database, images, Excel, logs) in the `datos\` folder, which is outside version control.

### Try it with synthetic invoices

The demo uses **synthetic invoices only**: fictitious issuers and RUTs, drawn by `evaluacion/sinteticas.py`. This command generates three photos in `evaluacion\salida\`:

```bat
.venv\Scripts\python -c "from pathlib import Path; from evaluacion import sinteticas; s = Path('evaluacion/salida'); s.mkdir(parents=True, exist_ok=True); [(s / f'demo_{n}.jpg').write_bytes(sinteticas.a_jpeg(sinteticas.generar(n)[1])) for n in (1, 2, 3)]"
```

In the **Procesar** tab use **Agregar fotos…** ("Add photos…") and pick those images, then check the result in **Lote**. Each reading is a real call to the model, so it uses API credit. To measure the extractor against a set of synthetic invoices: `.venv\Scripts\python -m evaluacion.ejecutar --n 10`.

### Enable LangSmith (optional, development only)

Add these variables to `.env` (remove the `#` from the matching lines of `.env.example`) and restart the app:

```
LANGSMITH_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=facturextract-v2
```

With `LANGSMITH_TRACING` set to `false` or undefined, nothing is sent. Traces include the extracted fields (RUTs, amounts): use them only with synthetic invoices and do not enable them on a client's machine with real invoices. The photo is not part of the trace, only its path.

### Open LangGraph Studio (optional, development only)

`langgraph.json` registers both graphs, `extraccion_facturas` and `atencion_vencimientos` (their factories are in `studio/grafos.py`). With `langgraph-cli` installed (it comes with `requirements-dev.txt`):

```bat
.venv\Scripts\langgraph dev --no-reload --allow-blocking
```

The server listens on `http://127.0.0.1:2024`; open `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`. Both flags are needed in this project: `--allow-blocking` because the nodes read and write to disk (without it Studio rejects them), and `--no-reload` so the server does not restart when it detects file changes. Studio works with a **temporary** database, Excel and images, so it does not touch `datos\`.

To run the extraction from Studio, use a synthetic photo as input (the path must be absolute):

```json
{"factura_id": "demo-1", "intentos": 0, "origen": "modelo", "hallazgos": [],
 "rutas_imagen": ["C:\\path\\to\\evaluacion\\salida\\demo_1.jpg"]}
```

Pauses are resumed with `{"accion": "aprobar"}` (review) and `{"accion": "confirmar", "pago": {"estado": "pendiente", "nro_documento": null, "fecha_alerta": null}}` (payment).

### Tests

```bat
.venv\Scripts\python -m pytest
```

It includes interface tests that open real Tkinter windows, so avoid using the computer while they run.

## Observability

LangSmith tracing is optional and is enabled with environment variables. It records each step with its timings, inputs and outputs. Free-plan traces are kept for a limited time, which is why this repository includes screenshots in `docs/capturas`.

The graph can be opened in LangGraph Studio thanks to `langgraph.json`.

More screenshots: [`docs/capturas`](docs/capturas)

## Tests

A pytest suite covering the validation rules, date and alert logic, persistence and the interface.

## Stack

Python · LangGraph · LangSmith · vision model · SQLite · Tkinter · read-only monthly Excel

This project is licensed under the MIT License; see [LICENSE](LICENSE).
