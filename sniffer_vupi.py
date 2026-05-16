"""
sniffer_vupi.py — Captura todas as requisições relevantes da Vupi
durante uma sessão (especialmente apostas) pra a gente replicar via API.

USO:
  1. Chrome aberto na porta 9231, logado na Vupi
  2. python sniffer_vupi.py
  3. Faça UMA aposta na Vupi manualmente (ou várias)
  4. Ctrl+C → gera sniffer_capture_<timestamp>.json

O sniffer:
  - Filtra ruído (analytics, fontes, imagens, smartico, zaraz)
  - Foca em domínios da Vupi/Altenar
  - Captura URL, método, headers, body request, status, response body
  - Marca com 🎯 as URLs claramente relacionadas a aposta
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright


CDP_PORT = 9231

# URLs chave — sempre captura, mesmo GET
URLS_CHAVE = (
    "placewidget", "place-widget", "place_widget", "placement",
    "/place", "/bet", "/ticket", "/stake", "updatebets",
    "opensportsbook", "signin", "/auth/", "betslip",
    "geteventdetails", "getlivevents", "getliveoverview",
)

# Domínios relevantes pra captura geral
DOMINIOS_FOCO = (
    "vupi.bet.br", "biahosted.com", "sb2frontend",
    "sb2auth", "sb2betgateway", "bff.vupi",
)

# Extensões a ignorar
EXT_IGNORAR = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
               ".woff", ".woff2", ".ttf", ".ico", ".webp", ".mp4")

# Domínios de spam a ignorar
DOMINIOS_IGNORAR = (
    "googletagmanager", "google-analytics", "doubleclick",
    "facebook", "smartico", "zaraz", "newrelic", "sentry",
    "datadog", "hotjar", "fullstory", "cloudflareinsights",
    "googleadservices", "gstatic", "googleapis", "/clarity/",
    "linkedin.com/li/track", "tiktok",
)


def url_relevante(url: str, method: str) -> bool:
    u = url.lower()
    if any(d in u for d in DOMINIOS_IGNORAR):
        return False
    # Tira query string pra checar extensão
    base = u.split("?")[0]
    if any(base.endswith(ext) for ext in EXT_IGNORAR):
        return False
    # URLs chave sempre interessam
    if any(k in u for k in URLS_CHAVE):
        return True
    # POST/PUT/PATCH/DELETE em domínio relevante
    if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        return any(d in u for d in DOMINIOS_FOCO)
    return False


def is_url_chave(url: str) -> bool:
    u = url.lower()
    return any(k in u for k in URLS_CHAVE)


async def main():
    capturado: list[dict] = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = Path(f"sniffer_capture_{timestamp}.json")

    print("=" * 64)
    print(" SNIFFER VUPI")
    print("=" * 64)
    print(f"  Output: {output}")
    print()

    pw = await async_playwright().start()
    print(f"-> conectando Chrome CDP :{CDP_PORT}...")
    try:
        browser = await pw.chromium.connect_over_cdp(
            f"http://localhost:{CDP_PORT}"
        )
    except Exception as e:
        print(f"❌ Falha ao conectar CDP: {e}")
        print("   Confira se o Chrome tá aberto com --remote-debugging-port=9231")
        return

    if not browser.contexts:
        print("X Chrome sem contexts")
        return
    ctx = browser.contexts[0]

    page = None
    for p in ctx.pages:
        if "vupi.bet.br" in (p.url or ""):
            page = p
            break
    if not page:
        page = ctx.pages[0]
    print(f"  aba: {page.url}\n")

    async def handle_finished(req):
        try:
            if not url_relevante(req.url, req.method):
                return
            resp = await req.response()
            if resp is None:
                return
            try:
                body_resp = await resp.text()
            except Exception:
                body_resp = "<falha ao ler body>"
            entry = {
                "ts": datetime.now().isoformat(),
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "request_headers": dict(req.headers),
                "request_body": req.post_data,
                "status": resp.status,
                "response_headers": dict(resp.headers),
                "response_body": body_resp,
            }
            capturado.append(entry)
            marker = "🎯" if is_url_chave(req.url) else "  "
            url_show = req.url[:95]
            print(f"{marker} {req.method:6s} {resp.status} {url_show}")
        except Exception as e:
            print(f"   erro capturando: {e}")

    def on_finished(req):
        asyncio.create_task(handle_finished(req))

    # Listener no CONTEXTO inteiro pra pegar tudo (frames, popups, etc)
    ctx.on("requestfinished", on_finished)

    print("✅ SNIFFER ATIVO")
    print("   Faça UMA aposta na Vupi agora (manualmente).")
    print("   Ctrl+C pra parar e salvar.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Parando...")
    finally:
        # Pequena espera pra responses pendentes
        await asyncio.sleep(2)
        output.write_text(
            json.dumps(capturado, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n✅ {len(capturado)} requisições capturadas")
        print(f"   Salvo em: {output.resolve()}")
        chave_count = sum(1 for e in capturado if is_url_chave(e["url"]))
        print(f"   Destas, {chave_count} são URLs chave (🎯)")
        try:
            await pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
