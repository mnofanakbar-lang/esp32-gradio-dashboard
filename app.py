
import os
import json
import html
import math
from statistics import NormalDist
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
    "Status",
    "RSSI (dBm)",
    "Jarak est. (m)"
]


# =====================================================
# 1b. MODEL PERENCANAAN INFRASTRUKTUR
#     Acuan: T.S. Rappaport, Wireless Communications:
#     Principles and Practice.
#     - Friis / free space        : Pers. (3.1), (3.5)
#     - EIRP                      : Pers. (3.4)
#     - Log-distance + shadowing  : Pers. (3.69), (3.93), Tabel 3.2
#     - Probabilitas cakupan (Q)  : Pers. (3.70), (3.71)
#     - Redaman material/dinding  : Tabel 3.3
#     - Kapasitas Shannon         : Pers. (5.36), (5.37)
#     Data radio: datasheet ESP32-WROOM-32E (Espressif),
#     Tabel TX power & RX sensitivity.
# =====================================================

C_CAHAYA = 299_792_458.0
FREK_HZ = 2.437e9          # Wi-Fi kanal 6 (2,4 GHz)
D0_M = 1.0                 # jarak referensi d0
BW_HZ = 20e6               # lebar kanal Wi-Fi HT20
NOISE_FIGURE_DB = 7.0      # asumsi noise figure penerima

# (nama, Pt ESP32 dBm, sensitivitas dBm, laju PHY Mbps, jenis)
SKEMA_WIFI = [
    ("802.11b 1 Mbps", 19.5, -97.0, 1.0, "dsss"),
    ("802.11b 11 Mbps", 19.5, -88.0, 11.0, "dsss"),
    ("802.11g 6 Mbps", 18.0, -93.0, 6.0, "ofdm"),
    ("802.11n MCS0 6,5 Mbps", 18.0, -92.0, 6.5, "ofdm"),
    ("802.11g 54 Mbps", 14.0, -75.0, 54.0, "ofdm"),
    ("802.11n MCS7 65 Mbps", 13.0, -72.0, 65.0, "ofdm"),
]

# Batas layanan cloud & titik akses (asumsi perencanaan)
BATAS_CLOUD = {
    "Adafruit IO Free (30 data/menit)": 30,
    "Adafruit IO+ (60 data/menit)": 60,
    "IO+ + 1 boost (70 data/menit)": 70,
}
BATAS_AP = {
    "Hotspot HP (maks. 10 klien)": 10,
    "Router Wi-Fi rumahan (±32 klien)": 32,
    "Access point kelas bisnis (±100 klien)": 100,
}

TOPIK_CLOUD = "naufan123/feeds/esp32"
OVH_MQTT = 2 + 2 + len(TOPIK_CLOUD)   # fixed header + panjang topik + topik
OVH_TLS = 5 + 8 + 16                  # header record + nonce + tag AES-GCM
OVH_TCPIP = 20 + 20                   # header TCP + IPv4
OVH_MAC = 24 + 8 + 4                  # MAC + LLC/SNAP + FCS
TCP_ACK_BYTE = OVH_TCPIP + OVH_MAC    # ACK TCP dari server


def lambda_m():
    return C_CAHAYA / FREK_HZ


def pl_d0_db():
    """Path loss ruang bebas pada d0, Pers. (3.5) dengan G=1."""
    return 20 * math.log10(4 * math.pi * D0_M / lambda_m())


def path_loss_db(d, n, n_dinding, loss_dinding):
    """Log-distance + redaman partisi, Pers. (3.69)/(3.93)."""
    d = max(d, D0_M)
    return (
        pl_d0_db()
        + 10 * n * math.log10(d / D0_M)
        + n_dinding * loss_dinding
    )


def margin_fading_db(sigma, keandalan_pct):
    """Margin shadowing log-normal agar Pr[Pr>γ] = p (Pers. 3.71)."""
    p = min(max(keandalan_pct / 100.0, 0.5), 0.9999)
    return sigma * NormalDist().inv_cdf(p)


def jarak_maks_m(eirp, g_rx, l_sis, sens, margin, n, n_dinding, loss_dinding):
    """Jarak terjauh d sehingga Pr(d) >= sensitivitas + margin."""
    pl_izin = eirp + g_rx - l_sis - (sens + margin)
    sisa = pl_izin - pl_d0_db() - n_dinding * loss_dinding
    return D0_M * 10 ** (sisa / (10 * n))


def hitung_coverage(pt_esp, g_esp, pt_ap, g_ap, l_sis, n, sigma,
                    n_dinding, loss_dinding, keandalan):
    margin = margin_fading_db(sigma, keandalan)
    baris = []
    for nama, pt_tabel, sens, phy, _ in SKEMA_WIFI:
        pt_up = min(pt_esp, pt_tabel)
        d_up = jarak_maks_m(pt_up + g_esp, g_ap, l_sis, sens, margin,
                            n, n_dinding, loss_dinding)
        d_dn = jarak_maks_m(pt_ap + g_ap, g_esp, l_sis, sens, margin,
                            n, n_dinding, loss_dinding)
        baris.append({
            "Skema Wi-Fi": nama,
            "Pt ESP32 (dBm)": round(pt_up, 1),
            "Sensitivitas (dBm)": sens,
            "Ambang + margin (dBm)": round(sens + margin, 1),
            "Jarak uplink (m)": round(d_up, 1),
            "Jarak downlink (m)": round(d_dn, 1),
            "Radius coverage (m)": round(min(d_up, d_dn), 1),
        })
    return pd.DataFrame(baris), margin


def estimasi_jarak_m(rssi, pt_ap, g_ap, g_esp, l_sis, n, n_dinding,
                     loss_dinding, offset_kal=0.0):
    """Kebalikan Pers. (3.69): jarak dari daya terima (RSSI) downlink.
    PL = Pt_AP + Gt + Gr − L − RSSI ;
    d = d0 · 10^[(PL − PL(d0) − offset − ΣLdinding) / (10·n)]
    offset_kal = koreksi PL(d0) hasil kalibrasi lapangan (dB)."""
    pl = pt_ap + g_ap + g_esp - l_sis - rssi
    sisa = pl - (pl_d0_db() + offset_kal) - n_dinding * loss_dinding
    return max(D0_M * 10 ** (sisa / (10 * n)), 0.1)


def phy_terbaik(pr_dbm, margin):
    """Laju PHY tertinggi yang andal pada daya terima pr_dbm
    (syarat: Pr >= sensitivitas + margin fading)."""
    cocok = [s for s in SKEMA_WIFI if pr_dbm >= s[2] + margin]
    if not cocok:
        return "di luar jangkauan andal", 0.0
    terbaik = max(cocok, key=lambda s: s[3])
    return terbaik[0], terbaik[3]


def noise_dbm():
    return -174 + 10 * math.log10(BW_HZ) + NOISE_FIGURE_DB


def shannon_mbps(snr_db):
    """Pers. (5.37): C = B log2(1 + S/N)."""
    return BW_HZ * math.log2(1 + 10 ** (snr_db / 10)) / 1e6


def ukuran_frame(payload_byte):
    """Ukuran frame Wi-Fi untuk satu publish MQTT/TLS (byte)."""
    return payload_byte + OVH_MQTT + OVH_TLS + OVH_TCPIP + OVH_MAC


def airtime_us(frame_byte, phy_mbps, jenis):
    """Durasi kanal untuk 1 frame data + ACK MAC (CSMA/CA, DCF)."""
    if jenis == "dsss":           # 802.11b, preamble panjang
        difs, slot, cw, pre, sifs, ack = 50, 20, 31, 192, 10, 304
    else:                          # 802.11g/n OFDM
        difs, slot, cw, pre, sifs, ack = 28, 9, 15, 20, 10, 44
    backoff = cw / 2 * slot
    data = frame_byte * 8 / phy_mbps
    return difs + backoff + pre + data + sifs + ack


def hitung_kapasitas(interval_s, payload_byte, nama_cloud, nama_ap,
                     nama_phy, utilisasi_maks=50.0):
    skema = next(s for s in SKEMA_WIFI if s[0] == nama_phy)
    _, _, sens, phy, jenis = skema

    frame = ukuran_frame(payload_byte)
    t_pub = airtime_us(frame, phy, jenis)
    t_ack = airtime_us(TCP_ACK_BYTE, phy, jenis)
    t_laporan_s = (t_pub + t_ack) / 1e6

    bit_node = (frame + TCP_ACK_BYTE) * 8 / interval_s
    data_per_menit = 60.0 / interval_s
    batas_cloud = BATAS_CLOUD[nama_cloud]
    batas_ap = BATAS_AP[nama_ap]

    n_cloud = math.floor(batas_cloud / data_per_menit + 1e-9)
    n_ap = batas_ap
    n_wifi = math.floor((utilisasi_maks / 100) * interval_s / t_laporan_s)

    # SNR terburuk: sinyal tepat di ambang sensitivitas
    snr_tepi = sens - noise_dbm()
    c_shannon = shannon_mbps(snr_tepi)
    n_shannon = math.floor(c_shannon * 1e6 * (utilisasi_maks / 100) / bit_node)

    batas = {
        "Kuota data cloud (Adafruit IO)": n_cloud,
        "Jumlah klien titik akses": n_ap,
        "Airtime kanal Wi-Fi (CSMA/CA)": n_wifi,
    }
    bottleneck = min(batas, key=batas.get)

    laporan_bulan = 30 * 86400 / interval_s
    mb_bulan = laporan_bulan * (frame + TCP_ACK_BYTE) / 1e6

    return {
        "frame": frame,
        "t_laporan_ms": t_laporan_s * 1000,
        "bit_node": bit_node,
        "data_per_menit": data_per_menit,
        "batas": batas,
        "bottleneck": bottleneck,
        "n_maks": batas[bottleneck],
        "snr_tepi": snr_tepi,
        "c_shannon": c_shannon,
        "n_shannon": n_shannon,
        "mb_bulan": mb_bulan,
        "batas_cloud": batas_cloud,
        "batas_ap": batas_ap,
        "t_laporan_s": t_laporan_s,
        "utilisasi_maks": utilisasi_maks,
    }


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

.kartu-wrap.empat {
    grid-template-columns: repeat(4, minmax(0, 1fr));
}

.kartu .ket {
    color: #5B6B75;
    font-size: .78rem;
    margin-top: 4px;
}

/* KOTAK PENJELASAN PERENCANAAN */

.info {
    background: #fff;
    border: 1px solid #DCE4E9;
    border-radius: 14px;
    padding: 14px 18px;
    color: #12303B;
    font-size: .9rem;
    line-height: 1.6;
    margin-bottom: 12px;
}

.info b {
    color: #0F766E;
}

.info .rumus {
    font-family: Consolas, monospace;
    background: #F2F5F7;
    color: #12303B !important;
    border-radius: 8px;
    padding: 6px 10px;
    margin: 6px 0;
    display: block;
    white-space: pre-wrap;
}

.info table.lb {
    width: 100%;
    border-collapse: collapse;
    margin-top: 8px;
    font-size: .85rem;
}

.info table.lb th,
.info table.lb td {
    border-bottom: 1px solid #DCE4E9;
    padding: 5px 8px;
    color: #12303B;
    text-align: left;
}

.info table.lb th {
    background: #E6F2F1;
}

.info table.lb td.a {
    text-align: right;
    font-family: Consolas, monospace;
    white-space: nowrap;
}

.info table.lb td.k {
    color: #5B6B75;
    font-size: .78rem;
}

.info.peringatan {
    background: #FFF4E0;
    border-color: #F2CF8B;
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

/* TOMBOL TAB: teks selalu jelas (tidak transparan) di tema terang/gelap */

button[role="tab"] {
    color: #12303B !important;
    opacity: 1 !important;
    font-weight: 600 !important;
    background: #FFFFFF !important;
    border: 1px solid #DCE4E9 !important;
    border-radius: 10px 10px 0 0 !important;
    margin-right: 4px !important;
}

button[role="tab"]:hover {
    color: #0F766E !important;
    background: #E6F2F1 !important;
}

button[role="tab"][aria-selected="true"],
button[role="tab"].selected {
    color: #FFFFFF !important;
    background: #0F766E !important;
    border-color: #0F766E !important;
}

/* TABEL RIWAYAT: judul kolom tidak dipecah per huruf */

.tabel-riwayat th,
.tabel-riwayat th *,
.tabel-riwayat td,
.tabel-riwayat td * {
    white-space: nowrap !important;
    word-break: keep-all !important;
    overflow-wrap: normal !important;
    writing-mode: horizontal-tb !important;
}

.tabel-riwayat th {
    font-size: .8rem !important;
    padding: 6px 8px !important;
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

    .kartu-wrap,
    .kartu-wrap.empat {
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
        Perencanaan infrastruktur Wi-Fi &ndash; MQTT &ndash; Cloud
        untuk monitoring sensor DHT11 melalui Adafruit IO
    </p>

    <div class="alur">
        <span>ESP32 DevKit V1</span>
        <span>MQTT Cloud</span>
        <span>Adafruit IO</span>
        <span>Python</span>
        <span>Gradio</span>
        <span>Live: Coverage · Kapasitas · Kualitas</span>
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


def kualitas_html(m=None):
    """Kartu kualitas layanan: throughput, delivery, jitter, delay."""

    m = m or {}

    def fmt(key, desimal):
        v = m.get(key)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "–"
        return f"{v:.{desimal}f}"

    item = [
        ("Throughput aplikasi", fmt("bps", 0), "bit/s",
         f"{fmt('pps', 3)} paket/s · rata-rata {fmt('byte', 0)} byte",
         "#0F766E"),
        ("Delivery ratio", fmt("delivery", 2), "%",
         f"hilang {fmt('hilang', 0)} dari {fmt('diharapkan', 0)} paket",
         "#2F9E44"),
        ("Jitter interval", fmt("jitter", 0), "ms",
         f"σ interval kirim ESP32 · rata-rata {fmt('ia', 0)} ms",
         "#E67700"),
        ("Delay ESP32 → cloud", fmt("delay", 1), "s",
         "satu arah, median (created_at − timestamp), resolusi 1 detik",
         "#C2255C"),
        ("Latency RTT ESP32 ⇄ cloud", fmt("rtt_c_med", 0), "ms",
         f"median · P95 {fmt('rtt_c_p95', 0)} ms (publish → echo Adafruit IO)",
         "#5F3DC4"),
        ("Latency RTT jalur lokal", fmt("rtt_l_med", 0), "ms",
         f"median · P95 {fmt('rtt_l_p95', 0)} ms (ACK logger laptop)",
         "#1971C2"),
        ("Daya pancar ESP32", fmt("tx", 1), "dBm",
         f"EIRP {fmt('eirp_esp', 1)} dBm (Pt + Gt) · kanal {fmt('ch', 0)}",
         "#D9480F"),
        ("Daya diterima (RSSI)", fmt("rssi", 0), "dBm",
         f"di ESP32 (downlink) · estimasi di hotspot {fmt('pr_ul', 1)} dBm "
         f"· SNR {fmt('snr', 1)} dB",
         "#495057"),
    ]

    isi = ""
    for label, nilai, satuan, ket, warna in item:
        isi += (
            f'<div class="kartu" style="--warna:{warna}">'
            f'<div class="label">{label}</div>'
            f'<div class="nilai">{nilai}'
            f'<span class="satuan">{satuan}</span></div>'
            f'<div class="ket">{html.escape(ket)}</div>'
            f'</div>'
        )

    return f'<div class="kartu-wrap empat">{isi}</div>'


def hitung_kualitas(semua):
    """Metrik kualitas dari paket yang tercatat di Adafruit IO."""

    m = {}
    t = semua["_created"]
    n = len(semua)
    durasi = (t.iloc[-1] - t.iloc[0]).total_seconds() if n > 1 else 0

    m["byte"] = float(semua["_bytes"].mean())

    if durasi > 0:
        m["pps"] = (n - 1) / durasi
        m["bps"] = semua["_bytes"].iloc[1:].sum() * 8 / durasi

    # Jitter dari interval_ms yang diukur ESP32 (resolusi ms);
    # created_at Adafruit hanya beresolusi 1 detik.
    ia = pd.to_numeric(semua["Interval (ms)"], errors="coerce").dropna()
    if len(ia) > 1:
        m["ia"] = float(ia.mean())
        m["jitter"] = float(ia.std())

    # Delivery ratio dihitung dari seq pada sesi (sid) terakhir
    sesi = semua[semua["_sid"] == semua["_sid"].iloc[-1]]
    seq = pd.to_numeric(sesi["Seq"], errors="coerce").dropna()
    if len(seq):
        diharapkan = int(seq.max() - seq.min() + 1)
        diterima = int(seq.nunique())
        m["diharapkan"] = float(diharapkan)
        m["hilang"] = float(diharapkan - diterima)
        m["delivery"] = 100.0 * diterima / diharapkan

    delay = (
        semua["_created"] - semua["_ts_dev"]
    ).dt.total_seconds().dropna()
    if len(delay):
        m["delay"] = float(delay.median())

    return m


def jarak_html(rssi=None, radius=None, sigma=7.0, n=3.0, jarak=None):
    """Kartu estimasi jarak ESP32 ke hotspot (live)."""

    if jarak is None or rssi is None or pd.isna(rssi):
        return (
            '<div class="kartu-wrap empat"><div class="kartu" '
            'style="--warna:#1098AD;grid-column:1/-1">'
            '<div class="label">Estimasi jarak ESP32 → hotspot</div>'
            '<div class="nilai">–<span class="satuan">m</span></div>'
            '<div class="ket">Menunggu field "rssi" dari firmware '
            'terbaru.</div></div></div>'
        )

    # Rentang ±1σ shadowing (≈68% keyakinan)
    faktor = 10 ** (sigma / (10 * n))
    lo, hi = jarak / faktor, jarak * faktor
    di_dalam = jarak <= radius
    sisa = radius - jarak

    item = [
        ("Estimasi jarak ESP32 → hotspot", f"{jarak:.1f}", "m",
         f"rentang 68%: {lo:.1f} – {hi:.1f} m (σ = {sigma:.0f} dB)",
         "#1098AD"),
        ("RSSI ESP32 (median 5 paket)", f"{rssi:.0f}", "dBm",
         "masukan link budget, bukan parameter kualitas", "#495057"),
        ("Radius coverage rencana", f"{radius:.1f}", "m",
         "802.11n MCS0, keandalan sesuai tab Coverage", "#7048E8"),
        ("Status coverage", "DALAM" if di_dalam else "LUAR", "",
         (f"sisa {sisa:.1f} m sebelum batas" if di_dalam
          else f"lewat {-sisa:.1f} m dari batas — dekatkan ke hotspot"),
         "#2F9E44" if di_dalam else "#E03131"),
    ]

    isi = ""
    for label, nilai, satuan, ket, warna in item:
        isi += (
            f'<div class="kartu" style="--warna:{warna}">'
            f'<div class="label">{label}</div>'
            f'<div class="nilai">{nilai}'
            f'<span class="satuan">{satuan}</span></div>'
            f'<div class="ket">{html.escape(ket)}</div></div>'
        )

    return f'<div class="kartu-wrap empat">{isi}</div>'


# =====================================================
# 3b. LINK BUDGET, TRAFIK/BANDWIDTH & LATENCY LIVE
# =====================================================

def _terakhir(seri):
    seri = pd.to_numeric(seri, errors="coerce").dropna()
    return float(seri.iloc[-1]) if len(seri) else None


def hitung_live(semua, rssi, radius, p):
    """Gabungkan data ESP32 terbaru dengan model link budget Rappaport."""

    L = {"radius": radius, "sigma": p["sigma"], "keandalan": p["keandalan"]}
    L["margin_perlu"] = margin_fading_db(p["sigma"], p["keandalan"])

    # Daya pancar ESP32 yang dilaporkan firmware (fallback: slider)
    tx = _terakhir(semua["_tx_dbm"])
    L["tx_dari_esp"] = tx is not None
    L["tx"] = tx if tx is not None else p["pt_esp"]
    L["ch"] = _terakhir(semua["_ch"])
    L["g_esp"], L["g_ap"], L["l_sis"] = p["g_esp"], p["g_ap"], p["l_sis"]
    L["pt_ap"] = p["pt_ap"]

    # Latency (RTT) dari firmware
    for kunci, kolom in (("rtt_c", "_rtt_cloud"), ("rtt_l", "_rtt_lokal")):
        s = pd.to_numeric(semua[kolom], errors="coerce").dropna()
        if len(s):
            L[f"{kunci}_med"] = float(s.median())
            L[f"{kunci}_p95"] = float(s.quantile(.95))
            L[f"{kunci}_n"] = int(len(s))

    # Trafik: bit per paket & laju bit (aplikasi dan di udara)
    byte_app = float(semua["_bytes"].mean())
    t = semua["_created"]
    durasi = (t.iloc[-1] - t.iloc[0]).total_seconds() if len(t) > 1 else 0
    pps = (len(t) - 1) / durasi if durasi > 0 else None
    frame = ukuran_frame(byte_app)
    # uplink publish + ACK TCP, ditambah echo feed (downlink) untuk RTT cloud
    bit_udara_paket = (frame + TCP_ACK_BYTE) * 8
    if L.get("rtt_c_n"):
        bit_udara_paket += (frame + TCP_ACK_BYTE) * 8
    L.update(byte_app=byte_app, bit_app=byte_app * 8, frame=frame,
             bit_udara=bit_udara_paket, pps=pps)
    if pps:
        L["bps_app"] = byte_app * 8 * pps
        L["bps_udara"] = bit_udara_paket * pps

    if rssi is None or pd.isna(rssi):
        return L

    # Link budget terukur: path loss dari RSSI downlink (resiprositas kanal)
    L["rssi"] = float(rssi)
    L["pl"] = p["pt_ap"] + p["g_ap"] + p["g_esp"] - p["l_sis"] - L["rssi"]
    L["eirp_esp"] = L["tx"] + p["g_esp"]
    L["eirp_ap"] = p["pt_ap"] + p["g_ap"]
    L["pr_ul"] = L["eirp_esp"] + p["g_ap"] - p["l_sis"] - L["pl"]
    L["d"] = estimasi_jarak_m(L["rssi"], p["pt_ap"], p["g_ap"], p["g_esp"],
                              p["l_sis"], p["n"], p["n_dinding"],
                              p["loss_dinding"], p["offset_kal"])
    L["sens"] = SKEMA_WIFI[3][2]
    L["pr_min"] = min(L["rssi"], L["pr_ul"])
    L["margin_ada"] = L["pr_min"] - L["sens"]
    L["snr"] = L["pr_min"] - noise_dbm()
    L["c_live"] = shannon_mbps(L["snr"])
    L["phy_nama"], L["phy"] = phy_terbaik(L["pr_min"], L["margin_perlu"])
    L["c_tepi"] = shannon_mbps(L["sens"] + L["margin_perlu"] - noise_dbm())
    if L.get("bps_udara"):
        L["headroom"] = L["c_live"] * 1e6 / L["bps_udara"]
        L["util_pct"] = 100 * L["bps_udara"] / (L["c_live"] * 1e6)
    L["param"] = p
    return L


def kartu_grid(item):
    """Deretan kartu angka: (label, nilai, satuan, keterangan, warna)."""
    isi = ""
    for label, nilai, satuan, ket, warna in item:
        isi += (
            f'<div class="kartu" style="--warna:{warna}">'
            f'<div class="label">{html.escape(label)}</div>'
            f'<div class="nilai">{nilai}'
            f'<span class="satuan">{satuan}</span></div>'
            f'<div class="ket">{html.escape(ket)}</div></div>'
        )
    return f'<div class="kartu-wrap empat">{isi}</div>'


def link_budget_live_html(L):
    if not L or "rssi" not in L:
        return ('<div class="info">Link budget live menunggu data '
                '<b>rssi</b> dari firmware ESP32 terbaru.</div>')
    lolos = L["margin_ada"] >= L["margin_perlu"]
    sumber_tx = "dilaporkan ESP32" if L["tx_dari_esp"] else "dari slider"
    sisa = L["margin_ada"] - L["margin_perlu"]
    ch = f"kanal {int(L['ch'])}" if L.get("ch") else "kanal –"
    return kartu_grid([
        ("Daya pancar ESP32 (Pt)", f"{L['tx']:.1f}", "dBm",
         f"{sumber_tx} · {ch}", "#D9480F"),
        ("EIRP ESP32 = Pt + Gt", f"{L['eirp_esp']:.1f}", "dBm",
         f"Gt {L['g_esp']:+.1f} dBi (Pers. 3.4)", "#E8590C"),
        ("Path loss terukur", f"{L['pl']:.1f}", "dB",
         f"dari RSSI → jarak ≈ {L['d']:.1f} m (Pers. 3.69)", "#7048E8"),
        ("Rugi sistem L", f"{L['l_sis']:.1f}", "dB",
         f"gain penerima hotspot {L['g_ap']:+.1f} dBi", "#868E96"),
        ("Daya diterima di hotspot", f"{L['pr_ul']:.1f}", "dBm",
         "uplink ESP32 → AP (estimasi resiprositas)", "#1C7ED6"),
        ("Daya diterima ESP32 (RSSI)", f"{L['rssi']:.0f}", "dBm",
         f"downlink AP → ESP32, hotspot Pt {L['pt_ap']:.0f} dBm", "#1098AD"),
        ("Margin tersedia", f"{L['margin_ada']:.1f}", "dB",
         f"Pr terlemah − sensitivitas MCS0 ({L['sens']:.0f} dBm) · "
         f"dibutuhkan {L['margin_perlu']:.1f} dB (Pers. 3.71)", "#0F766E"),
        ("Status link", "ANDAL" if lolos else "LEMAH", "",
         (f"sisa margin {sisa:.1f} dB" if lolos else
          f"kurang {-sisa:.1f} dB — dekatkan ke hotspot"),
         "#2F9E44" if lolos else "#E03131"),
    ])


def kurva_lb_waktu(semua, rssi_halus, p):
    """Riwayat daya terima uplink/downlink vs ambang andal."""
    kolom = ["Waktu", "Daya (dBm)", "Kurva"]
    if rssi_halus.dropna().empty:
        return pd.DataFrame(columns=kolom)
    tx = pd.to_numeric(semua["_tx_dbm"], errors="coerce").fillna(p["pt_esp"])
    pl = p["pt_ap"] + p["g_ap"] + p["g_esp"] - p["l_sis"] - rssi_halus
    pr_ul = tx + p["g_esp"] + p["g_ap"] - p["l_sis"] - pl
    ambang = SKEMA_WIFI[3][2] + margin_fading_db(p["sigma"], p["keandalan"])
    df = pd.concat([
        pd.DataFrame({"Waktu": semua["_dt"], "Daya (dBm)": rssi_halus,
                      "Kurva": "RSSI di ESP32 (downlink)"}),
        pd.DataFrame({"Waktu": semua["_dt"], "Daya (dBm)": pr_ul,
                      "Kurva": "Daya di hotspot (uplink, estimasi)"}),
        pd.DataFrame({"Waktu": semua["_dt"], "Daya (dBm)": ambang,
                      "Kurva": "Ambang andal (sensitivitas + margin)"}),
    ])
    return df.dropna()


def kurva_latency(semua):
    """Riwayat latency RTT cloud & lokal yang dilaporkan ESP32."""
    kolom = ["Waktu", "Latency (ms)", "Kurva"]
    bagian = []
    for k, nama in (("_rtt_cloud", "RTT ESP32 ⇄ Adafruit IO"),
                    ("_rtt_lokal", "RTT jalur lokal (logger)")):
        s = pd.to_numeric(semua[k], errors="coerce")
        bagian.append(pd.DataFrame({"Waktu": semua["_dt"],
                                    "Latency (ms)": s, "Kurva": nama}))
    df = pd.concat(bagian).dropna()
    return df if len(df) else pd.DataFrame(columns=kolom)


def kapasitas_live_html(L):
    if not L:
        return ('<div class="info">Trafik & bandwidth live menunggu data '
                'ESP32 dari Adafruit IO.</div>')

    def fmt(v, d=0, sat=""):
        return "–" if v is None else f"{v:,.{d}f}{sat}"

    item = [
        ("Ukuran data per paket", fmt(L.get("bit_app")), "bit",
         f"{fmt(L.get('byte_app'))} byte JSON · di udara "
         f"{fmt(L.get('bit_udara'))} bit (MQTT+TLS+TCP/IP+802.11"
         f"{' + echo' if L.get('rtt_c_n') else ''})", "#0F766E"),
        ("Trafik live", fmt(L.get("bps_udara")), "bit/s",
         f"aplikasi {fmt(L.get('bps_app'))} bit/s · "
         f"{fmt(L.get('pps'), 3)} paket/s", "#1C7ED6"),
        ("Kapasitas kanal di posisi ESP32", fmt(L.get("c_live"), 1), "Mbps",
         f"Shannon, SNR {fmt(L.get('snr'), 1)} dB · PHY andal: "
         f"{L.get('phy_nama', '–')}", "#7048E8"),
        ("Cadangan bandwidth", fmt(L.get("headroom")), "×",
         f"utilisasi {fmt(L.get('util_pct'), 4)}% · jarak ≈ "
         f"{fmt(L.get('d'), 1)} m dari radius {fmt(L.get('radius'), 1)} m · "
         f"kapasitas di tepi sel {fmt(L.get('c_tepi'), 1)} Mbps", "#E67700"),
    ]
    isi = ""
    for label, nilai, satuan, ket, warna in item:
        isi += (
            f'<div class="kartu" style="--warna:{warna}">'
            f'<div class="label">{label}</div>'
            f'<div class="nilai">{nilai}'
            f'<span class="satuan">{satuan}</span></div>'
            f'<div class="ket">{html.escape(ket)}</div></div>'
        )
    return f'<div class="kartu-wrap empat">{isi}</div>'


def kurva_kapasitas_jarak(L):
    """Kapasitas Shannon & laju PHY andal vs jarak, dengan posisi ESP32."""
    kolom = ["Jarak (m)", "Kapasitas (Mbps)", "Kurva"]
    if not L or "param" not in L:
        return pd.DataFrame(columns=kolom)
    p = L["param"]
    d_maks = max(L["radius"] * 1.5, L["d"] * 1.3, 10.0)
    data = []
    for i in range(121):
        d = D0_M + i * (d_maks - D0_M) / 120
        pl = path_loss_db(d, p["n"], p["n_dinding"], p["loss_dinding"]) \
            + p["offset_kal"]
        pr = p["pt_ap"] + p["g_ap"] + p["g_esp"] - p["l_sis"] - pl
        data.append((d, shannon_mbps(pr - noise_dbm()),
                     "Kapasitas Shannon C = B·log₂(1+S/N)"))
        data.append((d, phy_terbaik(pr, L["margin_perlu"])[1],
                     "Laju PHY Wi-Fi andal"))
    c_atas = max(r[1] for r in data)
    for x, nama in ((L["d"], "Posisi ESP32 (live)"),
                    (L["radius"], "Radius coverage MCS0")):
        data += [(x, 0.0, nama), (x, c_atas, nama)]
    return pd.DataFrame(data, columns=kolom)


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

    df_jarak = pd.DataFrame({
        "Waktu": pd.to_datetime([]),
        "Jarak (m)": pd.Series(dtype=float),
        "Kurva": pd.Series(dtype=str)
    })

    return (
        status_html(pesan, ok=False),
        kartu_html(None, None, None),
        kualitas_html(),
        jarak_html(),
        df_plot,
        df_plot,
        df_plot,
        df_jarak,
        df_tabel,
        link_budget_live_html(None),
        kapasitas_live_html(None),
        pd.DataFrame({"Jarak (m)": pd.Series(dtype=float),
                      "Kapasitas (Mbps)": pd.Series(dtype=float),
                      "Kurva": pd.Series(dtype=str)}),
        pd.DataFrame({"Waktu": pd.to_datetime([]),
                      "Daya (dBm)": pd.Series(dtype=float),
                      "Kurva": pd.Series(dtype=str)}),
        pd.DataFrame({"Waktu": pd.to_datetime([]),
                      "Latency (ms)": pd.Series(dtype=float),
                      "Kurva": pd.Series(dtype=str)}),
        # Tab Coverage & Kapasitas tidak diubah bila belum ada data live
        *[gr.update() for _ in range(8)]
    )


# =====================================================
# 5. MENGAMBIL DATA CLOUD
# =====================================================

PT_ESP_DEFAULT = 19.5   # dipakai hanya bila firmware belum mengirim tx_dbm


def ambil_data(g_esp=2.0, pt_ap=15.0, g_ap=0.0, l_sis=2.0,
               n=3.0, sigma=7.0, n_dinding=1, loss_dinding=13.0,
               keandalan=90, offset_kal=0.0,
               kp_cloud=None, kp_ap=None, kp_phy=None, kp_n=1, kp_util=50):
    """Membaca feed Adafruit IO lalu menghitung SELURUH dashboard dari
    data live ESP32 (daya pancar, RSSI, interval, ukuran paket).
    Parameter = asumsi model propagasi & skenario perencanaan."""

    kp_cloud = kp_cloud or list(BATAS_CLOUD)[0]
    kp_ap = kp_ap or list(BATAS_AP)[0]
    kp_phy = kp_phy or SKEMA_WIFI[3][0]
    pt_esp = PT_ESP_DEFAULT

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

            ts_dev = pd.to_datetime(
                paket.get("timestamp"),
                utc=True,
                errors="coerce"
            )

            rows.append({

                "_bytes": len(
                    str(item.get("value", "")).encode("utf-8")
                ),

                "_sid": str(paket.get("sid", "-")),

                "_ts_dev": ts_dev,


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
                ),

                "RSSI (dBm)": paket.get("rssi"),

                "_tx_dbm": paket.get("tx_dbm"),

                "_ch": paket.get("ch"),

                "_rtt_cloud": paket.get("rtt_cloud_ms"),

                "_rtt_lokal": paket.get("rtt_lokal_ms")
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
        "Interval (ms)",
        "RSSI (dBm)",
        "_tx_dbm",
        "_ch",
        "_rtt_cloud",
        "_rtt_lokal"
    ]:

        semua[kolom] = pd.to_numeric(
            semua[kolom],
            errors="coerce"
        )

    # Estimasi jarak ke hotspot dari RSSI (log-distance terbalik).
    # RSSI dihaluskan dengan median 5 paket untuk meredam fading cepat.
    param_jarak = dict(pt_ap=pt_ap, g_ap=g_ap, g_esp=g_esp, l_sis=l_sis,
                       n=n, n_dinding=n_dinding,
                       loss_dinding=loss_dinding, offset_kal=offset_kal)
    semua.loc[semua["RSSI (dBm)"] >= 0, "RSSI (dBm)"] = float("nan")
    rssi_halus = semua["RSSI (dBm)"].rolling(5, min_periods=1).median()
    semua["Jarak est. (m)"] = [
        round(estimasi_jarak_m(r, **param_jarak), 1)
        if pd.notna(r) else float("nan")
        for r in rssi_halus
    ]

    # Daya pancar ESP32 = nilai LIVE dari firmware (tx_dbm)
    tx_live = _terakhir(semua["_tx_dbm"])
    if tx_live is not None:
        pt_esp = tx_live

    tabel_cov, _ = hitung_coverage(pt_esp, g_esp, pt_ap, g_ap, l_sis, n,
                                   sigma, n_dinding, loss_dinding,
                                   keandalan)
    radius = float(tabel_cov.iloc[3]["Radius coverage (m)"])

    df_jarak = pd.concat([
        pd.DataFrame({"Waktu": semua["_dt"],
                      "Jarak (m)": semua["Jarak est. (m)"],
                      "Kurva": "Estimasi jarak ESP32"}),
        pd.DataFrame({"Waktu": semua["_dt"],
                      "Jarak (m)": radius,
                      "Kurva": "Radius coverage MCS0"}),
    ]).dropna()

    info_jarak = jarak_html(
        rssi_halus.iloc[-1], radius, sigma, n,
        estimasi_jarak_m(rssi_halus.iloc[-1], **param_jarak)
        if pd.notna(rssi_halus.iloc[-1]) else None
    )

    # Link budget, trafik/bandwidth, daya & latency live
    live = hitung_live(semua, rssi_halus.iloc[-1], radius, dict(
        pt_esp=pt_esp, g_esp=g_esp, pt_ap=pt_ap, g_ap=g_ap, l_sis=l_sis,
        n=n, sigma=sigma, n_dinding=n_dinding, loss_dinding=loss_dinding,
        keandalan=keandalan, offset_kal=offset_kal))
    lb_live = link_budget_live_html(live)
    kp_live = kapasitas_live_html(live)
    df_kap_jarak = kurva_kapasitas_jarak(live)
    df_lb_waktu = kurva_lb_waktu(semua, rssi_halus, dict(
        pt_esp=pt_esp, g_esp=g_esp, pt_ap=pt_ap, g_ap=g_ap, l_sis=l_sis,
        sigma=sigma, keandalan=keandalan))
    df_rtt = kurva_latency(semua)

    # Tab Coverage & Kapasitas dihitung dari data live:
    # Pt ESP32 terukur, interval kirim & ukuran paket terukur.
    out_cov = tampil_coverage(pt_esp, g_esp, pt_ap, g_ap, l_sis, n, sigma,
                              n_dinding, loss_dinding, keandalan)
    ia = pd.to_numeric(semua["Interval (ms)"], errors="coerce").dropna()
    interval_live = round(float(ia.mean()) / 1000, 2) if len(ia) else 5.0
    payload_live = float(semua["_bytes"].mean())
    out_kap = tampil_kapasitas(max(interval_live, 0.1), payload_live,
                               kp_cloud, kp_ap, kp_phy, kp_n, kp_util)

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

        kualitas_html({**hitung_kualitas(semua), **live}),

        info_jarak,

        df_plot,
        df_plot,
        df_plot,
        df_jarak,
        df_tabel,

        lb_live,
        kp_live,
        df_kap_jarak,
        df_lb_waktu,
        df_rtt,
        *out_cov,
        *out_kap
    )


# =====================================================
# 8b. TAMPILAN PERENCANAAN COVERAGE
# =====================================================

def tampil_coverage(pt_esp, g_esp, pt_ap, g_ap, l_sis, n, sigma,
                    n_dinding, loss_dinding, keandalan):

    tabel, margin = hitung_coverage(
        pt_esp, g_esp, pt_ap, g_ap, l_sis, n, sigma,
        n_dinding, loss_dinding, keandalan
    )

    r_1m = float(tabel.iloc[0]["Radius coverage (m)"])
    r_mcs0 = float(tabel.iloc[3]["Radius coverage (m)"])
    r_mcs7 = float(tabel.iloc[5]["Radius coverage (m)"])
    luas = math.pi * r_mcs0 ** 2

    # Kurva daya terima rata-rata terhadap jarak
    d_maks = min(max(r_1m * 1.4, 20.0), 600.0)
    langkah = d_maks / 120
    jarak = [D0_M + i * langkah for i in range(121)]

    pt_up = min(pt_esp, SKEMA_WIFI[3][1])  # daya TX ESP32 untuk MCS0
    data = []
    for d in jarak:
        pl = path_loss_db(d, n, n_dinding, loss_dinding)
        data.append((d, pt_up + g_esp + g_ap - l_sis - pl,
                     "Uplink ESP32 → AP"))
        data.append((d, pt_ap + g_ap + g_esp - l_sis - pl,
                     "Downlink AP → ESP32"))
        for idx, label in ((3, "Ambang MCS0 + margin"),
                           (5, "Ambang MCS7 + margin")):
            data.append((d, SKEMA_WIFI[idx][2] + margin, label))

    df_plot = pd.DataFrame(
        data, columns=["Jarak (m)", "Daya terima (dBm)", "Kurva"]
    )

    pl0 = pl_d0_db()
    eirp = pt_up + g_esp
    ringkas = f"""
<div class="info">
  <b>Hasil perencanaan coverage</b> (f = {FREK_HZ/1e9:.3f} GHz,
  λ = {lambda_m()*100:.2f} cm, PL(d<sub>0</sub>=1 m) = {pl0:.1f} dB,
  n = {n:.1f}, σ = {sigma:.1f} dB, {int(n_dinding)} dinding × {loss_dinding:.0f} dB)<br>
  Margin shadowing untuk keandalan tepi sel {keandalan:.0f}% =
  σ·Q<sup>-1</sup>(1−p) = <b>{margin:.1f} dB</b><br>
  Radius layanan andal: <b>{r_mcs0:.1f} m</b> pada laju operasi
  802.11n MCS0 (luas ≈ {luas:,.0f} m²), maksimum <b>{r_1m:.1f} m</b>
  pada 802.11b 1 Mbps, dan hanya <b>{r_mcs7:.1f} m</b> bila ingin MCS7.
  <span class="rumus">EIRP = Pt + Gt = {eirp:.1f} dBm   (Pers. 3.4)
PL(d) = PL(d0) + 10·n·log10(d/d0) + ΣL_dinding + Xσ   (Pers. 3.69/3.93)
Pr(d) = EIRP + Gr − L − PL(d) ≥ Sensitivitas + Margin  →  d_maks</span>
  Paket sensor hanya ±190 byte, sehingga laju PHY rendah (MCS0/1 Mbps)
  sudah cukup; yang menentukan desain adalah <b>jangkauan</b>,
  bukan laju data.
</div>
"""
    # Kartu link budget rencana di tepi sel (radius MCS0)
    pl_jarak = 10 * n * math.log10(max(r_mcs0, 1))
    pl_r = path_loss_db(r_mcs0, n, n_dinding, loss_dinding)
    pr_ul = eirp + g_ap - l_sis - pl_r
    pr_dl = pt_ap + g_ap + g_esp - l_sis - pl_r
    sens = SKEMA_WIFI[3][2]
    lb_rencana = kartu_grid([
        ("EIRP ESP32 (uplink)", f"{eirp:.1f}", "dBm",
         f"Pt {pt_up:.1f} dBm (MCS0) + Gt {g_esp:.1f} dBi", "#E8590C"),
        ("EIRP hotspot (downlink)", f"{pt_ap + g_ap:.1f}", "dBm",
         f"Pt {pt_ap:.1f} dBm + Gt {g_ap:.1f} dBi (asumsi)", "#D9480F"),
        ("Path loss total di tepi sel", f"{pl_r:.1f}", "dB",
         f"PL(d0) {pl0:.1f} + 10·n·log d {pl_jarak:.1f} + dinding "
         f"{n_dinding * loss_dinding:.0f} dB", "#7048E8"),
        ("Radius tepi sel", f"{r_mcs0:.1f}", "m",
         f"802.11n MCS0, keandalan {keandalan:.0f}%", "#5F3DC4"),
        ("Daya diterima hotspot", f"{pr_ul:.1f}", "dBm",
         f"uplink, rugi sistem {l_sis:.1f} dB", "#1C7ED6"),
        ("Daya diterima ESP32", f"{pr_dl:.1f}", "dBm",
         "downlink (arah pembatas)", "#1098AD"),
        ("Sensitivitas MCS0", f"{sens:.0f}", "dBm",
         "datasheet ESP32-WROOM-32E", "#868E96"),
        ("Margin fading di tepi sel", f"{min(pr_ul, pr_dl) - sens:.1f}", "dB",
         f"= margin dibutuhkan {margin:.1f} dB (Pers. 3.71)", "#0F766E"),
    ])
    return ringkas, df_plot, tabel, lb_rencana


# =====================================================
# 8c. TAMPILAN PERENCANAAN KAPASITAS
# =====================================================

def tampil_kapasitas(interval_s, payload, nama_cloud, nama_ap,
                     nama_phy, n_rencana, utilisasi):

    k = hitung_kapasitas(interval_s, payload, nama_cloud, nama_ap,
                         nama_phy, utilisasi)
    n_rencana = int(n_rencana)
    aman = n_rencana <= k["n_maks"]

    kartu = ""
    for (label, nilai, satuan, ket, warna) in [
        ("Trafik per node", f"{k['bit_node']:.0f}", "bit/s",
         f"frame {k['frame']:.0f} B + ACK TCP {TCP_ACK_BYTE} B tiap "
         f"{interval_s:g} s (terukur)",
         "#0F766E"),
        ("Kapasitas maksimum", f"{k['n_maks']}", "node",
         f"pembatas: {k['bottleneck']}", "#7048E8"),
        ("Rencana", f"{n_rencana}", "node",
         "AMAN" if aman else "MELEBIHI KAPASITAS",
         "#2F9E44" if aman else "#E03131"),
        ("Kuota internet", f"{k['mb_bulan']*n_rencana:,.0f}", "MB/bulan",
         f"{k['mb_bulan']:.0f} MB per node (tanpa dashboard)", "#E67700"),
    ]:
        kartu += (
            f'<div class="kartu" style="--warna:{warna}">'
            f'<div class="label">{label}</div>'
            f'<div class="nilai">{nilai}<span class="satuan">{satuan}</span></div>'
            f'<div class="ket">{html.escape(ket)}</div></div>'
        )
    kartu = f'<div class="kartu-wrap empat">{kartu}</div>'

    kelas = "info" if aman else "info peringatan"
    ringkas = f"""
<div class="{kelas}">
  <b>Model trafik</b>: 1 node = {k['data_per_menit']:.1f} data/menit
  ke Adafruit IO. Satu laporan menempati kanal
  ±{k['t_laporan_ms']:.2f} ms ({nama_phy}).<br>
  <b>Batas kapasitas</b> — kuota cloud: {k['batas']['Kuota data cloud (Adafruit IO)']} node ·
  klien AP: {k['batas']['Jumlah klien titik akses']} node ·
  airtime Wi-Fi (utilisasi ≤ {utilisasi:.0f}%):
  {k['batas']['Airtime kanal Wi-Fi (CSMA/CA)']:,} node.<br>
  <b>Kapasitas Shannon</b> di tepi sel (SNR = {k['snr_tepi']:.1f} dB,
  B = 20 MHz): C = B·log₂(1+S/N) = <b>{k['c_shannon']:.1f} Mbps</b>
  → setara {k['n_shannon']:,} node (Pers. 5.37).<br>
  Kesimpulan: bandwidth radio sangat berlebih; kapasitas sistem
  dibatasi oleh <b>{k['bottleneck'].lower()}</b>
  = <b>{k['n_maks']} node</b> pada interval {interval_s:g} s.
</div>
"""

    # Utilisasi tiap sumber daya terhadap jumlah node
    data = []
    n_atas = max(20, n_rencana * 2, k["batas_ap"] + 5)
    for n in range(1, n_atas + 1):
        data.append((n, 100 * n * k["data_per_menit"] / k["batas_cloud"],
                     "Kuota cloud"))
        data.append((n, 100 * n / k["batas_ap"], "Klien AP"))
        data.append((n, 100 * n * k["t_laporan_s"] / interval_s,
                     "Airtime Wi-Fi"))
        data.append((n, 100.0, "Batas 100%"))
    df_plot = pd.DataFrame(data, columns=["Jumlah node", "Utilisasi (%)",
                                          "Sumber daya"])

    # Grafik kapasitas (jumlah node maks.) terhadap interval kirim
    baris = []
    for t in range(1, 61):
        kk = hitung_kapasitas(t, payload, nama_cloud, nama_ap,
                              nama_phy, utilisasi)
        baris += [
            (t, kk["batas"]["Kuota data cloud (Adafruit IO)"], "Batas kuota cloud"),
            (t, kk["batas"]["Jumlah klien titik akses"], "Batas klien titik akses"),
            (t, kk["n_maks"], "Kapasitas sistem (minimum)"),
        ]
    df_interval = pd.DataFrame(baris, columns=["Interval kirim (s)",
                                               "Jumlah node maks.", "Kurva"])

    return kartu, ringkas, df_plot, df_interval


# =====================================================
# 9. MEMBUAT DASHBOARD GRADIO
# =====================================================

NAMA_SKEMA = [s[0] for s in SKEMA_WIFI]

# Gradio 6 memindahkan theme & css dari Blocks() ke launch()
GRADIO_6 = int(gr.__version__.split(".")[0]) >= 6
GAYA = {"theme": TEMA, "css": CSS}

with gr.Blocks(
    title="ESP32 MQTT Monitoring Dashboard",
    **({} if GRADIO_6 else GAYA)
) as demo:

    # Header
    gr.HTML(HEADER_HTML)

    with gr.Tabs():

        # =============================================
        # TAB 1 : MONITORING & KUALITAS LAYANAN
        # =============================================

        with gr.Tab("Monitoring & Kualitas"):

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

            gr.HTML(
                '<div class="judul-bagian">'
                'Kualitas layanan (dari 60 paket terakhir di cloud)'
                '</div>'
            )

            kualitas = gr.HTML(kualitas_html())

            grafik_rtt = gr.LinePlot(
                x="Waktu",
                y="Latency (ms)",
                color="Kurva",
                title="Latency RTT live",
                x_title="Waktu",
                y_title="ms",
                x_label_angle=-45,
                height=260,
                elem_classes="panel"
            )

            gr.HTML(
                '<div class="judul-bagian">'
                'Coverage live: estimasi jarak ESP32 ke hotspot '
                '(dari RSSI, model log-distance Pers. 3.69)'
                '</div>'
            )

            jarak = gr.HTML(jarak_html())

            grafik_jarak = gr.LinePlot(
                x="Waktu",
                y="Jarak (m)",
                color="Kurva",
                title="Estimasi jarak ESP32 → hotspot vs radius coverage",
                x_title="Waktu",
                y_title="m",
                x_label_angle=-45,
                height=260,
                elem_classes="panel"
            )

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

            gr.HTML(
                '<div class="judul-bagian">'
                'Riwayat paket MQTT (terbaru di atas)'
                '</div>'
            )

            tabel = gr.Dataframe(
                headers=KOLOM,
                interactive=False,
                wrap=False,
                max_height=420,
                show_label=False,
                elem_classes=["panel", "tabel-riwayat"]
            )

            tombol = gr.Button(
                "↻ Refresh sekarang",
                elem_classes="btn-refresh"
            )

        # =============================================
        # TAB 2 : PERENCANAAN COVERAGE (LINK BUDGET)
        # =============================================

        with gr.Tab("Real Coverage"):

            gr.HTML(
                '<div class="judul-bagian">'
                'Link budget live: ESP32 terbaru (RSSI & daya pancar terukur)'
                '</div>'
            )

            cv_lb_live = gr.HTML(link_budget_live_html(None))

            grafik_lb = gr.LinePlot(
                x="Waktu",
                y="Daya (dBm)",
                color="Kurva",
                title="Link budget live: daya diterima vs ambang andal",
                x_title="Waktu",
                y_title="dBm",
                x_label_angle=-45,
                height=280,
                elem_classes="panel"
            )

            gr.HTML(
                '<div class="judul-bagian">'
                'Radius coverage dari daya pancar ESP32 live'
                '</div>'
            )

            # Ringkasan teks rumus tidak ditampilkan (angka sudah di kartu)
            cv_ringkas = gr.HTML(visible=False)

            cv_lb_rencana = gr.HTML()

            cv_grafik = gr.LinePlot(
                x="Jarak (m)",
                y="Daya terima (dBm)",
                color="Kurva",
                title="Daya terima rata-rata vs jarak (link budget)",
                x_title="Jarak ESP32 – titik akses (m)",
                y_title="dBm",
                height=340,
                elem_classes="panel"
            )

            cv_tabel = gr.BarPlot(
                x="Skema Wi-Fi",
                y="Radius coverage (m)",
                title="Radius coverage andal per skema Wi-Fi",
                x_title="Skema Wi-Fi (laju PHY)",
                y_title="m",
                x_label_angle=-30,
                height=320,
                elem_classes="panel"
            )

            with gr.Accordion(
                "Asumsi model propagasi (konstanta Rappaport — tidak "
                "diukur ESP32)", open=False
            ):
                with gr.Row():
                    with gr.Column():
                        cv_g_esp = gr.Slider(-3, 6, value=2.0, step=0.5,
                                             label="Gain antena ESP32 (dBi)")
                        cv_pt_ap = gr.Slider(5, 23, value=15.0, step=0.5,
                                             label="Daya TX titik akses (dBm)")
                        cv_g_ap = gr.Slider(-3, 9, value=0.0, step=0.5,
                                            label="Gain antena titik akses (dBi)")
                        cv_l_sis = gr.Slider(0, 10, value=2.0, step=0.5,
                                             label="Rugi sistem L (dB)")
                        cv_offset = gr.Slider(-20, 20, value=0.0, step=0.5,
                                              label="Koreksi kalibrasi PL(d0) (dB)")
                    with gr.Column():
                        cv_n = gr.Slider(1.6, 6.0, value=3.0, step=0.1,
                                         label="Eksponen path loss n (Tabel 3.2)")
                        cv_sigma = gr.Slider(0, 14, value=7.0, step=0.5,
                                             label="Deviasi shadowing σ (dB)")
                        cv_dinding = gr.Slider(0, 5, value=1, step=1,
                                               label="Jumlah dinding/partisi")
                        cv_loss_dinding = gr.Slider(0, 20, value=13.0, step=0.5,
                                                    label="Redaman per dinding (dB), Tabel 3.3")
                        cv_keandalan = gr.Slider(50, 99, value=90, step=1,
                                                 label="Keandalan coverage tepi sel (%)")

        # =============================================
        # TAB 3 : PERENCANAAN KAPASITAS
        # =============================================

        with gr.Tab("Real Kapasitas"):

            gr.HTML(
                '<div class="judul-bagian">'
                'Trafik & bandwidth live (data ESP32 terbaru)'
                '</div>'
            )

            kp_live = gr.HTML(kapasitas_live_html(None))

            kp_live_plot = gr.LinePlot(
                x="Jarak (m)",
                y="Kapasitas (Mbps)",
                color="Kurva",
                title="Kapasitas kanal vs jarak: posisi ESP32 live & radius",
                x_title="Jarak ESP32 – hotspot (m)",
                y_title="Mbps",
                height=320,
                elem_classes="panel"
            )

            gr.HTML(
                '<div class="judul-bagian">'
                'Kapasitas jumlah node (dari trafik live ESP32)'
                '</div>'
            )

            kp_kartu = gr.HTML()
            # Ringkasan teks tidak ditampilkan (angka sudah di kartu)
            kp_ringkas = gr.HTML(visible=False)

            kp_grafik = gr.LinePlot(
                x="Jumlah node",
                y="Utilisasi (%)",
                color="Sumber daya",
                title="Utilisasi sumber daya vs jumlah node",
                x_title="Jumlah node sensor",
                y_title="% dari batas",
                height=320,
                elem_classes="panel"
            )

            kp_tabel = gr.LinePlot(
                x="Interval kirim (s)",
                y="Jumlah node maks.",
                color="Kurva",
                title="Jumlah node maksimum vs interval kirim",
                x_title="Interval kirim per node (s)",
                y_title="node",
                height=320,
                elem_classes="panel"
            )

            with gr.Accordion(
                "Skenario perencanaan (jumlah node & paket layanan)",
                open=False
            ):
                with gr.Row():
                    with gr.Column():
                        kp_n = gr.Slider(1, 100, value=1, step=1,
                                         label="Jumlah node yang direncanakan")
                        kp_util = gr.Slider(10, 90, value=50, step=5,
                                            label="Utilisasi kanal maksimum (%)")
                    with gr.Column():
                        kp_cloud = gr.Dropdown(list(BATAS_CLOUD),
                                               value=list(BATAS_CLOUD)[0],
                                               label="Paket layanan cloud")
                        kp_ap = gr.Dropdown(list(BATAS_AP),
                                            value=list(BATAS_AP)[0],
                                            label="Jenis titik akses")
                        kp_phy = gr.Dropdown(NAMA_SKEMA,
                                             value=NAMA_SKEMA[3],
                                             label="Laju PHY Wi-Fi")

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
        kualitas,
        jarak,
        grafik_suhu,
        grafik_lembap,
        grafik_interval,
        grafik_jarak,
        tabel,
        cv_lb_live,
        kp_live,
        kp_live_plot,
        grafik_lb,
        grafik_rtt,
        # Tab Coverage (dihitung dari Pt ESP32 live)
        cv_ringkas, cv_grafik, cv_tabel, cv_lb_rencana,
        # Tab Kapasitas (dihitung dari interval & ukuran paket live)
        kp_kartu, kp_ringkas, kp_grafik, kp_tabel
    ]

    # Asumsi model & skenario (urutan = parameter ambil_data)
    cv_input = [cv_g_esp, cv_pt_ap, cv_g_ap, cv_l_sis,
                cv_n, cv_sigma, cv_dinding, cv_loss_dinding,
                cv_keandalan, cv_offset]
    kp_input = [kp_cloud, kp_ap, kp_phy, kp_n, kp_util]
    data_input = cv_input + kp_input

    # Membaca data pertama kali ketika web dibuka
    demo.load(
        fn=ambil_data,
        inputs=data_input,
        outputs=hasil,
        queue=False,
        show_progress="hidden"
    )

    # Refresh secara manual
    tombol.click(
        fn=ambil_data,
        inputs=data_input,
        outputs=hasil,
        queue=False,
        show_progress="hidden"
    )

    # Refresh otomatis setiap 15 detik
    timer.tick(
        fn=ambil_data,
        inputs=data_input,
        outputs=hasil,
        queue=False,
        trigger_mode="always_last",
        show_progress="hidden"
    )

    # Mengubah asumsi/skenario -> hitung ulang dengan data live terbaru
    gr.on(
        triggers=[c.release for c in cv_input + [kp_n, kp_util]]
        + [c.change for c in (kp_cloud, kp_ap, kp_phy)],
        fn=ambil_data,
        inputs=data_input,
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
        server_port=port,
        **(GAYA if GRADIO_6 else {})
    )
