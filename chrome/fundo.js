// Jarvis Música: recebe pedidos do Jarvis pela ponte local (native messaging) e age na aba
// do YouTube Music. Pedido: {id, acao, url?}. Resposta: {id, ok, ...}.
//
// Por que existe: o Chrome mostra ao sistema uma sessão de mídia só (o pause ia para o
// podcast errado) e abre cada música por endereço numa janela nova. Daqui de dentro dá
// para achar a aba certa do YouTube Music e trocar a música nela.

const PONTE = "com.jarvis.musica";
const YTM = "https://music.youtube.com/*";
let porta = null;

function conectar() {
  if (porta) return;
  porta = chrome.runtime.connectNative(PONTE);  // a porta aberta mantém este worker vivo
  porta.onMessage.addListener(async (pedido) => {
    let resposta;
    try {
      resposta = await tratar(pedido);
    } catch (e) {
      resposta = { ok: false, erro: String(e) };
    }
    porta.postMessage({ id: pedido.id, ...resposta });
  });
  porta.onDisconnect.addListener(() => {
    porta = null;
    setTimeout(conectar, 5000);  // a ponte caiu (ou ainda não estava instalada): tenta de novo
  });
}

async function abaDaMusica() {
  const abas = await chrome.tabs.query({ url: YTM });
  // mais de uma: a que está tocando, senão a usada por último
  abas.sort((a, b) => (b.audible - a.audible) || (b.lastAccessed - a.lastAccessed));
  return abas[0] || null;
}

// roda dentro da página (mundo MAIN: precisa do navigator.mediaSession do próprio site)
function naPagina(acao) {
  const video = document.querySelector("video");
  const clicar = (sel) => { const b = document.querySelector(sel); if (b) b.click(); return !!b; };
  if (acao === "pausar" && video) video.pause();
  if (acao === "continuar" && video) video.play();
  if (acao === "proxima") clicar("ytmusic-player-bar .next-button");
  if (acao === "anterior") clicar("ytmusic-player-bar .previous-button");
  if (acao === "inicio" && video) video.currentTime = 0;
  const md = navigator.mediaSession && navigator.mediaSession.metadata;
  return {
    tocando: !!(video && !video.paused && !video.ended),
    titulo: (md && md.title) || "",
    artista: (md && md.artist) || "",
  };
}

// troca a música por dentro do app (o mesmo evento que um clique numa música dispara):
// sem recarregar a página, e sem o "Sair do site?" que o YouTube Music pergunta tocando
function navegarNoApp(url) {
  const u = new URL(url);
  const v = u.searchParams.get("v"), lista = u.searchParams.get("list"), q = u.searchParams.get("q");
  let endpoint = null;
  if (v || lista) endpoint = { watchEndpoint: { ...(v && { videoId: v }), ...(lista && { playlistId: lista }) } };
  else if (q) endpoint = { searchEndpoint: { query: q } };
  const app = document.querySelector("ytmusic-app");
  if (!endpoint || !app) return false;
  app.dispatchEvent(new CustomEvent("yt-navigate", { bubbles: true, composed: true, detail: { endpoint } }));
  return true;
}

// plano B: recarregar, mas antes calar o "Sair do site?"
function semPerguntaDeSaida() {
  const v = document.querySelector("video");
  if (v) v.pause();
  window.onbeforeunload = null;
  window.addEventListener("beforeunload", (e) => e.stopImmediatePropagation(), true);
}

async function rodar(aba, func, args = []) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId: aba.id }, world: "MAIN", func, args });
  return r.result;
}

async function trocarMusica(aba, url) {
  const alvo = new URL(url);
  const id = alvo.searchParams.get("v") || alvo.searchParams.get("list") || alvo.searchParams.get("q");
  if (await rodar(aba, navegarNoApp, [url])) {
    for (let i = 0; i < 20; i++) {  // espera o app trocar de página (até 3 s)
      await new Promise((r) => setTimeout(r, 150));
      const agora = (await chrome.tabs.get(aba.id)).url || "";
      if (id && decodeURIComponent(agora).includes(id)) return "no app";
    }
  }
  await rodar(aba, semPerguntaDeSaida);
  await chrome.tabs.update(aba.id, { url });
  return "recarregando";
}

async function naAba(aba, acao) {
  const [r] = await chrome.scripting.executeScript({
    target: { tabId: aba.id }, world: "MAIN", func: naPagina, args: [acao],
  });
  return r.result;
}

// roda na página que você está vendo: o texto dela, ou só o trecho selecionado
function lerPagina(limite) {
  const selecao = String(window.getSelection() || "").trim();
  const texto = (selecao || document.body.innerText || "").replace(/\n{3,}/g, "\n\n");
  return { titulo: document.title, url: location.href, selecao: !!selecao,
           texto: texto.slice(0, limite), cortado: texto.length > limite };
}

async function abaDaFrente() {
  // a janela comum (não a do app do YouTube Music) que você usou por último
  const janela = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
  const [aba] = await chrome.tabs.query({ active: true, windowId: janela.id });
  return aba || null;
}

async function tratar(pedido) {
  if (pedido.acao === "pagina") {  // só quando o Jarvis pede ("resume essa página")
    const aba = await abaDaFrente();
    if (!aba || !/^https?:/.test(aba.url || "")) {
      return { ok: false, erro: "a aba da frente não é uma página da web" };
    }
    const [r] = await chrome.scripting.executeScript({ target: { tabId: aba.id }, func: lerPagina, args: [20000] });
    return { ok: true, ...r.result };
  }
  if (pedido.acao === "ajeitar_musica") {
    // o app do YouTube Music acabou de abrir: se a tela do Jarvis está aberta, ele não fica por cima
    // dela (minimiza). Sem a tela aberta, o app fica visível. Janela criada pela extensão seria uma
    // aba comum do navegador, não o app — por isso quem abre é o Jarvis, e aqui só se ajeita
    for (let i = 0; i < 40; i++) {
      const [musica] = await chrome.tabs.query({ url: YTM });
      if (musica) {
        const telas = await chrome.tabs.query({ url: "http://127.0.0.1:8765/*" });
        if (telas.length) await chrome.windows.update(musica.windowId, { state: "minimized" });
        return { ok: true, minimizado: telas.length > 0 };
      }
      await new Promise((r) => setTimeout(r, 200));
    }
    return { ok: false, erro: "o app do YouTube Music não abriu" };
  }
  if (pedido.acao === "recarregar") {  // o Jarvis atualizou estes arquivos: relê sem você clicar
    setTimeout(() => chrome.runtime.reload(), 200);
    return { ok: true };
  }
  const aba = await abaDaMusica();
  if (pedido.acao === "tocar") {
    if (!aba) return { ok: true, aberta: false };  // sem aba: o Jarvis abre o app (ajeitar_musica depois)
    if (!String(pedido.url || "").startsWith("https://music.youtube.com/")) {
      return { ok: false, erro: "só endereço do YouTube Music" };
    }
    const como = await trocarMusica(aba, pedido.url);
    return { ok: true, aberta: true, como };
  }
  if (!aba) return { ok: true, aberta: false };
  if (["estado", "pausar", "continuar", "proxima", "anterior", "inicio"].includes(pedido.acao)) {
    let estado = await naAba(aba, pedido.acao);
    if (pedido.acao === "proxima" || pedido.acao === "anterior") {
      // a faixa nova leva um instante para aparecer
      const antes = estado.titulo;
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 150));
        estado = await naAba(aba, "estado");
        if (estado.titulo && estado.titulo !== antes) break;
      }
    } else if (pedido.acao !== "estado") {
      await new Promise((r) => setTimeout(r, 200));
      estado = await naAba(aba, "estado");
    }
    return { ok: true, aberta: true, ...estado };
  }
  return { ok: false, erro: `ação desconhecida: ${pedido.acao}` };
}

chrome.runtime.onStartup.addListener(conectar);
chrome.runtime.onInstalled.addListener(conectar);
conectar();
