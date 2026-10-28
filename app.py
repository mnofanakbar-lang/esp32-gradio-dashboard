
import os
import json
import html
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import gradio as gr


# =====================================================
# 1. KONFIGURASI ADAFRUIT IO
# =====================================================

USERNAME = "naufan123"
FEED = "esp32"

URL = (
    f"https://io.adafruit.com/api/v2/"
    f"{USERNAME}/feeds/{FEED}/data"
)

# AIO_KEY diambil dari Environment Variables SnapDeploy.
# Jangan menuliskan AIO Key rahasia di GitHub.

WIB = ZoneInfo("Asia/Jakarta")

REFRESH_SECONDS = 15
BATAS_DATA_LAMA = 60

KOLOM = [
    "Waktu",
    "Suhu (°C)",
    "Kelembapan (%)",
    "Interval (ms)",
    "Seq",
    "Status"
]


# =====================================================
# 2. DESAIN DASHBOARD
# =====================================================

CSS = """
.gradio-container {
    max-width: 1180px !important;
    margin: 0 auto !important;
    padding: 20px 16px 40px !important;
    background: #F2F5F7 !important;
}

footer {
    display: none !important;
}

/* HEADER */

.header {
    background: #12303B;
    color: #fff;
    border-radius: 16px;
    padding: 22px 26px;
    margin-bottom: 14px;
}

.header h1 {
    margin: 0 0 6px;
    font-size: 1.55rem;
    font-weight: 700;
    color: #fff;
}

.header p {
    margin: 0;
    color: #B9CED6;
    font-size: .95rem;
}

.alur {
    margin-top: 12px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.alur span {
    background: rgba(255,255,255,.10);
    border-radius: 999px;
    padding: 4px 12px;
    font-size: .8rem;
    color: #E3EEF2;
}

/* STATUS */

.status {
    border-radius: 12px;
    padding: 11px 16px;
    font-size: .92rem;
    margin-bottom: 14px;
    border: 1px solid;
    line-height: 1.6;
}

.status.ok {
    background: #E8F6EF;
    border-color: #A8DCC1;
    color: #14573A;
}

.status.warn {
    background: #FFF4E0;
    border-color: #F2CF8B;
    color: #7A4B00;
}

/* KARTU PARAMETER */

.kartu-wrap {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 14px;
    margin-bottom: 14px;
}

.kartu {
    background: #fff;
    border: 1px solid #DCE4E9;
    border-left: 5px solid var(--warna);
    border-radius: 14px;
    padding: 16px 20px;
}

.kartu .label {
    color: #5B6B75;
    font-size: .85rem;
    margin-bottom: 4px;
}

.kartu .nilai {
    color: #12303B;
    font-size: 2.1rem;
    font-weight: 700;
    line-height: 1.15;
}

.kartu .satuan {
    font-size: 1rem;
    font-weight: 500;
    color: #5B6B75;
    margin-left: 4px;
}

/* PANEL GRAFIK DAN TABEL */

.panel {
    background: #fff !important;
    border: 1px solid #DCE4E9 !important;
    border-radius: 14px !important;
    padding: 6px !important;
}

.panel text,
.panel svg text,
.panel .vega-embed text {
    fill: #12303B !important;
}

.panel .role-title text,
.panel .role-title-text text {
    fill: #12303B !important;
    font-weight: 700 !important;
    font-size: 15px !important;
}

.panel .role-axis-title text {
    fill: #12303B !important;
    font-weight: 600 !important;
    font-size: 12px !important;
}

.panel .role-axis-label text {
    fill: #2E4652 !important;
    font-size: 11px !important;
}

.panel .role-axis-grid line,
.panel .role-axis-grid path {
    stroke: #DCE4E9 !important;
}

.panel .role-axis-domain,
.panel .role-axis-tick line {
    stroke: #5B6B75 !important;
}

.judul-bagian {
    font-size: 1.05rem;
    font-weight: 600;
    color: #12303B;
    margin: 18px 2px 8px;
}

/* TOMBOL REFRESH */

.btn-refresh {
    background: #0F766E !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    max-width: 220px;
}

.btn-refresh:hover {
    background: #0B5C56 !important;
}

.btn-refresh:focus-visible {
    outline: 3px solid #7CD4CB !important;
}

/* TAMPILAN HP */

@media (max-width: 760px) {

    .kartu-wrap {
        grid-template-columns: 1fr;
    }

    .header {
        padding: 18px;
    }

    .header h1 {
        font-size: 1.3rem;
    }

    .kartu .nilai {
        font-size: 1.8rem;
    }

    .gradio-container {
        padding: 12px 10px 30px !important;
    }
}
"""


TEMA = gr.themes.Soft(
    primary_hue="teal",
    neutral_hue="slate",
    font=[
        gr.themes.GoogleFont("Plus Jakarta Sans"),
        "sans-serif"
    ]
)


HEADER_HTML = """
<div class="header">
    <h1>ESP32 MQTT Monitoring Dashboard</h1>

    <p>
        Pemantauan sensor DHT11 melalui Adafruit IO Cloud
    </p>

    <div class="alur">
        <span>ESP32 DevKit V1</span>
        <span>MQTT Cloud</span>
        <span>Adafruit IO</span>
        <span>Python</span>
        <span>Gradio</span>
    </div>
</div>
"""


# =====================================================
# 3. KOMPONEN STATUS DAN KARTU
# =====================================================

def status_html(pesan, ok=True):
    kelas = "ok" if ok else "warn"

    return (
        f'<div class="status {kelas}">'
        f'{html.escape(str(pesan))}'
        f'</div>'
    )


def kartu_html(suhu, lembap, interval):

    def fmt(value, desimal):
        if value is None or pd.isna(value):
            return "–"

        return f"{float(value):.{desimal}f}"

    item = [
        (
            "Suhu",
            fmt(suhu, 1),
            "°C",
            "#E8590C"
        ),
        (
            "Kelembapan",
            fmt(lembap, 1),
            "%",
            "#1C7ED6"
        ),
        (
            "Interval aktual",
            fmt(interval, 0),
            "ms",
            "#7048E8"
        )
    ]

    isi = ""

    for label, nilai, satuan, warna in item:

        isi += (
            f'<div class="kartu" style="--warna:{warna}">'
            f'<div class="label">{label}</div>'
            f'<div class="nilai">'
            f'{nilai}'
            f'<span class="satuan">{satuan}</span>'
            f'</div>'
            f'</div>'
        )

    return f'<div class="kartu-wrap">{isi}</div>'


# =====================================================
# 4. DATA KOSONG
# =====================================================

def data_kosong(pesan):

    df_plot = pd.DataFrame({
        "Waktu": pd.to_datetime([]),
        "Suhu (°C)": pd.Series(dtype=float),
        "Kelembapan (%)": pd.Series(dtype=float),
        "Interval (ms)": pd.Series(dtype=float)
    })

    df_tabel = pd.DataFrame(columns=KOLOM)

    return (
        status_html(pesan, ok=False),
        kartu_html(None, None, None),
        df_plot,
        df_plot,
        df_plot,
        df_tabel
    )


# =====================================================
# 5. MENGAMBIL DATA CLOUD
# =====================================================

def ambil_data():

    waktu_sekarang = datetime.now(WIB)

    waktu_cek = waktu_sekarang.strftime(
        "%d/%m/%Y %H:%M:%S"
    )

    print(
        f"[GRADIO AUTO REFRESH] {waktu_cek} WIB",
        flush=True
    )

    key = os.getenv("AIO_KEY", "").strip()

    if not key:

        return data_kosong(
            "AIO_KEY belum diatur pada "
            "Environment Variables SnapDeploy."
        )

    try:

        response = requests.get(
            URL,
            headers={
                "X-AIO-Key": key,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            },
            params={
                "limit": 60
            },
            timeout=12
        )

        response.raise_for_status()

        records = response.json()

    except requests.RequestException as error:

        print(
            f"[CLOUD ERROR] {type(error).__name__}",
            flush=True
        )

        return data_kosong(
            "Gagal mengambil data Adafruit IO. "
            f"Diperiksa: {waktu_cek} WIB"
        )

    except ValueError:

        return data_kosong(
            "Respons Adafruit IO bukan JSON valid."
        )

    # =================================================
    # 6. MEMPROSES DATA JSON
    # =================================================

    rows = []

    for item in records:

        try:

            paket = json.loads(
                item.get("value", "")
            )

            if not isinstance(paket, dict):
                continue

            # Gunakan waktu penerimaan di Cloud
            # agar indikator data lama lebih akurat.
            created_at = pd.to_datetime(
                item.get("created_at"),
                utc=True,
                errors="coerce"
            )

            if pd.isna(created_at):
                continue

            waktu_wib = created_at.tz_convert(
                "Asia/Jakarta"
            )

            rows.append({

                "Waktu": waktu_wib.strftime(
                    "%d/%m %H:%M:%S"
                ),

                "_dt": waktu_wib.tz_localize(None),

                "_created": created_at,

                "Suhu (°C)": paket.get(
                    "temperature_c"
                ),

                "Kelembapan (%)": paket.get(
                    "humidity_pct"
                ),

                "Interval (ms)": paket.get(
                    "interval_ms"
                ),

                "Seq": paket.get(
                    "seq", "-"
                ),

                "Status": paket.get(
                    "status", "-"
                )
            })

        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError
        ):
            continue

    if not rows:

        return data_kosong(
            "Belum ditemukan data JSON ESP32 "
            "yang valid di Adafruit IO."
        )

    # Data diurutkan berdasarkan waktu Cloud
    semua = pd.DataFrame(rows)

    semua = semua.sort_values(
        "_created"
    ).reset_index(drop=True)

    for kolom in [
        "Suhu (°C)",
        "Kelembapan (%)",
        "Interval (ms)"
    ]:

        semua[kolom] = pd.to_numeric(
            semua[kolom],
            errors="coerce"
        )

    # Tabel riwayat
    df_tabel = semua[KOLOM].iloc[::-1]

    df_tabel = df_tabel.reset_index(
        drop=True
    )

    # Grafik menggunakan tipe datetime
    df_plot = semua[
        [
            "_dt",
            "Suhu (°C)",
            "Kelembapan (%)",
            "Interval (ms)"
        ]
    ].copy()

    df_plot = df_plot.rename(
        columns={
            "_dt": "Waktu"
        }
    )

    # =================================================
    # 7. STATUS DATA TERBARU
    # =================================================

    terbaru = semua.iloc[-1]

    waktu_terakhir = terbaru["_created"]

    umur_data = (
        datetime.now(timezone.utc)
        - waktu_terakhir.to_pydatetime()
    ).total_seconds()

    umur_data = max(0, int(umur_data))

    status_sensor = str(
        terbaru["Status"]
    )

    data_segar = (
        umur_data <= BATAS_DATA_LAMA
        and status_sensor == "OK"
    )

    def angka(value):

        if pd.isna(value):
            return None

        return float(value)

    if data_segar:

        pesan = (
            f"● DATA TERBARU | "
            f"Data terakhir: {terbaru['Waktu']} WIB | "
            f"Sensor: {status_sensor} | "
            f"{len(semua)} paket | "
            f"Diperiksa: {waktu_cek} WIB | "
            f"Auto-refresh: {REFRESH_SECONDS} detik"
        )

    else:

        pesan = (
            f"● PERIKSA DATA / SENSOR | "
            f"Data terakhir: {terbaru['Waktu']} WIB | "
            f"Umur data: {umur_data} detik | "
            f"Sensor: {status_sensor} | "
            f"Diperiksa: {waktu_cek} WIB"
        )

    print(
        f"[CLOUD] {len(semua)} paket | "
        f"seq={terbaru['Seq']} | "
        f"umur={umur_data}s | "
        f"sensor={status_sensor}",
        flush=True
    )

    # =================================================
    # 8. KIRIM DATA KE TAMPILAN GRADIO
    # =================================================

    return (

        status_html(
            pesan,
            ok=data_segar
        ),

        kartu_html(
            angka(terbaru["Suhu (°C)"]),
            angka(terbaru["Kelembapan (%)"]),
            angka(terbaru["Interval (ms)"])
        ),

        df_plot,
        df_plot,
        df_plot,
        df_tabel
    )


# =====================================================
# 9. MEMBUAT DASHBOARD GRADIO
# =====================================================

with gr.Blocks(
    title="ESP32 MQTT Monitoring Dashboard",
    theme=TEMA,
    css=CSS
) as demo:

    # Header
    gr.HTML(HEADER_HTML)

    # Status Cloud
    status = gr.HTML(
        status_html(
            "Menghubungkan ke Adafruit IO Cloud...",
            ok=True
        )
    )

    # Kartu suhu, kelembapan dan interval
    kartu = gr.HTML(
        kartu_html(None, None, None)
    )

    # =================================================
    # GRAFIK SUHU DAN KELEMBAPAN
    # =================================================

    with gr.Row(equal_height=True):

        grafik_suhu = gr.LinePlot(
            x="Waktu",
            y="Suhu (°C)",
            title="Suhu DHT11",
            x_title="Waktu",
            y_title="°C",
            x_label_angle=-45,
            height=280,
            elem_classes="panel"
        )

        grafik_lembap = gr.LinePlot(
            x="Waktu",
            y="Kelembapan (%)",
            title="Kelembapan DHT11",
            x_title="Waktu",
            y_title="%",
            x_label_angle=-45,
            height=280,
            elem_classes="panel"
        )

    # =================================================
    # GRAFIK INTERVAL MQTT
    # =================================================

    grafik_interval = gr.LinePlot(
        x="Waktu",
        y="Interval (ms)",
        title="Interval Pengiriman MQTT",
        x_title="Waktu",
        y_title="ms",
        x_label_angle=-45,
        height=240,
        elem_classes="panel"
    )

    # =================================================
    # TABEL RIWAYAT MQTT
    # =================================================

    gr.HTML(
        '<div class="judul-bagian">'
        'Riwayat paket MQTT (terbaru di atas)'
        '</div>'
    )

    tabel = gr.Dataframe(
        headers=KOLOM,
        interactive=False,
        wrap=True,
        max_height=340,
        show_label=False,
        elem_classes="panel"
    )

    # =================================================
    # TOMBOL REFRESH MANUAL
    # =================================================

    tombol = gr.Button(
        "↻ Refresh sekarang",
        elem_classes="btn-refresh"
    )

    # =================================================
    # TIMER AUTO-REFRESH
    # =================================================

    timer = gr.Timer(
        value=float(REFRESH_SECONDS),
        active=True
    )

    hasil = [
        status,
        kartu,
        grafik_suhu,
        grafik_lembap,
        grafik_interval,
        tabel
    ]

    # Membaca data pertama kali ketika web dibuka
    demo.load(
        fn=ambil_data,
        outputs=hasil,
        queue=False,
        show_progress="hidden"
    )

    # Refresh secara manual
    tombol.click(
        fn=ambil_data,
        outputs=hasil,
        queue=False,
        show_progress="hidden"
    )

    # Refresh otomatis setiap 15 detik
    timer.tick(
        fn=ambil_data,
        outputs=hasil,
        queue=False,
        trigger_mode="always_last",
        show_progress="hidden"
    )


# =====================================================
# 10. MENJALANKAN SERVER DI SNAPDEPLOY
# =====================================================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "3000")
    )

    print(
        f"[SERVER] Gradio berjalan di port {port}",
        flush=True
    )

    demo.launch(
        server_name="0.0.0.0",
        server_port=port
    )
