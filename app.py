"""
N9Ctrl - controle do fone AKG N9 Hybrid pelo dongle USB, direto do Windows.

    pythonw app.py            abre a janela
    pythonw app.py --bandeja  inicia escondido na bandeja (usado ao iniciar com o Windows)
"""
import ctypes
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

if sys.platform != "win32":
    sys.exit("N9Ctrl only works on Windows.")

ctypes.windll.user32.SetProcessDPIAware()

AQUI = Path(__file__).resolve().parent
BASE = Path(getattr(sys, "_MEIPASS", AQUI))
sys.path.insert(0, str(AQUI))
DADOS = Path(os.environ.get("APPDATA", ".")) / "N9Ctrl"
DADOS.mkdir(parents=True, exist_ok=True)
TITULO = "N9Ctrl"
EVENTO_MOSTRAR = "Local\\N9Ctrl_Mostrar"
U32, K32 = ctypes.windll.user32, ctypes.windll.kernel32
U32.LoadImageW.restype = ctypes.c_void_p
U32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
U32.SendMessageW.restype = ctypes.c_void_p

PADRAO = {"fechar_para_bandeja": True, "iniciar_com_windows": False, "notificar_bateria": True, "idioma": "en"}

# Textos da bandeja e dos avisos. O ingles e o texto-fonte; aqui ficam as versoes em portugues.
TXT_PT = {
    "Open N9Ctrl": "Abrir N9Ctrl", "Exit": "Sair",
    "Noise cancelling": "Cancelamento de ruído", "Ambient mode": "Modo ambiente", "Off": "Desligado", "Ambient": "Ambiente",
    "{n}: dongle not found": "{n}: dongle não encontrado", "{n}: headset off": "{n}: fone desligado",
    "{n} battery at {b}%.": "Bateria do {n} em {b}%.",
}
IDIOMA = {"v": "en"}


def tr(s, **kw):
    s = TXT_PT.get(s, s) if IDIOMA["v"] == "pt" else s
    return s.format(**kw) if kw else s

log = logging.getLogger("n9ctrl")


# ---------------------------------------------------------------- utilidades Windows
def instancia_unica():
    K32.CreateMutexW(None, False, "Local\\N9Ctrl_Instancia")
    return K32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def evento(nome):
    return K32.CreateEventW(None, False, False, nome)


def caminho_inicio():
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return f'"{exe}" --bandeja'
    pyw = exe.with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else exe}" "{AQUI / "app.py"}" --bandeja'


def iniciar_com_windows(ligar):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                        0, winreg.KEY_SET_VALUE) as k:
        if ligar:
            winreg.SetValueEx(k, TITULO, 0, winreg.REG_SZ, caminho_inicio())
        else:
            try:
                winreg.DeleteValue(k, TITULO)
            except FileNotFoundError:
                pass


class Config(dict):
    arq = DADOS / "config.json"

    def __init__(self):
        super().__init__(PADRAO)
        try:
            self.update(json.loads(self.arq.read_text(encoding="utf-8")))
        except Exception:
            pass

    def salvar(self):
        self.arq.write_text(json.dumps(self, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- ponte com a interface
class Api:
    def __init__(self, app):
        self._app = app

    def estado(self):
        return self._app.retrato()

    def definir_modo(self, dev_id, modo):
        dev = self._app.devs.get(dev_id)
        return bool(dev and dev.definir_modo(modo))

    def atualizar(self, dev_id):
        dev = self._app.devs.get(dev_id)
        if dev:
            dev.atualizar(bateria=True)

    def ajuste(self, chave, valor):
        return self._app.ajuste(chave, valor)

    def recurso(self, dev_id, chave, valor):
        dev = self._app.devs.get(dev_id)
        try:
            return bool(dev and dev.definir_recurso(chave, valor))
        except Exception:
            log.exception("recurso %s", chave)
            return False



# ---------------------------------------------------------------- app
class App:
    def __init__(self, oculto):
        import webview
        from n9core import N9

        self.webview = webview
        self.config = Config()
        IDIOMA["v"] = self.config.get("idioma", "en")
        self.janela = None
        self.pronta = False
        self._hwnd = None
        self.icone = None
        self.avisou_bateria = False
        self.devs = {}
        n9 = N9(lambda est, i="n9": self._mudou(i, est))
        self.devs[n9.id] = n9
        self.api = Api(self)
        self.janela = webview.create_window(
            TITULO, url=str(BASE / "ui" / "index.html"), js_api=self.api,
            width=940, height=620, min_size=(560, 480), resizable=True,
            background_color="#05010c", hidden=oculto,
        )
        self.janela.events.loaded += self._carregou
        self.janela.events.closing += self._ao_fechar
        self.janela.events.shown += self._ao_mostrar
        self.janela.events.minimized += lambda: self._visivel(False)
        self.janela.events.restored += lambda: self._visivel(True)
        self._visivel(not oculto)
        threading.Thread(target=self._esperar_segunda, daemon=True).start()
        self._criar_bandeja()

    # ------------------------------------------------------------ estado
    def retrato(self):
        return {
            "dispositivos": [
                {"id": d.id, "nome": d.nome, "conexao": d.conexao, "recursos": d.recursos, "estado": d.estado}
                for d in self.devs.values()
            ],
            "ajustes": dict(self.config),
        }

    def _mudou(self, dev_id, est):
        if self.pronta:
            try:
                self.janela.evaluate_js(f"window.receber && window.receber({json.dumps(self.retrato())})")
            except Exception:
                pass
        self._atualizar_bandeja()
        b = est.get("bateria")
        if b is not None and self.config.get("notificar_bateria") and self.icone:
            if b <= 15 and not self.avisou_bateria:
                self.avisou_bateria = True
                try:
                    self.icone.notify(tr("{n} battery at {b}%.", n=self.devs[dev_id].nome, b=b), TITULO)
                except Exception:
                    pass
            elif b > 25:
                self.avisou_bateria = False

    def _carregou(self):
        self.pronta = True
        self._mudou("n9", self.devs["n9"].estado)

    def ajuste(self, chave, valor):
        if chave not in PADRAO:
            return False
        if chave == "iniciar_com_windows":
            try:
                iniciar_com_windows(bool(valor))
            except Exception:
                log.exception("registro")
                return False
        if chave == "idioma":
            if valor not in ("en", "pt"):
                return False
            IDIOMA["v"] = valor
        self.config[chave] = valor
        self.config.salvar()
        if chave == "idioma":
            self._atualizar_bandeja()
        return True

    # ------------------------------------------------------------ janela
    def hwnd(self):
        """Identificador da janela principal. Nao buscar pelo titulo: o icone da bandeja
        (pystray) cria uma janela invisivel com o mesmo nome "N9Ctrl"."""
        if not self._hwnd:
            try:
                self._hwnd = int(self.janela.native.Handle.ToInt64())     # Form do WinForms
            except Exception:
                log.exception("hwnd via native")
                self._hwnd = self._hwnd_por_processo()
            log.info("hwnd da janela: %s", self._hwnd)
        return self._hwnd

    def _hwnd_por_processo(self):
        """Plano B: janela visivel do nosso processo com o titulo e classe do WinForms."""
        pid, achado = K32.GetCurrentProcessId(), []
        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cada(h, _):
            p = ctypes.c_ulong()
            U32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid:
                cls, tit = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
                U32.GetClassNameW(h, cls, 256)
                U32.GetWindowTextW(h, tit, 256)
                if tit.value == TITULO and cls.value.startswith("WindowsForms"):
                    achado.append(h)
                    return False
            return True
        U32.EnumWindows(proto(cada), 0)
        return achado[0] if achado else None

    def _ao_mostrar(self):
        self._visivel(True)
        if not getattr(self, "_tema_ok", False):
            self._tema_ok = True
            try:
                aplicar_tema(self.hwnd())
            except Exception:
                log.exception("tema da janela")

    def _visivel(self, sim):
        for d in self.devs.values():
            d.janela_aberta = sim
            if sim:
                d.acordar.set()

    def mostrar(self):
        try:
            self.janela.show()
            self.janela.restore()
        except Exception:
            pass
        self._visivel(True)

    def fechar_janela(self):
        if self.config.get("fechar_para_bandeja") and self.icone:
            self.janela.hide()
            self._visivel(False)
        else:
            self.sair()

    def _ao_fechar(self):
        if getattr(self, "saindo", False):
            return True
        if self.config.get("fechar_para_bandeja") and self.icone:
            self.janela.hide()
            self._visivel(False)
            return False
        self.saindo = True
        return True

    def sair(self):
        self.saindo = True
        for d in self.devs.values():
            d.fechar()
        if self.icone:
            try:
                self.icone.stop()
            except Exception:
                pass
        try:
            self.janela.destroy()
        except Exception:
            pass
        os._exit(0)

    def _esperar_segunda(self):
        ev = evento(EVENTO_MOSTRAR)
        while True:
            if K32.WaitForSingleObject(ev, 0xFFFFFFFF) == 0:
                self.mostrar()

    # ------------------------------------------------------------ bandeja
    def _criar_bandeja(self):
        try:
            import pystray
            from pystray import Menu, MenuItem
        except ImportError:
            return
        app = self

        def bg(f):
            return lambda icon, item: threading.Thread(target=f, daemon=True).start()

        def itens_dev(d):
            def modo(m):
                return bg(lambda: d.definir_modo(m))

            def marcado(m):
                return lambda item: d.estado.get("modo") == m
            yield MenuItem(lambda item: app._resumo(d), None, enabled=False)
            for m, rot in (("anc", "Noise cancelling"), ("ambiente", "Ambient mode"), ("off", "Off")):
                yield MenuItem(tr(rot), modo(m), checked=marcado(m), radio=True,
                               enabled=lambda item: bool(d.estado.get("fone")))

        def menu():
            yield MenuItem(tr("Open N9Ctrl"), bg(self.mostrar), default=True)
            for d in app.devs.values():
                yield Menu.SEPARATOR
                yield from itens_dev(d)
            yield Menu.SEPARATOR
            yield MenuItem(tr("Exit"), bg(self.sair))

        self.icone = pystray.Icon(TITULO, desenhar_icone(None, None), TITULO, Menu(menu))
        threading.Thread(target=self.icone.run, daemon=True, name="bandeja").start()

    def _resumo(self, d):
        e = d.estado
        if not e.get("dongle"):
            return tr("{n}: dongle not found", n=d.nome)
        if not e.get("fone"):
            return tr("{n}: headset off", n=d.nome)
        bat = f"{e['bateria']}%" if e.get("bateria") is not None else "–"
        return f"{d.nome}: {bat} · {tr(NOMES_MODO.get(e.get('modo'), '?'))}"

    def _atualizar_bandeja(self):
        if not self.icone:
            return
        d = self.devs["n9"]
        e = d.estado
        try:
            self.icone.icon = desenhar_icone(e.get("bateria") if e.get("fone") else None, e.get("modo"))
            self.icone.title = self._resumo(d)
            self.icone.update_menu()
        except Exception:
            pass


def _cor(r, g, b):
    return ctypes.c_uint(r | (g << 8) | (b << 16))      # COLORREF = 0x00BBGGRR


def aplicar_tema(hwnd):
    """Barra de titulo nativa nas cores do app (Windows 11) e icone na janela."""
    if not hwnd:
        return
    dwm = ctypes.windll.dwmapi
    h = ctypes.c_void_p(hwnd)

    def attr(n, valor):
        dwm.DwmSetWindowAttribute(h, n, ctypes.byref(valor), ctypes.sizeof(valor))
    attr(20, ctypes.c_int(1))              # modo escuro
    attr(35, _cor(0x0a, 0x03, 0x19))       # cor da barra
    attr(36, _cor(0xec, 0xe6, 0xff))       # cor do texto
    attr(34, _cor(0x5b, 0x2b, 0xd6))       # cor da borda
    ico = DADOS / "icone.ico"
    desenhar_icone(None, None).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    for tipo, tam in ((0, 16), (1, 32)):   # ICON_SMALL, ICON_BIG
        hi = U32.LoadImageW(None, str(ico), 1, tam, tam, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
        if hi:
            U32.SendMessageW(h, 0x0080, ctypes.c_void_p(tipo), ctypes.c_void_p(hi))  # WM_SETICON


NOMES_MODO = {"anc": "ANC", "ambiente": "Ambient", "off": "Off"}
COR_MODO = {"anc": (255, 42, 109), "ambiente": (5, 217, 232), "off": (147, 132, 196)}


def desenhar_icone(bateria, modo):
    """Fone em gradiente synthwave com a bateria em um arco por baixo."""
    from PIL import Image, ImageDraw

    t = 64
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, t - 1, t - 1), radius=14, fill=(5, 1, 12, 255))
    cor = COR_MODO.get(modo, (185, 103, 255))
    # arco da tiara
    d.arc((12, 9, 52, 49), 180, 360, fill=(185, 103, 255, 255), width=5)
    # conchas
    d.rounded_rectangle((8, 27, 20, 46), radius=5, fill=cor + (255,))
    d.rounded_rectangle((44, 27, 56, 46), radius=5, fill=cor + (255,))
    # bateria
    d.rounded_rectangle((12, 52, 52, 58), radius=3, outline=(38, 22, 80, 255), fill=(13, 5, 32, 255))
    if bateria is not None:
        w = max(2, round(38 * bateria / 100))
        c = (255, 77, 109) if bateria <= 15 else (62, 240, 176)
        d.rounded_rectangle((13, 53, 13 + w, 57), radius=2, fill=c + (255,))
    return img


def configurar_log():
    from logging.handlers import RotatingFileHandler
    h = RotatingFileHandler(DADOS / "log.txt", maxBytes=256_000, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(h)
    threading.excepthook = lambda a: log.error("erro na thread %s", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main():
    configurar_log()
    log.info("iniciando (python %s)", sys.version.split()[0])
    if not instancia_unica():
        K32.SetEvent(evento(EVENTO_MOSTRAR))
        return
    try:
        import webview
    except ImportError:
        U32.MessageBoxW(None, "Missing dependencies. Run run.bat.", TITULO, 0x10)
        return
    App("--bandeja" in sys.argv)
    # private_mode=True faz o pywebview usar uma porta livre aleatoria. Com False ele usa
    # sempre a 42001, a mesma do BrilhoCtrl: com os dois abertos, a janela pegava a
    # pagina do outro app e dava 404. O N9Ctrl nao guarda nada no navegador.
    webview.start(private_mode=True)


if __name__ == "__main__":
    main()
