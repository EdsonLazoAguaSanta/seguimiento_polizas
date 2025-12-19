from pathlib import Path
import os
import msal
import requests
from typing import List, Optional

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    FileResponse,   # aquí va FileResponse
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import json
import re
from msal import ConfidentialClientApplication
from datetime import datetime, date
from dotenv import load_dotenv


# === CONFIGURACIÓN MSAL / GRAPH (SharePoint viejo) ===
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
SHAREPOINT_HOST = "servicioscruzdelsur.sharepoint.com"
SHAREPOINT_SITE_PATH = "/sites/Gestion"
POLIZAS_FOLDER_PATH = "/Seguros/Pólizas"
BANCOS_FOLDER_PATH = "/Seguros/Pólizas"

# ---------------------------------------------------------------------
# Cargar .env y configuración Graph (correo)
# ---------------------------------------------------------------------
load_dotenv()

GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
GRAPH_USER = os.getenv("GRAPH_USER", "")
GRAPH_FOLDER_DISPLAY_NAME = os.getenv("GRAPH_FOLDER_DISPLAY_NAME", "Seguros")
GRAPH_BANKS_FOLDER_DISPLAY_NAME = os.getenv("GRAPH_BANKS_FOLDER_DISPLAY_NAME", "Bancos")

# Carpeta de pólizas (misma del watcher local, casi no se usa ahora)
RUTA_POLIZAS = Path(
    r"/sites/Gestion/Documentos compartidos/Seguros/Pólizas"
)

# Rutas de clasificación en disco
RUTA_CLASIF = Path("clasificacion_siniestros.json")
RUTA_CLASIF_BANCOS = Path("clasificacion_bancos.json")


def cargar_clasificacion_bancos() -> dict[str, list[dict]]:
    if not RUTA_CLASIF_BANCOS.exists():
        return {}
    try:
        with RUTA_CLASIF_BANCOS.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): list(v) for k, v in data.items()}
    except Exception as e:
        print("[BANCOS] Error al leer clasificacion bancos:", e)
        return {}


def guardar_clasificacion_bancos(mapa: dict[str, list[dict]]) -> None:
    try:
        with RUTA_CLASIF_BANCOS.open("w", encoding="utf-8") as f:
            json.dump(mapa, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[BANCOS] Error al guardar clasificacion bancos:", e)


# ---------------------------------------------------------------------
# Almacenes en memoria
# ---------------------------------------------------------------------
# Siniestros
CLASIFICACION_SINIESTROS: dict[str, list[dict]] = {}
CORREOS_CLASIFICADOS: set[str] = set()
CLASIF_SINIESTROS_MAIL: dict[str, str] = {}
PATRON_SINIESTRO = re.compile(r"[Nn][°o]\s*([0-9]{3,})")

# Bancos
POLIZAS_BENEF_BANCO: set[str] = set()
CORREOS_BANCOS_CLASIFICADOS: dict[str, list[dict]] = cargar_clasificacion_bancos()

# ---------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------
app = FastAPI(title="Gestión de Seguros Agua Santa")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------
# Utilidades Pólizas
# ---------------------------------------------------------------------
def listar_pdfs_por_carpeta() -> dict[str, list[dict]]:
    """
    Devuelve un diccionario con PDFs de subcarpetas,
    solo de años 2024 y 2025 (según fecha de modificación):
    {
        "Carpeta1": [
            {
                "nombre": "archivo1.pdf",
                "mtime": "2025-12-16 10:15",
                "mtime_date": date,
                "rel_path": "Carpeta1/archivo1.pdf"
            },
            ...
        ],
        ...
    }
    """
    resultado: dict[str, list[dict]] = {}
    if not RUTA_POLIZAS.exists():
        return resultado

    for path in RUTA_POLIZAS.rglob("*.pdf"):
        rel = path.relative_to(RUTA_POLIZAS)

        # Solo considerar archivos que estén en subcarpetas, no en la raíz
        if rel.parent == Path("."):
            continue

        carpeta = str(rel.parent)
        nombre = rel.name

        stat_info = path.stat()
        mtime_dt = datetime.fromtimestamp(stat_info.st_mtime)
        year = mtime_dt.year

        # Filtrar por años 2024 y 2025
        if year not in (2024, 2025):
            continue

        mtime_str = mtime_dt.strftime("%Y-%m-%d %H:%M")

        resultado.setdefault(carpeta, []).append(
            {
                "nombre": nombre,
                "mtime": mtime_str,
                "mtime_date": mtime_dt.date(),
                "rel_path": str(rel).replace("\\", "/"),
            }
        )

    # Ordenar archivos dentro de cada carpeta
    for carpeta in resultado:
        resultado[carpeta].sort(key=lambda x: x["nombre"])

    # Ordenar carpetas por nombre
    return dict(sorted(resultado.items(), key=lambda x: x[0]))


def listar_subcarpetas_polizas() -> list[str]:
    """Devuelve las subcarpetas (nivel 1) bajo RUTA_POLIZAS."""
    if not RUTA_POLIZAS.exists():
        return []
    return sorted(
        [p.name for p in RUTA_POLIZAS.iterdir() if p.is_dir()]
    )  # [web:148][web:149]

def cargar_clasificacion_siniestros() -> dict[str, str]:
    if not RUTA_CLASIF.exists():
        return {}
    try:
        with RUTA_CLASIF.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        print("[SINIESTROS] Error al leer clasificacion:", e)
        return {}


def guardar_clasificacion_siniestros(mapa: dict[str, str]) -> None:
    try:
        with RUTA_CLASIF.open("w", encoding="utf-8") as f:
            json.dump(mapa, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[SINIESTROS] Error al guardar clasificacion:", e)

def get_graph_token() -> str:
    """Obtiene un token de aplicación para Microsoft Graph."""
    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"No se pudo obtener token de Graph: {result}")
    return result["access_token"]


def get_sharepoint_site_and_drive():
    """
    Devuelve (site_id, drive_id) del sitio /sites/Gestion y la biblioteca
    'Documentos compartidos'.
    """
    token = get_graph_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 1) Resolver el sitio
    site_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}:{SHAREPOINT_SITE_PATH}"
    resp_site = requests.get(site_url, headers=headers)
    resp_site.raise_for_status()
    site_id = resp_site.json()["id"]

    # 2) Encontrar el drive de documentos
    drives_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
    resp_drives = requests.get(drives_url, headers=headers)
    resp_drives.raise_for_status()
    drives = resp_drives.json().get("value", [])

    drive_id = None
    for d in drives:
        print("[DEBUG] Drive:", d.get("name"), d.get("id"))
        nombre = (d.get("name") or "").lower()
        # aquí usamos el nombre que te muestra el error: 'Documentos'
        if "documentos" in nombre:
            drive_id = d.get("id")
            break

    if not drive_id:
        raise RuntimeError(
            f"No se encontró el drive de documentos en el sitio de SharePoint. "
            f"Drives encontrados: {[d.get('name') for d in drives]}"
        )

    return site_id, drive_id



from datetime import datetime


def get_sharepoint_folder_tree(folder_path: str):
    """
    Lee una carpeta de SharePoint (por ej. /Seguros/Pólizas) de forma recursiva
    y devuelve una lista de carpetas con sus archivos PDF filtrados:
      - nombre contiene 'póliza'
      - año actual o año anterior
    """
    token = get_graph_token()
    headers = {"Authorization": f"Bearer {token}"}

    _, drive_id = get_sharepoint_site_and_drive()

    # Carpeta raíz (ej /Seguros/Pólizas)
    folder_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:{folder_path}"
    resp_folder = requests.get(folder_url, headers=headers)
    resp_folder.raise_for_status()
    root = resp_folder.json()
    root_id = root["id"]

    year_now = datetime.utcnow().year
    years_validos = {year_now, year_now - 1}

    def listar_recursivo(item_id: str, nombre_carpeta: str, acumulador: dict):
        """
        Recorre una carpeta y sus subcarpetas, acumulando archivos por
        'nombre_carpeta' (clave de primer nivel).
        """
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        items = resp.json().get("value", [])

        for it in items:
            # Si es subcarpeta, seguimos recursivamente
            if "folder" in it:
                sub_id = it["id"]
                sub_name = it["name"]
                # Usamos el nombre de la carpeta de primer nivel como agrupador
                if nombre_carpeta is None:
                    agrupador = sub_name
                else:
                    agrupador = nombre_carpeta
                listar_recursivo(sub_id, agrupador, acumulador)
            # Si es archivo
            elif "file" in it:
                nombre_archivo = it.get("name", "")
                if not nombre_archivo.lower().endswith(".pdf"):
                    continue
                # debe contener "póliza" en el nombre
                if "póliza" not in nombre_archivo.lower() and "poliza" not in nombre_archivo.lower():
                    continue

                # año de modificación
                fecha_str = it.get("lastModifiedDateTime")
                try:
                    fecha_dt = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
                    if fecha_dt.year not in years_validos:
                        continue
                except Exception:
                    # si no se puede parsear la fecha, se descarta
                    continue

                download_url = it.get("@microsoft.graph.downloadUrl")
                # agrupamos por nombre_carpeta de primer nivel
                carpeta_key = nombre_carpeta or "Otros"

                acumulador.setdefault(carpeta_key, []).append(
                    {
                        "nombre": nombre_archivo,
                        "url": download_url,
                        "fecha": fecha_dt.strftime("%Y-%m-%d %H:%M"),
                    }
                )

    # acumulador: { "Aas": [archivos...], "Acar": [archivos...] }
    acumulador: dict[str, list[dict]] = {}
    # empezamos en la raíz; nombre_carpeta None porque aún no sabemos
    listar_recursivo(root_id, None, acumulador)

    # Convertir a la estructura que esperan los templates
    resultado = []
    for carpeta, archivos in acumulador.items():
        archivos_ordenados = sorted(archivos, key=lambda x: x["nombre"].lower())
        resultado.append(
            {
                "carpeta": carpeta,
                "cantidad": len(archivos_ordenados),
                "archivos": archivos_ordenados,
            }
        )

    resultado.sort(key=lambda x: x["carpeta"].lower())
    return resultado

def cargar_clasificacion_bancos() -> dict[str, list[dict]]:
    if not RUTA_CLASIF_BANCOS.exists():
        return {}
    try:
        with RUTA_CLASIF_BANCOS.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # asegurar tipos correctos
        return {str(k): list(v) for k, v in data.items()}
    except Exception as e:
        print("[BANCOS] Error al leer clasificacion bancos:", e)
        return {}


def guardar_clasificacion_bancos(mapa: dict[str, list[dict]]) -> None:
    try:
        with RUTA_CLASIF_BANCOS.open("w", encoding="utf-8") as f:
            json.dump(mapa, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[BANCOS] Error al guardar clasificacion bancos:", e)

# ---------------------------------------------------------------------
# Utilidades Microsoft Graph (correo)
# ---------------------------------------------------------------------
def get_graph_token() -> str | None:
    if not (GRAPH_TENANT_ID and GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET):
        print("[GRAPH] Faltan variables de entorno para autenticación.")
        return None

    authority = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}"
    app_conf = ConfidentialClientApplication(
        client_id=GRAPH_CLIENT_ID,
        client_credential=GRAPH_CLIENT_SECRET,
        authority=authority,
    )

    result = app_conf.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" in result:
        return result["access_token"]
    else:
        print(
            "[GRAPH] Error al obtener token:",
            result.get("error"),
            result.get("error_description"),
        )
        return None


def leer_correos_graph(max_mails: int = 50):
    mails: list[dict] = []

    token = get_graph_token()
    if not token:
        return mails

    headers = {"Authorization": f"Bearer {token}"}
    base_url = "https://graph.microsoft.com/v1.0"
    user = GRAPH_USER or "me"

    # Buscar carpeta "Seguros" dentro de Inbox
    resp = requests.get(
        f"{base_url}/users/{user}/mailFolders/inbox/childFolders",
        headers=headers,
        params={"$top": 200},
        timeout=15,
    )
    if resp.status_code != 200:
        print("[GRAPH] Error al listar carpetas:", resp.status_code, resp.text)
        return mails

    folders = resp.json().get("value", [])
    folder_id = None
    for f in folders:
        if f.get("displayName") == GRAPH_FOLDER_DISPLAY_NAME:
            folder_id = f.get("id")
            break

    if folder_id is None:
        print(f"[GRAPH] Carpeta '{GRAPH_FOLDER_DISPLAY_NAME}' no encontrada en Inbox.")
        return mails

    # Leer correos de esa carpeta
    resp = requests.get(
        f"{base_url}/users/{user}/mailFolders/{folder_id}/messages",
        headers=headers,
        params={
            "$top": max_mails,
            "$select": "subject,from,receivedDateTime",
            "$orderby": "receivedDateTime desc",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        print("[GRAPH] Error al leer mensajes:", resp.status_code, resp.text)
        return mails

    for item in resp.json().get("value", []):
        remitente = (
            item.get("from", {})
            .get("emailAddress", {})
            .get("address", "")
        )
        asunto = item.get("subject", "")
        fecha = item.get("receivedDateTime", "")
        mails.append(
            {
                "fecha": fecha,
                "remitente": remitente,
                "asunto": asunto,
            }
        )

    return mails

def cargar_clasificacion_siniestros() -> dict[str, str]:
    if not RUTA_CLASIF.exists():
        return {}
    try:
        with RUTA_CLASIF.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        print("[SINIESTROS] Error al leer clasificacion:", e)
        return {}


def guardar_clasificacion_siniestros(mapa: dict[str, str]) -> None:
    try:
        with RUTA_CLASIF.open("w", encoding="utf-8") as f:
            json.dump(mapa, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[SINIESTROS] Error al guardar clasificacion:", e)


CLASIF_SINIESTROS_MAIL = cargar_clasificacion_siniestros()


def leer_correos_bancos(max_mails: int = 50) -> list[dict]:
    """Lee correos desde la subcarpeta 'Bancos' de la Bandeja de entrada."""
    mails: list[dict] = []

    token = get_graph_token()
    if not token:
        return mails

    headers = {"Authorization": f"Bearer {token}"}
    baseurl = "https://graph.microsoft.com/v1.0"
    user = GRAPH_USER or "me"

    # Buscar subcarpeta Bancos dentro de Inbox
    resp = requests.get(
        f"{baseurl}/users/{user}/mailFolders/inbox/childFolders",
        headers=headers,
        params={"$top": 200},
        timeout=15,
    )
    if resp.status_code != 200:
        print("[GRAPH] Error al listar carpetas Bancos:", resp.status_code, resp.text)
        return mails

    folders = resp.json().get("value", [])
    print("DEBUG bancos folders:", [f.get("displayName") for f in folders])

    folder_id = None
    for f in folders:
        if f.get("displayName") == GRAPH_BANKS_FOLDER_DISPLAY_NAME:
            folder_id = f.get("id")
            break

    if folder_id is None:
        print(f"[GRAPH] Carpeta '{GRAPH_BANKS_FOLDER_DISPLAY_NAME}' no encontrada en Inbox.")
        return mails

    # Leer mensajes de esa carpeta
    resp = requests.get(
        f"{baseurl}/users/{user}/mailFolders/{folder_id}/messages",
        headers=headers,
        params={
            "$top": max_mails,
            "$select": "subject,from,receivedDateTime",
            "$orderby": "receivedDateTime desc",
        },
        timeout=15,
    )
    print("DEBUG bancos messages status:", resp.status_code)
    print("DEBUG bancos messages body:", resp.text[:500])

    if resp.status_code != 200:
        print("[GRAPH] Error al leer mensajes Bancos:", resp.status_code, resp.text)
        return mails

    items = resp.json().get("value", [])
    print("DEBUG bancos cantidad mails:", len(items))

    for item in items:
        remitente = (
            item.get("from", {})
            .get("emailAddress", {})
            .get("address", "")
        )
        asunto = item.get("subject", "")
        fecha = item.get("receivedDateTime", "")
        mail_id = item.get("id", "")
        mails.append(
            {
                "id": mail_id,
                "fecha": fecha,
                "remitente": remitente,
                "asunto": asunto,
            }
        )

    return mails



def detectar_numero_siniestro(asunto: str) -> str | None:
    if not asunto:
        return None
    m = PATRON_SINIESTRO.search(asunto)
    if m:
        return m.group(1)
    return None

def cargar_clasificacion_siniestros() -> dict[str, str]:
    if not RUTA_CLASIF.exists():
        return {}
    try:
        with RUTA_CLASIF.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # asegurarse de que todo es str -> str
        return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        print("[SINIESTROS] Error al leer clasificacion:", e)
        return {}


def guardar_clasificacion_siniestros(mapa: dict[str, str]) -> None:
    try:
        with RUTA_CLASIF.open("w", encoding="utf-8") as f:
            json.dump(mapa, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[SINIESTROS] Error al guardar clasificacion:", e)
        
CLASIF_SINIESTROS_MAIL = cargar_clasificacion_siniestros()

def cargar_clasificacion_bancos() -> dict[str, list[dict]]:
    if not RUTA_CLASIF_BANCOS.exists():
        return {}
    try:
        with RUTA_CLASIF_BANCOS.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # aseguramos tipos
        return {str(k): list(v) for k, v in data.items()}
    except Exception as e:
        print("[BANCOS] Error al leer clasificacion bancos:", e)
        return {}


def guardar_clasificacion_bancos(mapa: dict[str, list[dict]]) -> None:
    try:
        with RUTA_CLASIF_BANCOS.open("w", encoding="utf-8") as f:
            json.dump(mapa, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[BANCOS] Error al guardar clasificacion bancos:", e)

# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
        },
    )

@app.get("/polizas", response_class=HTMLResponse)
async def pagina_polizas(request: Request):
    try:
        polizas = get_sharepoint_folder_tree(POLIZAS_FOLDER_PATH)
        mensaje = "" if polizas else "No se encontraron archivos PDF en la carpeta de SharePoint configurada."
    except Exception as e:
        polizas = []
        mensaje = f"Error al leer pólizas desde SharePoint: {e}"

    return templates.TemplateResponse(
        "polizas.html",
        {
            "request": request,
            "polizas": polizas,                     # árbol desde SharePoint
            "ruta_base": f"SharePoint: {POLIZAS_FOLDER_PATH}",
            "mensaje": mensaje,
        },
    )



@app.get("/siniestros", response_class=HTMLResponse)
async def pagina_siniestros(request: Request):
    mails_base = leer_correos_graph(max_mails=200)
    modo_demo = False
    if not mails_base:
        modo_demo = True
        mails_base = [
            {
                "id": "demo1",
                "fecha": "2025-12-16T10:15:00",
                "remitente": "cliente1@ejemplo.cl",
                "asunto": "[ALERTA] Nuevo siniestro N°123",
            },
            # ...
        ]

    nuevos: list[dict] = []
    historicos: list[dict] = []

    ahora = datetime.utcnow()
    for idx, m in enumerate(mails_base):
        fecha_str = m.get("fecha", "")
        try:
            fecha_dt = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
        except Exception:
            fecha_dt = None

        diff_dias = None
        if fecha_dt is not None:
            diff_dias = (ahora - fecha_dt.replace(tzinfo=None)).days

        mail_id = m.get("id") or f"mail{idx}"
        remitente = m.get("remitente", "")
        asunto = m.get("asunto", "")

        if fecha_dt:
            clave = f'{fecha_dt.strftime("%Y-%m-%d %H:%M")} - {asunto} ({remitente})'
        else:
            clave = f"{fecha_str} - {asunto} ({remitente})"

        numero_guardado = CLASIF_SINIESTROS_MAIL.get(clave)


        registro = {
            "id": mail_id,
            "fecha": fecha_str,
            "fecha_mostrar": fecha_dt.strftime("%Y-%m-%d %H:%M") if fecha_dt else fecha_str,
            "remitente": m.get("remitente", ""),
            "asunto": m.get("asunto", ""),
            "n_siniestro": numero_guardado,  # puede ser None
        }

        if diff_dias is not None and diff_dias <= 3:
            nuevos.append(registro)
        else:
            historicos.append(registro)

    agrupado: dict[str, list[dict]] = {}
    for m in nuevos + historicos:
        numero = m.get("n_siniestro")
        if not numero:
            continue
        agrupado.setdefault(numero, []).append(m)

    return templates.TemplateResponse(
        "siniestros.html",
        {
            "request": request,
            "nuevos": nuevos,
            "historicos": historicos,
            "modo_demo": modo_demo,
            "agrupado": agrupado,
        },
    )


@app.post("/siniestros")
async def clasificar_siniestros(request: Request):
    form = await request.form()
    origen = form.get("origen", "")

    global CLASIF_SINIESTROS_MAIL

    # Nuevos
    if origen == "nuevos":
        idx = 0
        while f"id_nuevo_{idx}" in form:
            numero = form.get(f"siniestro_nuevo_{idx}", "").strip()
            fecha = form.get(f"fecha_nuevo_{idx}", "").strip()
            remitente = form.get(f"remitente_nuevo_{idx}", "").strip()
            asunto = form.get(f"asunto_nuevo_{idx}", "").strip()

            clave = f"{fecha} - {asunto} ({remitente})"

            if numero:
                CLASIF_SINIESTROS_MAIL[clave] = numero
            else:
                CLASIF_SINIESTROS_MAIL.pop(clave, None)

            idx += 1

    # Históricos
    elif origen == "historicos":
        idx = 0
        while f"id_historico_{idx}" in form:
            numero = form.get(f"siniestro_historico_{idx}", "").strip()
            fecha = form.get(f"fecha_historico_{idx}", "").strip()
            remitente = form.get(f"remitente_historico_{idx}", "").strip()
            asunto = form.get(f"asunto_historico_{idx}", "").strip()

            clave = f"{fecha} - {asunto} ({remitente})"

            if numero:
                CLASIF_SINIESTROS_MAIL[clave] = numero
            else:
                CLASIF_SINIESTROS_MAIL.pop(clave, None)

            idx += 1

    guardar_clasificacion_siniestros(CLASIF_SINIESTROS_MAIL)
    return RedirectResponse(url="/siniestros", status_code=303)


@app.get("/siniestros/clasificar", response_class=HTMLResponse)
async def mostrar_clasificacion(request: Request):
    mails_base = leer_correos_graph(max_mails=50)
    modo_demo = False
    if not mails_base:
        modo_demo = True
        mails_base = [
            {
                "fecha": "2025-12-16T10:15:00",
                "remitente": "cliente1@ejemplo.cl",
                "asunto": "[ALERTA] Nuevo siniestro N°123",
            },
            {
                "fecha": "2025-12-16T11:20:00",
                "remitente": "cliente2@ejemplo.cl",
                "asunto": "Consulta cobertura siniestro N°456",
            },
            {
                "fecha": "2025-12-16T12:05:00",
                "remitente": "aseguradora@ejemplo.cl",
                "asunto": "Actualización estado siniestro N°123",
            },
        ]

    mails = []
    for idx, m in enumerate(mails_base):
        asunto = m.get("asunto", "")
        n_sin = detectar_numero_siniestro(asunto)
        mail_id = f"mail{idx}"

        if mail_id in CORREOS_CLASIFICADOS:
            continue

        mails.append(
            {
                "id": mail_id,
                "fecha": m.get("fecha", ""),
                "remitente": m.get("remitente", ""),
                "asunto": asunto,
                "n_siniestro_detectado": n_sin,
            }
        )

    return templates.TemplateResponse(
        "clasificar_siniestros.html",
        {
            "request": request,
            "mails": mails,
            "modo_demo": modo_demo,
            "agrupado": CLASIFICACION_SINIESTROS,
        },
    )


@app.post("/siniestros/clasificar", response_class=HTMLResponse)
async def guardar_clasificacion(request: Request):
    form = await request.form()
    CLASIFICACION_SINIESTROS.clear()
    indices = set()

    for key in form.keys():
        if key.startswith("siniestro_"):
            idx = key.split("_", 1)[1]
            indices.add(idx)

    for idx in indices:
        numero = str(form.get(f"siniestro_{idx}", "")).strip()
        mail_id = form.get(f"id_{idx}")
        asunto = form.get(f"asunto_{idx}", "").strip()
        if not numero or not mail_id:
            continue

        CORREOS_CLASIFICADOS.add(mail_id)

        descripcion = asunto or f"Correo {mail_id}"
        CLASIFICACION_SINIESTROS.setdefault(numero, []).append(
            {"fecha": "", "remitente": "", "asunto": descripcion}
        )

    return RedirectResponse(
        url="/siniestros/clasificar",
        status_code=303,
    )

from datetime import datetime

@app.get("/bancos", response_class=HTMLResponse)
async def pagina_bancos(request: Request):
    anio_actual = datetime.now().year

    try:
        polizas_data = get_sharepoint_folder_tree(POLIZAS_FOLDER_PATH)
        mensaje = ""
    except Exception as e:
        polizas_data = []
        mensaje = f"Error al cargar pólizas desde SharePoint: {e}"

    # Armar estructura compatible con bancos.html
    carpetas_filtradas: dict[str, list[dict]] = {}
    for carpeta_info in polizas_data:
        carpeta = carpeta_info["carpeta"]
        filtrados = []
        for a in carpeta_info["archivos"]:
            fecha_dt = datetime.strptime(a["fecha"], "%Y-%m-%d %H:%M")
            if fecha_dt.year != anio_actual:
                continue
            filtrados.append(
                {
                    "nombre": a["nombre"],
                    "mtime": a["fecha"],
                    "mtime_date": fecha_dt.date(),
                    "rel_path": a["nombre"],   # usa nombre como clave única
                }
            )
        if filtrados:
            carpetas_filtradas[carpeta] = filtrados

    mails_bancos = leer_correos_bancos(max_mails=50)
    subcarpetas_polizas = [c["carpeta"] for c in polizas_data]

    return templates.TemplateResponse(
        "bancos.html",
        {
            "request": request,
            "carpetas": carpetas_filtradas,
            "polizas_benef_banco": POLIZAS_BENEF_BANCO,
            "mails_bancos": mails_bancos,
            "subcarpetas_polizas": subcarpetas_polizas,
            "correos_bancos_clasificados": CORREOS_BANCOS_CLASIFICADOS,
            "mensaje": mensaje,
        },
    )


@app.post("/bancos", response_class=HTMLResponse)
async def clasificar_bancos(
    request: Request,
    seleccion: Optional[list[str]] = Form(default=None),
    mail_id: Optional[list[str]] = Form(default=None),
    mail_carpeta: Optional[list[str]] = Form(default=None),
):
    global POLIZAS_BENEF_BANCO, CORREOS_BANCOS_CLASIFICADOS

    # Normalizar listas
    seleccion = seleccion or []
    mail_id = mail_id or []
    mail_carpeta = mail_carpeta or []

    # 1) Actualizar set de PDFs beneficiario banco
    # OJO: aquí 'seleccion' debe traer el MISMO valor que uses como clave
    # en GET /bancos (por ejemplo, el nombre de archivo)
    POLIZAS_BENEF_BANCO = set(seleccion)

    # 2) Limpiar clasificación anterior de correos y reconstruir
    CORREOS_BANCOS_CLASIFICADOS = {}

    # Cachear mails una sola vez
    mails_bancos = leer_correos_bancos(max_mails=50)

    for mid, carpeta_destino in zip(mail_id, mail_carpeta):
        carpeta_destino = (carpeta_destino or "").strip()
        if not carpeta_destino:
            continue

        info = next((m for m in mails_bancos if m.get("id") == mid), None)
        if not info:
            continue

        CORREOS_BANCOS_CLASIFICADOS.setdefault(carpeta_destino, []).append(info)

    # 3) Volver a armar listado de PDFs filtrados (solo los marcados) desde SharePoint
    anio_actual = datetime.now().year
    try:
        polizas_data = get_sharepoint_folder_tree(POLIZAS_FOLDER_PATH)
    except Exception:
        polizas_data = []

    carpetas_filtradas: dict[str, list[dict]] = {}
    for carpeta_info in polizas_data:
        carpeta = carpeta_info["carpeta"]
        filtrados = []
        for a in carpeta_info["archivos"]:
            # 'fecha' viene como "YYYY-mm-dd HH:MM"
            fecha_dt = datetime.strptime(a["fecha"], "%Y-%m-%d %H:%M")
            if fecha_dt.year != anio_actual:
                continue

            nombre = a["nombre"]
            if "poliza" not in nombre.lower() and "póliza" not in nombre.lower():
                continue

            # aquí usamos nombre como "rel_path" lógico
            if nombre not in POLIZAS_BENEF_BANCO:
                continue

            filtrados.append(
                {
                    "nombre": nombre,
                    "mtime": a["fecha"],
                    "mtime_date": fecha_dt.date(),
                    "rel_path": nombre,
                }
            )
        if filtrados:
            carpetas_filtradas[carpeta] = filtrados

    subcarpetas_polizas = [c["carpeta"] for c in polizas_data]
       
     # Persistir clasificación de bancos
    guardar_clasificacion_bancos(CORREOS_BANCOS_CLASIFICADOS)

    return templates.TemplateResponse(
        "bancos.html",
        {
            "request": request,
            "carpetas": carpetas_filtradas,
            "polizas_benef_banco": POLIZAS_BENEF_BANCO,
            "mails_bancos": [],  # tras clasificar, ya no se muestran como nuevos
            "subcarpetas_polizas": subcarpetas_polizas,
            "correos_bancos_clasificados": CORREOS_BANCOS_CLASIFICADOS,
        },
    )

  
