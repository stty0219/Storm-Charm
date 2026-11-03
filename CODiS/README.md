# CODiS 臺北站（466920）2025 逐時觀測資料

用來校正 Storm Charm 降雨判斷模型的地面真值資料。資料僅供個人研究與專案分析使用，請遵守中央氣象署 CODiS 的使用條款。

## 資料來源

- 系統：中央氣象署 CODiS 氣候資料服務系統 <https://codis.cwa.gov.tw/StationData>
- 測站：臺北（466920，局屬站 `stn_type=cwb`），臺北市中正區公園路 64 號，海拔 6.3 m
- 期間：2025-01-01 01:00 ～ 2026-01-01 00:00（觀測時 1～24，共 365 天 × 24 小時）
- API（從前端 Network 觀察而得，不需要 token）：

```
POST https://codis.cwa.gov.tw/api/station
Content-Type: application/x-www-form-urlencoded

type=report_date&stn_type=cwb&stn_ID=466920
&start=2025-01-01T00:00:00&end=2025-01-01T23:59:59&more=
```

回傳 JSON 的 `data[0].dts` 是當日 24 筆資料，每筆的值放在巢狀 key 裡，例如 `AirTemperature.Instantaneous`。

## 目錄結構

```
CODiS/
├── crawl_codis.py                          爬蟲加合併腳本
├── README.md
├── raw/YYYY-MM-DD.json                     每日原始 API 回應（斷點續跑用）
├── logs/crawl.log                          成功、失敗、重試記錄
└── output/taipei_466920_hourly_2025.csv    最終檔案（UTF-8 with BOM）
```

## 欄位定義

| 欄位 | 說明 | 單位 | API 來源 |
|---|---|---|---|
| datetime | 觀測時間戳 `YYYY-MM-DD HH:MM:SS`（當地時間 UTC+8） | – | 日期 + obs_hour |
| obs_hour | CODiS 原始觀測時（1～24） | 時 | `DataTime` |
| station_pressure | 測站氣壓 | hPa | `StationPressure.Instantaneous` |
| temperature | 氣溫 | °C | `AirTemperature.Instantaneous` |
| dew_point | 露點溫度 | °C | `DewPointTemperature.Instantaneous` |
| relative_humidity | 相對溼度 | % | `RelativeHumidity.Instantaneous` |
| wind_speed | 風速（平均風） | m/s | `WindSpeed.Mean` |
| wind_direction | 風向（平均風，0～360，北=360/0） | 度 | `WindDirection.Mean` |
| precipitation | 前一小時降水量 | mm | `Precipitation.Accumulation` |
| precipitation_hours | 前一小時內降水時數 | hr | `PrecipitationDuration.Total` |
| precip_trace | 該小時降水量為雨跡（T） | bool | 由 precipitation 判定 |

## 觀測時 24 時的換算

CODiS 的觀測時是 1～24，代表「該小時結束時」的觀測或累計。第 24 時就是隔天 00:00。API 實際用「當日 `23:59:00`」來表示第 24 時，例如 `2025-01-01T23:59:00`。本資料的處理方式：

- `obs_hour` 保留原始值 24。
- `datetime` = 當日 00:00 + obs_hour 小時。所以 2025-01-01 的第 24 時寫成 `2025-01-02 00:00:00`，2025-12-31 的第 24 時寫成 `2026-01-01 00:00:00`。
- 因此全年第一筆是 `2025-01-01 01:00:00`，最後一筆是 `2026-01-01 00:00:00`。

## 特殊值處理

API 不回傳 `T`、`X`、`--` 這類文字，而是回傳數字代碼。下表的對應依據是前端 `Common/global.js` 的 `SpecialVal()`：

| API 值 | 網頁顯示 | 意義 | 處理方式 |
|---|---|---|---|
| `-9.8`（降水量） | T | 雨跡（微量） | 轉成 `0.0`，並把 `precip_trace=True` |
| `-9.5`（降水量、降水時數） | X | 儀器故障 | NaN |
| `-999.9`（降水時數） | / | 不明 | NaN |
| `null` 或空字串 | -- | 缺測 | NaN |
| 小於 -90 的代碼（如 -99.5、-999.x） | X、/、& | 故障、不明、累積於後 | NaN |
| `999.9`（風向） | V | 風向不定 | NaN |

其餘欄位一律轉成數值型態（float），並依 `datetime` 排序、去除重複列。

## 如何重跑

```bash
pip install requests pandas
cd CODiS
python crawl_codis.py                 # 抓取加合併；raw/ 已有的日期會自動跳過
python crawl_codis.py --merge-only    # 不連網，只從 raw/ 重建 CSV
python crawl_codis.py --start 2025-06-01 --end 2025-06-30   # 只補抓某段日期
```

- 每次請求間隔 1～2 秒（隨機），逐日依序抓取，不平行。
- 失敗會自動重試，共嘗試 3 次（間隔 2 s、4 s）。仍失敗就記錄為 FAIL，並在結尾列出失敗日期。重跑一次就只會補抓那幾天。
- 若回應是 HTTP 401/403/429 或驗證碼頁面，腳本會立即停止，不嘗試繞過。
- 要強制重抓某天，刪除 `raw/YYYY-MM-DD.json` 後重跑即可。
