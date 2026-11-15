// N9Ctrl - interface. O Python chama window.receber(estado) quando algo muda.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const S = { estado: null, view: null, trocando: null };
const api = () => window.pywebview && window.pywebview.api;

const ICONE_FONE = `<svg viewBox="0 0 24 24"><path d="M4 14v-2a8 8 0 0 1 16 0v2" fill="none" stroke="currentColor" stroke-width="2"/><rect x="2.5" y="13" width="5" height="8" rx="2" fill="currentColor"/><rect x="16.5" y="13" width="5" height="8" rx="2" fill="currentColor"/></svg>`;
const MODOS = [
  { id: "anc", nome: "Cancelamento", desc: "Bloqueia o ruído ao redor",
    svg: `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 4a10 10 0 1 0 0 20 10 10 0 0 0 0-20z"/><path d="M7 21L21 7"/><path d="M10 14h8" opacity=".5"/></svg>` },
  { id: "ambiente", nome: "Ambiente", desc: "Deixa o som de fora entrar",
    svg: `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 9a7 7 0 0 1 10 0M5.5 5.5a12 12 0 0 1 17 0"/><circle cx="14" cy="15" r="2.4"/><path d="M14 17.5V24"/></svg>` },
  { id: "off", nome: "Desligado", desc: "Som puro, gasta menos bateria",
    svg: `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 4v9"/><path d="M8.3 8.3a8 8 0 1 0 11.4 0"/></svg>` },
];

// Funcoes do fone: k = chave no Python (n9core.GETS/SETS).
// tipo: tog | op | tog3 (liga + opcoes) | passo (- valor +).  dep: depende do Hi-Res desligado no app oficial.
const ABAS = [["ruido", "Ruído"], ["audio", "Áudio"], ["voz", "Voz e sistema"]];
const CAT = {
  ruido: [
    { grupo: "Personalizar o ANC", itens: [
      { k: "anc_adapt", nome: "ANC adaptável", desc: "Ajusta o cancelamento ao ruído do ambiente", tipo: "tog" },
      { k: "auto_comp", nome: "Compensação automática", desc: "Ajusta o cancelamento ao encaixe do fone no ouvido", tipo: "tog" },
      { k: "anc_nivel", nome: "Nível do ANC", desc: "Intensidade do cancelamento (1 a 7)", tipo: "slider", min: 1, max: 7,
        se: (e) => e.modo === "anc", bloq: (rec) => rec.anc_adapt === 1, motivo: "Desligue o ANC adaptável para ajustar manualmente." },
      { k: "amb_nivel", nome: "Nível do som ambiente", desc: "Quanto do som de fora entra (1 a 7)", tipo: "slider", min: 1, max: 7, se: (e) => e.modo === "ambiente" },
    ] },
    { grupo: "Equilíbrio", itens: [
      { k: "balanco", nome: "Equilíbrio esquerda/direita", desc: "Desloca o som para um dos lados (−16 esquerda, +16 direita)", tipo: "balanco" },
    ] },
  ],
  audio: [
    { grupo: "Entretenimento", itens: [
      { k: "hires", nome: "Áudio de alta resolução", desc: "Ao trocar, o fone reinicia", tipo: "tog", confirma: "O fone vai reiniciar para trocar o Hi-Res. Continuar?" },
      { k: "audio3d", nome: "Som espacial", desc: "Áudio 3D", tipo: "tog3", sub: "tipo", ops: [[2, "Filme"], [1, "Música"], [3, "Jogo"]], dep: true },
      { k: "eq_baixo", nome: "EQ dinâmica volume baixo", desc: "Reforça graves quando o volume está baixo (efeito sutil)", tipo: "tog", dep: true },
      { k: "safesound", nome: "Controle de volume máximo", desc: "Protege a audição com um limite de volume", tipo: "tog" },
    ] },
    { grupo: "Modo de chamada", itens: [
      { k: "modo_audio", nome: "Perfil ativo", desc: "Ao terminar a chamada, volte para Entretenimento", tipo: "op", ops: [[8, "Entretenimento"], [9, "Chamada"]] },
      { k: "limitador", nome: "Otimizador de nível sonoro", desc: "Controla os picos de volume que você ouve", tipo: "tog" },
      { k: "efeito_som", nome: "Definições de som", desc: "Como a voz dos outros soa na chamada", tipo: "op", ops: [[5, "Natural"], [6, "Agudos aumentados"], [7, "Graves aumentados"]] },
      { k: "efeito_mic", nome: "Definições de voz", desc: "Como a sua voz é enviada na chamada", tipo: "op", ops: [[1, "Natural"], [2, "Potente"], [3, "Brilhante"]] },
    ] },
  ],
  voz: [
    { grupo: "Conexão", itens: [
      { k: "fonte", nome: "Saída do fone", desc: "Escolhe se o fone toca pelo dongle USB ou pelo Bluetooth", tipo: "op",
        ops: [[1, "Dongle USB"], [0, "Bluetooth"]],
        confirmaVal: { 0: "O fone vai sair do dongle e tocar pelo Bluetooth. O N9Ctrl perde o controle até você voltar para o dongle (pelo app do celular ou pelo botão do fone). Continuar?" } },
    ] },
    { grupo: "Microfone e voz", itens: [
      { k: "smart_talk", nome: "Voz inteligente (Smart Talk)", desc: "Pausa e deixa o ambiente entrar quando você fala. O app oficial só libera no Bluetooth", tipo: "tog3" },
      { k: "chat_opt", nome: "Otimizador de chat", desc: "Função interna do fone; o app oficial não mostra", tipo: "tog" },
      { k: "voice_prompt", nome: "Avisos de voz", desc: "Voz do fone ao ligar, conectar etc.", tipo: "tog" },
    ] },
    { grupo: "Sistema", itens: [
      { k: "auto_off", nome: "Desligar automático", desc: "Desliga o fone depois de um tempo parado", tipo: "tog3", sub: "min",
        ops: [[30, "30 min"], [60, "1 h"], [120, "2 h"]] },
    ] },
  ],
};
const FONTES = ["Bluetooth", "Dongle USB", "Cabo P2"];

function controleRec(it, rec) {
  const v = rec[it.k];
  if (v === undefined) return `<span class="rec-nd">indisponível</span>`;
  const chips = (atual, sub) => `<span class="chips">${it.ops.map(([val, nome]) =>
    `<button class="chip ${atual === val ? "ativo" : ""}" data-rec="${it.k}" data-sub="${sub || ""}" data-val="${val}">${nome}</button>`).join("")}</span>`;
  if (it.tipo === "op") return chips(v);
  if (it.tipo === "slider" || it.tipo === "balanco") {
    const bal = it.tipo === "balanco";
    const num = bal ? v.nivel - 100 : v;
    const lim = bal ? [-16, 16] : [it.min, it.max];
    const bloq = bal ? !v.on : (it.bloq && it.bloq(rec));
    const tog = bal ? `<label class="chave"><input type="checkbox" data-rectog="${it.k}" ${v.on ? "checked" : ""}><i class="trilho"></i></label>` : "";
    return `${tog}<span class="deslizar"><input type="range" min="${lim[0]}" max="${lim[1]}" step="1" value="${num}" data-slider="${it.k}" ${bloq ? "disabled" : ""} aria-label="${it.nome}"><b>${bal && num > 0 ? "+" : ""}${num}</b></span>`;
  }
  if (it.tipo === "passo") return `<span class="passo"><button class="chip" data-passo="${it.k}" data-delta="-1" aria-label="Diminuir">−</button><b>${v}</b><button class="chip" data-passo="${it.k}" data-delta="1" aria-label="Aumentar">+</button></span>`;
  const lig = typeof v === "object" ? !!v.on : !!v;
  const chave = `<label class="chave"><input type="checkbox" data-rectog="${it.k}" ${lig ? "checked" : ""}><i class="trilho"></i></label>`;
  if (it.tipo === "tog3" && it.ops) return chave + chips(v[it.sub], it.sub);
  return chave;
}

function viewRecursos(d) {
  const e = d.estado, rec = e.rec || {};
  if (!e.fone) return "";
  if (!Object.keys(rec).length) return `<section class="secao"><p class="nota">Lendo as funções do fone…</p></section>`;
  const aba = ABAS.some(([id]) => id === S.aba) ? S.aba : ABAS[0][0];
  const hires = rec.hires === 1;
  const grupos = CAT[aba].map((g) => {
    const itens = g.itens.filter((it) => rec[it.k] !== undefined && (!it.se || it.se(e))).map((it) => `
      <div class="linha-rec ${it.dep && hires ? "dep" : ""}"><div class="rec-txt"><b>${it.nome}</b>${it.desc ? `<span>${it.desc}</span>` : ""}
        ${it.bloq && it.bloq(rec) ? `<span class="aviso">${it.motivo}</span>` : ""}
        ${it.dep && hires ? `<span class="aviso">Com Hi-Res ligado o app oficial bloqueia isto, mas o fone aceita o comando.</span>` : ""}</div>
        <div class="rec-ctl">${controleRec(it, rec)}</div></div>`).join("");
    return itens ? `<h3>${g.grupo}</h3><div class="grupo-rec">${itens}</div>` : "";
  }).join("");
  const info = aba === "voz" && rec.fonte === 2 ? `<p class="nota">Fonte ativa agora: <b>${FONTES[2]}</b> (cabo no fone)</p>` : "";
  return `<section class="secao">${grupos}${info}</section>`;
}

function toast(texto, erro = false) {
  const t = document.createElement("div");
  t.className = "toast" + (erro ? " erro" : "");
  t.textContent = texto;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 3500);
}

function status(e) {
  if (!e.dongle) return { txt: "Dongle não encontrado", cls: "" };
  if (!e.fone) return { txt: "Fone desligado ou fora de alcance", cls: "alerta" };
  return { txt: "Conectado", cls: "ok" };
}

function desenharLista() {
  const devs = S.estado?.dispositivos || [];
  $("#lista").innerHTML = devs.map((d) => {
    const e = d.estado, st = status(e);
    const bat = e.fone && e.bateria != null ? `${e.bateria}%` : "";
    return `<button class="dev-card ${S.view === d.id ? "ativo" : ""}" data-dev="${d.id}">
      <div class="dev-ico" title="${esc(d.nome)}">${ICONE_FONE}</div>
      <div class="dev-txt"><b>${esc(d.nome)}</b><span><i class="ponto ${st.cls}"></i>${e.fone ? "conectado" : e.dongle ? "fone desligado" : "sem dongle"}${bat ? ` · ${bat}` : ""}</span></div></button>`;
  }).join("");
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("ativo", b.dataset.view === S.view));
}

function viewDispositivo(d) {
  const e = d.estado, st = status(e);
  const bat = e.fone ? e.bateria : null;
  const segs = Array.from({ length: 10 }, (_, i) => `<i class="${bat != null && i < Math.ceil(bat / 10) ? "on" : ""}"></i>`).join("");
  const modos = MODOS.map((m) => `
    <button class="modo ${e.fone && e.modo === m.id ? "ativo" : ""} ${S.trocando === m.id ? "trocando" : ""}" data-modo="${m.id}" ${e.fone ? "" : "disabled"}
      aria-pressed="${e.modo === m.id}">${m.svg}<b>${m.nome}</b><span>${m.desc}</span></button>`).join("");
  return `
  <section class="hero" style="--b:${bat != null ? bat / 100 : 0.3}">
    <div class="horizonte"></div>
    <div class="hero-topo">
      <div>
        <h1>${esc(d.nome)}</h1>
        <p class="hero-meta">
          <span><i class="ponto ${st.cls}"></i>${st.txt}</span>
          <span>${esc(d.conexao)}</span>
          ${e.atualizado ? `<span>lido às ${e.atualizado}</span>` : ""}
        </p>
      </div>
      <div class="bateria">
        <div class="bateria-num">${bat != null ? `${bat}<small>%</small>` : "––"}</div>
        <div class="segs ${bat != null && bat <= 15 ? "baixa" : ""}">${segs}</div>
        <div class="bateria-rot">Bateria</div>
      </div>
    </div>
  </section>
  <div class="abas" role="tablist">${ABAS.map(([id, nome]) => `<button class="aba ${(S.aba || "ruido") === id ? "ativo" : ""}" role="tab" data-aba="${id}">${nome}</button>`).join("")}</div>
  ${(S.aba || "ruido") === "ruido" ? `<section class="secao">
    <h2>Controle de ruído</h2>
    <div class="modos">${modos}</div>
    <p class="nota">O botão do fone e o app do celular também trocam o modo, e o N9Ctrl acompanha na hora.</p>
  </section>` : ""}${viewRecursos(d)}`;
}

function viewAjustes() {
  const a = S.estado?.ajustes || {};
  const chave = (k, nome, desc) => `
    <label class="linha-aj"><div><b>${nome}</b><span>${desc}</span></div>
      <span class="chave"><input type="checkbox" data-ajuste="${k}" ${a[k] ? "checked" : ""}><i class="trilho"></i></span></label>`;
  return `<div class="pagina">
    <h1>Ajustes</h1><p class="intro">O N9Ctrl fica na bandeja do Windows e só consulta o fone de tempos em tempos.</p>
    ${chave("iniciar_com_windows", "Iniciar com o Windows", "Abre escondido na bandeja quando você liga o PC.")}
    ${chave("fechar_para_bandeja", "Fechar para a bandeja", "O X esconde a janela. Para sair de vez, use Sair no menu da bandeja.")}
    ${chave("notificar_bateria", "Avisar bateria baixa", "Mostra uma notificação quando o fone chega a 15%.")}
    <p class="sobre">N9Ctrl 0.1 · controle pelo dongle USB (protocolo RACE da Airoha) · log em %APPDATA%\\N9Ctrl\\log.txt</p>
  </div>`;
}

function desenhar() {
  const devs = S.estado?.dispositivos || [];
  if (!S.view && devs.length) S.view = devs[0].id;
  desenharLista();
  const main = $("#conteudo");
  if (S.view === "ajustes") main.innerHTML = viewAjustes();
  else {
    const d = devs.find((x) => x.id === S.view);
    if (d) main.innerHTML = viewDispositivo(d);
  }
}

window.receber = (estado) => { S.estado = estado; if (!S.arrastando) desenhar(); };

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("button, [data-ajuste]");
  if (!t) return;
  if (t.dataset.rec) {
    const v = Number(t.dataset.val);
    const it = Object.values(CAT).flat().flatMap((g) => g.itens).find((x) => x.k === t.dataset.rec);
    if (it?.confirmaVal?.[v] && !confirm(it.confirmaVal[v])) return;
    return enviarRec(t.dataset.rec, t.dataset.sub ? { [t.dataset.sub]: v } : v);
  }
  if (t.dataset.aba) { S.aba = t.dataset.aba; return desenhar(); }
  if (t.dataset.passo) {
    const rec = S.estado.dispositivos.find((d) => d.id === S.view).estado.rec;
    const cur = rec[t.dataset.passo], novo = Math.min(10, Math.max(1, cur + Number(t.dataset.delta)));
    return novo === cur ? null : enviarRec(t.dataset.passo, novo);
  }
  if (t.dataset.dev) { S.view = t.dataset.dev; return desenhar(); }
  if (t.dataset.view) { S.view = t.dataset.view; return desenhar(); }
  if (t.dataset.modo && !t.disabled && !S.trocando) {
    const modo = t.dataset.modo;
    if (S.estado?.dispositivos.find((d) => d.id === S.view)?.estado.modo === modo) return;
    S.trocando = modo; desenhar();
    try {
      const ok = await api().definir_modo(S.view, modo);
      if (!ok) toast("O fone não confirmou a troca. Tente de novo.", true);
    } catch (e) { toast("Falha ao falar com o fone.", true); }
    S.trocando = null; desenhar();
  }
});

async function enviarRec(k, valor) {
  if (S.ocupado) return;
  S.ocupado = true;
  document.body.classList.add("ocupado");
  try {
    const ok = await api().recurso(S.view, k, valor);
    if (!ok) toast("O fone não confirmou essa troca.", true);
  } catch (e) { toast("Falha ao falar com o fone.", true); }
  S.ocupado = false;
  document.body.classList.remove("ocupado");
  desenhar();
}

document.addEventListener("pointerdown", (ev) => { if (ev.target.matches?.("input[type=range]")) S.arrastando = true; });
document.addEventListener("pointerup", () => { setTimeout(() => { S.arrastando = false; }, 50); });
document.addEventListener("input", (ev) => {
  const r = ev.target.closest?.("input[type=range]");
  if (!r) return;
  const n = Number(r.value), b = r.nextElementSibling;
  if (b) b.textContent = (r.dataset.slider === "balanco" && n > 0 ? "+" : "") + n;
});

document.addEventListener("change", async (ev) => {
  const sl = ev.target.dataset.slider;
  if (sl) {
    const n = Number(ev.target.value);
    await enviarRec(sl, sl === "balanco" ? { nivel: n + 100 } : n);
    return;
  }
  const kt = ev.target.dataset.rectog;
  if (kt) {
    const cur = S.estado.dispositivos.find((d) => d.id === S.view).estado.rec[kt];
    const it = Object.values(CAT).flat().flatMap((g) => g.itens).find((x) => x.k === kt);
    if (it?.confirma && !confirm(it.confirma)) { ev.target.checked = !ev.target.checked; return; }
    return enviarRec(kt, typeof cur === "object" ? { on: ev.target.checked } : (ev.target.checked ? 1 : 0));
  }
  const k = ev.target.dataset.ajuste;
  if (!k) return;
  const ok = await api().ajuste(k, ev.target.checked);
  if (!ok) { ev.target.checked = !ev.target.checked; toast("Não consegui salvar esse ajuste.", true); }
  else S.estado.ajustes[k] = ev.target.checked;
});

window.addEventListener("pywebviewready", async () => {
  window.receber(await api().estado());
});
