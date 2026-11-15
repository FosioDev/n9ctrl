"""
n9core - conversa com o AKG N9 Hybrid pelo dongle USB (protocolo RACE da Airoha).

Formato (ver docs/PROTOCOL.md):
  envio  : relatorio HID 0x06 = 06 <len+1> <destino> <RACE>
  leitura: GET_REPORT 0x07   = 07 <len> <destino> <len bytes>   (fluxo continuo)
  destino: 0x00 = dongle, 0x80 = headset
  RACE   : 05 <tipo> <len u16le> <id u16le> <payload>
"""
import logging
import threading
import time

import hid

VID, PID = 0x0ECB, 0x20D9
HEADSET = 0x80
log = logging.getLogger("n9ctrl")

MODOS = {"anc": 1, "ambiente": 10, "off": 0}           # valor do filtro informado pelo fone
NOME_FILTRO = {v: k for k, v in MODOS.items()}


def race(rid, p=b""):
    n = 2 + len(p)
    return bytes([5, 0x5A, n & 255, n >> 8, rid & 255, rid >> 8]) + p


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


# ---------------------------------------------------------------- recursos
# Cada pergunta (GET) devolve um quadro f = 05 <tipo> <len2> <id2> <status> <dados...>:
#   f[6] = status, f[7:] = dados. As funcoes abaixo traduzem os dados em valores simples.
def _n(f, k):
    return len(f) >= k


def _lt_voz(f):          # modulo 19: efeito do mic, efeito de som, perfil de audio
    return {"efeito_mic": f[9], "efeito_som": f[10], "modo_audio": f[11]} if _n(f, 12) and f[7] == 19 else {}


def _lt_autooff(f):      # modulo 1: ligado (u16), tempo em segundos (u16)
    if not _n(f, 13) or f[7] != 1:
        return {}
    return {"auto_off": {"on": bool(f[9] | (f[10] << 8)), "min": (f[11] | (f[12] << 8)) // 60}}


def _um(chave):          # um byte em f[7]
    return lambda f: {chave: f[7]} if _n(f, 8) and f[6] == 0 else {}


def _par(chave, a, b):   # dois bytes em f[7], f[8]
    return lambda f: {chave: {a: f[7], b: f[8]}} if _n(f, 9) and f[6] == 0 else {}


def _lt_vp(f):           # aviso de voz: eco do modulo em f[6:8], status f[8], valor f[9]
    return {"voice_prompt": f[9]} if _n(f, 10) and f[6] == 1 and f[8] == 0 else {}


def _lt_fonte(f):        # fonte ativa: f[7] 0=BT 1=dongle 2=P2, f[8] P2 ligado, f[9] dongle pareado
    if not _n(f, 8) or f[6] != 0:
        return {}
    r = {"fonte": f[7]}
    if _n(f, 10):
        r.update(fonte_aux=f[8], fonte_dongle=f[9])
    return r


def _lt_adapt(f):        # ANC adaptavel (deteccao de ambiente): f[7]=3, f[8]=ligado
    return {"anc_adapt": f[8]} if _n(f, 9) and f[6] == 0 and f[7] == 3 else {}


def _lt_autocomp(f):     # compensacao automatica: o fone guarda 0 = ligada
    return {"auto_comp": 1 - f[14]} if _n(f, 15) and f[6] == 0 and f[7] == 13 else {}


def _lt_nivel_anc(f):    # modulo 5: f[9] = filtro ativo (1 = ANC), f[10:12] = nivel
    return {"anc_nivel": f[10] | (f[11] << 8)} if _n(f, 12) and f[6] == 5 and f[9] == 1 else {}


def _lt_nivel_amb(f):    # modulo 7: f[9:11] = nivel do ambiente
    return {"amb_nivel": f[9] | (f[10] << 8)} if _n(f, 11) and f[6] == 7 else {}


GETS = [
    (0x0E17, b"\x03\x02", _lt_adapt),
    (0x0E26, bytes([0, 13, 0, 0]), _lt_autocomp),
    (0x0901, b"\x05\x00", _lt_nivel_anc),
    (0x0901, b"\x07\x00", _lt_nivel_amb),
    (0x2C83, b"\x13\x00", _lt_voz),
    (0x2C83, b"\x01\x00", _lt_autooff),
    (0x0013, b"", _par("audio3d", "on", "tipo")),
    (0x0014, b"", _um("safesound")),
    (0x0053, b"", _um("chat_opt")),
    (0x0051, b"", _um("limitador")),
    (0x0058, b"", _um("eq_baixo")),
    (0x0060, b"", _par("smart_talk", "on", "lat")),
    (0x0075, b"", _um("hires")),
    (0x0064, b"", _um("mic_ganho")),
    (0x0037, b"", _par("balanco", "on", "nivel")),
    (0x0055, b"", _um("fonte")),
    (0x0901, b"\x01\x00", _lt_vp),
]


def _bt(v):
    return 1 if v else 0


# SETs: chave -> funcao(valor, atual) -> (id RACE, payload, funcao que confere a resposta)
def _ok0(f):
    return _n(f, 7) and f[6] == 0


def _ok_vp(f):
    return _n(f, 9) and f[6] == 1 and f[8] == 0


SETS = {
    "anc_adapt": lambda v, a: (0x0E17, bytes([3, _bt(v)]), _ok0),
    "auto_comp": lambda v, a: (0x0E13, bytes([5, 1 - _bt(v)]), _ok0),
    "anc_nivel": lambda v, a: (0x0E06, bytes([0, 30]) + int(v).to_bytes(2, "little"), _ok0),
    "amb_nivel": lambda v, a: (0x0E06, bytes([0, 30]) + int(v).to_bytes(2, "little"), _ok0),
    "balanco": lambda v, a: (0x0036, bytes([_bt(v["on"]), int(v["nivel"])]), _ok0),
    "efeito_som": lambda v, a: (0x2C82, bytes([19, 0, int(v)]), _ok0),
    "efeito_mic": lambda v, a: (0x2C82, bytes([19, 0, int(v)]), _ok0),
    "modo_audio": lambda v, a: (0x2C82, bytes([19, 0, int(v)]), _ok0),
    "auto_off": lambda v, a: (0x2C82, bytes([1, 0, _bt(v["on"]), 0]) + int(v["min"] * 60).to_bytes(2, "little"), _ok0),
    "audio3d": lambda v, a: (0x0012, bytes([_bt(v["on"]), int(v["tipo"])]), _ok0),
    "safesound": lambda v, a: (0x0015, bytes([_bt(v)]), _ok0),
    "chat_opt": lambda v, a: (0x0052, bytes([_bt(v)]), _ok0),
    "limitador": lambda v, a: (0x0050, bytes([_bt(v)]), _ok0),
    "eq_baixo": lambda v, a: (0x0059, bytes([_bt(v)]), _ok0),
    "smart_talk": lambda v, a: (0x0061, bytes([_bt(v["on"]), int(v["lat"])]), _ok0),
    "fonte": lambda v, a: (0x0054, bytes([int(v)]), _ok0),
    "hires": lambda v, a: (0x0076, bytes([_bt(v)]), _ok0),
    "voice_prompt": lambda v, a: (0x0900, bytes([1, 0, _bt(v)]), _ok_vp),
}


class N9:
    """Mantem a conexao com o dongle, reconecta sozinho e publica o estado."""

    id = "n9"
    nome = "AKG N9 Hybrid"
    conexao = "Dongle USB 2.4 GHz"
    recursos = ["bateria", "modo", "ajustes"]

    def __init__(self, ao_mudar):
        self.ao_mudar = ao_mudar          # funcao(estado: dict)
        self.dev = None
        self.io = threading.Lock()        # acesso ao HID
        self.cmd = threading.Lock()       # um pedido por vez
        self.bufs = {}
        self.ouvintes = []
        self.estado = {"dongle": False, "fone": False, "bateria": None, "modo": None, "atualizado": None, "rec": {}}
        self.janela_aberta = False        # janela aberta -> consulta mais vezes
        self.acordar = threading.Event()  # forca uma consulta imediata
        self.parar = False
        self._timer_rec = None
        self._pend = set()                # ids que estamos esperando (respostas, nao avisos)
        threading.Thread(target=self._leitor, daemon=True, name="leitor").start()
        threading.Thread(target=self._vigia, daemon=True, name="vigia").start()

    # ------------------------------------------------------------ conexao
    def _abrir(self):
        for i in hid.enumerate(VID, PID):
            if i["usage_page"] == 0xFF13 and "&Col02" in i["path"].decode(errors="ignore"):
                d = hid.device()
                d.open_path(i["path"])
                return d
        return None

    def _fechar(self):
        with self.io:
            if self.dev:
                try:
                    self.dev.close()
                except Exception:
                    pass
            self.dev = None
            self.bufs.clear()

    def _publicar(self, **mud):
        novo = dict(self.estado, **mud)
        if novo != self.estado:
            self.estado = novo
            try:
                self.ao_mudar(dict(novo))
            except Exception:
                log.exception("ao_mudar")

    # ------------------------------------------------------------ leitura
    def _leitor(self):
        while not self.parar:
            if not self.dev:
                time.sleep(0.2)
                continue
            try:
                with self.io:
                    r = bytes(self.dev.get_input_report(0x07, 62)) if self.dev else b""
            except Exception as e:
                log.warning("dongle sumiu: %s", e)
                self._fechar()
                self._publicar(dongle=False, fone=False)
                continue
            if len(r) >= 3 and r[1] > 0:
                alvo = r[2]
                self.bufs[alvo] = self._quebrar(alvo, self.bufs.get(alvo, b"") + r[3:3 + r[1]])
            else:
                time.sleep(0.03)

    def _quebrar(self, alvo, buf):
        while True:
            i = buf.find(b"\x05")
            if i < 0:
                return b""
            if len(buf) - i < 6:
                return buf[i:]
            if buf[i + 1] not in (0x5A, 0x5B, 0x5C, 0x5D):
                buf = buf[i + 1:]
                continue
            n = buf[i + 2] | (buf[i + 3] << 8)
            if n > 600:
                buf = buf[i + 1:]
                continue
            if len(buf) - i < 4 + n:
                return buf[i:]
            f = bytes(buf[i:i + 4 + n])
            buf = buf[i + 4 + n:]
            if f[4:6] == b"\x92\x0f":      # log de debug do firmware
                continue
            self._quadro(alvo, f)

    def _quadro(self, alvo, f):
        for cb in list(self.ouvintes):
            cb(alvo, f)
        rid = f[4] | (f[5] << 8)
        # o fone avisa sozinho quando o modo muda (ex.: botao fisico)
        if alvo == HEADSET and rid == 0x0901 and f[1] == 0x5D and len(f) >= 10:
            log.info("aviso de ruido 0x0901: %s", hexs(f[6:]))
            self._publicar(fone=True, modo=NOME_FILTRO.get(f[9], f"filtro {f[9]}"))
        # avisos espontaneos do fone (mudancas feitas pelo app do celular, p.ex.)
        if alvo == HEADSET and f[1] in (0x5C, 0x5D) and rid != 0x0CD6:
            if rid != 0x0901 and rid not in self._pend:
                log.info("aviso do fone 0x%04X tipo=%02X: %s", rid, f[1], hexs(f[6:]))
            for gid, _pay, tradutor in GETS:
                if gid == rid:
                    try:
                        novo = tradutor(f)
                    except Exception:
                        novo = {}
                    if novo:
                        rec = dict(self.estado.get("rec") or {})
                        rec.update(novo)
                        self._publicar(rec=rec)
            if rid not in self._pend:
                self._reler_logo()  # aviso espontaneo do fone: confere tudo daqui a pouco

    def _reler_logo(self, seg=1.2):
        """Junta varios avisos seguidos e relê os recursos uma vez (atualizacao em tempo real)."""
        t = self._timer_rec
        if t:
            t.cancel()
        self._timer_rec = threading.Timer(seg, self._reler_bg)
        self._timer_rec.daemon = True
        self._timer_rec.start()

    def _reler_bg(self):
        try:
            if self.dev and self.estado.get("fone") and not self.parar:
                self.ler_recursos()
        except Exception:
            log.exception("reler")

    # ------------------------------------------------------------ pedidos
    def _enviar(self, alvo, pkt):
        rep = (bytes([0x06, len(pkt) + 1, alvo]) + pkt).ljust(62, b"\x00")
        with self.io:
            if not self.dev:
                raise OSError("sem dongle")
            self.dev.write(rep)

    def pedir(self, pkt, rid, tipo=None, espera=2.5, tentativas=2):
        with self.cmd:
            for _ in range(tentativas):
                ev, caixa = threading.Event(), []

                def cb(alvo, f):
                    if alvo == HEADSET and (f[4] | (f[5] << 8)) == rid and (tipo is None or f[1] == tipo):
                        caixa.append(f)
                        ev.set()
                self.ouvintes.append(cb)
                self._pend.add(rid)
                try:
                    self._enviar(HEADSET, pkt)
                    ev.wait(espera)
                except OSError:
                    return None
                finally:
                    self.ouvintes.remove(cb)
                    time.sleep(0.05)
                    self._pend.discard(rid)
                if caixa:
                    return caixa[0]
            return None

    def ler_bateria(self):
        f = self.pedir(race(0x0CD6, b"\x00"), 0x0CD6, tipo=0x5D)
        return f[8] if f and len(f) >= 9 else None

    def ler_modo(self):
        f = self.pedir(race(0x0901, b"\x05\x00"), 0x0901)
        if f and len(f) >= 10:
            return NOME_FILTRO.get(f[9], f"filtro {f[9]}")
        return None

    def definir_modo(self, modo):
        if modo == "off":
            pkt = race(0x0E06, b"\x00\x0B")
        elif modo == "anc":
            pkt = race(0x0E06, bytes([0, 0x0A, 1, 0]))
        elif modo == "ambiente":
            pkt = race(0x0E06, bytes([0, 0x0A, 10, 4]))
        else:
            raise ValueError(modo)
        f = self.pedir(pkt, 0x0E06)
        ok = bool(f) and len(f) >= 7 and f[6] == 0
        log.info("definir_modo %s -> %s", modo, hexs(f) if f else "sem resposta")
        if ok:
            self._publicar(fone=True, modo=modo)
        threading.Timer(0.8, self.atualizar).start()   # confirma com o fone
        return ok

    # ------------------------------------------------------------ recursos
    def ler_recursos(self):
        """Pergunta ao fone o estado de todas as funcoes e publica o resultado."""
        rec = dict(self.estado.get("rec") or {})
        for rid, pay, tradutor in GETS:
            if self.parar or not self.dev:
                break
            f = self.pedir(race(rid, pay), rid, espera=1.5, tentativas=2)
            if not f:
                continue
            try:
                rec.update(tradutor(bytes(f)))
            except Exception:
                log.exception("traduzir 0x%04X", rid)
        self._publicar(rec=rec)

    def definir_recurso(self, chave, valor):
        """Envia uma troca, confere a resposta e le o valor de volta."""
        montar = SETS.get(chave)
        if not montar or not self.dev:
            return False
        atual = (self.estado.get("rec") or {}).get(chave)
        if isinstance(atual, dict) and isinstance(valor, dict):
            valor = dict(atual, **valor)
        rid, pay, confere = montar(valor, atual)
        f = self.pedir(race(rid, pay), rid, espera=2.5, tentativas=1)
        ok = bool(f) and confere(bytes(f))
        log.info("definir %s=%s -> %s", chave, valor, hexs(f) if f else "sem resposta")
        if chave == "fonte":
            # ao ir para o Bluetooth o fone sai do dongle: nao da para reler, so publica
            if ok:
                self._publicar(rec=dict(self.estado.get("rec") or {}, fonte=int(valor)))
            return ok
        self.ler_recursos()
        novo = (self.estado.get("rec") or {}).get(chave)
        if ok and novo is not None:
            quer = valor
            if isinstance(quer, dict):
                ok = all(novo.get(k) == (_bt(v) if k == "on" else int(v)) for k, v in quer.items())
            else:
                ok = novo == (_bt(quer) if chave in ("safesound", "chat_opt", "limitador", "eq_baixo", "hires", "voice_prompt", "anc_adapt", "auto_comp") else int(quer))
        return ok

    # ------------------------------------------------------------ ciclo
    def atualizar(self, bateria=False):
        if not self.dev:
            return
        modo = self.ler_modo()
        mud = {"fone": modo is not None}
        if modo is not None:
            mud["modo"] = modo
            mud["atualizado"] = time.strftime("%H:%M")
        if bateria or self.estado["bateria"] is None:
            b = self.ler_bateria()
            if b is not None:
                mud["bateria"] = b
        self._publicar(**mud)

    def _vigia(self):
        ultima_bat = ultima_rec = 0
        while not self.parar:
            if not self.dev:
                try:
                    d = self._abrir()
                except Exception:
                    d = None
                if d:
                    with self.io:
                        self.dev = d
                    log.info("dongle conectado")
                    self._publicar(dongle=True)
                    time.sleep(0.8)            # esvazia a fila antiga
                    ultima_bat = 0
                else:
                    self._publicar(dongle=False, fone=False)
                    time.sleep(3)
                    continue
            agora = time.time()
            try:
                self.atualizar(bateria=agora - ultima_bat > 120)
                if agora - ultima_bat > 120:
                    ultima_bat = agora
                if self.estado["fone"] and (not self.estado["rec"] or (self.janela_aberta and agora - ultima_rec > 8)):
                    self.ler_recursos()
                    ultima_rec = agora
            except Exception:
                log.exception("atualizar")
            self.acordar.wait(4 if self.janela_aberta else 20)
            self.acordar.clear()

    def fechar(self):
        self.parar = True
        self._fechar()
