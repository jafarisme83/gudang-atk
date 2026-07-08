import sqlite3
from datetime import datetime, date
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

DB_PATH = "atk_gudang_kppn.db"

st.set_page_config(
    page_title="Gudang Inventaris KPPN Sungai Penuh",
    page_icon="📦",
    layout="wide"
)

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

conn = get_conn()

def init_db():
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE NOT NULL,
            nama_barang TEXT NOT NULL,
            kategori TEXT,
            satuan TEXT,
            stok INTEGER NOT NULL DEFAULT 0,
            stok_minimum INTEGER NOT NULL DEFAULT 0,
            lokasi_rak TEXT,
            keterangan TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaksi_keluar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal TEXT NOT NULL,
            barcode TEXT NOT NULL,
            nama_barang TEXT NOT NULL,
            qty INTEGER NOT NULL,
            penerima TEXT,
            unit_tujuan TEXT,
            keperluan TEXT,
            petugas TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()

init_db()

def run_query(query, params=(), fetch=True):
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    if fetch:
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    return None

def add_item(barcode, nama_barang, kategori, satuan, stok, stok_minimum, lokasi_rak, keterangan):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("""
        INSERT INTO items (barcode, nama_barang, kategori, satuan, stok, stok_minimum, lokasi_rak, keterangan, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(barcode) DO UPDATE SET
            nama_barang=excluded.nama_barang,
            kategori=excluded.kategori,
            satuan=excluded.satuan,
            stok=excluded.stok,
            stok_minimum=excluded.stok_minimum,
            lokasi_rak=excluded.lokasi_rak,
            keterangan=excluded.keterangan,
            updated_at=excluded.updated_at
    """, (barcode, nama_barang, kategori, satuan, stok, stok_minimum, lokasi_rak, keterangan, now, now))
    conn.commit()

def process_outgoing(tanggal, barcode, qty, penerima, unit_tujuan, keperluan, petugas):
    item = run_query("SELECT * FROM items WHERE barcode = ?", (barcode,))
    if not item:
        raise ValueError("Barcode tidak ditemukan di master barang.")
    item = item[0]
    stok_sekarang = int(item["stok"])

    if qty <= 0:
        raise ValueError("Jumlah keluar harus lebih dari 0.")
    if qty > stok_sekarang:
        raise ValueError(f"Stok tidak cukup. Stok tersedia: {stok_sekarang}.")

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE items SET stok = ?, updated_at = ? WHERE barcode = ?",
        (stok_sekarang - qty, now, barcode)
    )
    conn.execute("""
        INSERT INTO transaksi_keluar (
            tanggal, barcode, nama_barang, qty, penerima, unit_tujuan, keperluan, petugas, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(tanggal), barcode, item["nama_barang"], qty,
        penerima, unit_tujuan, keperluan, petugas, now
    ))
    conn.commit()

def get_items_df(keyword=""):
    if keyword:
        rows = run_query("""
            SELECT * FROM items
            WHERE barcode LIKE ? OR nama_barang LIKE ? OR kategori LIKE ?
            ORDER BY nama_barang
        """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"))
    else:
        rows = run_query("SELECT * FROM items ORDER BY nama_barang")
    return pd.DataFrame(rows)

def get_transactions_df(start_date=None, end_date=None):
    q = "SELECT * FROM transaksi_keluar WHERE 1=1"
    params = []
    if start_date:
        q += " AND tanggal >= ?"
        params.append(str(start_date))
    if end_date:
        q += " AND tanggal <= ?"
        params.append(str(end_date))
    q += " ORDER BY tanggal DESC, id DESC"
    rows = run_query(q, tuple(params))
    return pd.DataFrame(rows)

def seed_sample_data():
    samples = [
        ("899100100001", "Pulpen Biru", "ATK", "pcs", 120, 20, "Rak A1", "Pulpen operasional"),
        ("899100100002", "Kertas A4 80 gsm", "ATK", "rim", 45, 10, "Rak A2", "Persediaan printer"),
        ("899100100003", "Tissue Gulung", "Kebersihan", "roll", 60, 12, "Rak B1", "Pantry dan toilet"),
        ("899100100004", "Pembersih Lantai", "Kebersihan", "botol", 18, 5, "Rak B2", "Cairan pel lantai"),
    ]
    for row in samples:
        add_item(*row)

scanner_html = """
<div id="reader" style="width:100%;"></div>
<div id="scan-status" style="font-family:Arial,sans-serif;color:#334155;margin-top:8px;">
Arahkan kamera ke barcode barang.
</div>

<script src="https://unpkg.com/html5-qrcode" type="text/javascript"></script>
<script>
const target = window.parent.document.querySelector('input[data-testid="stTextInput"]');

function setBarcodeValue(decodedText) {
    const inputs = window.parent.document.querySelectorAll('input');
    for (let i = 0; i < inputs.length; i++) {
        const el = inputs[i];
        if (el.placeholder && el.placeholder.toLowerCase().includes('scan atau ketik barcode')) {
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, "value"
            ).set;
            nativeInputValueSetter.call(el, decodedText);
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            break;
        }
    }
    const status = document.getElementById("scan-status");
    if (status) status.innerText = "Barcode terbaca: " + decodedText;
}

function onScanSuccess(decodedText, decodedResult) {
    if (decodedText && decodedText.trim() !== "") {
        setBarcodeValue(decodedText.trim());
    }
}

function onScanFailure(error) {}

async function startScanner() {
    const html5QrCode = new Html5Qrcode("reader");
    const cameras = await Html5Qrcode.getCameras();

    let cameraConfig = { facingMode: "environment" };
    if (cameras && cameras.length > 0) {
        const backCam = cameras.find(c => /back|rear|environment/i.test(c.label));
        cameraConfig = backCam ? backCam.id : cameras[0].id;
    }

    await html5QrCode.start(
        cameraConfig,
        { fps: 10, qrbox: { width: 250, height: 120 } },
        onScanSuccess,
        onScanFailure
    );
}

startScanner().catch(err => {
    const status = document.getElementById("scan-status");
    if (status) status.innerText = "Kamera gagal dibuka. Pastikan izin kamera aktif.";
});
</script>
"""

st.title("Aplikasi Gudang Inventaris KPPN Sungai Penuh")
st.caption("Pencatatan barang keluar untuk ATK, tissue, pembersih lantai, dan kebutuhan operasional kantor lainnya.")

with st.sidebar:
    st.header("Menu")
    if st.button("Isi data contoh"):
        seed_sample_data()
        st.success("Data contoh berhasil dimasukkan.")
    keyword = st.text_input("Cari barang", placeholder="Nama barang / barcode / kategori")

items_df = get_items_df(keyword)
trx_df_all = get_transactions_df()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Jenis barang", 0 if items_df.empty else len(items_df))
c2.metric("Total stok", 0 if items_df.empty else int(items_df["stok"].sum()))
c3.metric("Transaksi keluar", 0 if trx_df_all.empty else len(trx_df_all))
c4.metric("Stok menipis", 0 if items_df.empty else int((items_df["stok"] <= items_df["stok_minimum"]).sum()))

tab1, tab2, tab3 = st.tabs(["Barang keluar", "Master barang", "Laporan"])

with tab1:
    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Form barang keluar")
        st.text_input("Hasil scan barcode", key="barcode_scan", placeholder="Scan atau ketik barcode manual")
        components.html(scanner_html, height=420)

        with st.form("form_keluar"):
            tanggal = st.date_input("Tanggal keluar", value=date.today())
            barcode = st.text_input("Barcode final", value=st.session_state.get("barcode_scan", ""))
            qty = st.number_input("Jumlah keluar", min_value=1, step=1)
            penerima = st.text_input("Nama penerima")
            unit_tujuan = st.text_input("Seksi / ruangan tujuan")
            keperluan = st.text_area("Keperluan")
            petugas = st.text_input("Petugas gudang")
            submit_keluar = st.form_submit_button("Simpan transaksi", use_container_width=True)

        if submit_keluar:
            try:
                process_outgoing(
                    tanggal, barcode.strip(), int(qty),
                    penerima.strip(), unit_tujuan.strip(),
                    keperluan.strip(), petugas.strip()
                )
                st.success("Transaksi barang keluar berhasil disimpan.")
                st.session_state["barcode_scan"] = ""
                st.rerun()
            except Exception as e:
                st.error(str(e))

    with right:
        st.subheader("Preview barang")
        barcode_now = st.session_state.get("barcode_scan", "")
        if barcode_now:
            item = run_query("SELECT * FROM items WHERE barcode = ?", (barcode_now,))
            if item:
                item = item[0]
                st.info(f"{item['nama_barang']} | Stok: {item['stok']} {item['satuan']}")
                st.json(item)
            else:
                st.warning("Barcode belum terdaftar di master barang.")

        st.subheader("10 transaksi terakhir")
        recent = get_transactions_df().head(10)
        st.dataframe(recent, use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Master barang")

    with st.form("form_barang"):
        a, b = st.columns(2)
        with a:
            barcode = st.text_input("Barcode barang")
            nama_barang = st.text_input("Nama barang")
            kategori = st.selectbox("Kategori", ["ATK", "Kebersihan", "Konsumsi", "Lainnya"])
            satuan = st.text_input("Satuan", value="pcs")
        with b:
            stok = st.number_input("Stok awal", min_value=0, step=1)
            stok_minimum = st.number_input("Stok minimum", min_value=0, step=1)
            lokasi_rak = st.text_input("Lokasi rak")
            keterangan = st.text_area("Keterangan")

        submit_barang = st.form_submit_button("Simpan / update barang", use_container_width=True)

    if submit_barang:
        if not barcode.strip() or not nama_barang.strip():
            st.error("Barcode dan nama barang wajib diisi.")
        else:
            add_item(
                barcode.strip(), nama_barang.strip(), kategori, satuan.strip(),
                int(stok), int(stok_minimum), lokasi_rak.strip(), keterangan.strip()
            )
            st.success("Master barang berhasil disimpan.")
            st.rerun()

    st.dataframe(items_df, use_container_width=True, hide_index=True)

    if not items_df.empty:
        csv_items = items_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Unduh master barang (CSV)",
            csv_items,
            file_name="master_barang.csv",
            mime="text/csv"
        )

with tab3:
    st.subheader("Laporan transaksi keluar")

    r1, r2 = st.columns(2)
    with r1:
        start_date = st.date_input("Dari tanggal", value=date.today().replace(day=1), key="start_report")
    with r2:
        end_date = st.date_input("Sampai tanggal", value=date.today(), key="end_report")

    report_df = get_transactions_df(start_date, end_date)
    st.dataframe(report_df, use_container_width=True, hide_index=True)

    if not report_df.empty:
        rekap = report_df.groupby(["barcode", "nama_barang"], as_index=False)["qty"].sum()
        rekap = rekap.sort_values("qty", ascending=False)

        st.subheader("Rekap pemakaian per barang")
        st.dataframe(rekap, use_container_width=True, hide_index=True)
        st.bar_chart(rekap.set_index("nama_barang")["qty"])

        csv_report = report_df.to_csv(index=False).encode("utf-8-sig")
        csv_rekap = rekap.to_csv(index=False).encode("utf-8-sig")

        st.download_button(
            "Unduh transaksi keluar (CSV)",
            csv_report,
            file_name="laporan_transaksi_keluar.csv",
            mime="text/csv"
        )
        st.download_button(
            "Unduh rekap pemakaian (CSV)",
            csv_rekap,
            file_name="rekap_pemakaian_barang.csv",
            mime="text/csv"
        )
    else:
        st.info("Belum ada data pada periode tersebut.")

st.divider()
st.caption("Saran pengembangan berikutnya: login user, role admin/petugas, approval, audit trail, dan database PostgreSQL/MySQL.")