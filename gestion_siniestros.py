import json
from pathlib import Path

RUTA_CLASIF = Path("clasificacion_siniestros.json")


def cargar():
    if not RUTA_CLASIF.exists():
        return {}
    with RUTA_CLASIF.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {str(k): str(v) for k, v in data.items()}


def guardar(mapa: dict[str, str]) -> None:
    with RUTA_CLASIF.open("w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=2)


def main():
    datos = cargar()
    print(f"Hay {len(datos)} correos clasificados.")
    print("Opciones:")
    print("1) Listar todo")
    print("2) Agregar/actualizar uno")
    print("3) Borrar uno")
    op = input("Opción: ").strip()

    if op == "1":
        for mid, nro in datos.items():
            print(mid, "=>", nro)

    elif op == "2":
        mid = input("ID mail: ").strip()
        nro = input("N° siniestro: ").strip()
        if mid and nro:
            datos[mid] = nro
            guardar(datos)
            print("Guardado.")

    elif op == "3":
        mid = input("ID mail a borrar: ").strip()
        if mid in datos:
            datos.pop(mid)
            guardar(datos)
            print("Borrado.")


if __name__ == "__main__":
    main()
