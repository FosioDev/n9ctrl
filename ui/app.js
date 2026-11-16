// N9Ctrl - interface. O Python chama window.receber(estado) quando algo muda.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const S = { estado: null, view: null, trocando: null };
const api = () => window.pywebview && window.pywebview.api;

// Idioma: o ingles e o texto-fonte; T() troca pelo portugues quando window.LANG === "pt" (ui/i18n.js).
function aplicarIdioma(l) {
  window.LANG = l === "pt" ? "pt" : "en";
  document.documentElement.lang = window.LANG === "pt" ? "pt-BR" : "en";
  document.querySelectorAll("[data-i]").forEach((el) => { el.textContent = T(el.dataset.i); });
  document.querySelectorAll("[data-i-aria]").forEach((el) => { el.setAttribute("aria-label", T(el.dataset.iAria)); });
}

const ICONE_FONE = `<svg viewBox="0 0 24 24"><path d="M4 14v-2a8 8 0 0 1 16 0v2" fill="none" stroke="currentColor" stroke-width="2"/><rect x="2.5" y="13" width="5" height="8" rx="2" fill="currentColor"/><rect x="16.5" y="13" width="5" height="8" rx="2" fill="currentColor"/></svg>`;
const MODOS = [
  { id: "anc", nome: "Noise cancelling", desc: "Blocks the noise around you",
    svg: `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 4a10 10 0 1 0 0 20 10 10 0 0 0 0-20z"/><path d="M7 21L21 7"/><path d="M10 14h8" opacity=".5"/></svg>` },
  { id: "ambiente", nome: "Ambient", desc: "Lets outside sound in",
    svg: `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 9a7 7 0 0 1 10 0M5.5 5.5a12 12 0 0 1 17 0"/><circle cx="14" cy="15" r="2.4"/><path d="M14 17.5V24"/></svg>` },
  { id: "off", nome: "Off", desc: "Pure sound, uses less battery",
    svg: `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 4v9"/><path d="M8.3 8.3a8 8 0 1 0 11.4 0"/></svg>` },
];

// Funcoes do fone: k = chave no Python (n9core.GETS/SETS).
// tipo: tog | op | tog3 (liga + opcoes) | passo (- valor +).  dep: depende do Hi-Res desligado no app oficial.
const ABAS = [["ruido", "Noise"], ["audio", "Audio"], ["voz", "Voice & system"]];
const CAT = {
  ruido: [
    { grupo: "Customize ANC", itens: [
      { k: "anc_adapt", nome: "Adaptive ANC", desc: "Adjusts cancelling to the noise around you", tipo: "tog" },
      { k: "auto_comp", nome: "Auto compensation", desc: "Adjusts cancelling to how the headset sits on your ears", tipo: "tog" },
      { k: "anc_nivel", nome: "ANC level", desc: "Cancelling strength (1 to 7)", tipo: "slider", min: 1, max: 7,
        se: (e) => e.modo === "anc", bloq: (rec) => rec.anc_adapt === 1, motivo: "Turn off Adaptive ANC to adjust manually." },
      { k: "amb_nivel", nome: "Ambient sound level", desc: "How much outside sound comes in (1 to 7)", tipo: "slider", min: 1, max: 7, se: (e) => e.modo === "ambiente" },
    ] },
    { grupo: "Balance", itens: [
      { k: "balanco", nome: "Left/right balance", desc: "Shifts the sound to one side (−16 left, +16 right)", tipo: "balanco" },
    ] },
  ],
  audio: [
    { grupo: "Entertainment", itens: [
      { k: "hires", nome: "Hi-Res audio", desc: "The headset restarts when you change this", tipo: "tog", confirma: "The headset will restart to change Hi-Res. Continue?" },
      { k: "audio3d", nome: "Spatial sound", desc: "3D audio", tipo: "tog3", sub: "tipo", ops: [[2, "Movie"], [1, "Music"], [3, "Game"]], dep: true },
      { k: "eq_baixo", nome: "Low-volume dynamic EQ", desc: "Boosts bass when the volume is low (subtle effect)", tipo: "tog", dep: true },
      { k: "safesound", nome: "Maximum volume control", desc: "Protects your hearing with a volume limit", tipo: "tog" },
    ] },
    { grupo: "Call mode", itens: [
      { k: "modo_audio", nome: "Active profile", desc: "When the call ends, switch back to Entertainment", tipo: "op", ops: [[8, "Entertainment"], [9, "Call"]] },
      { k: "limitador", nome: "Sound level optimizer", desc: "Controls the volume peaks you hear", tipo: "tog" },
      { k: "efeito_som", nome: "Sound settings", desc: "How other people's voices sound in a call", tipo: "op", ops: [[5, "Natural"], [6, "Boosted treble"], [7, "Boosted bass"]] },
      { k: "efeito_mic", nome: "Voice settings", desc: "How your voice is sent in a call", tipo: "op", ops: [[1, "Natural"], [2, "Powerful"], [3, "Bright"]] },
    ] },
  ],
  voz: [
    { grupo: "Connection", itens: [
      { k: "fonte", nome: "Headset output", desc: "Choose whether the headset plays through the USB dongle or Bluetooth", tipo: "op",
        ops: [[1, "USB dongle"], [0, "Bluetooth"]],
        confirmaVal: { 0: "The headset will leave the dongle and play over Bluetooth. N9Ctrl loses control until you switch back to the dongle (from the phone app or the headset button). Continue?" } },
    ] },
    { grupo: "Microphone & voice", itens: [
      { k: "smart_talk", nome: "Smart Talk", desc: "Pauses and lets ambient sound in when you speak. The official app only enables it on Bluetooth", tipo: "tog3" },
      { k: "chat_opt", nome: "Chat optimizer", desc: "Internal headset feature; the official app doesn't show it", tipo: "tog" },
      { k: "voice_prompt", nome: "Voice prompts", desc: "Headset voice when powering on, connecting, etc.", tipo: "tog" },
    ] },
    { grupo: "System", itens: [
      { k: "auto_off", nome: "Auto power off", desc: "Turns the headset off after a while idle", tipo: "tog3", sub: "min",
        ops: [[30, "30 min"], [60, "1 h"], [120, "2 h"]] },
    ] },
  ],
};
const FONTES = ["Bluetooth", "USB dongle", "P2 cable"];

function controleRec(it, rec) {
  const v = rec[it.k];
  if (v === undefined) return `<span class="rec-nd">${T("unavailable")}</span>`;
  const chips = (atual, sub) => `<span class="chips">${it.ops.map(([val, nome]) =>
    `<button class="chip ${atual === val ? "ativo" : ""}" data-rec="${it.k}" data-sub="${sub || ""}" data-val="${val}">${T(nome)}</button>`).join("")}</span>`;
  if (it.tipo === "op") return chips(v);
  if (it.tipo === "slider" || it.tipo === "balanco") {
    const bal = it.tipo === "balanco";
    const num = bal ? v.nivel - 100 : v;
    const lim = bal ? [-16, 16] : [it.min, it.max];
    const bloq = bal ? !v.on : (it.bloq && it.bloq(rec));
    const tog = bal ? `<label class="chave"><input type="checkbox" data-rectog="${it.k}" ${v.on ? "checked" : ""}><i class="trilho"></i></label>` : "";
    return `${tog}<span class="deslizar"><input type="range" min="${lim[0]}" max="${lim[1]}" step="1" value="${num}" data-slider="${it.k}" ${bloq ? "disabled" : ""} aria-label="${T(it.nome)}"><b>${bal && num > 0 ? "+" : ""}${num}</b></span>`;
  }
  if (it.tipo === "passo") return `<span class="passo"><button class="chip" data-passo="${it.k}" data-delta="-1" aria-label="${T("Decrease")}">−</button><b>${v}</b><button class="chip" data-passo="${it.k}" data-delta="1" aria-label="${T("Increase")}">+</button></span>`;
  const lig = typeof v === "object" ? !!v.on : !!v;
  const chave = `<label class="chave"><input type="checkbox" data-rectog="${it.k}" ${lig ? "checked" : ""}><i class="trilho"></i></label>`;
  if (it.tipo === "tog3" && it.ops) return chave + chips(v[it.sub], it.sub);
  return chave;
}

function viewRecursos(d) {
  const e = d.estado, rec = e.rec || {};
  if (!e.fone) return "";
  if (!Object.keys(rec).length) return `<section class="secao"><p class="nota">${T("Reading the headset's features…")}</p></section>`;
  const aba = ABAS.some(([id]) => id === S.aba) ? S.aba : ABAS[0][0];
  const hires = rec.hires === 1;
  const grupos = CAT[aba].map((g) => {
    const itens = g.itens.filter((it) => rec[it.k] !== undefined && (!it.se || it.se(e))).map((it) => `
      <div class="linha-rec ${it.dep && hires ? "dep" : ""}"><div class="rec-txt"><b>${T(it.nome)}</b>${it.desc ? `<span>${T(it.desc)}</span>` : ""}
        ${it.bloq && it.bloq(rec) ? `<span class="aviso">${T(it.motivo)}</span>` : ""}
        ${it.dep && hires ? `<span class="aviso">${T("The official app locks this while Hi-Res is on, but the headset accepts the command.")}</span>` : ""}</div>
        <div class="rec-ctl">${controleRec(it, rec)}</div></div>`).join("");
    return itens ? `<h3>${T(g.grupo)}</h3><div class="grupo-rec">${itens}</div>` : "";
  }).join("");
  const info = aba === "voz" && rec.fonte === 2 ? `<p class="nota">${T("Active source right now")}: <b>${T(FONTES[2])}</b></p>` : "";
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
  if (!e.dongle) return { txt: T("Dongle not found"), cls: "" };
  if (!e.fone) return { txt: T("Headset off or out of range"), cls: "alerta" };
  return { txt: T("Connected"), cls: "ok" };
}

function desenharLista() {
  const devs = S.estado?.dispositivos || [];
  $("#lista").innerHTML = devs.map((d) => {
    const e = d.estado, st = status(e);
    const bat = e.fone && e.bateria != null ? `${e.bateria}%` : "";
    return `<button class="dev-card ${S.view === d.id ? "ativo" : ""}" data-dev="${d.id}">
      <div class="dev-ico" title="${esc(d.nome)}">${ICONE_FONE}</div>
      <div class="dev-txt"><b>${esc(d.nome)}</b><span><i class="ponto ${st.cls}"></i>${e.fone ? T("connected") : e.dongle ? T("headset off") : T("no dongle")}${bat ? ` · ${bat}` : ""}</span></div></button>`;
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
          <span>${esc(T(d.conexao))}</span>
          ${e.atualizado ? `<span>${T("read at")} ${e.atualizado}</span>` : ""}
        </p>
      </div>
      <div class="bateria">
        <div class="bateria-num">${bat != null ? `${bat}<small>%</small>` : "––"}</div>
        <div class="segs ${bat != null && bat <= 15 ? "baixa" : ""}">${segs}</div>
        <div class="bateria-rot">${T("Battery")}</div>
      </div>
    </div>
  </section>
  <div class="abas" role="tablist">${ABAS.map(([id, nome]) => `<button class="aba ${(S.aba || "ruido") === id ? "ativo" : ""}" role="tab" data-aba="${id}">${T(nome)}</button>`).join("")}</div>
  ${(S.aba || "ruido") === "ruido" ? `<section class="secao">
    <h2>${T("Noise control")}</h2>
    <div class="modos">${modos}</div>
    <p class="nota">${T("The headset button and the phone app also change the mode, and N9Ctrl follows instantly.")}</p>
  </section>` : ""}${viewRecursos(d)}`;
}

function viewAjustes() {
  const a = S.estado?.ajustes || {};
  const chave = (k, nome, desc) => `
    <label class="linha-aj"><div><b>${T(nome)}</b><span>${T(desc)}</span></div>
      <span class="chave"><input type="checkbox" data-ajuste="${k}" ${a[k] ? "checked" : ""}><i class="trilho"></i></span></label>`;
  const lang = window.LANG;
  return `<div class="pagina">
    <h1>${T("Settings")}</h1><p class="intro">${T("N9Ctrl lives in the Windows tray and only checks the headset from time to time.")}</p>
    ${chave("iniciar_com_windows", "Start with Windows", "Opens hidden in the tray when you turn on the PC.")}
    ${chave("fechar_para_bandeja", "Close to tray", "The X hides the window. To quit for good, use Exit in the tray menu.")}
    ${chave("notificar_bateria", "Low battery alert", "Shows a notification when the headset reaches 15%.")}
    <div class="linha-aj"><div><b>${T("Language")}</b><span>${T("Interface and tray menu language.")}</span></div>
      <span class="chips"><button class="chip ${lang === "en" ? "ativo" : ""}" data-idioma="en">English</button><button class="chip ${lang === "pt" ? "ativo" : ""}" data-idioma="pt">Português</button></span></div>
    <p class="sobre">N9Ctrl 1.0 · ${T("control over the USB dongle (Airoha RACE protocol)")} · log: %APPDATA%\\N9Ctrl\\log.txt</p>
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

window.receber = (estado) => {
  S.estado = estado;
  aplicarIdioma(estado.ajustes?.idioma);
  if (!S.arrastando) desenhar();
};

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("button, [data-ajuste]");
  if (!t) return;
  if (t.dataset.idioma) {
    const ok = await api().ajuste("idioma", t.dataset.idioma);
    if (ok) { S.estado.ajustes.idioma = t.dataset.idioma; aplicarIdioma(t.dataset.idioma); desenhar(); }
    return;
  }
  if (t.dataset.rec) {
    const v = Number(t.dataset.val);
    const it = Object.values(CAT).flat().flatMap((g) => g.itens).find((x) => x.k === t.dataset.rec);
    if (it?.confirmaVal?.[v] && !confirm(T(it.confirmaVal[v]))) return;
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
      if (!ok) toast(T("The headset didn't confirm the change. Try again."), true);
    } catch (e) { toast(T("Couldn't talk to the headset."), true); }
    S.trocando = null; desenhar();
  }
});

async function enviarRec(k, valor) {
  if (S.ocupado) return;
  S.ocupado = true;
  document.body.classList.add("ocupado");
  try {
    const ok = await api().recurso(S.view, k, valor);
    if (!ok) toast(T("The headset didn't confirm this change."), true);
  } catch (e) { toast(T("Couldn't talk to the headset."), true); }
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
    if (it?.confirma && !confirm(T(it.confirma))) { ev.target.checked = !ev.target.checked; return; }
    return enviarRec(kt, typeof cur === "object" ? { on: ev.target.checked } : (ev.target.checked ? 1 : 0));
  }
  const k = ev.target.dataset.ajuste;
  if (!k) return;
  const ok = await api().ajuste(k, ev.target.checked);
  if (!ok) { ev.target.checked = !ev.target.checked; toast(T("Couldn't save this setting."), true); }
  else S.estado.ajustes[k] = ev.target.checked;
});

window.addEventListener("pywebviewready", async () => {
  window.receber(await api().estado());
});
