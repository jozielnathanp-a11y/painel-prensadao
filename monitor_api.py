"""
monitor_api.py
==============
Monitor do Anotaai via API — renova o token automaticamente.
Consulta os pedidos a cada 5 segundos e avisa o painel quando ficam prontos.

Requisitos:
  pip install requests selenium webdriver-manager

Uso:
  python monitor_api.py
"""

import time
import requests
import os
import json
from datetime import datetime

# ── Configurações ──────────────────────────────────────────
PAINEL_URL     = "https://painel-prensadao.onrender.com"
CHECK_INTERVAL = 5  # segundos

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE  = os.path.join(SCRIPT_DIR, 'anotaai_token.txt')
VISTOS_FILE = os.path.join(SCRIPT_DIR, 'vistos_api.txt')

# Status Anotaai
STATUS_PRODUCAO = 1
STATUS_PRONTO   = 2
# ──────────────────────────────────────────────────────────

vistos_producao = set()
vistos_pronto   = set()
token_atual     = None

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ── Salvar/carregar token ──
def salvar_token(token):
    try:
        with open(TOKEN_FILE, 'w') as f:
            f.write(token)
    except: pass

def carregar_token():
    try:
        if os.path.exists(TOKEN_FILE):
            return open(TOKEN_FILE).read().strip()
    except: pass
    return None

# ── Salvar/carregar histórico ──
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

# ── Renovar token via Selenium ──
def renovar_token():
    global token_atual
    log("🔑 Renovando token via login...")
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from webdriver_manager.chrome import ChromeDriverManager

        # Credenciais — edite aqui se necessário
        EMAIL = os.environ.get('ANOTAAI_EMAIL', '')
        SENHA = os.environ.get('ANOTAAI_SENHA', '')

        if not EMAIL or not SENHA:
            # Lê de arquivo de config
            cfg = os.path.join(SCRIPT_DIR, 'anotaai_config.json')
            if os.path.exists(cfg):
                data = json.load(open(cfg))
                EMAIL = data.get('email', '')
                SENHA = data.get('senha', '')

        if not EMAIL or not SENHA:
            log("❌ Email/senha não configurados! Crie o arquivo anotaai_config.json")
            return False

        opts = Options()
        opts.add_argument("--headless")  # sem janela
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])

        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

        try:
            driver.get("https://admin.anota.ai/login")
            wait = WebDriverWait(driver, 20)

            campo_email = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[placeholder*='e-mail'], input[placeholder*='email']")))
            campo_email.send_keys(EMAIL)

            campo_senha = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            campo_senha.send_keys(SENHA)

            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], button.entrar, button:not([disabled])")
            btn.click()

            time.sleep(5)

            # Captura o token dos cookies/localStorage
            token = driver.execute_script("return localStorage.getItem('token') || localStorage.getItem('accessToken') || document.cookie;")

            # Tenta pegar dos cookies
            cookies = driver.get_cookies()
            for c in cookies:
                if 'token' in c['name'].lower() or 'auth' in c['name'].lower():
                    token = c['value']
                    break

            if token and len(token) > 50:
                token_atual = token
                salvar_token(token)
                log(f"✅ Token renovado!")
                return True
            else:
                log("⚠️  Não conseguiu capturar token")
                return False

        finally:
            driver.quit()

    except Exception as e:
        log(f"❌ Erro ao renovar token: {e}")
        return False

# ── Consultar pedidos ──
def consultar_pedidos():
    global token_atual
    if not token_atual:
        return None

    headers = {
        'Authorization': token_atual,
        'Content-Type': 'application/json'
    }

    try:
        r = requests.get(
            'https://api-parceiros.anota.ai/partnerauth/ping/list',
            headers=headers,
            timeout=10
        )
        if r.status_code == 401:
            log("🔄 Token expirado — renovando...")
            return None  # sinaliza para renovar
        if r.status_code != 200:
            log(f"⚠️  API retornou {r.status_code}")
            return []
        data = r.json()
        if not data.get('success'):
            return []
        return data.get('info', {}).get('docs', [])
    except Exception as e:
        log(f"⚠️  Erro ao consultar: {e}")
        return []

# ── Enviar para o painel ──
def enviar_para_painel(pedido_id, numero, nome, chamar=False):
    try:
        if chamar:
            r = requests.get(f"{PAINEL_URL}/pedidos", timeout=5)
            lista = r.json()
            pid = None
            for p in lista.get('prontos', []):
                if p.get('numero') == str(numero):
                    pid = p.get('id')
                    break
            if pid:
                requests.post(f"{PAINEL_URL}/retirado", json={"id": pid}, timeout=5)
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
        log(f"⚠️  Erro ao enviar: {e}")

# ── Extrair info do pedido ──
def extrair_info(pedido):
    pid   = str(pedido.get('_id', ''))
    check = pedido.get('check', 0)
    numero = pedido.get('dailyNumber') or pedido.get('number') or pid[-4:]
    cliente = pedido.get('client') or pedido.get('buyer') or {}
    if isinstance(cliente, dict):
        nome = cliente.get('name') or cliente.get('nome') or 'Cliente'
    else:
        nome = str(cliente) or 'Cliente'
    return pid, int(check), str(numero), nome

# ── Main ──
def main():
    global token_atual

    log("🚀 Monitor Prensadão iniciando...")
    log(f"📡 Painel: {PAINEL_URL}")

    # Verifica config
    cfg = os.path.join(SCRIPT_DIR, 'anotaai_config.json')
    if not os.path.exists(cfg):
        log("⚠️  Arquivo anotaai_config.json não encontrado!")
        log("   Criando exemplo...")
        with open(cfg, 'w') as f:
            json.dump({"email": "SEU_EMAIL_AQUI", "senha": "SUA_SENHA_AQUI"}, f, indent=2)
        log(f"   Edite o arquivo: {cfg}")
        log("   Depois rode novamente.")
        return

    data = json.load(open(cfg))
    if data.get('email') == 'SEU_EMAIL_AQUI':
        log("❌ Configure seu email/senha no arquivo anotaai_config.json!")
        return

    # Carrega token salvo
    token_atual = carregar_token()
    if token_atual:
        log("🔑 Token carregado do arquivo")
    else:
        log("🔑 Sem token salvo — fazendo login...")
        if not renovar_token():
            log("❌ Falhou no login. Verifique email/senha no anotaai_config.json")
            return

    carregar_vistos()

    loop = 0
    falhas_seguidas = 0

    while True:
        loop += 1
        try:
            pedidos = consultar_pedidos()

            if pedidos is None:
                # Token expirou
                falhas_seguidas += 1
                if falhas_seguidas >= 3:
                    log("🔄 Tentando renovar token...")
                    if renovar_token():
                        falhas_seguidas = 0
                    else:
                        log("⚠️  Falha ao renovar. Tentando em 60s...")
                        time.sleep(60)
                time.sleep(CHECK_INTERVAL)
                continue

            falhas_seguidas = 0

            for pedido in pedidos:
                pid, check, numero, nome = extrair_info(pedido)

                if check >= STATUS_PRONTO:
                    if pid not in vistos_pronto:
                        vistos_pronto.add(pid)
                        vistos_producao.add(pid)
                        log(f"✅ PRONTO: #{numero} - {nome}")
                        salvar_vistos()
                        enviar_para_painel(pid, numero, nome, chamar=True)

                elif check >= STATUS_PRODUCAO:
                    if pid not in vistos_producao:
                        vistos_producao.add(pid)
                        log(f"👨‍🍳 EM PREPARO: #{numero} - {nome}")
                        salvar_vistos()
                        enviar_para_painel(pid, numero, nome, chamar=False)

            if loop % 60 == 0:
                log(f"♻️  Ativo | {len(pedidos)} pedidos | {len(vistos_pronto)} chamados")

        except requests.exceptions.ConnectionError:
            log("⚠️  Sem internet — aguardando 10s...")
            time.sleep(10)
            continue
        except Exception as e:
            log(f"⚠️  Erro: {str(e)[:80]}")
            time.sleep(5)
            continue

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
