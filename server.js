const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = 3000;

// Upload de vídeo — parse multipart simples
function receberUpload(req, destDir) {
  return new Promise((resolve, reject) => {
    const contentType = req.headers['content-type'] || '';
    const boundary = contentType.split('boundary=')[1];
    if (!boundary) return reject('Sem boundary');
    let chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => {
      const buf = Buffer.concat(chunks);
      const sep = Buffer.from('--' + boundary);
      let start = 0;
      for (let i = 0; i < buf.length; i++) {
        if (buf.slice(i, i + sep.length).equals(sep)) {
          if (start > 0) {
            const part = buf.slice(start, i - 2);
            const headerEnd = part.indexOf('\r\n\r\n');
            if (headerEnd === -1) continue;
            const headers = part.slice(0, headerEnd).toString();
            const body = part.slice(headerEnd + 4);
            const nameMatch = headers.match(/filename="([^"]+)"/);
            if (!nameMatch) continue;
            const filename = nameMatch[1].replace(/[^a-zA-Z0-9._\-]/g, '_');
            fs.writeFileSync(path.join(destDir, filename), body);
            return resolve(filename);
          }
          start = i + sep.length + 2;
        }
      }
      reject('Arquivo não encontrado');
    });
    req.on('error', reject);
  });
}

// Garante que a pasta videos/ existe ao lado do server.js
const VIDEOS_DIR = path.join(__dirname, 'videos');
if (!fs.existsSync(VIDEOS_DIR)) {
  fs.mkdirSync(VIDEOS_DIR, { recursive: true });
  console.log('📁 Pasta videos/ criada em: ' + VIDEOS_DIR);
}
console.log('📁 Pasta de vídeos: ' + VIDEOS_DIR);

// Pasta de fotos
const FOTOS_DIR = path.join(__dirname, 'fotos');
if (!fs.existsSync(FOTOS_DIR)) {
  fs.mkdirSync(FOTOS_DIR, { recursive: true });
}
console.log('📁 Pasta de fotos: ' + FOTOS_DIR);

// Estado dos pedidos
let pedidos = {
  prontos: [],    // em preparo / aguardando chamada
  retirados: []   // chamados para retirar (visível na tela do cliente)
};

// Clientes SSE conectados
let clientes = [];

function broadcast(data) {
  const msg = `data: ${JSON.stringify(data)}\n\n`;
  clientes = clientes.filter(res => {
    try { res.write(msg); return true; } catch (e) { return false; }
  });
}

function lerBody(req) {
  return new Promise((resolve) => {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try { resolve(JSON.parse(body)); } catch { resolve({}); }
    });
  });
}

function servirHTML(res, arquivo) {
  try {
    const html = fs.readFileSync(path.join(__dirname, arquivo));
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(html);
  } catch (e) {
    res.writeHead(404);
    res.end('Arquivo não encontrado');
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(200); res.end(); return; }

  // ── PÁGINAS ──

  // Tela do cliente (monitor)
  if (req.method === 'GET' && url.pathname === '/') {
    return servirHTML(res, 'painel.html');
  }

  // Tela do operador
  if (req.method === 'GET' && url.pathname === '/operador') {
    return servirHTML(res, 'operador.html');
  }

  // PWA manifest + service worker + ícones
  if (req.method === 'GET' && url.pathname === '/manifest.json') {
    const manifest = {
      name: 'Prensadão Painel',
      short_name: 'Prensadão',
      description: 'Painel de pedidos do Prensadão',
      start_url: '/',
      display: 'fullscreen',
      orientation: 'landscape',
      background_color: '#0a0a0a',
      theme_color: '#c8102e',
      icons: [
        { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
        { src: '/icon-512.png', sizes: '512x512', type: 'image/png' }
      ]
    };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(manifest));
    return;
  }

  if (req.method === 'GET' && url.pathname === '/sw.js') {
    res.writeHead(200, { 'Content-Type': 'application/javascript' });
    res.end(`self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));`);
    return;
  }

  if (req.method === 'GET' && (url.pathname === '/icon-192.png' || url.pathname === '/icon-512.png')) {
    const iconPath = path.join(__dirname, url.pathname.slice(1));
    if (fs.existsSync(iconPath)) {
      res.writeHead(200, { 'Content-Type': 'image/png' });
      fs.createReadStream(iconPath).pipe(res);
    } else { res.writeHead(404); res.end(); }
    return;
  }

  // ── SSE ──
  if (req.method === 'GET' && url.pathname === '/events') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no'
    });
    // Envia estado atual imediatamente
    res.write(`data: ${JSON.stringify({ tipo: 'estado', pedidos })}\n\n`);
    clientes.push(res);

    // Heartbeat a cada 15s para manter conexão viva
    const hb = setInterval(() => {
      try { res.write(': ping\n\n'); } catch(e) { clearInterval(hb); }
    }, 15000);

    req.on('close', () => {
      clearInterval(hb);
      clientes = clientes.filter(c => c !== res);
    });
    return;
  }

  // ── API ──

  // Adicionar pedido em preparo (sem chamar na tela do cliente ainda)
  if (req.method === 'POST' && url.pathname === '/pedido-manual') {
    const { nome, numero, chamar } = await lerBody(req);
    if (!nome) { res.writeHead(400); res.end(JSON.stringify({ erro: 'Nome obrigatório' })); return; }

    const pedido = {
      id: `${Date.now()}`,
      numero: numero || Math.floor(Math.random() * 900 + 100).toString(),
      nome,
      prontoEm: new Date().toISOString()
    };

    pedidos.prontos.unshift(pedido);

    if (chamar) {
      // Move imediatamente para retirados com alerta
      pedidos.prontos = pedidos.prontos.filter(p => p.id !== pedido.id);
      pedido.retiradoEm = new Date().toISOString();
      pedidos.retirados.unshift(pedido);
      if (pedidos.retirados.length > 20) pedidos.retirados.pop();
      broadcast({ tipo: 'novo_pedido', pedido });
    } else {
      broadcast({ tipo: 'estado', pedidos });
    }

    console.log(`[MANUAL] Pedido adicionado: #${pedido.numero} - ${pedido.nome}`);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, pedido }));
    return;
  }

  // Chamar cliente na tela (mover de "em preparo" para "retirar" com alerta)
  if (req.method === 'POST' && url.pathname === '/retirado') {
    const { id } = await lerBody(req);
    const idx = pedidos.prontos.findIndex(p => p.id === id);
    if (idx !== -1) {
      const pedido = pedidos.prontos.splice(idx, 1)[0];
      pedido.retiradoEm = new Date().toISOString();
      pedidos.retirados.unshift(pedido);
      if (pedidos.retirados.length > 20) pedidos.retirados.pop();
      broadcast({ tipo: 'novo_pedido', pedido });
      console.log(`[CHAMADO] #${pedido.numero} - ${pedido.nome}`);
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // Remover pedido (cancelar)
  if (req.method === 'POST' && url.pathname === '/remover') {
    const { id } = await lerBody(req);
    pedidos.prontos = pedidos.prontos.filter(p => p.id !== id);
    broadcast({ tipo: 'estado', pedidos });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // Concluir pedido (remover da coluna "retire aqui")
  if (req.method === 'POST' && url.pathname === '/concluir') {
    const { id } = await lerBody(req);
    pedidos.retirados = pedidos.retirados.filter(p => p.id !== id);
    broadcast({ tipo: 'estado', pedidos });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // Estado atual
  if (req.method === 'GET' && url.pathname === '/pedidos') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(pedidos));
    return;
  }

  // Lista de vídeos disponíveis na pasta videos/
  if (req.method === 'GET' && url.pathname === '/videos') {
    try {
      const exts = ['.mp4', '.webm', '.ogg', '.mov'];
      const lista = fs.readdirSync(VIDEOS_DIR)
        .filter(f => exts.includes(path.extname(f).toLowerCase()))
        .map(f => `/videos/${f}`);
      console.log(`[VIDEOS] Encontrados: ${lista.length} arquivo(s) em ${VIDEOS_DIR}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(lista));
    } catch(e) {
      console.log(`[VIDEOS] Erro ao listar: ${e.message}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify([]));
    }
    return;
  }

  // Serve arquivos de vídeo
  if (req.method === 'GET' && url.pathname.startsWith('/videos/')) {
    const filePath = path.join(VIDEOS_DIR, path.basename(url.pathname));
    if (fs.existsSync(filePath)) {
      const ext = path.extname(filePath).toLowerCase();
      const types = { '.mp4': 'video/mp4', '.webm': 'video/webm', '.ogg': 'video/ogg', '.mov': 'video/mp4' };
      res.writeHead(200, { 'Content-Type': types[ext] || 'video/mp4' });
      fs.createReadStream(filePath).pipe(res);
    } else {
      res.writeHead(404); res.end('Not found');
    }
    return;
  }

  // Upload de vídeo pelo painel
  if (req.method === 'POST' && url.pathname === '/upload-video') {
    try {
      const filename = await receberUpload(req, VIDEOS_DIR);
      const filePath = path.join(VIDEOS_DIR, filename);
      const sizeMB = fs.statSync(filePath).size / 1024 / 1024;
      console.log(`[UPLOAD] Vídeo salvo: ${filename} (${sizeMB.toFixed(1)}MB)`);
      if (sizeMB > 30) {
        console.log(`[UPLOAD] ⚠️ Vídeo grande (${sizeMB.toFixed(1)}MB) — pode travar na TV Box!`);
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, filename, sizeMB: sizeMB.toFixed(1) }));
    } catch(e) {
      console.log(`[UPLOAD] Erro: ${e}`);
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, erro: String(e) }));
    }
    return;
  }

  // Deletar vídeo
  if (req.method === 'POST' && url.pathname === '/deletar-video') {
    const { filename } = await lerBody(req);
    const filePath = path.join(VIDEOS_DIR, path.basename(filename));
    try {
      if (fs.existsSync(filePath)) fs.unlinkSync(filePath);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch(e) {
      res.writeHead(500); res.end(JSON.stringify({ ok: false }));
    }
    return;
  }

  // Lista de fotos
  if (req.method === 'GET' && url.pathname === '/fotos') {
    try {
      const exts = ['.jpg', '.jpeg', '.png', '.gif', '.webp'];
      const lista = fs.readdirSync(FOTOS_DIR)
        .filter(f => exts.includes(path.extname(f).toLowerCase()))
        .map(f => `/fotos/${f}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(lista));
    } catch(e) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify([]));
    }
    return;
  }

  // Serve arquivos de foto
  if (req.method === 'GET' && url.pathname.startsWith('/fotos/')) {
    const filePath = path.join(FOTOS_DIR, path.basename(url.pathname));
    if (fs.existsSync(filePath)) {
      const ext = path.extname(filePath).toLowerCase();
      const types = { '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.png':'image/png', '.gif':'image/gif', '.webp':'image/webp' };
      res.writeHead(200, { 'Content-Type': types[ext] || 'image/jpeg' });
      fs.createReadStream(filePath).pipe(res);
    } else { res.writeHead(404); res.end(); }
    return;
  }

  // Upload de foto
  if (req.method === 'POST' && url.pathname === '/upload-foto') {
    try {
      const filename = await receberUpload(req, FOTOS_DIR);
      console.log(`[UPLOAD FOTO] ${filename}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, filename }));
    } catch(e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, erro: String(e) }));
    }
    return;
  }

  // Deletar foto
  if (req.method === 'POST' && url.pathname === '/deletar-foto') {
    const { filename } = await lerBody(req);
    const filePath = path.join(FOTOS_DIR, path.basename(filename));
    try {
      if (fs.existsSync(filePath)) fs.unlinkSync(filePath);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    } catch(e) {
      res.writeHead(500); res.end(JSON.stringify({ ok: false }));
    }
    return;
  }

  // Limpar todos os pedidos (retirados + prontos)
  if (req.method === 'POST' && url.pathname === '/limpar') {
    pedidos = { prontos: [], retirados: [] };
    broadcast({ tipo: 'estado', pedidos });
    console.log('[LIMPAR] Todos os pedidos removidos');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // Limpar só os retirados (concluídos)
  if (req.method === 'POST' && url.pathname === '/limpar-retirados') {
    pedidos.retirados = [];
    broadcast({ tipo: 'estado', pedidos });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // Webhook Anotaai
  if (req.method === 'POST' && url.pathname === '/webhook') {
    const data = await lerBody(req);
    console.log('[WEBHOOK] Recebido:', JSON.stringify(data));

    const status = (data.status || data.orderStatus || data.order?.status || '').toString().toLowerCase();
    const isPronto = ['ready', 'pronto', 'order_ready', 'ready_to_pickup', '4'].includes(status);

    if (isPronto) {
      const nome = data.client?.name || data.customer?.name || data.order?.client?.name || 'Cliente';
      const numero = (data.displayId || data.orderId || data.order?.displayId || '').toString();
      const pedido = { id: `${Date.now()}`, numero, nome, prontoEm: new Date().toISOString() };
      pedidos.prontos.unshift(pedido);
      broadcast({ tipo: 'estado', pedidos });
      console.log(`[WEBHOOK] Pedido em preparo: #${numero} - ${nome}`);
    }

    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // Teste manual via linha de comando
  if (req.method === 'POST' && url.pathname === '/teste') {
    const { nome, numero } = await lerBody(req);
    const pedido = {
      id: `${Date.now()}`,
      numero: numero || Math.floor(Math.random() * 900 + 100).toString(),
      nome: nome || 'Cliente Teste',
      prontoEm: new Date().toISOString()
    };
    pedidos.prontos.unshift(pedido);
    broadcast({ tipo: 'novo_pedido', pedido });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, pedido }));
    return;
  }

  res.writeHead(404);
  res.end('Not found');
});

server.listen(PORT, () => {
  console.log('');
  console.log('╔══════════════════════════════════════════════════╗');
  console.log('║         🍔 PAINEL PRENSADÃO - ATIVO              ║');
  console.log('╠══════════════════════════════════════════════════╣');
  console.log(`║  📺 Tela do cliente:   http://localhost:${PORT}        ║`);
  console.log(`║  🖥️  Tela do operador:  http://localhost:${PORT}/operador ║`);
  console.log('╚══════════════════════════════════════════════════╝');
  console.log('');
});
