"""
monitor_anotaai.py
==================
Monitor do Anotaai para o Painel Prensadão.
Usa OCR (leitura de tela) para detectar pedidos prontos.

Requisitos:
  pip install selenium webdriver-manager requests pytesseract pillow

Uso:
  python monitor_anotaai.py
"""

import re
import time
import requests
import sys
import os
from datetime import datetime

PAINEL_URL     = "https://painel-prensadao.onrender.com"
ANOTAAI_URL    = "https://admin.anota.ai"
ANOTAAI_ORDERS = "https://admin.anota.ai/main/orders"
CHECK_INTERVAL = 3

# Arquivo de histórico na mesma pasta do script
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
VISTOS_FILE = os.path.join(SCRIPT_DIR, 'vistos.txt')  # segundos

# Textos EXATOS dos títulos das colunas (confirmado pelo anotaai_texto.txt)
COL_PRONTO   = ["prontos para entrega", "pronto para entrega"]
COL_PRODUCAO = ["em produção", "em producao"]
COL_IGNORAR  = ["em análise", "em analise", "nenhum pedido"]

vistos_producao = set()
vistos_pronto   = set()

def salvar_vistos():
    try:
        with open(VISTOS_FILE, 'w', encoding='utf-8') as f:
            for v in vistos_pronto:
                f.write(f'pronto:{v}\n')
            for v in vistos_producao:
                f.write(f'producao:{v}\n')
    except Exception as e:
        log(f"⚠️  Erro ao salvar histórico: {e}")

def carregar_vistos():
    try:
        if not os.path.exists(VISTOS_FILE):
            return
        with open(VISTOS_FILE, 'r', encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if linha.startswith('pronto:'):
                    vistos_pronto.add(linha[7:])
                elif linha.startswith('producao:'):
                    vistos_producao.add(linha[9:])
        log(f"📂 Histórico: {len(vistos_pronto)} prontos já processados")
    except Exception as e:
        log(f"⚠️  Erro ao carregar histórico: {e}")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def extrair_pedidos_do_texto(texto_pagina):
    """
    Analisa o texto da página e retorna pedidos por coluna.
    """
    resultado = {'producao': [], 'pronto': []}
    linhas = [l.strip() for l in texto_pagina.split('\n') if l.strip()]

    col_atual = None
    i = 0
    while i < len(linhas):
        linha = linhas[i]
        t = linha.lower()

        # Detecta coluna
        if "prontos para entrega" in t or "pronto para entrega" in t:
            col_atual = 'pronto'
            i += 1
            continue
        if "em produção" in t or "em producao" in t:
            col_atual = 'producao'
            i += 1
            continue
        if "em análise" in t or "em analise" in t or "nenhum pedido" in t:
            col_atual = None
            i += 1
            continue

        # Detecta pedido pelo número
        m = re.search(r'pedido\s*#?\s*(\d+)', linha, re.IGNORECASE)
        if m and col_atual:
            numero = m.group(1)
            nome = "Cliente"
            # Busca nome nas próximas 4 linhas (ignora tempo de preparo)
            for j in range(i+1, min(i+5, len(linhas))):
                candidato = linhas[j].strip()
                if not candidato:
                    continue
                # Ignora tempo de preparo "Prepare em até XX:XX:XX"
                if re.search(r'prepare|preparo|até|ate|\d{2}:\d{2}:\d{2}|\d{2}:\d{2}', candidato, re.IGNORECASE):
                    continue
                if re.search(r'^\d+$|R\$|retirada|delivery|ifood|rappi|balcão|finalizar|aceitar|pedido', candidato, re.IGNORECASE):
                    continue
                if re.search(r'^\(\d{2}\)|\+55|\d{8,}', candidato):
                    continue
                if len(candidato) > 2 and re.search(r'[a-zA-ZÀ-ú]', candidato):
                    nome = candidato
                    break
            resultado[col_atual].append((numero, nome))
            log(f"  📋 [{col_atual}]: Pedido #{numero} - {nome}")

        i += 1

    return resultado


def enviar_para_painel(nome, numero, chamar=False):
    try:
        if chamar:
            # Tenta mover de "Em Preparo" → "Retirar no Balcão"
            r = requests.get(f"{PAINEL_URL}/pedidos", timeout=5)
            lista = r.json()
            pedido_id = None
            for p in lista.get("prontos", []):
                if str(numero) == str(p.get("numero", "")):
                    pedido_id = p["id"]
                    break
                if nome != "Cliente" and nome.lower() in p.get("nome", "").lower():
                    pedido_id = p["id"]
                    break
            if pedido_id:
                requests.post(f"{PAINEL_URL}/retirado", json={"id": pedido_id}, timeout=5)
                log(f"🔔 CHAMADO: {nome} (#{numero})")
            else:
                requests.post(f"{PAINEL_URL}/pedido-manual",
                    json={"nome": nome, "numero": str(numero), "chamar": True}, timeout=5)
                log(f"🔔 CHAMADO DIRETO: {nome} (#{numero})")
        else:
            requests.post(f"{PAINEL_URL}/pedido-manual",
                json={"nome": nome, "numero": str(numero), "chamar": False}, timeout=5)
            log(f"👨‍🍳 EM PREPARO: {nome} (#{numero})")
    except Exception as e:
        log(f"❌ Erro painel: {e}")


def monitorar_com_selenium(driver):
    from selenium.webdriver.common.by import By

    log("👀 Monitorando pedidos... (Ctrl+C para parar)\n")
    loop = 0

    while True:
        loop += 1
        try:
            # Garante que está no kanban de pedidos (não no PDV)
            url = driver.current_url
            if "pdv" in url or "admin.anota.ai/main/orders" not in url:
                # Só redireciona se estiver MUITO fora do lugar
                if "admin.anota.ai" not in url:
                    log("↩️  Abrindo tela de pedidos...")
                    driver.get(ANOTAAI_ORDERS)
                    time.sleep(4)
                    continue

            # Lê o texto completo da página
            try:
                body = driver.find_element(By.TAG_NAME, "body")
                texto = body.text
            except:
                time.sleep(2)
                continue

            if not texto or len(texto) < 50:
                time.sleep(3)
                continue

            # Salva HTML para debug nos primeiros 3 loops e a cada 30
            if loop <= 3 or loop % 30 == 0:
                try:
                    with open("anotaai_debug.html", "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    with open("anotaai_texto.txt", "w", encoding="utf-8") as f:
                        f.write(texto)
                    if loop <= 3:
                        log("📄 Debug salvo em anotaai_texto.txt")
                except:
                    pass

            # Analisa o texto
            pedidos = extrair_pedidos_do_texto(texto)

            for numero, nome in pedidos['pronto']:
                chave = str(numero).strip()
                if chave not in vistos_pronto:
                    vistos_pronto.add(chave)
                    vistos_producao.add(chave)
                    log(f"✅ NOVO pronto: #{chave} — adicionado ao histórico ({len(vistos_pronto)} total)")
                    salvar_vistos()
                    enviar_para_painel(nome, chave, chamar=True)
                else:
                    log(f"⏭️  Ignorando #{chave} (já processado)")

            for numero, nome in pedidos['producao']:
                chave = str(numero).strip()
                if chave not in vistos_producao:
                    vistos_producao.add(chave)
                    log(f"✅ NOVO em produção: #{chave}")
                    salvar_vistos()
                    enviar_para_painel(nome, chave, chamar=False)
                else:
                    pass  # Silêncio para não poluir o log

            if loop % 20 == 0:
                log(f"♻️  Ativo | prontos chamados: {len(vistos_pronto)} | arquivo: {VISTOS_FILE}")

        except Exception as e:
            msg = str(e)
            if any(x in msg for x in ["no such window", "target window", "session deleted", "web view"]):
                log("⚠️  Chrome fechou!")
                raise
            log(f"⚠️  Erro: {msg[:80]}")
            time.sleep(5)
            try:
                driver.refresh()
                time.sleep(4)
            except:
                raise

        time.sleep(CHECK_INTERVAL)


def iniciar_navegador():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        opts = Options()
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1366,768")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    except ImportError:
        log("❌ Selenium não instalado! Execute o INSTALAR_MONITOR.bat")
        sys.exit(1)
    except Exception as e:
        log(f"❌ Erro ao iniciar Chrome: {e}")
        log("   Certifique-se que o Google Chrome está instalado.")
        sys.exit(1)


def fazer_login(driver, email, senha):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    log("🔐 Abrindo Anotaai...")
    driver.get(f"{ANOTAAI_URL}/login/admin")
    time.sleep(4)

    try:
        wait = WebDriverWait(driver, 20)
        # Tenta encontrar campo de email
        campo_email = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR,
            "input[type='email'], input[name='email'], input[placeholder*='E-mail'], input[placeholder*='email'], input[placeholder*='usuário']")))
        campo_email.clear()
        campo_email.send_keys(email)
        time.sleep(0.5)

        campo_senha = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        campo_senha.clear()
        campo_senha.send_keys(senha)
        time.sleep(0.5)

        # Tenta clicar no botão
        for sel in ["button[type='submit']", "button.login", ".btn-login", ".btn-entrar", "button"]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                btn.click()
                break
            except:
                continue

        time.sleep(6)
        log("✅ Login realizado!")

    except Exception as e:
        log(f"⚠️  Não consegui logar automaticamente.")
        log("   👉 Faça o login manualmente na janela do Chrome que abriu.")
        log("   Após logar e ver os pedidos, pressione ENTER aqui...")
        input()


def main():
    print()
    print("╔══════════════════════════════════════════╗")
    print("║  🍔 MONITOR ANOTAAI — PRENSADÃO DO LUCÃO ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print("  ⚠️  NÃO feche a janela do Chrome que vai abrir!")
    print("  ⚠️  Ela precisa ficar aberta e visível.")
    print()

    print("Digite seu e-mail do Anotaai:")
    email = input("  ➜ Email: ").strip()
    print("Digite sua senha:")
    import getpass
    senha = getpass.getpass("  ➜ Senha: ")
    print()

    # Verifica painel
    try:
        requests.get(PAINEL_URL, timeout=3)
        log("✅ Painel Prensadão detectado!")
    except:
        log("⚠️  Painel não encontrado em localhost:3000")
        log("   Abra o INICIAR.bat primeiro e depois pressione ENTER")
        input()

    # Carrega histórico de pedidos já processados
    carregar_vistos()

    # Loop principal com reconexão automática
    tentativas = 0
    while True:
        driver = None
        try:
            tentativas += 1
            log(f"🌐 Iniciando Chrome (tentativa {tentativas})...")
            driver = iniciar_navegador()
            fazer_login(driver, email, senha)
            time.sleep(2)

            # Vai para a tela de pedidos
            log("📦 Abrindo tela de pedidos do Anotaai...")
            driver.get(ANOTAAI_ORDERS)
            time.sleep(5)

            log("✅ Pronto! Monitorando automaticamente...")
            monitorar_com_selenium(driver)

        except KeyboardInterrupt:
            log("👋 Monitor encerrado pelo usuário.")
            if driver:
                try: driver.quit()
                except: pass
            sys.exit(0)

        except Exception as e:
            log(f"🔄 Problema detectado, reiniciando em 8s... ({str(e)[:50]})")
            if driver:
                try: driver.quit()
                except: pass
            time.sleep(8)
            # NÃO limpa os vistos — senão reenvia pedidos já processados!
            log("♻️  Reiniciando monitor...")


if __name__ == "__main__":
    main()
