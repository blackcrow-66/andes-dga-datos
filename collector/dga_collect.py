"""Recolector de estaciones DGA (DGASAT) para Andes Weather.

Consulta el grafico de DGASAT (trae ~1 mes de mediciones cada 2-30 min en una sola peticion),
agrega las mediciones nuevas a CSV mensuales y publica data/latest.json con las ultimas 48 h.

Uso:
    python collector/dga_collect.py [carpeta_data]

Las horas de la DGA vienen en hora local de Chile (America/Santiago) sin zona horaria.
Nunca desactivar la verificacion TLS: el certificado de snia.mop.gob.cl es valido.
"""
import csv
import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = "https://snia.mop.gob.cl/dgasat/pages/dgasat_param/"
TZ = ZoneInfo("America/Santiago")
RAIN = "Pptación Acum. (mm)"

# id, codigo DGA, parametro (id DGA, nombre), tipo de serie
SERIES = [
    ("antupiren_lluvia", "05730013-2", "3", RAIN, "rain"),
    ("quebrada_macul_lluvia", "05730039-6", "3", RAIN, "rain"),
    ("quebrada_ramon_nivel", "05730008-6", "1", "Nivel de Agua (m)", "level"),
    ("quebrada_ramon_caudal", "05730008-6", "12", "Caudal (m3/seg)", "flow"),
]
STATIONS = {
    "05730013-2": {"nombre": "Antupirén"},
    "05730039-6": {"nombre": "Quebrada de Macul"},
    "05730008-6": {"nombre": "Quebrada de Ramón en recinto EMOS"},
}
LATEST_HOURS = 48
TIMEOUT_S = 600
RETRIES = 3


def fetch_graph(end: dt.date) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "andes-weather-dga-collector/1.0")]
    codes = list(dict.fromkeys(s[1] for s in SERIES))
    codes += ["-1"] * (3 - len(codes))
    opener.open(BASE + "dgasat_param.jsp?param=1", timeout=TIMEOUT_S).read()
    form = {"accion": "refresca", "param": "1", "EsDL1": "0", "EsDL2": "0", "EsDL3": "0", "UserID": "nobody"}
    form.update({f"estacion{i + 1}": c for i, c in enumerate(codes)})
    opener.open(BASE + "dgasat_param_1.jsp", urllib.parse.urlencode(form).encode(), timeout=TIMEOUT_S).read()
    day = end.strftime("%d/%m/%Y")
    data = [(f"estacion{i + 1}", c) for i, c in enumerate(codes)]
    data += [("parametros", f"{code}_{pid}_{name}") for _, code, pid, name, _ in SERIES]
    data += [("accion", "refresca"), ("param", ""), ("tipo", "ANO"), ("fechaFinGrafico", day), ("hora_fin", "0"),
             ("tiporep", "I"), ("period", "1d"), ("fechaInicioTabla", day), ("fechaFinTabla", day),
             ("UserID", "nobody"), ("EsDL1", "0"), ("EsDL2", "0"), ("EsDL3", "0")]
    body = urllib.parse.urlencode(data, encoding="latin-1").encode()
    return opener.open(BASE + "dgasat_param_graficos.jsp", body, timeout=TIMEOUT_S).read().decode("latin-1")


POINT = re.compile(
    r"valor=([-\d.]+);\s*fechacelda=new Date\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\);"
    r"\s*chartData\d+\.push\(\{\s*date: fechacelda,\s*COD(\d{8})_(\d+): valor"
)


def parse(html: str) -> dict:
    """{id de serie: {datetime local: valor}}. La clave JS es COD<codigo sin digito verificador>_<grafico><parametro>."""
    out = {s[0]: {} for s in SERIES}
    for v, y, mo, d, h, mi, s, code, suffix in POINT.findall(html):
        when = dt.datetime(int(y), int(mo) + 1, int(d), int(h), int(mi), int(s))
        for sid, scode, pid, _, _ in SERIES:
            # El primer digito del sufijo es el numero de grafico; el resto, el id del parametro.
            if scode.split("-")[0] == code and suffix[1:] == pid:
                out[sid][when] = float(v)
    return out


def merge_csv(folder: str, sid: str, points: dict) -> int:
    """Agrega a data/raw/<sid>/<AAAA-MM>.csv las mediciones nuevas; devuelve cuantas."""
    added = 0
    by_month = {}
    for when, value in points.items():
        by_month.setdefault(when.strftime("%Y-%m"), {})[when] = value
    for month, values in by_month.items():
        path = os.path.join(folder, "raw", sid, f"{month}.csv")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing = {}
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    existing[dt.datetime.fromisoformat(row["hora_local"])] = float(row["valor"])
        new = {k: v for k, v in values.items() if k not in existing}
        if not new:
            continue
        added += len(new)
        existing.update(new)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["hora_local", "valor"])
            for k in sorted(existing):
                w.writerow([k.isoformat(timespec="minutes"), f"{existing[k]:g}"])
    return added


def hourly_rain(points: dict) -> dict:
    """Lluvia por hora (mm) desde el contador acumulado: clave = fin de la hora (como Open-Meteo)."""
    pts = sorted(points.items())
    hours = {}
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        end = (t1 + dt.timedelta(minutes=59, seconds=59)).replace(minute=0, second=0)
        dv = v1 - v0
        # Reinicios del contador, huecos largos y saltos imposibles no cuentan como lluvia.
        if dv < 0 or (t1 - t0) > dt.timedelta(hours=2) or dv > 30:
            dv = 0.0
        hours[end] = hours.get(end, 0.0) + dv
    return hours


def iso(when: dt.datetime) -> str:
    return when.replace(tzinfo=TZ).isoformat(timespec="minutes")


def latest(series: dict, now: dt.datetime) -> dict:
    since = now - dt.timedelta(hours=LATEST_HOURS)
    result = {"generado": iso(now), "fuente": "DGA DGASAT (snia.mop.gob.cl)", "estaciones": []}
    for sid, code, _, _, kind in SERIES:
        points = series.get(sid) or {}
        entry = {"id": sid, "codigo": code, **STATIONS[code], "tipo": kind}
        if not points:
            entry["sin_datos"] = True
            result["estaciones"].append(entry)
            continue
        last_time = max(points)
        entry["ultima_medicion"] = iso(last_time)
        if kind == "rain":
            hours = hourly_rain(points)
            entry["lluvia_por_hora_mm"] = [
                {"hora": iso(h), "mm": round(mm, 1)} for h, mm in sorted(hours.items()) if h > since
            ]

            def rain_since(start):
                before = [v for t, v in points.items() if t <= start]
                if not before:
                    return None
                after = sorted((t, v) for t, v in points.items() if t >= start)
                total, prev = 0.0, max(before)
                for _, v in after:
                    if v >= prev:
                        total += v - prev
                    prev = v
                return round(total, 1)

            entry["lluvia_ultima_hora_mm"] = rain_since(last_time - dt.timedelta(hours=1))
            entry["lluvia_24h_mm"] = rain_since(last_time - dt.timedelta(hours=24))
        else:
            entry["valor"] = points[last_time]
            entry["unidad"] = "m" if kind == "level" else "m3/s"
            # Una lectura cada 10 min basta para seguir una crecida y mantiene liviano el archivo.
            buckets = {}
            for t, v in sorted(points.items()):
                if t > since:
                    buckets.setdefault(t.replace(minute=t.minute - t.minute % 10, second=0), (t, v))
            entry["serie"] = [{"hora": iso(t), "valor": v} for t, v in buckets.values()]
        result["estaciones"].append(entry)
    return result


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else "data"
    today = dt.datetime.now(TZ).date()
    html = None
    for attempt in range(1, RETRIES + 1):
        try:
            started = time.time()
            html = fetch_graph(today)
            print(f"DGA respondio en {time.time() - started:.0f} s ({len(html) // 1024} KB)")
            break
        except Exception as e:  # noqa: BLE001 - se reintenta cualquier falla de red
            print(f"intento {attempt} fallo: {e}")
            if attempt < RETRIES:
                time.sleep(30 * attempt)
    if html is None:
        return 1
    series = parse(html)
    total = sum(len(v) for v in series.values())
    if total == 0:
        print("La respuesta no trae mediciones: la pagina de la DGA puede haber cambiado.")
        return 2
    for sid, points in series.items():
        added = merge_csv(folder, sid, points)
        last = max(points).isoformat(timespec="minutes") if points else "-"
        print(f"{sid}: {len(points)} mediciones, {added} nuevas, ultima {last}")
    os.makedirs(folder, exist_ok=True)
    now = dt.datetime.now(TZ).replace(tzinfo=None, second=0, microsecond=0)
    with open(os.path.join(folder, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest(series, now), f, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
