#!/usr/bin/env python3
"""
CODiS 逐時觀測資料爬蟲 — 臺北站 (466920)，2025-01-01 ~ 2025-12-31

API（從 https://codis.cwa.gov.tw/StationData 前端觀察而得）:
    POST https://codis.cwa.gov.tw/api/station   (application/x-www-form-urlencoded)
    type=report_date, stn_type=cwb, stn_ID=466920,
    start=YYYY-MM-DDT00:00:00, end=YYYY-MM-DDT23:59:59, more=
    回傳 JSON: data[0].dts = 當日 24 筆逐時資料

用法:
    python crawl_codis.py                 # 抓取 + 合併（已抓過的日期自動跳過）
    python crawl_codis.py --merge-only    # 只用 raw/ 重新產生 CSV
    python crawl_codis.py --start 2025-03-01 --end 2025-03-31
"""
import argparse
import json
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
RAW_DIR = BASE / "raw"
LOG_DIR = BASE / "logs"
OUT_DIR = BASE / "output"

API_URL = "https://codis.cwa.gov.tw/api/station"
STATION_ID = "466920"
STATION_TYPE = "cwb"
MAX_RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal research crawler)",
    "Referer": "https://codis.cwa.gov.tw/StationData",
    "X-Requested-With": "XMLHttpRequest",
}

# API 巢狀欄位 -> 輸出英文欄名
FIELD_MAP = [
    ("StationPressure", "Instantaneous", "station_pressure"),
    ("AirTemperature", "Instantaneous", "temperature"),
    ("DewPointTemperature", "Instantaneous", "dew_point"),
    ("RelativeHumidity", "Instantaneous", "relative_humidity"),
    ("WindSpeed", "Mean", "wind_speed"),
    ("WindDirection", "Mean", "wind_direction"),
    ("Precipitation", "Accumulation", "precipitation"),
    ("PrecipitationDuration", "Total", "precipitation_hours"),
]
OUT_COLS = ["datetime", "obs_hour"] + [c for _, _, c in FIELD_MAP] + ["precip_trace"]

TRACE_CODE = -9.8  # 雨跡 T


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("codis")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = logging.FileHandler(LOG_DIR / "crawl.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


class BlockedError(Exception):
    """偵測到 CAPTCHA / 存取限制，需停止。"""


def fetch_day(session, d, log):
    ds = d.isoformat()
    payload = {
        "type": "report_date",
        "stn_type": STATION_TYPE,
        "stn_ID": STATION_ID,
        "start": f"{ds}T00:00:00",
        "end": f"{ds}T23:59:59",
        "more": "",
    }
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.post(API_URL, data=payload, headers=HEADERS, timeout=30)
            if r.status_code in (401, 403, 429):
                raise BlockedError(f"HTTP {r.status_code}")
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "json" not in ctype:
                low = r.text[:2000].lower()
                if "captcha" in low or "recaptcha" in low:
                    raise BlockedError("CAPTCHA detected")
                raise ValueError(f"non-JSON response ({ctype})")
            j = r.json()
            if j.get("code") != 200 or not j.get("data"):
                raise ValueError(f"unexpected payload code={j.get('code')} msg={j.get('message')}")
            n = len(j["data"][0].get("dts", []))
            return j, n
        except BlockedError:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt  # 2s, 4s
                log.warning(f"{ds} RETRY {attempt}/{MAX_RETRIES - 1} after error: {e}; wait {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"failed after {MAX_RETRIES} attempts: {last_err}")


def crawl(start, end, log):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    failed = []
    d = start
    while d <= end:
        ds = d.isoformat()
        fp = RAW_DIR / f"{ds}.json"
        if fp.exists() and fp.stat().st_size > 0:
            log.info(f"{ds} SKIP (exists)")
            d += timedelta(days=1)
            continue
        try:
            j, n = fetch_day(session, d, log)
            tmp = fp.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
            tmp.replace(fp)
            lvl = log.info if n == 24 else log.warning
            lvl(f"{ds} OK ({n} rows)")
        except BlockedError as e:
            log.error(f"{ds} BLOCKED: {e} — 停止爬取（不嘗試繞過）")
            failed.append(ds)
            break
        except Exception as e:  # noqa: BLE001
            log.error(f"{ds} FAIL: {e}")
            failed.append(ds)
        time.sleep(random.uniform(1.0, 2.0))
        d += timedelta(days=1)
    return failed


def clean_value(v, col):
    """回傳 (數值或 None, 是否雨跡)。"""
    if v is None or v == "":
        return None, False
    try:
        x = float(v)
    except (TypeError, ValueError):
        s = str(v).strip().upper()
        if s == "T" and col == "precipitation":
            return 0.0, True
        return None, False  # --, /, X, & ...
    if col in ("precipitation", "precipitation_hours") and abs(x - TRACE_CODE) < 1e-9:
        return (0.0, True) if col == "precipitation" else (0.0, False)
    if col == "wind_direction" and abs(x - 999.9) < 1e-9:  # V 風向不定
        return None, False
    if col in ("precipitation", "precipitation_hours") and -10.0 < x < 0:  # -9.5 X 故障 等
        return None, False
    if x < -90:                   # -99.5, -999.x 等缺測/故障代碼（同前端 SpecialVal）
        return None, False
    return x, False


def parse_raw_file(fp):
    j = json.loads(fp.read_text(encoding="utf-8"))
    day = date.fromisoformat(fp.stem)
    rows = []
    for rec in j["data"][0].get("dts", []):
        dt = datetime.fromisoformat(rec["DataTime"])
        # CODiS 觀測時 1~24；第 24 時 API 以「當日 23:59:00」表示
        if dt.hour == 23 and dt.minute == 59:
            obs_hour = 24
        elif dt.date() == day + timedelta(days=1) and dt.hour == 0:
            obs_hour = 24
        else:
            obs_hour = dt.hour
        stamp = datetime.combine(day, datetime.min.time()) + timedelta(hours=obs_hour)
        row = {"datetime": stamp, "obs_hour": obs_hour, "precip_trace": False}
        for key, sub, col in FIELD_MAP:
            raw_v = (rec.get(key) or {}).get(sub)
            val, trace = clean_value(raw_v, col)
            row[col] = val
            if trace:
                row["precip_trace"] = True
        rows.append(row)
    return rows


def merge(log, year=2025):
    import pandas as pd

    files = sorted(RAW_DIR.glob(f"{year}-*.json"))
    rows = []
    for fp in files:
        try:
            rows.extend(parse_raw_file(fp))
        except Exception as e:  # noqa: BLE001
            log.error(f"{fp.name} PARSE FAIL: {e}")
    df = pd.DataFrame(rows, columns=OUT_COLS)
    for _, _, c in FIELD_MAP:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["obs_hour"] = df["obs_hour"].astype(int)
    df["precip_trace"] = df["precip_trace"].astype(bool)
    df = df.sort_values("datetime").drop_duplicates().drop_duplicates(subset=["datetime"], keep="first")
    df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"taipei_{STATION_ID}_hourly_{year}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    log.info(f"MERGE done: {len(df)} rows from {len(files)} day files -> {out}")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--merge-only", action="store_true")
    args = ap.parse_args()
    log = setup_logging()
    if not args.merge_only:
        s, e = date.fromisoformat(args.start), date.fromisoformat(args.end)
        log.info(f"=== crawl start {s} ~ {e} station {STATION_ID} ===")
        failed = crawl(s, e, log)
        if failed:
            log.error(f"FAILED DATES ({len(failed)}): {', '.join(failed)}")
        else:
            log.info("all dates fetched")
    merge(log)


if __name__ == "__main__":
    main()
