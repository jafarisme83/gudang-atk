import os
import hmac
import hashlib
import secrets
from base64 import b64encode
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Text, Date, DateTime,
    create_engine, select, insert, update, func, or_
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

st.set_page_config(
    page_title="Gudang Inventaris KPPN Sungai Penuh V2",
    page_icon="📦",
    layout="wide"
)

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(50), unique=True, nullable=False),
    Column("full_name", String(150), nullable=False),
    Column("role", String(20), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("is_active", Integer, nullable=False, default=1),
    Column("created_at", DateTime, nullable=False),
)

items = Table(
    "items", metadata,
    Column("id", Integer, primary_key=True),
    Column("barcode", String(120), unique=True, nullable=False),
    Column("nama_barang", String(200), nullable=False),
    Column("kategori", String(80), nullable=False),
    Column("satuan", String(40), nullable=False),
    Column("stok", Integer, nullable=False, default=0),
    Column("stok_minimum", Integer, nullable=False, default=0),
    Column("lokasi_rak", String(80)),
    Column("keterangan", Text),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

transactions = Table(
    "transactions", metadata,
    Column("id", Integer, primary_key=True),
    Column("tanggal", Date, nullable=False),
    Column("barcode", String(120), nullable=False),
    Column("nama_barang", String(200), nullable=False),
    Column("qty", Integer, nullable=False),
    Column("penerima", String(150)),
    Column("unit_tujuan", String(150)),
    Column("keperluan", Text),
    Column("petugas", String(150)),
    Column("created_by", Integer),
    Column("created_at", DateTime, nullable=False),
)


def now_ts():
    return datetime.now()


def get_engine():
    pg_cfg = None
    try:
        if "connections" in st.secrets and "postgresql" in st.secrets["connections"]:
            pg_cfg = st.secrets["connections"]["postgresql"]
        elif "postgresql" in st.secrets:
            pg_cfg = st.secrets["postgresql"]
    except Exception:
        pg_cfg = None

    if pg_cfg:
        url = URL.create(
            drivername="postgresql+psycopg2",
            username=pg_cfg.get("username") or pg_cfg.get("user"),
            password=pg_cfg.get("password"),
            host=pg_cfg.get("host", "localhost"),
            port=int(pg_cfg.get("port", 5432)),
            database=pg_cfg.get("database") or pg_cfg.get("dbname"),
        )
        return create_engine(url, pool_pre_ping=True, future=True)

    return create_engine("sqlite:///atk_gudang_kppn_v2.db", future=True)


engine = get_engine()
metadata.create_all(engine)


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000)
    return f"{salt}${b64encode(digest).decode()}"


def verify_password(password, hashed_value):
    salt, encoded = hashed_value.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000)
    return hmac.compare_digest(encoded, b64encode(digest).decode())


def fetch_all(stmt):
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return [dict(row._mapping) for row in result.fetchall()]


def fetch_one(stmt):
    rows = fetch_all(stmt)
    return rows[0] if rows else None


def exec_stmt(stmt):
    with engine.begin() as conn:
        conn.execute(stmt)


def scalar(stmt):
    with engine.begin() as conn:
        return conn.execute(stmt).scalar()


def has_any_user():
    return (scalar(select(func.count()).select_from(users)) or 0) > 0


def create_user(username, full_name, role, password, is_active=1):
    exec_stmt(
        insert(users).values(
            username=username.strip().lower(),
            full_name=full_name.strip(),
            role=role,
            password_hash=hash_password(password),
            is_active=is_active,
            created_at=now_ts(),
        )
    )


def authenticate(username, password):
    user = fetch_one(
        select(users).where(users.c.username == username.strip().lower())
    )
    if not user:
        return None
    if int(user["is_active"]) != 1:
        return None
    if verify_password(password, user["password_hash"]):
        return user
    return None


def get_item_by_barcode(barcode):
    return fetch_one(select(items).where(items.c.barcode == barcode.strip()))


def upsert_item(data):
    current = get_item_by_barcode(data["barcode"])
    if current:
        exec_stmt(
            update(items)
            .where(items.c.barcode == data["barcode"])
            .values(
                nama_barang=data["nama_barang"],
                kategori=data["kategori"],
                satuan=data["satuan"],
                stok=int(data["stok"]),
                stok_minimum=int(data["stok_minimum"]),
                lokasi_rak=data.get("lokasi_rak", ""),
                keterangan=data.get("keterangan", ""),
                updated_at=now_ts(),
            )
        )
    else:
        exec_stmt(
            insert(items).values(
                barcode=data["barcode"],
                nama_barang=data["nama_barang"],
                kategori=data["kategori"],
                satuan=data["satuan"],
                stok=int(data["stok"]),
                stok_minimum=int(data["stok_minimum"]),
                lokasi_rak=data.get("lokasi_rak", ""),
                keterangan=data.get("keterangan", ""),
                created_at=now_ts(),
                updated_at=now_ts(),
            )
        )


def process_outgoing(data, user_id):
    barcode = data["barcode"].strip()
    item = get_item_by_barcode(barcode)

    if not item:
        raise ValueError("Barcode tidak ditemukan di master barang.")

    qty = int(data["qty"])
    stok = int(item["stok"])

    if qty <= 0:
        raise ValueError("Jumlah keluar harus lebih dari 0.")
    if qty > stok:
        raise ValueError(f"Stok tidak cukup. Stok tersedia: {stok}.")

    with engine.begin() as conn:
        conn.execute(
            update(items)
            .where(items.c.barcode == barcode)
            .values(stok=stok - qty, updated_at=now_ts())
        )
        conn.execute(
            insert(transactions).values(
                tanggal=data["tanggal"],
                barcode=barcode,
                nama_barang=item["nama_barang"],
                qty=qty,
                penerima=data.get("penerima", "").strip(),
                unit_tujuan=data.get("unit_tujuan", "").strip(),
                keperluan=data.get("keperluan", "").strip(),
                petugas=data.get("petugas", "").strip(),
                created_by=user_id,
                created_at=now_ts(),
            )
        )


def get_items_df(keyword=""):
    stmt = select(items)
    if keyword:
        kw = f"%{keyword.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(items.c.barcode).like(kw),
                func.lower(items.c.nama_barang).like(kw),
                func.lower(items.c.kategori).like(kw),
                func.lower(items.c.lokasi_rak).like(kw),
            )
        )
    df = pd.read_sql(stmt, engine)
    if not df.empty:
        df = df.sort_values(["kategori", "nama_barang"]).reset_index(drop=True)
    return df


def get_transactions_df(start_date=None, end_date=None):
    stmt = select(transactions)
    if start_date:
        stmt = stmt.where(transactions.c.tanggal >= start_date)
    if end_date:
        stmt = stmt.where(transactions.c.tanggal <= end_date)
    df = pd.read_sql(stmt, engine)
    if not df.empty:
        df["tanggal"] = pd.to_datetime(df["tanggal"]).dt.date
        df = df.sort_values(["tanggal", "id"], ascending=[False, False]).reset_index(drop=True)
    return df


def get_users_df():
    df = pd.read_sql(
        select(
            users.c.id,
            users.c.username,
            users.c.full_name,
            users.c.role,
            users.c.is_active,
            users.c.created_at
        ),
        engine
    )
    if not df.empty:
        df = df.sort_values(["role", "full_name"]).reset_index(drop=True)
    return df


def seed_demo():
    demo = [
        {"barcode": "899100100001", "nama_barang": "Pulpen Biru", "kategori": "ATK", "satuan": "pcs", "stok": 120, "stok_minimum": 20, "lokasi_rak": "Rak A1", "keterangan": "Pulpen operasional"},
        {"barcode": "899100100002", "nama_barang": "Kertas A4 80 gsm", "kategori": "ATK", "satuan": "rim", "stok": 45, "stok_minimum": 10, "lokasi_rak": "Rak A2", "keterangan": "Persediaan printer"},
        {"barcode": "899100100003", "nama_barang": "Tissue Gulung", "kategori": "Kebersihan", "satuan": "roll", "stok": 60, "stok_minimum": 12, "lokasi_rak": "Rak B1", "keterangan": "Pantry dan toilet"},
        {"barcode": "899100100004", "nama_barang": "Pembersih Lantai", "kategori": "Kebersihan", "satuan": "botol", "stok": 18, "stok_minimum": 5, "lokasi_rak": "Rak B2", "keterangan": "Cairan pel lantai"},
        {"barcode": "899100100005", "nama_barang": "Map Folder", "kategori": "ATK", "satuan": "pcs", "stok": 80, "stok_minimum": 15, "lokasi_rak": "Rak A3", "keterangan": "Administrasi"},
    ]
    for row in demo:
        upsert_item(row)


def init_session():
    st.session_state.setdefault("logged_in", False)
    st.session_state.setdefault("user", None)
    st.session_state.setdefault("scan_result", "")


def scanner_component():
    html = """
    <div style="font-family: Arial, sans-serif;">
      <div id="reader" style="width:100%;"></div>
      <div id="scan-status" style="margin-top:8px;color:#334155;">
        Arahkan kamera belakang ke barcode atau QR code. Jika sulit terbaca, gunakan pilihan unggah gambar.
      </div>
    </div>

    <script src="https://unpkg.com/html5-qrcode" type="text/javascript"></script>
    <script>
      const parentDoc = window.parent.document;

      function findTargetInput() {
        const inputs = [...parentDoc.querySelectorAll("input")];
        return inputs.find(el => {
          const aria = (el.getAttribute("aria-label") || "").toLowerCase();
          const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
          return aria.includes("barcode / hasil scan") || placeholder.includes("barcode / hasil scan");
        });
      }

      function setInputValue(value) {
        const target = findTargetInput();
        if (!target) return false;
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, "value"
        ).set;
        setter.call(target, value);
        target.dispatchEvent(new Event("input", { bubbles: true }));
        target.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }

      function setStatus(msg) {
        const el = document.getElementById("scan-status");
        if (el) el.innerText = msg;
      }

      function selectBackCamera() {
        const selectEl = document.getElementById("html5-qrcode-select-camera");
        if (!selectEl) return;
        const options = [...selectEl.options];
        const back = options.find(opt => /back|rear|environment/i.test(opt.text));
        if (back) {
          selectEl.value = back.value;
          selectEl.dispatchEvent(new Event("change"));
        }
      }

      function onScanSuccess(decodedText) {
        const code = (decodedText || "").trim();
        if (!code) return;
        setInputValue(code);
        setStatus("Kode terbaca: " + code);
      }

      function onScanFailure(_) {}

      const formats = [
        Html5QrcodeSupportedFormats.QR_CODE,
        Html5QrcodeSupportedFormats.CODE_128,
        Html5QrcodeSupportedFormats.CODE_39,
        Html5QrcodeSupportedFormats.EAN_13,
        Html5QrcodeSupportedFormats.EAN_8,
        Html5QrcodeSupportedFormats.UPC_A,
        Html5QrcodeSupportedFormats.UPC_E
      ];

      const scanner = new Html5QrcodeScanner(
        "reader",
        {
          fps: 10,
          aspectRatio: 1.3333333,
          rememberLastUsedCamera: true,
          supportedScanTypes: [
            Html5QrcodeScanType.SCAN_TYPE_CAMERA,
            Html5QrcodeScanType.SCAN_TYPE_FILE
          ],
          qrbox: function(w, h) {
            const width = Math.min(w * 0.9, 320);
            return { width: width, height: Math.max(140, width * 0.45) };
          },
          videoConstraints: {
            facingMode: { ideal: "environment" },
            width: { min: 640, ideal: 1280 },
            height: { min: 480, ideal: 720 },
            aspectRatio: 4/3
          },
          formatsToSupport: formats,
          showTorchButtonIfSupported: true,
          showZoomSliderIfSupported: true
        },
        false
      );

      scanner.render(onScanSuccess, onScanFailure);
      setTimeout(selectBackCamera, 1200);
      setTimeout(selectBackCamera, 2500);
    </script>
    """
    components.html(html, height=520)


def login_screen():
    st.title("Gudang Inventaris KPPN Sungai Penuh V2")
    st.caption("Login pengguna, dashboard bulanan, PostgreSQL-ready, dan scan barcode/QR mobile-friendly.")

    if not has_any_user():
        st.info("Belum ada pengguna. Buat admin awal terlebih dahulu.")
        with st.form("setup_admin"):
            c1, c2 = st.columns(2)
            with c1:
                username = st.text_input("Username admin", value="admin")
                full_name = st.text_input("Nama lengkap admin", value="Administrator Gudang")
            with c2:
                password = st.text_input("Password", type="password")
                confirm = st.text_input("Konfirmasi password", type="password")
            submit = st.form_submit_button("Buat admin awal", use_container_width=True)

        if submit:
            if not username.strip() or not full_name.strip() or not password:
                st.error("Semua field wajib diisi.")
            elif password != confirm:
                st.error("Konfirmasi password tidak cocok.")
            else:
                try:
                    create_user(username, full_name, "admin", password)
                    st.success("Admin awal berhasil dibuat. Silakan login.")
                    st.rerun()
                except IntegrityError:
                    st.error("Username sudah digunakan.")
        return

    left, right = st.columns([1, 1.1])
    with left:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Masuk", use_container_width=True)
        if submit:
            user = authenticate(username, password)
            if user:
                st.session_state.logged_in = True
                st.session_state.user = user
                st.rerun()
            st.error("Username atau password tidak valid.")

    with right:
        st.markdown("### Fitur")
        st.markdown("- Role: admin, petugas, pimpinan")
        st.markdown("- PostgreSQL ready, fallback SQLite")
        st.markdown("- Scan barcode dan QR code")
        st.markdown("- Dashboard bulanan dan laporan CSV")


def sidebar_area():
    user = st.session_state.user
    with st.sidebar:
        st.markdown("## Menu")
        st.caption(f"Login sebagai: {user['full_name']} ({user['role']})")
        keyword = st.text_input("Cari barang", placeholder="Nama, barcode, kategori, rak")

        if st.button("Isi data contoh", use_container_width=True):
            seed_demo()
            st.success("Data contoh ditambahkan.")

        if st.button("Logout", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.user = None
            st.rerun()

    return keyword


def dashboard_page(items_df, trx_df):
    st.subheader("Dashboard bulanan")

    today = date.today()
    awal_bulan = today.replace(day=1)
    month_df = trx_df[trx_df["tanggal"] >= awal_bulan].copy() if not trx_df.empty else trx_df

    a, b, c, d = st.columns(4)
    a.metric("Jenis barang", 0 if items_df.empty else int(len(items_df)))
    b.metric("Total stok", 0 if items_df.empty else int(items_df["stok"].sum()))
    c.metric("Transaksi bulan ini", 0 if month_df.empty else int(len(month_df)))
    d.metric("Stok menipis", 0 if items_df.empty else int((items_df["stok"] <= items_df["stok_minimum"]).sum()))

    left, right = st.columns(2)

    with left:
        st.markdown("### Pemakaian harian")
        if month_df.empty:
            st.info("Belum ada transaksi bulan ini.")
        else:
            daily = month_df.groupby("tanggal", as_index=False)["qty"].sum().sort_values("tanggal")
            st.line_chart(daily.set_index("tanggal")["qty"], use_container_width=True)
            st.dataframe(daily, hide_index=True, use_container_width=True)

    with right:
        st.markdown("### Barang paling sering keluar")
        if month_df.empty:
            st.info("Belum ada data.")
        else:
            top = month_df.groupby(["barcode", "nama_barang"], as_index=False)["qty"].sum()
            top = top.sort_values("qty", ascending=False).head(10)
            st.bar_chart(top.set_index("nama_barang")["qty"], use_container_width=True)
            st.dataframe(top, hide_index=True, use_container_width=True)

    st.markdown("### Daftar stok minimum")
    if items_df.empty:
        st.info("Belum ada master barang.")
    else:
        low = items_df[items_df["stok"] <= items_df["stok_minimum"]].copy()
        if low.empty:
            st.success("Semua stok masih aman.")
        else:
            st.dataframe(
                low[["barcode", "nama_barang", "kategori", "stok", "stok_minimum", "lokasi_rak"]],
                hide_index=True,
                use_container_width=True
            )


def outgoing_page(user):
    st.subheader("Barang keluar")

    left, right = st.columns([1.15, 1])

    with left:
        with st.form("form_outgoing"):
            st.text_input("Barcode / hasil scan", key="scan_result", placeholder="Barcode / hasil scan")
            scanner_component()

            c1, c2 = st.columns(2)
            with c1:
                tanggal = st.date_input("Tanggal keluar", value=date.today())
                qty = st.number_input("Jumlah keluar", min_value=1, step=1)
                penerima = st.text_input("Nama penerima / peminjam")
            with c2:
                unit_tujuan = st.text_input("Seksi / ruangan tujuan")
                petugas = st.text_input("Petugas gudang", value=user["full_name"])
                barcode_final = st.text_input("Barcode final", value=st.session_state.get("scan_result", ""))

            keperluan = st.text_area("Keperluan")
            submit = st.form_submit_button("Simpan transaksi keluar", use_container_width=True)

        if submit:
            try:
                process_outgoing(
                    {
                        "tanggal": tanggal,
                        "barcode": barcode_final,
                        "qty": qty,
                        "penerima": penerima,
                        "unit_tujuan": unit_tujuan,
                        "keperluan": keperluan,
                        "petugas": petugas,
                    },
                    user["id"]
                )
                st.success("Transaksi keluar berhasil disimpan.")
                st.session_state.scan_result = ""
                st.rerun()
            except Exception as e:
                st.error(str(e))

    with right:
        st.markdown("### Preview barang")
        code = (st.session_state.get("scan_result") or "").strip()
        if code:
            item = get_item_by_barcode(code)
            if item:
                st.info(f"{item['nama_barang']} | stok {item['stok']} {item['satuan']} | rak {item.get('lokasi_rak') or '-'}")
                st.json({
                    "barcode": item["barcode"],
                    "nama_barang": item["nama_barang"],
                    "kategori": item["kategori"],
                    "stok": item["stok"],
                    "stok_minimum": item["stok_minimum"],
                    "lokasi_rak": item.get("lokasi_rak") or ""
                })
            else:
                st.warning("Barcode belum terdaftar di master barang.")

        st.markdown("### 12 transaksi terakhir")
        latest = get_transactions_df(date.today() - timedelta(days=30), date.today()).head(12)
        st.dataframe(latest, hide_index=True, use_container_width=True)


def items_page(items_df):
    st.subheader("Master barang")

    with st.form("form_items"):
        a, b = st.columns(2)
        with a:
            barcode = st.text_input("Barcode barang")
            nama_barang = st.text_input("Nama barang")
            kategori = st.selectbox("Kategori", ["ATK", "Kebersihan", "Konsumsi", "Lainnya"])
            satuan = st.text_input("Satuan", value="pcs")
        with b:
            stok = st.number_input("Stok", min_value=0, step=1)
            stok_minimum = st.number_input("Stok minimum", min_value=0, step=1)
            lokasi_rak = st.text_input("Lokasi rak")
            keterangan = st.text_area("Keterangan")

        submit = st.form_submit_button("Simpan / update barang", use_container_width=True)

    if submit:
        if not barcode.strip() or not nama_barang.strip():
            st.error("Barcode dan nama barang wajib diisi.")
        else:
            upsert_item({
                "barcode": barcode.strip(),
                "nama_barang": nama_barang.strip(),
                "kategori": kategori,
                "satuan": satuan.strip() or "pcs",
                "stok": int(stok),
                "stok_minimum": int(stok_minimum),
                "lokasi_rak": lokasi_rak.strip(),
                "keterangan": keterangan.strip(),
            })
            st.success("Data barang berhasil disimpan.")
            st.rerun()

    st.dataframe(items_df, hide_index=True, use_container_width=True)

    if not items_df.empty:
        st.download_button(
            "Unduh master barang (CSV)",
            items_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="master_barang_kppn_sungai_penuh.csv",
            mime="text/csv"
        )


def reports_page():
    st.subheader("Laporan")

    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Dari tanggal", value=date.today().replace(day=1), key="report_start")
    with c2:
        end_date = st.date_input("Sampai tanggal", value=date.today(), key="report_end")

    trx_df = get_transactions_df(start_date, end_date)
    st.dataframe(trx_df, hide_index=True, use_container_width=True)

    if trx_df.empty:
        st.info("Belum ada transaksi pada periode tersebut.")
        return

    rekap_barang = trx_df.groupby(["barcode", "nama_barang"], as_index=False)["qty"].sum()
    rekap_barang = rekap_barang.sort_values("qty", ascending=False)

    rekap_unit = trx_df.groupby(["unit_tujuan"], dropna=False, as_index=False)["qty"].sum()
    rekap_unit = rekap_unit.sort_values("qty", ascending=False)

    left, right = st.columns(2)
    with left:
        st.markdown("### Rekap per barang")
        st.dataframe(rekap_barang, hide_index=True, use_container_width=True)
    with right:
        st.markdown("### Rekap per unit")
        st.dataframe(rekap_unit, hide_index=True, use_container_width=True)

    st.download_button(
        "Unduh transaksi (CSV)",
        trx_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="laporan_transaksi_keluar.csv",
        mime="text/csv"
    )

    st.download_button(
        "Unduh rekap barang (CSV)",
        rekap_barang.to_csv(index=False).encode("utf-8-sig"),
        file_name="rekap_pemakaian_barang.csv",
        mime="text/csv"
    )


def users_page():
    st.subheader("Manajemen pengguna")

    with st.form("form_users"):
        a, b = st.columns(2)
        with a:
            username = st.text_input("Username baru")
            full_name = st.text_input("Nama lengkap")
        with b:
            role = st.selectbox("Role", ["admin", "petugas", "pimpinan"])
            password = st.text_input("Password awal", type="password")
        submit = st.form_submit_button("Tambah pengguna", use_container_width=True)

    if submit:
        if not username.strip() or not full_name.strip() or not password:
            st.error("Semua field wajib diisi.")
        else:
            try:
                create_user(username, full_name, role, password)
                st.success("Pengguna berhasil ditambahkan.")
                st.rerun()
            except IntegrityError:
                st.error("Username sudah dipakai.")

    df = get_users_df()
    st.dataframe(df, hide_index=True, use_container_width=True)


def main():
    init_session()

    if not st.session_state.logged_in:
        login_screen()
        return

    user = st.session_state.user
    keyword = sidebar_area()

    items_df = get_items_df(keyword)
    trx_df = get_transactions_df(date.today() - timedelta(days=365), date.today())

    st.title("Aplikasi Gudang Inventaris KPPN Sungai Penuh")
    st.caption("Versi 2: login, PostgreSQL, dashboard bulanan, dan scan barcode/QR mobile-friendly.")

    menu = ["Dashboard", "Barang Keluar", "Master Barang", "Laporan"]
    if user["role"] == "admin":
        menu.append("Pengguna")
    if user["role"] == "pimpinan":
        menu = ["Dashboard", "Laporan"]

    selected = st.radio("Pilih menu", menu, horizontal=True)

    if selected == "Dashboard":
        dashboard_page(items_df, trx_df)
    elif selected == "Barang Keluar":
        outgoing_page(user)
    elif selected == "Master Barang":
        items_page(items_df)
    elif selected == "Laporan":
        reports_page()
    elif selected == "Pengguna":
        users_page()

    st.divider()
    st.caption("Gunakan PostgreSQL untuk produksi dan akses aplikasi dari browser ponsel petugas.")


if __name__ == "__main__":
    main()
