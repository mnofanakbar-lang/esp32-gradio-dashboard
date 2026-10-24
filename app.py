import os
import json
import html
import requests
import pandas as pd
import gradio as gr

# KONFIGURASI ADAFRUIT IO
USERNAME = "naufan123"
FEED = "esp32"

# AIO Key diambil dari pengaturan Render,
# bukan ditulis di GitHub.
URL = (
    f"https://io.adafruit.com/api/v2/"
    f"{USERNAME}/feeds/{FEED}/data"
)

KOLOM = [
    "Waktu", "Suhu (°C)", "Kelembapan (%)",
    "Interval (ms)", "Seq", "Status"
]

# ---------------------------------------------------------------
# TAMPILAN
# ---------------------------------------------------------------
CSS = """
.gradio-container {
    max-width: 1180px !important;
    margin: 0 auto !important;
    padding: 20px 16px 40px !important;
    background: #F2F5F7 !important;
}
footer { display: none !important; }

/* Header */
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
.header p { margin: 0; color: #B9CED6; font-size: .95rem; }
.alur { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px; }
.alur span {
    background: rgba(255,255,255,.10);
    border-radius: 999px;
    padding: 4px 12px;
    font-size: .8rem;
    color: #E3EEF2;
}

/* Status */
.status {
    border-radius: 12px;
    padding: 11px 16px;
    font-size: .92rem;
    margin-bottom: 14px;
    border: 1px solid;
}
.status.ok   { background: #E8F6EF; border-color: #A8DCC1; color: #14573A; }
.status.warn { background: #FFF4E0; border-color: #F2CF8B; color: #7A4B00; }

/* Kartu metrik */
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
.kartu .label { color: #5B6B75; font-size: .85rem; margin-bottom: 4px; }
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

/* Panel grafik & tabel */
.panel {
    background: #fff !important;
    border: 1px solid #DCE4E9 !important;
    border-radius: 14px !important;
    padding: 6px !important;
}

/* Teks grafik: judul, label sumbu, angka tick, legend */
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

/* Garis grid & sumbu dibuat lebih halus supaya teks lebih menonjol */
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

/* Tombol */
.btn-refresh {
    background: #0F766E !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    max-width: 220px;
}
.btn-refresh:hover { background: #0B5C56 !important; }
.btn-refresh:focus-visible { outline: 3px solid #7CD4CB !important; }

/* Mobile */
@media (max-width: 760px) {
    .kartu-wrap { grid-template-columns: 1fr; }
    .header { padding: 18px; }
    .header h1 { font-size: 1.3rem; }
    .kartu .nilai { font-size: 1.8rem; }
}
"""

TEMA = gr.themes.Soft(
    primary_hue="teal",
    neutral_hue="slate",
    font=[gr.themes.GoogleFont("Plus Jakarta Sans"), "sans-serif"],
)

HEADER_HTML = """
<div class="header">
  <h1>ESP32 MQTT Monitoring Dashboard</h1>
  <p>Pemantauan sensor DHT11 lewat Adafruit IO Cloud</p>
  <div class="alur">
    <span>ESP32</span><span>MQTT Cloud</span><span>Gradio</span>
  </div>
</div>
"""


def status_html(pesan, ok):
    kelas = "ok" if ok else "warn"
    return f'<div class="status {kelas}">{html.escape(pesan)}</div>'


def kartu_html(suhu, lembap, interval):
    def fmt(v, desimal):
        return "–" if v is None else f"{v:.{desimal}f}"

    item = [
        ("Suhu", fmt(suhu, 1), "°C", "#E8590C"),
        ("Kelembapan", fmt(lembap, 1), "%", "#1C7ED6"),
        ("Interval aktual", fmt(interval, 0), "ms", "#7048E8"),
    ]
    isi = "".join(
        f'<div class="kartu" style="--warna:{w}">'
        f'<div class="label">{label}</div>'
        f'<div class="nilai">{nilai}<span class="satuan">{satuan}</span></div>'
        f'</div>'
        for label, nilai, satuan, w in item
    )
    return f'<div class="kartu-wrap">{isi}</div>'


# ---------------------------------------------------------------
# DATA
# ---------------------------------------------------------------
def data_kosong(pesan):
    kosong = pd.DataFrame(columns=KOLOM)
    return (
        status_html(pesan, ok=False),
        kartu_html(None, None, None),
        kosong, kosong, kosong, kosong
    )


def ambil_data():
    key = os.getenv("AIO_KEY", "").strip()

    if not key:
        return data_kosong("AIO_KEY belum diatur di Render.")

    try:
        response = requests.get(
            URL,
            headers={"X-AIO-Key": key},
            params={"limit": 60},
            timeout=15
        )
        response.raise_for_status()
        records = response.json()

    except requests.RequestException as e:
        return data_kosong(
            f"Gagal membaca Adafruit IO: {type(e).__name__}"
        )

    rows = []

    for item in records:
        try:
            paket = json.loads(item["value"])

            if not isinstance(paket, dict):
                continue

            waktu_asli = (
                paket.get("timestamp")
                or item.get("created_at")
            )

            waktu = pd.to_datetime(
                waktu_asli, utc=True
            ).tz_convert("Asia/Jakarta")

            rows.append({
                "Waktu": waktu.strftime("%d/%m %H:%M:%S"),
                "_dt": waktu.tz_localize(None),
                "Suhu (°C)": paket.get("temperature_c"),
                "Kelembapan (%)": paket.get("humidity_pct"),
                "Interval (ms)": paket.get("interval_ms"),
                "Seq": paket.get("seq"),
                "Status": paket.get("status", "-")
            })

        except (ValueError, TypeError, KeyError):
            continue

    if not rows:
        return data_kosong("Belum ditemukan paket JSON yang valid.")

    # API mengembalikan data terbaru lebih dulu
    semua = pd.DataFrame(rows[::-1])

    for kolom in ["Suhu (°C)", "Kelembapan (%)", "Interval (ms)"]:
        semua[kolom] = pd.to_numeric(semua[kolom], errors="coerce")

    # Tabel: waktu sebagai teks
    df = semua[KOLOM]

    # Grafik: waktu sebagai datetime asli, supaya label sumbu-X
    # otomatis dijarangkan dan tidak menumpuk
    df_plot = semua.drop(columns=["Waktu"]).rename(
        columns={"_dt": "Waktu"}
    )

    terbaru = df.iloc[-1]

    def angka(v):
        return None if pd.isna(v) else float(v)

    pesan = (
        f"Data terakhir: {terbaru['Waktu']} WIB  |  "
        f"Status sensor: {terbaru['Status']}  |  "
        f"{len(df)} paket dimuat"
    )

    return (
        status_html(pesan, ok=True),
        kartu_html(
            angka(terbaru["Suhu (°C)"]),
            angka(terbaru["Kelembapan (%)"]),
            angka(terbaru["Interval (ms)"]),
        ),
        df_plot,
        df_plot,
        df_plot,
        df.iloc[::-1].reset_index(drop=True)
    )


# ---------------------------------------------------------------
# UI
# ---------------------------------------------------------------
with gr.Blocks(
    title="ESP32 MQTT Monitoring Dashboard",
    theme=TEMA,
    css=CSS
) as demo:

    gr.HTML(HEADER_HTML)

    status = gr.HTML(status_html("Mengambil data dari cloud...", ok=True))
    kartu = gr.HTML(kartu_html(None, None, None))

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

    gr.HTML('<div class="judul-bagian">Riwayat paket MQTT (terbaru di atas)</div>')

    tabel = gr.Dataframe(
        headers=KOLOM,
        interactive=False,
        wrap=True,
        max_height=340,
        show_label=False,
        elem_classes="panel"
    )

    tombol = gr.Button("Refresh sekarang", elem_classes="btn-refresh")
    timer = gr.Timer(15)

    hasil = [
        status,
        kartu,
        grafik_suhu,
        grafik_lembap,
        grafik_interval,
        tabel
    ]

    demo.load(ambil_data, outputs=hasil, show_progress="hidden")
    tombol.click(ambil_data, outputs=hasil)
    timer.tick(ambil_data, outputs=hasil, show_progress="hidden")


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860"))
    )
