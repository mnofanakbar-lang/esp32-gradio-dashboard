
import os
import json
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

def data_kosong(pesan):
    kosong = pd.DataFrame(columns=KOLOM)
    return pesan, None, None, None, kosong, kosong, kosong, kosong


def ambil_data():
    key = os.getenv("AIO_KEY", "").strip()

    if not key:
        return data_kosong(
            "⚠️ AIO_KEY belum diatur di Render."
        )

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
            f"⚠️ Gagal membaca Adafruit IO: {type(e).__name__}"
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
                "Suhu (°C)": paket.get("temperature_c"),
                "Kelembapan (%)": paket.get("humidity_pct"),
                "Interval (ms)": paket.get("interval_ms"),
                "Seq": paket.get("seq"),
                "Status": paket.get("status", "-")
            })

        except (ValueError, TypeError, KeyError):
            continue

    if not rows:
        return data_kosong(
            "⚠️ Belum ditemukan paket JSON yang valid."
        )

    # API mengembalikan data terbaru lebih dulu
    df = pd.DataFrame(rows[::-1], columns=KOLOM)

    for kolom in [
        "Suhu (°C)",
        "Kelembapan (%)",
        "Interval (ms)"
    ]:
        df[kolom] = pd.to_numeric(
            df[kolom], errors="coerce"
        )

    terbaru = df.iloc[-1]

    suhu = terbaru["Suhu (°C)"]
    lembap = terbaru["Kelembapan (%)"]
    interval = terbaru["Interval (ms)"]

    return (
        f"✅ Data terakhir: {terbaru['Waktu']} WIB | "
        f"Status sensor: {terbaru['Status']}",
        None if pd.isna(suhu) else float(suhu),
        None if pd.isna(lembap) else float(lembap),
        None if pd.isna(interval) else float(interval),
        df,
        df,
        df,
        df.iloc[::-1].reset_index(drop=True)
    )


with gr.Blocks(
    title="ESP32 MQTT Monitoring Dashboard"
) as demo:

    gr.Markdown(
        "# ESP32 MQTT Monitoring Dashboard\n"
        "Monitoring DHT11 melalui Adafruit IO Cloud\n\n"
        "**ESP32 → MQTT Cloud → Gradio**"
    )

    status = gr.Markdown("Mengambil data dari cloud...")

    with gr.Row():
        suhu = gr.Number(
            label="Suhu (°C)",
            interactive=False
        )
        lembap = gr.Number(
            label="Kelembapan (%)",
            interactive=False
        )
        interval = gr.Number(
            label="Interval Aktual (ms)",
            interactive=False
        )

    grafik_suhu = gr.LinePlot(
        x="Waktu",
        y="Suhu (°C)",
        title="Grafik Suhu DHT11"
    )

    grafik_lembap = gr.LinePlot(
        x="Waktu",
        y="Kelembapan (%)",
        title="Grafik Kelembapan DHT11"
    )

    grafik_interval = gr.LinePlot(
        x="Waktu",
        y="Interval (ms)",
        title="Grafik Interval Pengiriman MQTT"
    )

    gr.Markdown("### Riwayat Paket MQTT")

    tabel = gr.Dataframe(
        headers=KOLOM,
        interactive=False,
        label="Paket terbaru di atas"
    )

    tombol = gr.Button("Refresh Sekarang")
    timer = gr.Timer(15)

    hasil = [
        status,
        suhu,
        lembap,
        interval,
        grafik_suhu,
        grafik_lembap,
        grafik_interval,
        tabel
    ]

    demo.load(
        ambil_data,
        outputs=hasil,
        show_progress="hidden"
    )

    tombol.click(
        ambil_data,
        outputs=hasil
    )

    timer.tick(
        ambil_data,
        outputs=hasil,
        show_progress="hidden"
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860"))
    )
