# aimarket-agent — Guía del SDK y la CLI de Python

El **agente consumidor** de referencia para el [AIMarket Protocol v2](https://github.com/alexar76/aimarket-protocol), escrito en Python puro. Un solo `pip install` permite que cualquier agente de servidor, herramienta de LangChain o CLI **descubra → pague → invoque** capabilities a lo largo de la federación AIMarket.

> **Hub en vivo:** [modelmarket.dev](https://modelmarket.dev) · **Ecosistema:** [modeldev.modelmarket.dev](https://modeldev.modelmarket.dev) · **Repo:** [alexar76/aimarket-agent](https://github.com/alexar76/aimarket-agent) · **SDKs multiplataforma:** [`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md)

---

## 1. Qué es

`aimarket-agent` es el miembro en **Python** de la familia de SDKs de AIMarket. Hace una sola cosa: convierte una tarea en lenguaje natural en una llamada a una capability descubierta, pagada e invocada, y devuelve una factura de materiales firmada.

Es deliberadamente ligero — solo `httpx` + `cryptography`, sin FastAPI, sin base de datos — de modo que encaja sin fricción en una herramienta de LangChain, un worker en segundo plano, una Lambda o una invocación de una línea desde la CLI. Este paquete pertenece a la línea **Python 2.1.x** (los SDKs de Dart/TypeScript/Rust forman una línea `0.1.x` aparte — véase [§8 Versionado](#8-versionado)).

---

## 2. Instalación

```bash
pip install aimarket-agent
```

| Requisito | Valor |
|-------------|-------|
| Python | **>= 3.11** |
| Dependencias en runtime | `httpx>=0.28`, `cryptography>=44` |
| Script de consola | `aimarket-agent` |
| Licencia | MIT |

---

## 3. El modelo — un consumidor sin estado (sin wallet aquí)

Esto es lo más importante de entender, porque el agente de Python es **arquitectónicamente distinto** de los SDKs multiplataforma.

| | Python `aimarket-agent` (este paquete) | Dart / TypeScript / Rust ([`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md)) |
|---|---|---|
| Estado | **Consumidor HTTP sin estado** | Mantiene una wallet / clave de firma |
| Wallet/seed en la API pública | **Ninguna** | `walletKey` (seed Ed25519), opcional `ethereumPrivateKeyHex` |
| Firma la petición de invoke | **No** se envía `X-Market-Signature` | Sí — `X-Market-Signature: ed25519:<base64>` |
| Dónde vive la autenticación | **Del lado del hub** | Del lado del cliente |
| Criptografía que *sí* usa | **Verifica** los receipts del hub contra la clave pública Ed25519 del hub | Firma peticiones + verifica receipts |

No hay **ningún `walletKey`, ninguna seed ni ninguna firma** en toda la API pública de Python. El agente nunca produce una cabecera de firma. La autorización de pago se gestiona del lado del hub; el agente simplemente habla con los endpoints v2 del hub sobre JSON/HTTP plano y referencia un canal de pago por id (`X-Payment-Channel`) y un id de afiliado opcional (`X-AIMarket-Affiliate`).

Lo que el agente *sí* puede hacer criptográficamente es **verificar** lo que llega de vuelta. Cuando `verify_receipts=True` (el valor por defecto), obtiene la clave pública Ed25519 anunciada por el hub desde `/.well-known/ai-market.json` y comprueba cada receipt de invoke contra ella. El resultado de esa comprobación se expone en la respuesta (`receipt_verified`, `receipt_verify_reason`) y **nunca se lanza** como excepción — una verificación fallida no aborta tu llamada, solo marca la salida como no verificada para que decidas qué hacer.

> Si necesitas un cliente que firme las peticiones con su propia wallet (apps EVM, débitos de canal on-chain vía EIP-712), usa los SDKs multiplataforma documentados en [`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md). Son una familia de paquetes aparte.

---

## 4. Uso como librería

```python
from aimarket_agent import AIMarketAgent

agent = AIMarketAgent(
    base_url="https://modelmarket.dev",
    budget=3.00,
    affiliate_id="my_app",
    verify_receipts=True,
)
```

### Constructor

```python
AIMarketAgent(
    base_url: str,
    budget: float = 3.0,
    timeout: float = 120.0,
    affiliate_id: str = "",
    verify_receipts: bool = True,
)
```

| Argumento | Por defecto | Significado |
|----------|---------|---------|
| `base_url` | — (obligatorio) | URL del hub. Se elimina la barra final. |
| `budget` | `3.0` | Gasto máximo en USD; acota el depósito del canal y la búsqueda de descubrimiento. |
| `timeout` | `120.0` | Timeout HTTP por petición (segundos). |
| `affiliate_id` | `""` | Se envía como `X-AIMarket-Affiliate` para el reparto de ingresos; vacío = no se envía. |
| `verify_receipts` | `True` | Verifica cada receipt contra la clave Ed25519 del hub obtenida de `/.well-known`. |

### Métodos

```python
# Ciclo autónomo completo: discover → channel → invoke → settle → factura de materiales.
result = agent.run("translate spec to 5 languages + legal review")
print(f"Spent: ${result['total_spent_usd']:.2f}")
print("verified OK:", result["bill_of_materials"]["all_ok"])

# Solo descubrimiento — devuelve una lista de dicts de coincidencias de capabilities (sin invoke, sin gasto).
matches = agent.discover("summarize long documents", limit=8)
for m in matches:
    print(m["capability_id"], "$", m.get("price_per_call_usd", 0))

# Invoca una capability directamente (abre + cierra un canal alrededor de la llamada).
res = agent.invoke_single(
    "prod-translate",                       # product_id
    "translate.multi@v2",                   # capability_id
    {"text": "Hello world", "locales": ["ru", "fr", "de"]},  # input
    source_hub="local",                     # o la URL de un hub federado
)

# Verifica un único receipt fuera de banda contra la clave pública del hub.
vr = agent.verify_receipt(res["receipt"])
print(bool(vr), vr.reason)
```

| Método | Firma | Devuelve |
|--------|-----------|---------|
| `run` | `run(task: str)` | `dict` de factura de materiales (véase §6) |
| `discover` | `discover(query: str, limit: int = 8)` | `list[dict]` de coincidencias de capabilities |
| `invoke_single` | `invoke_single(product_id, capability_id, input_payload: dict, source_hub: str = "local")` | `dict` de resultado de invoke |
| `verify_receipt` | `verify_receipt(receipt: dict)` | `receipts.VerifyResult` (truthy cuando se verifica; tiene `.reason`) |
| `close` | `close()` | cierra el `httpx.Client` subyacente |

El agente es además un context manager, de modo que el cliente HTTP siempre se limpia:

```python
with AIMarketAgent(base_url="https://modelmarket.dev", budget=1.50) as agent:
    matches = agent.discover("code review")
```

---

## 5. Uso de la CLI

Al instalar el paquete se añade un script de consola `aimarket-agent` a tu `PATH`.

```bash
# Ciclo autónomo completo
aimarket-agent run "translate spec to 5 languages + legal review" \
  --base-url https://modelmarket.dev \
  --budget 3.00

# Descubrir capabilities (sin gasto)
aimarket-agent search "code review" --base-url https://modelmarket.dev

# Invocar una única capability — la referencia es product_id/capability_id
aimarket-agent invoke prod-translate/translate.multi@v2 \
  --base-url https://modelmarket.dev \
  --input '{"text":"Hello world"}'
```

| Comando | Posicional | Flags |
|---------|-----------|-------|
| `run` | `task` | `--base-url`, `--budget`, `--affiliate`, `--json` |
| `search` | `query` | `--base-url`, `--limit`, `--json` |
| `invoke` | `capability_ref` (`product_id/capability_id`) | `--base-url`, `--input`, `--budget` |

| Flag de la CLI | Por defecto | Descripción |
|----------|---------|-------------|
| `--base-url` | `http://127.0.0.1:9083` | URL del hub (valor por defecto para desarrollo local; usa `https://modelmarket.dev` para el hub en vivo) |
| `--budget` | `3.0` | Presupuesto máximo en USD |
| `--affiliate` | `""` | Id de afiliado para el reparto de ingresos (solo `run`) |
| `--limit` | `8` | Máximo de resultados de búsqueda (solo `search`) |
| `--input` | `{}` | Payload de entrada JSON (solo `invoke`) |
| `--json` | off | Emite JSON en crudo en lugar del resumen legible para humanos |

`run` escribe un archivo de auditoría `bill_of_materials.json` en el directorio actual y termina con código distinto de cero si el ciclo no se completó por completo con éxito. Ejemplo de salida legible para humanos:

```
[plan]  translate.multi@v2  (est $0.40)
[call]  translate.multi@v2 $0.40 ✓
[settle] used $0.40, refund $2.60
[total] $0.40
[saved] bill_of_materials.json
```

---

## 6. El ciclo de 5 fases, tal como lo ejecuta Python

`run(task)` impulsa el mismo ciclo de vida del valor canónico de 5 fases que los SDKs multiplataforma, mapeado sobre los endpoints v2 del hub. La diferencia está únicamente en *quién posee la clave* (§3) — las fases son idénticas.

| # | Fase canónica | Endpoint del hub (v2) | Qué hace `run()` |
|---|-----------------|-------------------|-------------------|
| 1 | **Discovery** | `GET /.well-known/ai-market.json` → `GET /ai-market/v2/search` | lee el documento well-known, construye el verificador de receipts a partir de la clave del hub, luego busca por `intent`/`budget`/`limit` y sintetiza un plan de un solo paso a partir de la primera coincidencia |
| 2 | **Channel** | `POST /ai-market/v2/channel/open` | abre un canal pre-financiado dimensionado a `budget`; si el hub devuelve `404` (sin plugin de canales) continúa silenciosamente sin canal |
| 3 | **Invoke** | `POST /ai-market/v2/invoke` | llama a la capability con las cabeceras `X-Payment-Channel` (si se abrió un canal) y `X-AIMarket-Affiliate` (si está fijado) — **sin cabecera de firma** |
| 4 | **Settle** | `POST /ai-market/v2/channel/close` | cierra el canal y registra la liquidación (usado / reembolso) |
| 5 | **Verify** | *(local)* | cuando `verify_receipts=True`, comprueba cada receipt contra la clave pública Ed25519 del hub y estampa `receipt_verified` / `receipt_verify_reason` en el resultado |

Para un gasto predecible, `run()` limita el plan a la **primera** capability coincidente (los DAGs multipaso son una funcionalidad futura a nivel de protocolo). Usa `discover()` + `invoke_single()` si quieres conducir los pasos por tu cuenta.

El dict de factura de materiales devuelto tiene estas claves:

```python
{
  "task": "...",
  "plan": [{"product_id": ..., "capability_id": ..., "source_hub": ..., "est_price_usd": ...}],
  "results": [ { "success": True, "result": {...}, "price_usd": 0.40,
                 "receipt": {...}, "receipt_verified": True, "receipt_verify_reason": "..." } ],
  "settlement": {"used_usd": 0.40, "refund_usd": 2.60},
  "channel_id": "ch_a8f3",
  "total_spent_usd": 0.40,
  "all_ok": True,
  "protocol_version": "v2",
  "agent_version": "2.0.0"
}
```

`run()` devuelve `{"task": ..., "ok": ..., "bill_of_materials": <el dict de arriba>, "total_spent_usd": ...}`.

---

## 7. Presupuesto y errores

**Presupuesto.** `budget` es el techo de gasto en USD. Se envía como el parámetro `budget` en el descubrimiento (de modo que el hub solo devuelve capabilities que puedes costear) y es el `deposit_usd` usado para pre-financiar el canal de pago. El depósito no gastado se reembolsa en el momento de la liquidación (`settlement.refund_usd`). Como `run()` invoca solo la primera coincidencia, un único `run()` gasta como mucho el `price_usd` de una capability.

**Errores.** El agente es defensivo por diseño: los fallos de descubrimiento y liquidación se capturan y se devuelven como datos, no se lanzan como excepciones. Las respuestas de invoke con un estado HTTP significativo se convierten en entradas de resultado estructuradas:

| Condición | HTTP | Qué obtienes de vuelta |
|-----------|------|-------------------|
| Falló el discovery / search | 4xx/5xx | `{"error": "discovery failed: ..."}` o `{"error": "search failed: ..."}` (sin plan) |
| Sin plugin de canales en el hub | 404 | el canal se abre silenciosamente como `""`; el ciclo continúa sin canal |
| Saltó la barrera de seguridad | 403 | entrada de resultado `{"safety_blocked": True, "category": ..., "reason": ...}`; el ciclo se detiene |
| Pago requerido | 402 | entrada de resultado `{"payment_required": True, "detail": ...}`; el ciclo se detiene |
| Otro fallo de invoke | otro | entrada de resultado `{"error": "HTTP <code>", "capability_id": ...}`; el ciclo se detiene |
| El receipt falló la verificación | 200 | el resultado se conserva, pero `receipt_verified=False` + `receipt_verify_reason`; **no** se lanza |

Cuando la barrera de seguridad bloquea una llamada (inyección, PII, etc.), el hub devuelve HTTP 403 con un receipt de rechazo firmado y el canal se reembolsa automáticamente — de modo que una llamada bloqueada no cuesta nada.

---

## 8. Versionado

Este paquete sigue la línea **Python 2.1.x**. Eso es intencional y está separado de los SDKs de Dart/TypeScript/Rust, que comparten la línea multilenguaje **0.1.x**. Todos ellos apuntan al **AIMarket Protocol v2**.

| Paquete | Registro | Línea de versión |
|---------|----------|--------------|
| `aimarket-agent` (este) | PyPI | **2.1.x** |
| `@aimarket/agent` | npm | 0.1.x |
| `aimarket_agent` | pub.dev | 0.1.x |
| `aimarket-agent` (crate) | crates.io | 0.1.x |

La línea de Python se publicó primero (CLI, bucle `run()`, registro de auditoría BOM, canales con confianza en el hub, sin wallet en la API pública); la línea multilenguaje se lanzó junto con la firma de invocaciones del lado del cliente mediante **Ed25519** (más una clave opcional EIP-712/secp256k1 para débitos de canal on-chain). Python podría pasar a la **3.x** si/cuando su API pública gane paridad explícita de wallet y firma. Justificación completa y la matriz de disparadores de release: [`../../docs/sdk-version-policy.md`](../../docs/sdk-version-policy.md).

---

## 9. Documentación relacionada

- [SDKs multiplataforma de AIMarket (Dart / TypeScript / Rust)](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md) — la familia de SDKs que posee la wallet
- [Política de versionado de los SDKs](../../docs/sdk-version-policy.md) — por qué Python es 2.x y los demás son 0.1.x
- [Oracles](https://github.com/alexar76/oracles) — aleatoriedad verificable, VDF, consenso, capabilities de reputación descubribles desde este agente
- [AIMarket Hub](https://github.com/alexar76/aimarket-hub) — cataloga y enruta capabilities (la `base_url` a la que apuntas)
- [Documento well-known del hub en vivo](https://modelmarket.dev/.well-known/ai-market.json)

---

🇬🇧 [English](en.md) · 🇷🇺 [Русский](ru.md) · 🇪🇸 [Español](es.md)
