import time
import re
import os
import requests
import socket
import sys
import random
from datetime import datetime
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse, parse_qs



# ==============================================================================
# ⚙️ CONFIGURAÇÕES
# ==============================================================================
TELEGRAM_TOKEN = "8491434153:AAF0REgAxlOgGh7oq2LKmf2Fha4NYCgaPpM"
SEU_CHAT_ID = "6622310450"
ARQUIVO_TIPS = "tips.txt"
ARQUIVO_FEEDBACK = "resultado_apostas.json"
TEMPO_VIGIA_POR_TURNO = 10
EMAIL = "fasftesxxss@gmail.com"
SENHA = "Mavic29@"
ODD_MINIMA = 1.1
ultimo_login_ok = 0  # timestamp do último login/verificação bem sucedida

# ==============================================================================
# 🔒 SISTEMA DE TRAVAl
# ==============================================================================
def trava_seguranca_escuta():
    nome_pc = socket.gethostname()
    codigo_acesso = random.randint(1111, 9999)

    print("\n" + "█" * 50)
    print("🔒 SISTEMA BLOQUEADO - AGUARDANDO LIBERAÇÃO")
    print("█" * 50)
    print(f"👤 PC: {nome_pc}")
    print("⏳ Enviando solicitação ao Admin...", flush=True)

    mensagem = (
        f"🚨 <b>SOLICITAÇÃO DE ACESSO</b>\n"
        f"💻 PC: {nome_pc}\n"
        f"🔑 Código: <code>{codigo_acesso}</code>\n\n"
        f"Digite <b>{codigo_acesso}</b> para liberar."
    )

    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": SEU_CHAT_ID, "text": mensagem, "parse_mode": "HTML"})
        print("✅ Solicitação enviada! Verifique seu Telegram.", flush=True)
    except Exception as e:
        print(f"❌ Erro de conexão: {e}")

    print("\n🎧 MODO DE ESCUTA ATIVADO (Não feche esta janela)", flush=True)

    url_updates = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    last_update_id = 0

    try:
        r = requests.get(url_updates, params={"timeout": 5})
        d = r.json()
        if "result" in d and len(d["result"]) > 0:
            last_update_id = d["result"][-1]["update_id"]
    except:
        pass

    while True:
        try:
            params = {"offset": last_update_id + 1, "timeout": 10}
            response = requests.get(url_updates, params=params)
            dados = response.json()

            if "result" in dados and len(dados["result"]) > 0:
                for msg in dados["result"]:
                    last_update_id = msg["update_id"]
                    if "message" in msg and "text" in msg["message"]:
                        texto = msg["message"]["text"].strip()
                        id_remetente = str(msg["message"]["from"]["id"])
                        if id_remetente == SEU_CHAT_ID:
                            if texto == str(codigo_acesso):
                                print("\n🔓 SENHA CORRETA! INICIANDO O ROBÔ...\n", flush=True)
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                              data={"chat_id": SEU_CHAT_ID, "text": f"✅ Liberado: {nome_pc}"})
                                time.sleep(1)
                                return True
                            elif texto.lower() == "negar":
                                print("\n❌ ACESSO NEGADO.\n", flush=True)
                                sys.exit()

            print(".", end="", flush=True)

        except KeyboardInterrupt:
            sys.exit()
        except:
            time.sleep(2)


# ==============================================================================
# 🎰 FUNÇÕES
# ==============================================================================
def extrair_jogador(texto):
    match = re.search(r'\((.+?)\)', texto)
    return match.group(1).lower() if match else ""


def salvar_log(mensagem):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {mensagem}", flush=True)


def escrever_feedback(tip, status, motivo=""):
    """Escreve feedback pro orquestrador: sucesso ou falha."""
    import json
    feedback = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": tip.get("url", ""),
        "alvo": tip.get("alvo", ""),
        "linha": tip.get("linha", 0),
        "status": status,
        "motivo": motivo,
    }
    existente = []
    if os.path.exists(ARQUIVO_FEEDBACK):
        try:
            with open(ARQUIVO_FEEDBACK, "r") as f:
                content = f.read().strip()
                if content:
                    existente = json.loads(content)
        except:
            existente = []
    existente.append(feedback)
    with open(ARQUIVO_FEEDBACK, "w") as f:
        json.dump(existente, f, ensure_ascii=False, indent=2)
    salvar_log(f"📨 Feedback: {status} | {tip.get('alvo','')} | {motivo}")


def esperar_elemento(locator, ciclos_max):
    for _ in range(ciclos_max):
        if locator.is_visible(): return True
        time.sleep(0.5)
    return False


def shadow_find_btn(page):
    """Busca o botão de apostar dentro dos shadow roots"""
    return page.evaluate("""
        () => {
            const divs = document.querySelectorAll('div');
            for (const div of divs) {
                if (div.shadowRoot) {
                    const btns = div.shadowRoot.querySelectorAll('button[class*="PlaceBetButton"]');
                    if (btns.length > 0) {
                        return {
                            found: true,
                            disabled: btns[0].disabled,
                            text: btns[0].innerText.trim()
                        };
                    }
                }
            }
            return {found: false, disabled: false, text: ''};
        }
    """)


def shadow_click_btn(page):
    """Clica no botão de apostar dentro dos shadow roots"""
    page.evaluate("""
        () => {
            const divs = document.querySelectorAll('div');
            for (const div of divs) {
                if (div.shadowRoot) {
                    const btns = div.shadowRoot.querySelectorAll('button[class*="PlaceBetButton"]');
                    if (btns.length > 0) {
                        btns[0].click();
                        return;
                    }
                }
            }
        }
    """)


def shadow_find_alerta(page):
    """Busca alerta dentro dos shadow roots"""
    return page.evaluate("""
        () => {
            const divs = document.querySelectorAll('div');
            for (const div of divs) {
                if (div.shadowRoot) {
                    const alertas = div.shadowRoot.querySelectorAll('[class*="AlertMessage"]');
                    if (alertas.length > 0) return alertas[0].innerText.trim();
                }
            }
            return '';
        }
    """)


def shadow_find_recibo(page):
    """Verifica se recibo apareceu dentro dos shadow roots"""
    return page.evaluate("""
        () => {
            const divs = document.querySelectorAll('div');
            for (const div of divs) {
                if (div.shadowRoot) {
                    if (div.shadowRoot.querySelectorAll('[class*="BetSlipBetReceiptContainer"]').length > 0) {
                        return true;
                    }
                }
            }
            return false;
        }
    """)


def shadow_fill_stake(page, valor):
    """Preenche o stake dentro dos shadow roots"""
    return page.evaluate(f"""
        () => {{
            const divs = document.querySelectorAll('div');
            for (const div of divs) {{
                if (div.shadowRoot) {{
                    const inputs = div.shadowRoot.querySelectorAll('input[placeholder="Definir a aposta"]');
                    if (inputs.length > 0) {{
                        const input = inputs[0];
                        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        nativeInputValueSetter.call(input, '{valor}');
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return true;
                    }}
                }}
            }}
            return false;
        }}
    """)

import subprocess

def abrir_brave():
    try:
        salvar_log("🚀 Abrindo Brave com CDP...")
        subprocess.Popen([
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            "--remote-debugging-port=9224",
            "--no-sandbox"
        ])
        salvar_log("⏳ Aguardando Brave iniciar...")
        time.sleep(8)
        return True
    except Exception as e:
        salvar_log(f"❌ Erro ao abrir Brave: {e}")
        return False


def fazer_login(page):
    global ultimo_login_ok
    salvar_log("🔐 Iniciando login...")
    btn_abrir_login = page.locator('button:has-text("Entrar")').first
    campo_email     = page.locator('input[data-testid="login-input-cpf-email"]')
    campo_senha     = page.locator('input[data-testid="login-input-password"]')
    btn_submit      = page.locator('button[data-testid="login-button-submit"]').first

    try:
        if not campo_email.is_visible():
            salvar_log("Abrindo modal de login...")
            btn_abrir_login.dispatch_event("click")
            campo_email.wait_for(state="visible", timeout=25000)

        if campo_email.input_value() != EMAIL:
            campo_email.click()
            campo_email.fill("")
            campo_email.press_sequentially(EMAIL, delay=random.randint(20, 45))

        time.sleep(random.uniform(0.1, 0.2))

        if campo_senha.input_value() != SENHA:
            campo_senha.click()
            campo_senha.fill("")
            campo_senha.press_sequentially(SENHA, delay=random.randint(20, 40))

        time.sleep(random.uniform(0.1, 0.2))

        salvar_log("Clicando em ENTRAR...")
        btn_submit.dispatch_event("click")
        page.wait_for_timeout(2000)
        salvar_log("✅ Login realizado!")
        ultimo_login_ok = time.time()
        return True

    except Exception as e:
        salvar_log(f"❌ Erro no login: {e}")
        return False

def configurar_aceitar_odds(page):
    try:
        page.evaluate(
            "() => {"
            "const divs = document.querySelectorAll('div');"
            "for (const div of divs) {"
            "  if (div.shadowRoot) {"
            "    const btns = div.shadowRoot.querySelectorAll('[class*=\"OddsChange\"]');"
            "    if (btns.length > 0) { btns[0].click(); return; }"
            "  }"
            "}"
            "}"
        )
        time.sleep(0.5)

        page.evaluate(
            "() => {"
            "const divs = document.querySelectorAll('div');"
            "for (const div of divs) {"
            "  if (div.shadowRoot) {"
            "    const btns = div.shadowRoot.querySelectorAll('button');"
            "    for (const btn of btns) {"
            "      if (btn.innerText && btn.innerText.includes('qualquer')) {"
            "        btn.click(); return;"
            "      }"
            "    }"
            "  }"
            "}"
            "}"
        )
        time.sleep(0.3)

        page.evaluate(
            "() => {"
            "const divs = document.querySelectorAll('div');"
            "for (const div of divs) {"
            "  if (div.shadowRoot) {"
            "    const salvar = div.shadowRoot.querySelector('[class*=\"SaveButton\"]');"
            "    if (salvar) { salvar.click(); return; }"
            "  }"
            "}"
            "}"
        )
        time.sleep(0.3)

        page.evaluate(
            "() => {"
            "const btns = document.querySelectorAll('button');"
            "for (const btn of btns) {"
            "  if (btn.innerText && btn.innerText.includes('bilhete')) {"
            "    btn.click(); return;"
            "  }"
            "}"
            "}"
        )
        time.sleep(0.3)
        salvar_log("✅ Odds configuradas!")
    except Exception as e:
        salvar_log(f"⚠️ Erro ao configurar odds: {e}")



def verificar_login(page):
    global ultimo_login_ok
    try:
        if "estrelabet.bet.br" not in (page.url or ""):
            salvar_log("🌐 Navegando para o site...")
            page.goto("https://www.estrelabet.bet.br/apostas-ao-vivo",
                      timeout=30000, wait_until='domcontentloaded')
            time.sleep(2)

        loc = page.locator('[data-testid="header-balance-value"]')
        # Espera até 15s pelo saldo aparecer, retorna assim que achar
        try:
            loc.first.wait_for(state="visible", timeout=15000)
        except:
            pass

        if loc.count() == 0:
            salvar_log("⚠️ Usuário não logado. Fazendo login...")
            ok = fazer_login(page)
            if ok: ultimo_login_ok = time.time()
            return ok

        for i in range(loc.count()):
            item = loc.nth(i)
            try:
                visivel = item.is_visible()
                texto   = item.text_content()
            except:
                continue
            if visivel and texto and texto.strip():
                salvar_log(f"✅ Logado | Saldo: {texto.strip()}")
                ultimo_login_ok = time.time()
                return True

        salvar_log("⚠️ Saldo não encontrado. Fazendo login...")
        ok = fazer_login(page)
        if ok: ultimo_login_ok = time.time()
        return ok

    except Exception as e:
        salvar_log(f"❌ Erro ao verificar login: {e}")
        ok = fazer_login(page)
        if ok: ultimo_login_ok = time.time()
        return ok


def fechar_popups(page):
    try:
        page.keyboard.press("Escape")
        time.sleep(0.3)

        page.evaluate("""
            () => {
                const seletores = [
                    'button[class*="close"]',
                    'button[class*="Close"]',
                    'button[aria-label="Close"]',
                    'button[aria-label="Fechar"]',
                    '[class*="modal"] button',
                    '[class*="overlay"] button',
                    '[class*="popup"] button',
                    'button[class*="dismiss"]',
                    'svg[class*="close"]',
                ];

                seletores.forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => el.click());
                });

                document.querySelectorAll('div').forEach(div => {
                    if (div.shadowRoot) {
                        seletores.forEach(sel => {
                            div.shadowRoot.querySelectorAll(sel).forEach(el => el.click());
                        });
                    }
                });
            }
        """)
        time.sleep(0.3)
    except:
        pass


def detectar_liga(page):
    try:
        liga = page.locator('div[class*="ScoreboardTitle"]').first.inner_text().strip()
        salvar_log(f"🏆 Liga: {liga}")
        return liga
    except:
        return ""

def calcular_stake(valor_base, liga):
    if "2x6" in liga:
        salvar_log("⚡ Liga especial — usando 1 unidade")
        return valor_base * 1
    elif "Cyber Live Arena" in liga:
        salvar_log("⚡ Liga especial — usando 1 unidade")
        return valor_base * 1
    else:
        salvar_log("📊 Liga battle — usando 1 unidade")
        return valor_base * 1

def recuperar_pagina(page, context, url_atual):
    try:
        salvar_log("🧹 Tentativa 1: Limpando cache e recarregando...")
        page.evaluate("""() => {
            localStorage.clear();
            sessionStorage.clear();
            if ('caches' in window) {
                caches.keys().then(names => {
                    names.forEach(name => caches.delete(name));
                });
            }
        }""")
        time.sleep(0.5)
        page.reload(timeout=15000, wait_until='domcontentloaded')
        salvar_log("✅ Tentativa 1 bem sucedida!")
        return page
    except Exception as e:
        salvar_log(f"⚠️ Tentativa 1 falhou: {e}")

    try:
        salvar_log("🔄 Tentativa 2: Navegando para o site...")
        page.goto(url_atual, timeout=30000, wait_until='domcontentloaded')
        verificar_login(page)
        salvar_log("✅ Tentativa 2 bem sucedida!")
        return page
    except Exception as e:
        salvar_log(f"⚠️ Tentativa 2 falhou: {e}")
        return None

def executar_turno(page, tip, context, p):
    url       = tip['url']
    alvo      = tip['alvo'].strip().lower()
    linha_tip = float(tip['linha'])

    SEL_MERCADO_BLOCO    = 'div[class*="EventDetailsMarketBoxRoot"]'
    SEL_MERCADO_NOME     = 'div[class*="EventDetailsMarketName"]'
    SEL_BTN_ODD          = 'button[class*="OddBoxButton"]'
    SEL_LABEL            = 'span[class*="OddLabel"]'
    SEL_HC_VALOR         = 'span[class*="OddSpecialValue"]'
    SEL_ODD_VALOR        = 'div[class*="OddValue"]'
    SEL_LIXEIRA          = 'div.bg-c_bg\\.lightest.cursor_pointer:has(svg.nebulosa-icon__root)'
    SEL_EVENTO_ENCERRADO = 'div[class*="PlaceHolderContainer"]'

    # ── Detecta tipo de mercado ────────────────────────────────
    eh_over_under   = alvo in ["over", "under"]
    eh_dupla_chance = alvo.startswith("dupla_chance:")
    eh_moneyline    = alvo.startswith("ml:")
    eh_ou_jgd       = alvo.startswith("over_jgd:") or alvo.startswith("under_jgd:")

    nome_selecao = ""
    if eh_dupla_chance:
        nome_selecao = alvo.replace("dupla_chance:", "")
    elif eh_moneyline:
        nome_selecao = alvo.replace("ml:", "")
    elif eh_ou_jgd:
        nome_selecao = alvo.split(":", 1)[1]

    params  = parse_qs(urlparse(url).query)
    id_jogo = params.get("eventId", [""])[0]

    if id_jogo and id_jogo not in page.url:
        salvar_log(f"🚚 Indo para: {tip['alvo']}")
        try:
            page.goto(url, timeout=15000, wait_until='domcontentloaded')
        except:
            salvar_log("⚠️ Página travou! Recuperando...")
            nova = recuperar_pagina(page, context, url)
            if nova:
                page = nova
            else:
                browser, context, page = conectar_cdp(p)

        time.sleep(1)

        # Só checa login se não verificou nos últimos 10min
        if time.time() - ultimo_login_ok > 600:
            # Check rápido: botão "Entrar" visível = deslogado
            btn_entrar = page.locator('button:has-text("Entrar")').first
            try:
                if btn_entrar.is_visible(timeout=2000):
                    salvar_log("⚠️ Deslogado, fazendo login...")
                    fazer_login(page)
                    try:
                        page.goto(url, timeout=15000, wait_until='domcontentloaded')
                    except:
                        pass
            except:
                pass  # não achou o botão = tá logado

    liga = detectar_liga(page)
    stake_final = calcular_stake(tip['valor'], liga)
    apostou_neste_turno = False
    fim_turno = time.time() + TEMPO_VIGIA_POR_TURNO

    while time.time() < fim_turno:
        try:
            if tip['feitas'] >= tip['max']:
                return True

            if id_jogo and id_jogo not in page.url:
                salvar_log(f"🏁 Site redirecionou! Evento encerrado: {tip['alvo']}")
                tip['feitas'] = tip['max']
                tip['encerrado'] = True
                return False

            if page.locator(SEL_EVENTO_ENCERRADO).first.is_visible():
                salvar_log(f"🏁 Evento encerrado! Removendo {tip['alvo']} da fila...")
                tip['feitas'] = tip['max']
                tip['encerrado'] = True
                return False

            lixeira = page.locator(SEL_LIXEIRA).first
            if lixeira.is_visible():
                lixeira.dispatch_event("click")
                time.sleep(0.3)

            # ── Determina qual bloco de mercado buscar ─────────
            if eh_ou_jgd:
                bloco_hc = page.locator(SEL_MERCADO_BLOCO).filter(
                    has=page.locator(SEL_MERCADO_NOME, has_text="total")
                ).filter(
                    has=page.locator(SEL_MERCADO_NOME, has_text=re.compile(re.escape(nome_selecao), re.IGNORECASE))
                ).first
            else:
                if eh_dupla_chance:
                    texto_mercado = "Dupla Chance"
                elif eh_over_under:
                    texto_mercado = "Total de Gols"
                elif eh_moneyline:
                    texto_mercado = "1x2"
                else:
                    texto_mercado = "Handicap"

                bloco_hc = page.locator(SEL_MERCADO_BLOCO).filter(
                    has=page.locator(SEL_MERCADO_NOME, has_text=texto_mercado)
                ).first

            if not bloco_hc.is_visible():
                salvar_log(f"⏳ Aguardando mercado aparecer...")
                time.sleep(1)
                continue

            botoes = bloco_hc.locator(SEL_BTN_ODD)
            melhor_btn      = None
            melhor_linha    = -999.0
            mercado_fechado = False

            for i in range(botoes.count()):
                btn = botoes.nth(i)

                if btn.get_attribute("disabled") is not None:
                    mercado_fechado = True
                    break

                # ── MONEYLINE (1x2) ───────────────────────────
                if eh_moneyline:
                    label_txt = btn.locator(SEL_LABEL).first.inner_text().strip().lower()
                    if nome_selecao in label_txt:
                        melhor_btn = btn
                        melhor_linha = 0
                        break

                # ── DUPLA CHANCE ───────────────────────────────
                elif eh_dupla_chance:
                    label_txt = btn.locator(SEL_LABEL).first.inner_text().strip().lower()
                    partes_dc = nome_selecao.lower().split(" ou ")
                    if all(p.strip() in label_txt for p in partes_dc):
                        melhor_btn = btn
                        melhor_linha = 0
                        break

                # ── OVER/UNDER JOGADOR ─────────────────────────
                elif eh_ou_jgd:
                    label_txt = btn.locator(SEL_LABEL).first.inner_text().strip().lower()
                    texto_busca = "mais de" if alvo.startswith("over_jgd:") else "menos de"

                    if texto_busca not in label_txt:
                        continue

                    match = re.search(r'(\d+\.?\d*)', label_txt)
                    if not match:
                        continue

                    hc_val = float(match.group(1))

                    if alvo.startswith("over_jgd:"):
                        if hc_val <= linha_tip:
                            melhor_btn = btn
                            melhor_linha = hc_val
                            break
                    else:
                        if hc_val >= linha_tip:
                            melhor_btn = btn
                            melhor_linha = hc_val
                            break

                # ── OVER/UNDER (Total de Gols) ─────────────────
                elif eh_over_under:
                    label_txt = btn.locator(SEL_LABEL).first.inner_text().strip().lower()
                    texto_busca = "mais de" if alvo == "over" else "menos de"

                    if texto_busca not in label_txt:
                        continue

                    match = re.search(r'(\d+\.?\d*)', label_txt)
                    if not match:
                        continue

                    hc_val = float(match.group(1))

                    if alvo == "over":
                        if hc_val <= linha_tip:
                            melhor_btn = btn
                            melhor_linha = hc_val
                            break
                    else:
                        if hc_val >= linha_tip:
                            melhor_btn = btn
                            melhor_linha = hc_val
                            break

                # ── HANDICAP (padrão) ──────────────────────────
                else:
                    label_txt = btn.locator(SEL_LABEL).first.inner_text().strip()
                    jogador = extrair_jogador(label_txt)

                    if alvo not in jogador:
                        continue

                    hc_el = btn.locator(SEL_HC_VALOR).first
                    if not hc_el.is_visible():
                        continue

                    match = re.search(r'([+-]?\d+\.?\d*)', hc_el.inner_text().strip())
                    if not match:
                        continue

                    hc_val = float(match.group(1))

                    if hc_val >= linha_tip and hc_val > melhor_linha:
                        melhor_linha = hc_val
                        melhor_btn = btn

            if mercado_fechado:
                salvar_log(f"⏸️ Mercado fechado temporariamente para {alvo}, aguardando...")
                time.sleep(2)
                continue

            if not melhor_btn:
                salvar_log(f"🔍 Linha não disponível para {alvo}, aguardando...")
                time.sleep(1)
                continue

            # ── Verifica odd mínima ────────────────────────────
            try:
                odd_txt = melhor_btn.locator(SEL_ODD_VALOR).first.inner_text().strip()
                odd_valor = float(odd_txt)
                if odd_valor < ODD_MINIMA:
                    salvar_log(f"⚠️ Odd {odd_valor} abaixo do mínimo ({ODD_MINIMA}) para {alvo}, pulando...")
                    time.sleep(1)
                    continue
            except:
                pass

            # ── Clica na odd ───────────────────────────────────
            salvar_log(f"📍 Linha {melhor_linha} para {alvo} (Meta {tip['feitas'] + 1}/{tip['max']})")
            melhor_btn.dispatch_event("click")
            time.sleep(0.8)
            configurar_aceitar_odds(page)



            # ── Preenche stake via Shadow DOM ──────────────────
            preencheu = shadow_fill_stake(page, stake_final)
            if preencheu:
                time.sleep(0.5)
                salvar_log("📝 Stake preenchido!")
                time.sleep(0.3)
            else:
                salvar_log("⚠️ Stake não encontrado, tentando novamente...")
                time.sleep(1)
                continue

            # ── Verifica alertas via Shadow DOM ────────────────────
            texto_alerta = shadow_find_alerta(page)

            if "elegível" in texto_alerta:
                salvar_log(f"🔒 Bloqueado: {texto_alerta}")
                lixeira = page.locator(SEL_LIXEIRA).first
                if lixeira.is_visible():
                    lixeira.dispatch_event("click")
                time.sleep(1)
                continue

            if "diminuiu" in texto_alerta:
                salvar_log(f"⚠️ Valor diminuído pelo site — aceitando...")
                shadow_click_btn(page)
                time.sleep(1)
                recebeu = False
                for _ in range(10):
                    if shadow_find_recibo(page):
                        recebeu = True
                        break
                    time.sleep(0.5)
                if recebeu:
                    tip['feitas'] += 1
                    apostou_neste_turno = True
                    salvar_log(f"✅ SUCESSO com valor diminuído! Linha {melhor_linha} | {tip['feitas']}/{tip['max']}")
                    time.sleep(0.5)
                    fechar_popups(page)
                    lixeira = page.locator(SEL_LIXEIRA).first
                    if lixeira.is_visible():
                        lixeira.dispatch_event("click")
                continue

            if texto_alerta:
                salvar_log(f"⚠️ Alerta: {texto_alerta} — confirmando mesmo assim...")

            # ── Verifica botão via Shadow DOM ──────────────────
            info_btn = shadow_find_btn(page)

            if not info_btn['found']:
                salvar_log("⚠️ Botão apostar não encontrado...")
                time.sleep(1)
                continue

            if info_btn['disabled']:
                salvar_log("🔒 Botão apostar bloqueado, limpando bilhete...")
                lixeira = page.locator(SEL_LIXEIRA).first
                if lixeira.is_visible():
                    lixeira.dispatch_event("click")
                time.sleep(1)
                continue

            if "Entre na sua conta" in info_btn['text']:
                salvar_log("⚠️ Sessão expirada! Fazendo login...")
                fazer_login(page)
                time.sleep(1)
                continue

            time.sleep(random.uniform(0.3, 0.7))
            shadow_click_btn(page)
            salvar_log(f"🎯 Clique em apostar: {info_btn['text']}")

            # ── Aguarda recibo via Shadow DOM ──────────────────
            recebeu = False
            for _ in range(15):
                if shadow_find_recibo(page):
                    recebeu = True
                    break
                time.sleep(0.3)

            if recebeu:
                tip['feitas'] += 1
                apostou_neste_turno = True
                salvar_log(f"✅ SUCESSO! Linha {melhor_linha} | {tip['feitas']}/{tip['max']}")
                time.sleep(1)
                page.evaluate("""() => {
                                        localStorage.clear();
                                        sessionStorage.clear();
                                        if ('caches' in window) {
                                            caches.keys().then(names => {
                                                names.forEach(name => caches.delete(name));
                                            });
                                        }
                                    }""")
                time.sleep(1)
                lixeira = page.locator(SEL_LIXEIRA).first
                if lixeira.is_visible():
                    lixeira.dispatch_event("click")
            else:
                print("Tentando forçar clickar novamente...")
                time.sleep(random.uniform(0.3, 0.7))
                shadow_click_btn(page)
                salvar_log(f"🎯 Clique em apostar: {info_btn['text']}")

                time.sleep(random.uniform(0.3, 0.7))
                shadow_click_btn(page)
                salvar_log(f"🎯 Clique em apostar: {info_btn['text']}")

                recebeu = False
                for _ in range(10):
                    if shadow_find_recibo(page):
                        recebeu = True
                        break
                    time.sleep(0.5)

                if recebeu:
                    tip['feitas'] += 1
                    apostou_neste_turno = True
                    salvar_log(f"✅ SUCESSO! Linha {melhor_linha} | {tip['feitas']}/{tip['max']}")
                    time.sleep(1)
                    page.evaluate("""() => {
                                                       localStorage.clear();
                                                       sessionStorage.clear();
                                                       if ('caches' in window) {
                                                           caches.keys().then(names => {
                                                               names.forEach(name => caches.delete(name));
                                                           });
                                                       }
                                                   }""")
                    time.sleep(1)
                    lixeira = page.locator(SEL_LIXEIRA).first
                    if lixeira.is_visible():
                        lixeira.dispatch_event("click")

                else:
                    salvar_log("⚠️ Recibo não apareceu, tentando novamente...")
                    lixeira = page.locator(SEL_LIXEIRA).first
                    if lixeira.is_visible():
                        lixeira.dispatch_event("click")

            time.sleep(1)

        except Exception as e:
            salvar_log(f"❌ Erro no turno: {e}")
            time.sleep(1)

    return apostou_neste_turno


def cdp_ativo(page):
    try:
        _ = page.url
        return True
    except:
        return False


def conectar_cdp(p):
    tentativas = 0
    brave_aberto = False
    while True:
        try:
            salvar_log("🔌 Tentando conectar ao Brave (CDP)...")
            browser = p.chromium.connect_over_cdp("http://localhost:9224")

            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = browser.new_context()

            context.add_init_script("""
                (function() {
                  const originalAttachShadow = Element.prototype.attachShadow;
                  Element.prototype.attachShadow = function(init) {
                    return originalAttachShadow.call(this, {mode: 'open'});
                  };
                })();
            """)

            if context.pages:
                page = None
                for pg in reversed(context.pages):
                    try:
                        if "estrelabet.bet.br" in (pg.url or ""):
                            page = pg
                            break
                    except:
                        pass
                if page is None:
                    page = context.pages[-1]
            else:
                page = context.new_page()
                page.goto("https://www.estrelabet.bet.br", wait_until='domcontentloaded')


            salvar_log(f"✅ CDP conectado | Aba: {page.url}")
            return browser, context, page

        except Exception as e:
            tentativas += 1
            salvar_log(f"⏳ Brave não disponível ({e}). Aguardando...")

            if tentativas >= 3 and not brave_aberto:
                salvar_log("🔄 Tentando abrir o Brave automaticamente...")
                abrir_brave()
                brave_aberto = True
                tentativas = 0

            time.sleep(3)


# ==============================================================================
# --- MAIN ---
# ==============================================================================
if __name__ == "__main__":

    fila_de_trabalho = []
    ultimo_check_login = time.time()
    ultimo_reload = time.time()
    INTERVALO_RELOAD = 60 * 30

    with sync_playwright() as p:
        browser, context, page = conectar_cdp(p)
        verificar_login(page)
        configurar_aceitar_odds(page)
        salvar_log("🚀 BOT AJUSTADO ONLINE")

        while True:
            if not cdp_ativo(page):
                salvar_log("⚠️ Conexão CDP perdida. Tentando reconectar...")
                try:
                    browser, context, page = conectar_cdp(p)
                    verificar_login(page)
                except Exception as e:
                    salvar_log(f"❌ Falha ao reconectar CDP: {e}")
                    time.sleep(5)
                    continue

            if os.path.exists(ARQUIVO_TIPS) and os.path.getsize(ARQUIVO_TIPS) > 0:
                with open(ARQUIVO_TIPS, 'r') as f:
                    linhas = f.readlines()
                open(ARQUIVO_TIPS, 'w').close()

                for l in linhas:
                    p_line = l.strip().split(',')
                    if len(p_line) == 5:
                        fila_de_trabalho.append({
                            'url':    p_line[0],
                            'alvo':   p_line[1],
                            'linha':  float(p_line[2]),
                            'valor':  float(p_line[3]),
                            'max':    int(p_line[4]),
                            'feitas': 0,
                            'erros':  0
                        })
                        salvar_log(f"➕ Tip Adicionada: {p_line[1]}")

            if not fila_de_trabalho:

                if time.time() - ultimo_check_login > 150:
                    salvar_log("🔍 Verificando sessão...")
                    try:
                        page.reload(timeout=35000, wait_until='domcontentloaded')
                        time.sleep(1)

                        loc = page.locator('[data-testid="header-balance-value"]')
                        if loc.count() == 0 or not loc.first.is_visible():
                            salvar_log("🔄 Sessão expirada! Renovando login...")
                            verificar_login(page)
                        else:
                            salvar_log("✅ Sessão ativa!")
                    except Exception as e:
                        salvar_log(f"⚠️ Erro na verificação: {e}")
                    ultimo_check_login = time.time()

                if time.time() - ultimo_reload > INTERVALO_RELOAD:
                    salvar_log("🔄 Manutenção preventiva...")
                    url_atual = page.url
                    nova_page = recuperar_pagina(page, context, url_atual)
                    if nova_page:
                        page = nova_page
                        ultimo_reload = time.time()
                    else:
                        salvar_log("🆘 Reconectando CDP inteiro...")
                        browser, context, page = conectar_cdp(p)
                        verificar_login(page)
                        ultimo_reload = time.time()
                time.sleep(0.5)
                continue

            current_tip = fila_de_trabalho.pop(0)
            status_sucesso = executar_turno(page, current_tip, context, p)

            if current_tip['feitas'] < int(current_tip['max']):
                if not status_sucesso:
                    current_tip['erros'] += 1
                else:
                    current_tip['erros'] = 0

                if current_tip['erros'] < 5:
                    fila_de_trabalho.append(current_tip)
                    salvar_log(f"🔄 Rodízio: {current_tip['alvo']} ({current_tip['erros']}/5)")
                else:
                    salvar_log(f"🚨 Removendo {current_tip['alvo']} (Mercado parou ou linha não bate)")
                    escrever_feedback(current_tip, "falha", "mercado_indisponivel")
            else:
                if current_tip.get('encerrado'):
                    salvar_log(f"🏁 Evento encerrado: {current_tip['alvo']}")
                    escrever_feedback(current_tip, "falha", "evento_encerrado")
                else:
                    salvar_log(f"🏆 Meta Batida: {current_tip['alvo']}")
                    escrever_feedback(current_tip, "sucesso")

            time.sleep(0.5)