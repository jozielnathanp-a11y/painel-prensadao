"""
monitor_api.py
==============
Monitor do Anotaai via API oficial — sem Chrome, sem Selenium.
Consulta os pedidos a cada 5 segundos e avisa o painel quando ficam prontos.

Requisitos:
  pip install requests

Uso:
  python monitor_api.py
"""

import time
import requests
import os
from datetime import datetime

# ── Configurações ──────────────────────────────────────────
ANOTAAI_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZHBhZ2UiOiI2M2I4M2NmYzg3YzY0ZTAwMTJhZDg1ZTkiLCJ0b2tlbmlkIjoiNjljZWY0NWZlYjE0YzliYjc0NGI1NzUwIiwiaWF0IjoxNzQ3MDMwNDczLCJhdWQiOiJpbnRlZ3JhdGlvbiIsImlzcyI6InNlc3Npb24tYXBpIn0.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
PAINEL_URL    = "https://painel-prensadao.onrender.com"
CHECK_INTERVAL = 5  # segundos entre cada consulta

# Status do Anotaai (campo "check"):
# 0 = Novo pedido
# 1 = Aceito / Em preparo
# 2 = Pronto para retirada
# 3 = Finalizado
STATUS_PRONTO    = 2
STATUS_PRODUCAO  = 1

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
VISTOS_FILE = os.path.join(SCRIPT_DIR, 'vistos_api.txt')
# ──────────────────────────────────────────────────────────

vistos_producao = set()
vistos_pronto   = set()

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

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

def consultar_pedidos():
    """Consulta pedidos do dia via API Anotaai."""
    headers = {
        'Authorization': ANOTAAI_TOKEN,
        'Content-Type': 'application/json'
    }
    r = requests.get(
        'https://api-parceiros.anota.ai/partnerauth/ping/list',
        headers=headers,
        timeout=10
    )
    if r.status_code == 401:
        log("❌ Token expirado ou inválido! Precisa atualizar o token.")
        return None
    if r.status_code != 200:
        log(f"⚠️  API retornou {r.status_code}")
        return None
    data = r.json()
    if not data.get('success'):
        log(f"⚠️  API retornou erro: {data}")
        return None
    return data.get('info', {}).get('docs', [])

def enviar_para_painel(pedido_id, numero, nome, chamar=False):
    try:
        if chamar:
            # Tenta mover pedido existente para retirar
            r = requests.get(f"{PAINEL_URL}/pedidos", timeout=5)
            lista = r.json()
            pid = None
            for p in lista.get('prontos', []):
                if p.get('numero') == str(numero):
                    pid = p.get('id')
                    break
            if pid:
                requests.post(f"{PAINEL_URL}/retirado",
                    json={"id": pid}, timeout=5)
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

def extrair_info(pedido):
    """Extrai número e nome do pedido."""
    pid   = str(pedido.get('_id', ''))
    check = pedido.get('check', 0)

    # Número do pedido
    numero = pedido.get('dailyNumber') or pedido.get('number') or pid[-4:]

    # Nome do cliente
    cliente = pedido.get('client') or pedido.get('buyer') or {}
    if isinstance(cliente, dict):
        nome = cliente.get('name') or cliente.get('nome') or 'Cliente'
    else:
        nome = str(cliente) or 'Cliente'

    return pid, int(check), str(numero), nome

def main():
    log("🚀 Monitor Prensadão via API iniciando...")
    log(f"📡 Painel: {PAINEL_URL}")

    # Verifica painel
    try:
        requests.get(PAINEL_URL, timeout=10)
        log("✅ Painel detectado!")
    except:
        log("⚠️  Painel não respondeu — continuando mesmo assim...")

    carregar_vistos()

    loop = 0
    while True:
        loop += 1
        try:
            pedidos = consultar_pedidos()

            if pedidos is None:
                time.sleep(CHECK_INTERVAL)
                continue

            for pedido in pedidos:
                pid, check, numero, nome = extrair_info(pedido)

                if check >= STATUS_PRONTO:
                    # Pedido pronto para retirada
                    if pid not in vistos_pronto:
                        vistos_pronto.add(pid)
                        vistos_producao.add(pid)
                        log(f"✅ PRONTO: #{numero} - {nome}")
                        salvar_vistos()
                        enviar_para_painel(pid, numero, nome, chamar=True)
                    else:
                        pass  # já processado

                elif check >= STATUS_PRODUCAO:
                    # Pedido em produção
                    if pid not in vistos_producao:
                        vistos_producao.add(pid)
                        log(f"👨‍🍳 EM PREPARO: #{numero} - {nome}")
                        salvar_vistos()
                        enviar_para_painel(pid, numero, nome, chamar=False)

            if loop % 60 == 0:
                log(f"♻️  Ativo | {len(pedidos)} pedido(s) hoje | {len(vistos_pronto)} prontos chamados")

        except requests.exceptions.ConnectionError:
            log("⚠️  Sem internet — tentando novamente em 10s...")
            time.sleep(10)
            continue
        except Exception as e:
            log(f"⚠️  Erro: {str(e)[:80]}")
            time.sleep(5)
            continue

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
