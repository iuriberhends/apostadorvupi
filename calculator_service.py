"""
calculator_service.py — Serviço que lê fila_calculator.json, processa
confrontos no TipManager via calc_valkyrie.py, e escreve resultado_calculator.json.

Roda junto com:
  - orquestrador.py  (escreve na fila, lê resultados)
  - bot_estrelabet.py (lê tips.txt)

python calculator_service.py
"""

import json
import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

from calc_valkyrie import (
    CDP_URL, verificar_login, carregar_confronto, calcular, fechar_overlays
)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

FILA_CALCULATOR = "fila_calculator.json"
RESULTADO_CALCULATOR = "resultado_calculator.json"
POLL_INTERVAL = 3  # segundos entre checks da fila


def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] CALC | {msg}", flush=True)


def ler_fila():
    """Lê e limpa a fila do calculator."""
    if not os.path.exists(FILA_CALCULATOR):
        return []
    try:
        with open(FILA_CALCULATOR, "r") as f:
            content = f.read().strip()
            if not content:
                return []
            data = json.loads(content)
        # Limpa a fila
        open(FILA_CALCULATOR, "w").close()
        return data if isinstance(data, list) else []
    except Exception as e:
        log(f"⚠️ Erro lendo fila: {e}")
        return []


def escrever_resultado(resultado):
    """Adiciona resultado ao arquivo de resultados."""
    existentes = []
    if os.path.exists(RESULTADO_CALCULATOR):
        try:
            with open(RESULTADO_CALCULATOR, "r") as f:
                content = f.read().strip()
                if content:
                    existentes = json.loads(content)
                    if not isinstance(existentes, list):
                        existentes = []
        except:
            existentes = []
    existentes.append(resultado)
    with open(RESULTADO_CALCULATOR, "w") as f:
        json.dump(existentes, f, ensure_ascii=False, indent=2)


def processar_item(page, item):
    """Processa um item da fila: carrega confronto e calcula."""
    jogador_a = item.get("jogador_a", "")
    jogador_b = item.get("jogador_b", "")
    event_id = item.get("event_id", "")
    liga_tipo = item.get("tipo", "valkyrie")

    log(f"🔄 Processando [{liga_tipo.upper()}]: {jogador_a} vs {jogador_b} (eid={event_id})")

    resultado = {"event_id": event_id, "tips": []}

    try:
        if not carregar_confronto(page, jogador_a, jogador_b, liga_tipo=liga_tipo):
            log(f"❌ Falha ao carregar confronto: {jogador_a} vs {jogador_b}")
            escrever_resultado(resultado)
            return

        tips = calcular(page, jogador_a, jogador_b, liga_tipo=liga_tipo)
        if tips:
            resultado["tips"] = tips
            log(f"✅ {len(tips)} tip(s) encontrada(s) para {jogador_a} vs {jogador_b}")
            for t in tips:
                log(f"   → {t['bot']}: {t['tipo']} {t.get('linha', t.get('jogador', ''))}")
        else:
            log(f"⚠️ Sem tips para {jogador_a} vs {jogador_b}")

    except Exception as e:
        log(f"❌ Erro processando {jogador_a} vs {jogador_b}: {e}")

    escrever_resultado(resultado)


def main():
    log("🚀 Calculator Service iniciando...")
    log(f"   CDP: {CDP_URL}")
    log(f"   Fila: {FILA_CALCULATOR}")
    log(f"   Resultado: {RESULTADO_CALCULATOR}")

    with sync_playwright() as p:
        log("🌐 Conectando ao Chrome (TipManager)...")
        browser = p.chromium.connect_over_cdp(CDP_URL, timeout=30000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # Encontrar ou criar aba do TipManager
        page = None
        for pg in context.pages:
            if "tipmanager" in (pg.url or ""):
                page = pg
                break
        if page is None:
            page = context.new_page()

        # Verificar login
        if not verificar_login(page):
            log("❌ Falha no login do TipManager. Encerrando.")
            return

        log("✅ Calculator pronto. Aguardando fila...")

        try:
            while True:
                fila = ler_fila()
                if fila:
                    log(f"📥 {len(fila)} item(s) na fila")
                    for item in fila:
                        processar_item(page, item)
                        time.sleep(1)
                    log(f"✅ Fila processada")

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log("⏹️ Calculator Service parado.")


if __name__ == "__main__":
    main()