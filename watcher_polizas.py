# watcher_polizas.py
# Vigila Pólizas y subcarpetas, envía correo cuando aparece/modifica un PDF
from pathlib import Path
import time
import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 1) Cargar variables del archivo .env
load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER or "watcher@localhost")
MAIL_TO = os.getenv("MAIL_TO", "")
MAIL_SUBJECT_PREFIX = os.getenv("MAIL_SUBJECT_PREFIX", "[Watcher PDFs]")

# Carpeta real de pólizas (ajusta si cambia el path)
RUTA_POLIZAS = Path(
    r"C:\Users\EdsonLazo\Comercial y Servicios Cruz del Sur\Gestion - Documentos\Seguros\Pólizas"
)

LOG_FILE = Path("log_polizas.txt")  # registro simple en texto


def _destinatarios():
    if not MAIL_TO:
        return []
    raw = MAIL_TO.replace(";", ",")
    return [t.strip() for t in raw.split(",") if t.strip()]


def enviar_correo_alerta(path: Path) -> tuple[bool, str]:
    tos = _destinatarios()
    if not tos:
        return False, "MAIL_TO vacío; revisa .env"

    asunto = f"{MAIL_SUBJECT_PREFIX} Nuevo PDF: {path.name}"
    cuerpo = (
        "Se ha detectado un archivo PDF en la carpeta de pólizas.\n\n"
        f"Nombre: {path.name}\n"
        f"Ruta: {path}\n"
        f"Tamaño: {path.stat().st_size if path.exists() else 'N/A'} bytes\n"
        f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    msg = MIMEText(cuerpo, _charset="utf-8")
    msg["Subject"] = asunto
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(tos)

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        server.ehlo()
        server.starttls()
        server.ehlo()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, tos, msg.as_string())
        server.quit()
        return True, "Correo enviado"
    except Exception as e:
        return False, f"Error SMTP: {e}"


def escribir_log(texto: str) -> None:
    linea = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {texto}\n"
    LOG_FILE.write_text(LOG_FILE.read_text(encoding="utf-8") + linea if LOG_FILE.exists() else linea,
                        encoding="utf-8")
    print(linea, end="")  # también a la consola


class HandlerPolizas(FileSystemEventHandler):
    def _procesar(self, path_str: str, evento: str):
        path = Path(path_str)
        if not path.is_file():
            return
        if path.suffix.lower() != ".pdf":
            return

        # pequeña espera para que termine de copiarse
        time.sleep(1.0)

        ok, msg = enviar_correo_alerta(path)
        estado = "OK" if ok else "ERROR"
        escribir_log(f"{estado} | evento={evento} | archivo={path} | detalle={msg}")

    def on_created(self, event):
        if event.is_directory:
            return
        self._procesar(event.src_path, "created")

    def on_modified(self, event):
        if event.is_directory:
            return
        self._procesar(event.src_path, "modified")


def main():
    if not RUTA_POLIZAS.exists():
        print(f"La carpeta de pólizas NO existe: {RUTA_POLIZAS}")
        return

    print(f"Vigilando pólizas y subcarpetas: {RUTA_POLIZAS}")
    event_handler = HandlerPolizas()
    observer = Observer()
    observer.schedule(event_handler, str(RUTA_POLIZAS), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nDeteniendo watcher...")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
