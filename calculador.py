"""
calculator_service.py — Serviço que roda em loop, lê fila_calculator.json,
processa no TipManager (Valkyrie OU Battle) e escreve resultado_calculator.json.

Precisa: Chrome com --remote-debugging-port=9223 aberto no TipManager.

python calculator_service.py
"""

import json
import time
import os
import sys
from playwright.sync_api import sync_playwright

# Importar os calculators
# Coloque calculador.py (Valkyrie) e calculador_battle.py (Battle) na mesma pasta
# Ou cole as funções aqui

CDP_URL = "http://localhost:9223"
URL_LOGIN = "https://tipmanager.net/login"
EMAIL = "igorpjacadastros@gmail.com"
SENHA = "IgoR*Ethan2022"

FILA_CALCULATOR = "fila_calculator.json"
RESULTADO_CALCULATOR = "resultado_calculator.json"

# URLs base por tipo
URLS_TIPMANAGER = {
    "valkyrie": "https://tipmanager.net/pt/sports/e-soccer/48118024058/alicia-vs-nicol",
    "battle": "https://tipmanager.net/pt/sports/e-soccer/48118024058/alicia-vs-nicol",
}

# ══════════════════════════════════════════════════════════════
# IMPORT CALCULATORS (inline pra não depender de imports)
# ══════════════════════════════════════════════════════════════

# Importa os módulos se existirem, senão usa funções inline
try:
    from calculador import calcular as calcular_valkyrie, carregar_confronto as carregar_valkyrie
    from calculador import verificar_login as verificar_login_valkyrie
    VALKYRIE_IMPORT = True
except ImportError:
    VALKYRIE_IMPORT = False

try:
    from calculador_battle import calcular as calcular_battle, carregar_confronto as carregar_battle
    from calculador_battle import verificar_login as verificar_login_battle
    BATTLE_IMPORT = True
except ImportError:
    BATTLE_IMPORT = False


# ══════════════════════════════════════════════════════════════
# FILA
# ══════════════════════════════════════════════════════════════

def ler_fila():
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
    except:
        return []


def escrever_resultado(resultados):
    """Adiciona resultados ao arquivo."""
    existente = []
    if os.path.exists(RESULTADO_CALCULATOR):
        try:
            with open(RESULTADO_CALCULATOR, "r") as f:
                content = f.read().strip()
                if content:
                    existente = json.loads(content)
        except:
            existente = []

    existente.extend(resultados)
    with open(RESULTADO_CALCULATOR, "w") as f:
        json.dump(existente, f, ensure_ascii=False, indent=2)


def log(msg):
    from datetime import datetime
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    log("🧮 Calculator Service iniciando...")

    if not VALKYRIE_IMPORT:
        log("⚠️ calculador.py não encontrado — Valkyrie desabilitado")
    if not BATTLE_IMPORT:
        log("⚠️ calculador_battle.py não encontrado — Battle desabilitado")

    with sync_playwright() as p:
        log(f"🔌 Conectando CDP {CDP_URL}...")
        browser = p.chromium.connect_over_cdp(CDP_URL, timeout=30000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = None
        for pg in context.pages:
            if "tipmanager" in (pg.url or ""):
                page = pg
                break
        if page is None:
            page = context.new_page()
        log(f"✅ CDP conectado — {page.url}")

        log("🚀 Calculator pronto! Aguardando fila...\n")

        while True:
            try:
                fila = ler_fila()

                if fila:
                    resultados = []

                    for item in fila:
                        tipo = item.get("tipo", "")
                        jog_a = item.get("jogador_a", "")
                        jog_b = item.get("jogador_b", "")
                        event_id = item.get("event_id", "")

                        log(f"🎯 [{tipo.upper()}] {jog_a} vs {jog_b} (event={event_id})")

                        tips = []

                        if tipo == "valkyrie" and VALKYRIE_IMPORT:
                            try:
                                if not verificar_login_valkyrie(page):
                                    log("❌ Login falhou")
                                    continue
                                if not carregar_valkyrie(page, jog_a, jog_b):
                                    log("❌ Confronto não carregou")
                                    continue
                                tips = calcular_valkyrie(page, jog_a, jog_b) or []
                            except Exception as e:
                                log(f"❌ Erro Valkyrie: {e}")

                        elif tipo == "battle" and BATTLE_IMPORT:
                            try:
                                if not verificar_login_battle(page):
                                    log("❌ Login falhou")
                                    continue
                                if not carregar_battle(page, jog_a, jog_b):
                                    log("❌ Confronto não carregou")
                                    continue
                                tips = calcular_battle(page, jog_a, jog_b) or []
                            except Exception as e:
                                log(f"❌ Erro Battle: {e}")

                        else:
                            log(f"⚠️ Tipo desconhecido ou módulo não disponível: {tipo}")
                            continue

                        if tips:
                            resultados.append({
                                "event_id": event_id,
                                "tips": tips,
                            })
                            log(f"✅ {len(tips)} tip(s) gerada(s)")
                        else:
                            log(f"📭 Sem tips válidas")

                    if resultados:
                        escrever_resultado(resultados)
                        log(f"📤 {len(resultados)} resultado(s) escritos")

                else:
                    pass  # Silencioso quando não tem fila

            except Exception as e:
                log(f"⚠️ Erro geral: {e}")
                # Tentar reconectar
                try:
                    browser = p.chromium.connect_over_cdp(CDP_URL, timeout=30000)
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else context.new_page()
                    log("🔄 Reconectado")
                except:
                    log("❌ Reconexão falhou, aguardando...")

            time.sleep(2)


if __name__ == "__main__":
    main()