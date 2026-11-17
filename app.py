import hmac
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from supabase import create_client

# =========================================================
# SIA BUDIDAYA LELE
# =========================================================
st.set_page_config(page_title="Lele Segar 🐠", page_icon="🐠", layout="wide")

KAS, PIUTANG, UTANG, MODAL, JUAL = "1110", "1120", "2110", "3110", "4110"
KOLAM_AKUN, DP_AKUN = "1230", "2120"            # Ikan Dalam Pemeliharaan, Uang Muka Pelanggan
HPP, RUGI_MATI, UPAH, LISTRIK = "5150", "5160", "5170", "5180"
AKUN_BELI = {"Pakan": "1210", "Bibit": "1220", "Operasional": "5130"}
AKUN_PAKAI = {"Pakan": ("5110", "1210"), "Bibit": ("5120", "1220")}
PAGES = ["Dashboard", "Pembelian", "Penjualan", "Budidaya", "Peralatan",
         "Akses Cepat", "Data Master", "Periode", "Riwayat"]
AKSES = ["Jurnal Umum", "Buku Besar", "Buku Pembantu", "Neraca Saldo", "Neraca",
         "Laba Rugi", "Laba per Kolam", "Arus Kas"]

KOLAM = [f"KLM-{i:02d}" for i in range(1, 16)]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]
STOK_MIN = {"Pakan": 150, "Bibit": 0}   # batas peringatan stok (Kg / Ekor)
LAMA_PELIHARA = {"Kemarau": 90, "Hujan": 60}  # hari
ZONA = ZoneInfo("Asia/Jakarta")

MAKS_GAGAL = 5       # jumlah salah login sebelum dikunci sementara
DETIK_KUNCI = 60     # lama penguncian (detik)

# Satuan baku: uang = Rp, berat pakan/ikan = Kg, bibit = Ekor
SATUAN_KAT = {"Pakan": "Kg", "Bibit": "Ekor"}
SATUAN_OPSI = ["Kg", "Ekor", "Unit", "Sak"]
_SATUAN_RAPI = {"kg": "Kg", "ekor": "Ekor", "unit": "Unit", "sak": "Sak"}

AKUN_DEFAULT = [
    {"kode": "1110", "nama": "Kas", "tipe": "Aset"},
    {"kode": "1120", "nama": "Piutang Usaha", "tipe": "Aset"},
    {"kode": "1210", "nama": "Persediaan Pakan", "tipe": "Aset"},
    {"kode": "1220", "nama": "Persediaan Bibit Lele", "tipe": "Aset"},
    {"kode": "1230", "nama": "Ikan Dalam Pemeliharaan", "tipe": "Aset"},
    {"kode": "1310", "nama": "Peralatan", "tipe": "Aset"},
    {"kode": "1311", "nama": "Akumulasi Penyusutan Peralatan", "tipe": "Aset"},
    {"kode": "2110", "nama": "Utang Usaha", "tipe": "Kewajiban"},
    {"kode": "2120", "nama": "Uang Muka Pelanggan (DP)", "tipe": "Kewajiban"},
    {"kode": "3110", "nama": "Modal Pemilik", "tipe": "Ekuitas"},
    {"kode": "4110", "nama": "Penjualan Lele", "tipe": "Pendapatan"},
    {"kode": "5110", "nama": "Beban Pakan", "tipe": "Beban"},
    {"kode": "5120", "nama": "Beban Bibit", "tipe": "Beban"},
    {"kode": "5130", "nama": "Beban Operasional", "tipe": "Beban"},
    {"kode": "5140", "nama": "Beban Penyusutan", "tipe": "Beban"},
    {"kode": "5150", "nama": "Harga Pokok Penjualan", "tipe": "Beban"},
    {"kode": "5160", "nama": "Kerugian Ikan Mati", "tipe": "Beban"},
    {"kode": "5170", "nama": "Beban Upah Panen", "tipe": "Beban"},
    {"kode": "5180", "nama": "Beban Listrik dan Air", "tipe": "Beban"},
]

KOLOM_KOSONG = {
    "accounts": ["kode", "nama", "tipe"],
    "journals": ["id", "tanggal", "keterangan", "ref_type"],
    "journal_lines": ["id", "journal_id", "kode_akun", "debit", "kredit", "kolam"],
    "purchases": ["id", "tanggal", "supplier", "kategori", "item", "qty", "satuan",
                  "harga", "total", "metode", "jatuh_tempo", "lunas",
                  "batal", "alasan_batal", "journal_id"],
    "sales": ["id", "tanggal", "pembeli", "kg", "harga_kg", "total", "metode", "lunas",
              "batal", "alasan_batal", "journal_id", "kolam", "dp_dipakai"],
    "stock_moves": ["id", "tanggal", "kategori", "item", "qty"],
    "equipment": ["id", "nama", "tanggal", "harga", "umur_bulan", "residu"],
    "cycles": ["id", "kolam", "tgl_tebar", "jumlah_bibit", "musim", "perkiraan_panen",
               "status", "tgl_panen", "kg_panen", "penjualan", "hpp"],
    "fish_deaths": ["id", "tanggal", "kolam", "jumlah"],
    "down_payments": ["id", "tanggal", "pembeli", "jumlah", "sisa", "journal_id"],
    "periods": ["bulan", "tgl_tutup", "penjualan", "laba", "kas"],
    "activity_log": ["id", "waktu", "pengguna", "aksi", "objek", "detail"],
    "suppliers": ["id", "nama", "jenis_pasokan", "daerah", "kontak", "termin", "catatan", "aktif"],
    "customers": ["id", "nama", "kategori", "kontak", "aturan_dp", "catatan", "aktif"],
    "items": ["id", "nama", "kategori", "satuan", "harga_ref", "stok_min", "aktif"],
}


# ------------------------- Konfigurasi -------------------------
def baca_config(*kunci):
    """Membaca nilai dari Streamlit secrets atau kunci.toml."""
    hasil = {k: None for k in kunci}
    try:
        for k in kunci:
            hasil[k] = st.secrets.get(k)
    except Exception:
        pass

    if any(not hasil[k] for k in kunci):
        try:
            import tomllib
            with open("kunci.toml", "rb") as f:
                cfg = tomllib.load(f)
            for k in kunci:
                if not hasil[k]:
                    hasil[k] = cfg.get(k)
        except FileNotFoundError:
            pass
        except Exception as e:
            raise RuntimeError(f"Gagal membaca kunci.toml: {e}")
    return hasil


# ------------------------- Koneksi & database -------------------------
@st.cache_resource
def db():
    """Membaca kredensial dari Streamlit secrets atau kunci.toml."""
    try:
        cfg = baca_config("SUPABASE_URL", "SUPABASE_KEY")
    except RuntimeError:
        raise
    url, key = cfg["SUPABASE_URL"], cfg["SUPABASE_KEY"]

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL atau SUPABASE_KEY belum diisi. Periksa kunci.toml "
            "(di folder yang sama dengan app.py) atau .streamlit/secrets.toml."
        )
    return create_client(str(url).split("/rest/v1")[0].rstrip("/"), str(key))



# ------------------------- Penyimpanan tambahan (tanpa SQL) -------------------------
# Data baru disimpan di folder data_lokal (berkas JSON). Tabel lama di Supabase
# tidak diubah: kolom tambahan (batal, journal_id, kolam, dp_dipakai) disimpan
# terpisah dan digabung saat dibaca.
DATA_LOKAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_lokal")
LOKAL = {"cycles", "fish_deaths", "down_payments", "periods", "activity_log",
         "suppliers", "customers", "items"}
EKSTRA = {
    "purchases": {"batal", "alasan_batal", "journal_id"},
    "sales": {"batal", "alasan_batal", "journal_id", "kolam", "dp_dipakai"},
    "journal_lines": {"kolam"},
}


def _baca(nama, default):
    p = os.path.join(DATA_LOKAL, nama + ".json")
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _tulis(nama, data):
    """Tulis ke berkas sementara lalu ganti, supaya tidak ada data setengah tersimpan."""
    os.makedirs(DATA_LOKAL, exist_ok=True)
    p = os.path.join(DATA_LOKAL, nama + ".json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, p)


class _Hasil:
    def __init__(self, data):
        self.data = data


class _Query:
    """Meniru cara pakai supabase: select/insert/update/delete/eq/order/range/execute."""

    def __init__(self, nama):
        self.nama, self.op, self.sel, self.payload = nama, "select", "*", None
        self.filters, self.rng, self.urut = [], None, None

    def select(self, kolom="*"):
        self.op, self.sel = "select", kolom
        return self

    def insert(self, p):
        self.op, self.payload = "insert", p
        return self

    def update(self, p):
        self.op, self.payload = "update", p
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def order(self, k, **kw):
        self.urut = k
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        if self.nama in LOKAL:
            return _Hasil(self._lokal())
        return _Hasil(self._ekstra())

    def _cocok(self, r):
        return all(str(r.get(k)) == str(v) for k, v in self.filters)

    def _lokal(self):
        rows = _baca(self.nama, [])
        if self.op == "select":
            out = [r for r in rows if self._cocok(r)]
            if self.urut:
                out.sort(key=lambda r: (r.get(self.urut) is None, r.get(self.urut)))
            if self.rng:
                out = out[self.rng[0]:self.rng[1] + 1]
            return [dict(r) for r in out]
        if self.op == "insert":
            baru = self.payload if isinstance(self.payload, list) else [self.payload]
            nid = max([r["id"] for r in rows if isinstance(r.get("id"), int)] or [0])
            hasil = []
            for p in baru:
                r = dict(p)
                if self.nama != "periods":
                    nid += 1
                    r["id"] = nid
                rows.append(r)
                hasil.append(dict(r))
            _tulis(self.nama, rows)
            return hasil
        if self.op == "update":
            hasil = []
            for r in rows:
                if self._cocok(r):
                    r.update(self.payload)
                    hasil.append(dict(r))
            _tulis(self.nama, rows)
            return hasil
        sisa = [r for r in rows if not self._cocok(r)]
        _tulis(self.nama, sisa)
        return []

    def _ekstra(self):
        kol = EKSTRA[self.nama]
        ext = _baca("ekstra_" + self.nama, {})
        tb = db().table(self.nama)
        if self.op == "select":
            q = tb.select(self.sel)
            for k, v in self.filters:
                q = q.eq(k, v)
            if self.urut:
                q = q.order(self.urut)
            if self.rng:
                q = q.range(*self.rng)
            data = q.execute().data or []
            for r in data:
                r.update(ext.get(str(r.get("id")), {}))
            return data
        if self.op == "insert":
            daftar = self.payload if isinstance(self.payload, list) else [self.payload]
            real = [{k: v for k, v in p.items() if k not in kol} for p in daftar]
            extra = [{k: v for k, v in p.items() if k in kol} for p in daftar]
            data = tb.insert(real if isinstance(self.payload, list) else real[0]).execute().data or []
            for r, e in zip(data, extra):
                if e:
                    ext[str(r["id"])] = e
            if any(extra):
                _tulis("ekstra_" + self.nama, ext)
            return data
        if self.op == "update":
            real = {k: v for k, v in self.payload.items() if k not in kol}
            e = {k: v for k, v in self.payload.items() if k in kol}
            ids = [v for k, v in self.filters if k == "id"]
            if real:
                q = tb.update(real)
                for k, v in self.filters:
                    q = q.eq(k, v)
                q.execute()
            if e and ids:
                ext.setdefault(str(ids[0]), {}).update(e)
                _tulis("ekstra_" + self.nama, ext)
            return []
        # delete
        if any(k in kol for k, _ in self.filters):
            ids = [i for i, e in ext.items()
                   if all(str(e.get(k)) == str(v) for k, v in self.filters)]
            for i in ids:
                tb.delete().eq("id", int(i)).execute()
                ext.pop(i, None)
            _tulis("ekstra_" + self.nama, ext)
        else:
            q = tb.delete()
            for k, v in self.filters:
                q = q.eq(k, v)
            q.execute()
            for k, v in self.filters:
                if k == "id":
                    ext.pop(str(v), None)
            _tulis("ekstra_" + self.nama, ext)
        return []


def tabel(nama):
    if nama in LOKAL or nama in EKSTRA:
        return _Query(nama)
    return db().table(nama)


def ambil_semua(query_fn, batas=1000):
    """Mengambil seluruh baris dengan pagination."""
    hasil, mulai = [], 0
    while True:
        data = query_fn().range(mulai, mulai + batas - 1).execute().data or []
        hasil.extend(data)
        if len(data) < batas:
            return hasil
        mulai += batas


def n(v):
    """Angka aman: None/NaN jadi 0.0."""
    try:
        return 0.0 if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return 0.0


def tbl(nama, semua=False):
    """Baca tabel. Untuk purchases/sales, transaksi dibatalkan disembunyikan
    kecuali semua=True."""
    if nama not in KOLOM_KOSONG:
        raise ValueError(f"Nama tabel tidak dikenal: {nama}")
    urut = {"accounts": "kode", "periods": "bulan"}.get(nama, "id")
    data = ambil_semua(lambda: tabel(nama).select("*").order(urut))
    # DataFrame kosong tetap memiliki kolom yang benar agar tidak memicu KeyError.
    df = pd.DataFrame(data) if data else pd.DataFrame(columns=KOLOM_KOSONG[nama])
    for k in KOLOM_KOSONG[nama]:
        if k not in df:
            df[k] = None
    if nama in ("purchases", "sales"):
        if "batal" not in df:
            df["batal"] = False
        df["batal"] = df["batal"].fillna(False).astype(bool)
        if nama == "sales":
            if "dp_dipakai" not in df:
                df["dp_dipakai"] = 0.0
            df["dp_dipakai"] = pd.to_numeric(df["dp_dipakai"], errors="coerce").fillna(0)
            df["piutang"] = pd.to_numeric(df["total"], errors="coerce").fillna(0) - df["dp_dipakai"]
        if not semua:
            df = df[~df["batal"]]
    return df


def belum_lunas(df):
    if df is None or df.empty:
        return df
    return df[~df["lunas"].fillna(False).astype(bool)]


def beri_status(df):
    if df is None or df.empty:
        return df
    d = df.copy()
    d["status"] = d["batal"].map(lambda b: "DIBATALKAN" if b else "Diposting")
    return d


def pastikan_akun():
    """Isi akun standar yang belum ada (aman untuk akun yang sudah ada)."""
    data = ambil_semua(lambda: tabel("accounts").select("*").order("kode"))
    ada = {d.get("kode") for d in data}
    baru = [x for x in AKUN_DEFAULT if x["kode"] not in ada]
    if baru:
        tabel("accounts").upsert(baru, on_conflict="kode").execute()
        data = ambil_semua(lambda: tabel("accounts").select("*").order("kode"))
    if not data:
        raise RuntimeError(
            "Tabel public.accounts terbaca kosong. Pastikan SQL schema sudah dijalankan "
            "pada project Supabase yang sama dengan URL di kunci.toml, lalu periksa "
            "Table Editor > accounts."
        )
    kolom = set(data[0].keys())
    wajib = {"kode", "nama", "tipe"}
    if not wajib.issubset(kolom):
        raise RuntimeError(
            "Struktur tabel accounts tidak sesuai. Kolom yang wajib ada: kode, nama, tipe. "
            f"Kolom yang terbaca: {', '.join(sorted(kolom))}"
        )


# ------------------------- Format angka, rupiah, dan satuan -------------------------
def angka(x):
    """Bilangan bulat dengan titik pemisah ribuan, tanpa desimal: 6000000 -> 6.000.000."""
    try:
        if x is None or pd.isna(x):
            x = 0
        return f"{int(round(float(x))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def rp(x):
    """Format rupiah: Rp 1.234.567 (negatif: Rp -1.234)."""
    return "Rp " + angka(x)


def satuan_rapi(s):
    """Satuan ditulis baku: kg -> Kg, ekor -> Ekor."""
    if s is None:
        return ""
    try:
        if pd.isna(s):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(s).strip()
    return _SATUAN_RAPI.get(s.lower(), s)


def fmt_qty(v, sat=""):
    """Jumlah dengan satuan: 1500 -> '1.500 Kg'. Kosong ditampilkan '-'."""
    try:
        if v is None or pd.isna(v):
            return "-"
    except (TypeError, ValueError):
        return "-"
    s = satuan_rapi(sat)
    return f"{angka(v)} {s}".strip()


def ke_angka(teks_input):
    """Ubah isian teks jadi bilangan bulat. Titik dianggap pemisah ribuan,
    bagian setelah koma dibuang, sehingga '6.000.000' dan '6000000' sama-sama 6000000."""
    s = str(teks_input or "").split(",")[0]
    s = re.sub(r"\D", "", s)
    return int(s) if s else 0


def input_angka(label, key, nilai=0, kolom=None):
    """Kolom isian bilangan bulat. Titik ribuan muncul otomatis saat diketik
    (lewat skrip kecil di pasang_pemformat). Hasilnya selalu int tanpa desimal."""
    kolom = kolom or st
    awal = angka(nilai) if nilai else ""
    return ke_angka(kolom.text_input(label, value=awal, placeholder="0", key=key))


_JS_FORMAT = r"""
<script>
(function () {
  var doc = window.parent.document;
  if (doc.getElementById('fmt-angka-skrip')) return;
  var kode = '(' + function () {
    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    function titik(v) {
      var d = String(v).split(',')[0].replace(/\D/g, '');
      return d.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    }
    document.addEventListener('input', function (e) {
      var el = e.target;
      if (!el || el.tagName !== 'INPUT' || el.getAttribute('placeholder') !== '0') return;
      var lama = el.value, f = titik(lama);
      if (f === lama) return;
      var pos = el.selectionStart || 0;
      var nd = lama.slice(0, pos).replace(/\D/g, '').length;
      setter.call(el, f);
      var np = 0, c = 0;
      while (np < f.length && c < nd) { if (/\d/.test(f.charAt(np))) c++; np++; }
      try { el.setSelectionRange(np, np); } catch (x) {}
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }, true);
  }.toString() + ')();';
  var s = doc.createElement('script');
  s.id = 'fmt-angka-skrip';
  s.textContent = kode;
  doc.head.appendChild(s);
})();
</script>
"""


def pasang_pemformat():
    """Pasang pemformat titik ribuan sekali per halaman. Jika gagal, isian tetap
    bisa dipakai (titik akan diabaikan saat dibaca)."""
    try:
        components.html(_JS_FORMAT, height=0)
    except Exception:
        pass


def fmt_rp(df, kolom):
    """Salinan DataFrame dengan kolom nominal diubah ke format Rp + titik ribuan.
    Hanya untuk tampilan; data aslinya tidak diubah."""
    if df is None or df.empty:
        return df
    d = df.copy()
    for k in kolom:
        if k in d.columns:
            d[k] = d[k].map(rp)
    return d


def fmt_qty_df(df, kolom, sat="", sat_kolom=None, buang_sat=False):
    """Salinan DataFrame dengan kolom jumlah diberi titik ribuan dan satuan (Kg, Ekor).
    Satuan diambil dari kolom sat_kolom bila ada, jika tidak dari sat."""
    if df is None or df.empty:
        return df
    d = df.copy()
    for k in kolom:
        if k not in d.columns:
            continue
        if sat_kolom and sat_kolom in d.columns:
            d[k] = [fmt_qty(v, s) for v, s in zip(d[k], d[sat_kolom])]
        else:
            d[k] = d[k].map(lambda v: fmt_qty(v, sat))
    if buang_sat and sat_kolom in d.columns:
        d = d.drop(columns=[sat_kolom])
    return d


def fmt_qty_kategori(df, kolom="qty", kat_kolom="kategori"):
    """Jumlah stok dengan satuan menurut kategori (Pakan = Kg, Bibit = Ekor)."""
    if df is None or df.empty:
        return df
    d = df.copy()
    d[kolom] = [fmt_qty(v, SATUAN_KAT.get(k, "")) for v, k in zip(d[kolom], d[kat_kolom])]
    return d


def lines():
    rows = []
    data = ambil_semua(
        lambda: tabel("journal_lines")
        .select("*, journals(tanggal,keterangan,ref_type)")
        .order("id")
    )
    for item in data:
        item = dict(item)
        j = item.pop("journals", None) or {}
        rows.append({**item, **j})
    df = pd.DataFrame(
        rows,
        columns=["id", "journal_id", "kode_akun", "debit", "kredit", "kolam",
                 "tanggal", "keterangan", "ref_type"]
    )
    df[["debit", "kredit"]] = df[["debit", "kredit"]].apply(
        pd.to_numeric, errors="coerce"
    ).fillna(0)
    return df


def filter_tgl(x, dari=None, sampai=None):
    if x.empty or (not dari and not sampai):
        return x
    t = x["tanggal"].astype(str)
    mask = pd.Series(True, index=x.index)
    if dari:
        mask &= t >= str(dari)
    if sampai:
        mask &= t <= str(sampai)
    return x[mask]


# ------------------------- Periode -------------------------
def nama_bulan(key):
    y, m = key.split("-")
    return f"{BULAN[int(m) - 1]} {y}"


def rentang(key):
    y, m = map(int, key.split("-"))
    awal = date(y, m, 1)
    akhir = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return awal, akhir


def _hitung_periode():
    p = tbl("periods")
    if p.empty:
        return date.today().strftime("%Y-%m")
    y, m = map(int, max(p["bulan"]).split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y}-{m:02d}"


def periode_aktif():
    """Periode terbuka = bulan setelah bulan terakhir yang ditutup."""
    if st.session_state.get("_per") is None:
        st.session_state["_per"] = _hitung_periode()
    return st.session_state["_per"]


def tgl_default():
    a, b = rentang(periode_aktif())
    return min(max(date.today(), a), b)


def tgl_input(label, **kw):
    """Input tanggal yang dibatasi ke periode aktif."""
    a, b = rentang(periode_aktif())
    return st.date_input(label, tgl_default(), min_value=a, max_value=b, **kw)


def cek_periode(tgl):
    key = periode_aktif()
    a, b = rentang(key)
    d = tgl if isinstance(tgl, date) else date.fromisoformat(str(tgl)[:10])
    if not (a <= d <= b):
        raise ValueError(f"Tanggal {d} di luar periode aktif ({nama_bulan(key)}).")


def pilih_bulan(key, label="Periode"):
    x = lines()
    bulan = {str(t)[:7] for t in x["tanggal"].dropna()} | {periode_aktif()}
    p = st.selectbox(label, ["Semua"] + sorted(bulan, reverse=True), key=key,
                     format_func=lambda v: v if v == "Semua" else nama_bulan(v))
    return (None, None) if p == "Semua" else rentang(p)


# ------------------------- Riwayat aktivitas -------------------------
def catat(aksi, objek="", detail="", pengguna=None):
    """Catat aktivitas. Tidak pernah menyimpan PIN. Gagal mencatat tidak boleh
    menghentikan aplikasi."""
    try:
        tabel("activity_log").insert({
            "waktu": datetime.now(ZONA).isoformat(),
            "pengguna": pengguna or st.session_state.get("user", "-"),
            "aksi": aksi, "objek": str(objek), "detail": str(detail),
        }).execute()
    except Exception:
        pass


# ------------------------- Login -------------------------
def sama(a, b):
    """Perbandingan string yang aman dari timing attack."""
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def halaman_login():
    cfg = baca_config("APP_USERNAME", "APP_PIN")
    user_benar, pin_benar = cfg["APP_USERNAME"], cfg["APP_PIN"]

    st.write("")
    st.write("")
    kiri, kanan = st.columns([1.15, 1], gap="large", vertical_alignment="center")

    with kiri:
        st.markdown("# Lele Segar 🐠")
        st.markdown("##### Catatan keuangan budidaya lele, dari kolam sampai laporan.")
        st.write("")
        st.markdown(
            ":material/water: &nbsp; Pantau kolam dan jadwal panen  \n"
            ":material/receipt_long: &nbsp; Jurnal tercatat otomatis  \n"
            ":material/query_stats: &nbsp; Laba rugi dan neraca siap lihat"
        )
        st.write("")
        st.caption(":material/location_on: Jl. Nongkosawit Raya, Nongkosawit, "
                   "Kecamatan Gunung Pati, Kota Semarang, Jawa Tengah, Indonesia")

    with kanan:
        with st.container(border=True):
            st.subheader("Masuk ke akun")

            if not user_benar or not pin_benar:
                st.error(
                    "Akun login belum dikonfigurasi. Tambahkan APP_USERNAME dan APP_PIN "
                    "di kunci.toml atau .streamlit/secrets.toml."
                )
                return

            kunci_sampai = st.session_state.get("kunci_sampai", 0)
            sisa = int(kunci_sampai - time.time())
            if sisa > 0:
                st.error(f"Terlalu banyak percobaan gagal. Coba lagi dalam {sisa} detik.")
                return

            with st.form("form_login", border=False):
                username = st.text_input("Username", icon=":material/person:")
                pin = st.text_input("PIN", type="password", icon=":material/key:")
                masuk = st.form_submit_button("Masuk", type="primary", use_container_width=True)

            if masuk:
                ok_user = sama(username.strip(), user_benar)
                ok_pin = sama(pin, pin_benar)
                if ok_user and ok_pin:
                    st.session_state.login = True
                    st.session_state.user = username.strip()
                    st.session_state.gagal = 0
                    catat("login_berhasil", pengguna=username.strip())
                    st.rerun()
                else:
                    gagal = st.session_state.get("gagal", 0) + 1
                    st.session_state.gagal = gagal
                    catat("login_gagal", detail=f"percobaan ke-{gagal}", pengguna="-")
                    if gagal >= MAKS_GAGAL:
                        st.session_state.kunci_sampai = time.time() + DETIK_KUNCI
                        st.session_state.gagal = 0
                        catat("akun_dikunci", detail=f"{DETIK_KUNCI} detik", pengguna="-")
                        st.error(f"Terlalu banyak percobaan gagal. Terkunci {DETIK_KUNCI} detik.")
                    else:
                        st.error(f"Username atau PIN salah. Sisa percobaan: {MAKS_GAGAL - gagal}.")


def logout():
    catat("logout")
    for k in ("login", "user", "page", "ak", "_per"):
        st.session_state.pop(k, None)


# ------------------------- Jurnal -------------------------
def hapus_jurnal(jurnal_id):
    tabel("journal_lines").delete().eq("journal_id", jurnal_id).execute()
    tabel("journals").delete().eq("id", jurnal_id).execute()


def rollback(jid, nama_tbl=None):
    """Bersihkan jurnal (dan baris transaksi terkait) bila penyimpanan gagal."""
    try:
        if nama_tbl:
            tabel(nama_tbl).delete().eq("journal_id", jid).execute()
        hapus_jurnal(jid)
    except Exception:
        pass


def jurnal(tgl, ket, baris, ref=None):
    """Membuat jurnal berpasangan; total debit harus sama dengan kredit.
    Baris: (akun, debit, kredit) atau (akun, debit, kredit, kolam).
    Tanggal harus di dalam periode aktif. Mengembalikan id jurnal."""
    cek_periode(tgl)
    baris = [(str(b[0]), float(b[1]), float(b[2]), b[3] if len(b) > 3 else None)
             for b in baris]
    total_debit = round(sum(x[1] for x in baris), 2)
    total_kredit = round(sum(x[2] for x in baris), 2)
    if total_debit != total_kredit:
        raise ValueError(
            f"Jurnal tidak seimbang: debit {rp(total_debit)}, kredit {rp(total_kredit)}."
        )
    hasil = tabel("journals").insert({
        "tanggal": str(tgl), "keterangan": ket, "ref_type": ref
    }).execute().data
    if not hasil:
        raise RuntimeError("Supabase tidak mengembalikan data jurnal yang baru dibuat.")
    jurnal_id = hasil[0]["id"]
    try:
        isi = []
        for a, d, k, kol in baris:
            row = {"journal_id": jurnal_id, "kode_akun": a, "debit": d, "kredit": k}
            if kol:
                row["kolam"] = kol
            isi.append(row)
        tabel("journal_lines").insert(isi).execute()
    except Exception:
        tabel("journals").delete().eq("id", jurnal_id).execute()
        raise
    return jurnal_id


def saldo(dari=None, sampai=None):
    akun = tbl("accounts")
    if akun.empty:
        raise RuntimeError("Daftar akun kosong. Periksa tabel accounts di Supabase.")
    akun = akun.set_index("kode")
    l = filter_tgl(lines(), dari, sampai)
    if l.empty:
        akun["debit"] = 0.0
        akun["kredit"] = 0.0
    else:
        jumlah = l.groupby("kode_akun")[["debit", "kredit"]].sum()
        akun = akun.join(jumlah).fillna({"debit": 0.0, "kredit": 0.0})
    dk = akun["debit"] - akun["kredit"]
    akun["saldo"] = dk.where(akun["tipe"].isin(["Aset", "Beban"]), -dk)
    return akun


def laba_bersih(a):
    return float(a.loc[a.tipe == "Pendapatan", "saldo"].sum()
                 - a.loc[a.tipe == "Beban", "saldo"].sum())


# ---- Navigasi (REVISI TAMPILAN) ----
PAGE_OBJ = {}   # diisi oleh main(): nama menu -> objek st.Page


def go(page, akses=None):
    """Pindah halaman (dipakai tombol pintasan di Dashboard)."""
    if akses:
        st.session_state.ak = akses
    st.switch_page(PAGE_OBJ[page])


def sudah_susut(key):
    j = tbl("journals")
    return (not j.empty) and (j["ref_type"] == f"susut:{key}").any()


# ------------------------- Stok & budidaya (logika) -------------------------
def item_stok(kat):
    sm = tbl("stock_moves")
    if sm.empty:
        return []
    return sorted(sm.loc[sm.kategori == kat, "item"].dropna().unique().tolist())


def pakai_stok(tgl, kat, item, jml, kolam=None, awalan=""):
    """Catat pemakaian stok (harga rata-rata, dibulatkan ke Rupiah). Jika kolam diisi,
    biaya masuk ke Ikan Dalam Pemeliharaan kolam itu; jika tidak, ke beban."""
    sm = tbl("stock_moves")
    tersedia = float(pd.to_numeric(
        sm.loc[(sm.kategori == kat) & (sm["item"] == item), "qty"], errors="coerce"
    ).fillna(0).sum()) if not sm.empty else 0.0
    pb = tbl("purchases")
    pb = pb[(pb.kategori == kat) & (pb["item"] == item)]
    if jml <= 0:
        raise ValueError("Jumlah pemakaian harus lebih dari nol.")
    if jml > tersedia:
        raise ValueError(f"Stok hanya tersedia {fmt_qty(tersedia, SATUAN_KAT.get(kat, ''))}.")
    if pb.empty or float(pb.qty.sum() or 0) <= 0:
        raise ValueError("Data pembelian untuk item ini tidak ditemukan.")
    hpp = round(float(pb.total.sum()) / float(pb.qty.sum()) * jml, 0)
    beban, persed = AKUN_PAKAI[kat]
    debit_akun = KOLAM_AKUN if kolam else beban
    ket = f"{awalan}Pakai {item}" + (f" ({kolam})" if kolam else "")
    jid = jurnal(tgl, ket, [(debit_akun, hpp, 0, kolam), (persed, 0, hpp)], "pakai")
    try:
        tabel("stock_moves").insert({
            "tanggal": str(tgl), "kategori": kat, "item": item, "qty": -jml
        }).execute()
    except Exception:
        rollback(jid)
        raise
    return hpp


def tabel_kolam():
    """Status tiap kolam, dihitung dari siklus yang berjalan."""
    c = tbl("cycles")
    c = c[c.status == "berjalan"] if not c.empty else c
    x, m = lines(), tbl("fish_deaths")
    rows = []
    for k in KOLAM:
        r = c[c.kolam == k] if not c.empty else c
        if r.empty:
            rows.append({"kolam": k, "status": "Kosong", "tgl_tebar": None, "bibit": None,
                         "mati": None, "perkiraan_panen": None, "biaya_berjalan": 0.0})
            continue
        r = r.iloc[0]
        mati = 0.0
        if not m.empty:
            mm = m[(m.kolam == k) & (m.tanggal.astype(str) >= str(r.tgl_tebar))]
            mati = float(pd.to_numeric(mm.jumlah, errors="coerce").fillna(0).sum())
        kk = x[(x.kolam == k) & (x.kode_akun == KOLAM_AKUN)]
        biaya = float(kk.debit.sum() - kk.kredit.sum())
        status = "Pemeliharaan"
        if pd.notna(r.perkiraan_panen):
            if (pd.to_datetime(r.perkiraan_panen).date() - date.today()).days <= 3:
                status = "Siap panen"
        rows.append({"kolam": k, "status": status, "tgl_tebar": r.tgl_tebar,
                     "bibit": n(r.jumlah_bibit), "mati": mati,
                     "perkiraan_panen": r.perkiraan_panen, "biaya_berjalan": biaya})
    return pd.DataFrame(rows)


def laba_per_kolam():
    """Laba per siklus yang sudah panen: penjualan - HPP - upah panen."""
    c = tbl("cycles")
    if c.empty:
        return pd.DataFrame()
    sem = c.copy()
    c = c[c.status == "selesai"]
    if c.empty:
        return pd.DataFrame()
    x = lines()
    rows = []
    for _, r in c.iterrows():
        lain = sem[(sem.kolam == r.kolam) & (sem.tgl_tebar.astype(str) > str(r.tgl_tebar))]
        u = x[(x.kolam == r.kolam) & (x.kode_akun == UPAH)
              & (x.tanggal.astype(str) >= str(r.tgl_tebar))]
        if not lain.empty:   # upah dihitung sampai sebelum siklus berikutnya di kolam yang sama
            u = u[u.tanggal.astype(str) < str(lain.tgl_tebar.astype(str).min())]
        upah = float(u.debit.sum() - u.kredit.sum())
        pj, hp, kg = n(r.penjualan), n(r.hpp), n(r.kg_panen)
        laba = pj - hp - upah
        rows.append({"kolam": r.kolam, "tgl_tebar": r.tgl_tebar, "tgl_panen": r.tgl_panen,
                     "kg_panen": kg, "penjualan": pj, "hpp": hp, "upah": upah,
                     "laba": laba, "laba_per_kg": laba / kg if kg else 0.0})
    return pd.DataFrame(rows).sort_values("tgl_panen")


def batalkan(nama_tbl, r, alasan):
    """Batalkan transaksi lewat jurnal pembalik; data asli tetap tersimpan."""
    jid = r["journal_id"]
    if pd.isna(jid):
        raise ValueError("Transaksi lama tanpa nomor jurnal tidak bisa dibatalkan otomatis.")
    cek_periode(r["tanggal"])
    if nama_tbl == "purchases":
        if r["metode"] == "Utang" and bool(r["lunas"]):
            raise ValueError("Utang ini sudah dibayar. Batalkan pembayarannya lewat jurnal koreksi.")
        if r["kategori"] != "Operasional":
            sm = tbl("stock_moves")
            tersedia = float(pd.to_numeric(
                sm.loc[(sm.kategori == r["kategori"]) & (sm["item"] == r["item"]), "qty"],
                errors="coerce").fillna(0).sum())
            if tersedia < n(r["qty"]):
                raise ValueError("Stok barang ini sudah terpakai, pembelian tidak bisa dibatalkan.")
    else:
        if pd.notna(r.get("kolam")) and r.get("kolam"):
            raise ValueError("Penjualan panen kolam tidak bisa dibatalkan otomatis (HPP dan siklus sudah ditutup).")
        if r["metode"] == "Piutang" and bool(r["lunas"]) and n(r["piutang"]) > 0:
            raise ValueError("Piutang sudah dilunasi. Batalkan lewat jurnal koreksi.")
    asli = ambil_semua(lambda: tabel("journal_lines").select("*").eq("journal_id", int(jid)))
    if not asli:
        raise ValueError("Baris jurnal asli tidak ditemukan.")
    baris = [(b["kode_akun"], n(b["kredit"]), n(b["debit"]), b.get("kolam")) for b in asli]
    jurnal(r["tanggal"], f"Pembalik: batal transaksi #{int(r['id'])} ({alasan})",
           baris, f"pembalik:{int(jid)}")
    tabel(nama_tbl).update({"batal": True, "alasan_batal": alasan}).eq("id", int(r["id"])).execute()
    if nama_tbl == "purchases" and r["kategori"] != "Operasional":
        tabel("stock_moves").insert({
            "tanggal": str(r["tanggal"]), "kategori": r["kategori"],
            "item": r["item"], "qty": -n(r["qty"])
        }).execute()
    if nama_tbl == "sales" and n(r["dp_dipakai"]) > 0:
        tabel("down_payments").insert({
            "tanggal": str(r["tanggal"]), "pembeli": r["pembeli"],
            "jumlah": n(r["dp_dipakai"]), "sisa": n(r["dp_dipakai"])
        }).execute()
    catat("batal_transaksi", f"{nama_tbl}#{int(r['id'])}", alasan)



# ------------------------- Dashboard -------------------------
def dashboard():
    st.title("Dashboard Lele Segar 🐠")
    a = saldo()
    sm, pb = tbl("stock_moves"), tbl("purchases")
    aktif = periode_aktif()
    laba = laba_bersih(saldo(*rentang(aktif)))

    kas = float(a["saldo"].get(KAS, 0))
    persediaan = float(a["saldo"].get("1210", 0) + a["saldo"].get("1220", 0))
    c1, c2, c3 = st.columns(3)
    c1.metric("Jumlah Kas", rp(kas), border=True)
    if c1.button("Buka Neraca", key="b_kas", icon=":material/balance:"):
        go("Akses Cepat", "Neraca")
    c2.metric(f"Profit {nama_bulan(aktif)}", rp(laba), border=True,
              delta="Laba" if laba >= 0 else "Rugi")
    if c2.button("Buka Laba Rugi", key="b_laba", icon=":material/trending_up:"):
        go("Akses Cepat", "Laba Rugi")
    c3.metric("Total Persediaan", rp(persediaan), border=True)
    if c3.button("Buka Buku Pembantu", key="b_stok", icon=":material/menu_book:"):
        go("Akses Cepat", "Buku Pembantu")

    k1, k2, k3 = st.columns(3)
    k1.metric("Piutang Usaha", rp(a["saldo"].get(PIUTANG, 0)), border=True)
    k2.metric("Utang Usaha", rp(a["saldo"].get(UTANG, 0)), border=True)
    k3.metric("DP Pelanggan", rp(a["saldo"].get(DP_AKUN, 0)), border=True)

    st.subheader("📈 Grafik Pendapatan (Rp)")
    sl = tbl("sales")
    if sl.empty:
        st.info("Belum ada data penjualan.")
    else:
        sl["tanggal"] = pd.to_datetime(sl["tanggal"])
        sl["total"] = pd.to_numeric(sl["total"], errors="coerce").fillna(0)
        per_bulan = (
            sl.groupby(sl["tanggal"].dt.to_period("M").astype(str))["total"]
            .sum()
            .rename("Pendapatan (Rp)")
        )
        st.bar_chart(per_bulan)

    st.subheader("🗺️ Peta Kolam")
    tk = tabel_kolam()
    ikon = {"Kosong": "⬜", "Pemeliharaan": "🟦", "Siap panen": "🟧"}
    for i in range(0, len(KOLAM), 5):
        cols = st.columns(5)
        for col, (_, r) in zip(cols, tk.iloc[i:i + 5].iterrows()):
            col.markdown(f"{ikon[r.status]} **{r.kolam}**  \n{r.status}")

    st.subheader("🏆 Laba per Kolam (siklus terakhir)")
    lk = laba_per_kolam()
    if lk.empty:
        st.info("Belum ada data panen.")
    else:
        terakhir = lk.drop_duplicates("kolam", keep="last")
        st.bar_chart(terakhir.set_index("kolam")["laba"].rename("Laba (Rp)"))
        st.dataframe(
            fmt_rp(fmt_qty_df(terakhir.sort_values("laba", ascending=False), ["kg_panen"], "Kg"),
                   ["penjualan", "hpp", "upah", "laba", "laba_per_kg"]),
            hide_index=True, use_container_width=True
        )

    st.subheader("Persediaan")
    d1, d2 = st.columns(2)

    def stok(k):
        if sm.empty:
            return 0.0
        return float(pd.to_numeric(sm.loc[sm.kategori == k, "qty"], errors="coerce").fillna(0).sum())
    d1.metric("Pakan", fmt_qty(stok("Pakan"), "Kg"), border=True)
    d2.metric("Bibit", fmt_qty(stok("Bibit"), "Ekor"), border=True)

    st.subheader("🔔 Reminder")
    ada = False
    if not pb.empty:
        jatuh = belum_lunas(pb[pb.metode == "Utang"])
        for _, r in jatuh.iterrows():
            if pd.isna(r.get("jatuh_tempo")):
                continue
            sisa = (pd.to_datetime(r.jatuh_tempo).date() - date.today()).days
            teks_u = f"Utang ke {r.supplier} ({r['item']}) {rp(r.total)} jatuh tempo {r.jatuh_tempo}"
            if sisa < 0:
                st.error(f"TERLAMBAT {-sisa} hari: {teks_u}")
                ada = True
            elif sisa <= 7:
                st.warning(f"{sisa} hari lagi: {teks_u}")
                ada = True
    if not tbl("equipment").empty and not sudah_susut(aktif):
        st.warning(f"Penyesuaian penyusutan bulan {aktif} belum dijalankan (menu Peralatan).")
        ada = True
    for kat in ("Pakan", "Bibit"):
        batas = batas_stok(kat)
        if batas > 0 and stok(kat) < batas:
            st.warning(f"Stok {kat} {fmt_qty(stok(kat), SATUAN_KAT[kat])} di bawah batas minimum "
                       f"{fmt_qty(batas, SATUAN_KAT[kat])}.")
            ada = True
    for _, r in tk[tk.status == "Siap panen"].iterrows():
        st.info(f"{r.kolam} mendekati/melewati jadwal panen ({r.perkiraan_panen}).")
        ada = True
    if date.today() > rentang(aktif)[1]:
        st.warning(f"Bulan {nama_bulan(aktif)} sudah berakhir. Periode siap ditutup (menu Periode).")
        ada = True
    if not ada:
        st.success("Tidak ada reminder.")

    with st.expander("Setoran modal awal"):
        with st.form("modal", clear_on_submit=True):
            tgl = tgl_input("Tanggal", key="modal_tgl")
            nom = input_angka("Jumlah setoran (Rp)", "in_modal")
            if st.form_submit_button("Simpan setoran modal"):
                if nom > 0:
                    try:
                        jurnal(tgl, "Setoran modal pemilik", [(KAS, nom, 0), (MODAL, 0, nom)], "modal")
                        catat("setoran_modal", detail=rp(nom))
                        st.success("Setoran modal berhasil dicatat.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                else:
                    st.warning("Masukkan jumlah lebih dari nol.")


# ------------------------- Pembelian -------------------------
def pembelian():
    st.title("🛒 Pembelian")
    with st.form("beli", clear_on_submit=True):
        c = st.columns(3)
        tgl = tgl_input("Tanggal")
        kat = c[1].selectbox("Kategori", list(AKUN_BELI))
        item = input_nama("Nama barang", "items", "itm", c[2])
        c = st.columns(4)
        qty = input_angka("Jumlah (sesuai satuan)", "in_beli_qty", kolom=c[0])
        sat = c[1].selectbox("Satuan", SATUAN_OPSI)
        harga = input_angka("Harga satuan (Rp)", "in_beli_harga", kolom=c[2])
        metode = c[3].selectbox("Pembayaran", ["Tunai", "Utang"])
        supplier = input_nama("Supplier", "suppliers", "sup")
        tempo = st.date_input("Jatuh tempo (jika utang)", date.today())
        submit = st.form_submit_button("Simpan pembelian")
    if submit:
        if not item.strip() or qty <= 0 or harga <= 0:
            st.error("Isi nama barang, jumlah, dan harga satuan dengan nilai lebih dari nol.")
        else:
            total = qty * harga
            jid = None
            try:
                kredit = KAS if metode == "Tunai" else UTANG
                jid = jurnal(tgl, f"Beli {item.strip()}",
                             [(AKUN_BELI[kat], total, 0), (kredit, 0, total)], "beli")
                tabel("purchases").insert({
                    "tanggal": str(tgl), "supplier": supplier, "kategori": kat,
                    "item": item.strip(), "qty": qty, "satuan": sat,
                    "harga": harga, "total": total, "metode": metode,
                    "jatuh_tempo": str(tempo) if metode == "Utang" else None,
                    "lunas": metode == "Tunai", "journal_id": jid
                }).execute()
                if kat != "Operasional":
                    tabel("stock_moves").insert({
                        "tanggal": str(tgl), "kategori": kat,
                        "item": item.strip(), "qty": qty
                    }).execute()
                catat("pembelian", item.strip(), f"{rp(total)} {metode}")
                st.success("Pembelian berhasil disimpan.")
                st.rerun()
            except Exception as e:
                if jid:
                    rollback(jid, "purchases")
                st.error(f"Gagal menyimpan pembelian: {e}")

    with st.expander("Pemakaian pakan / bibit (kolam)"):
        sm = tbl("stock_moves")
        if sm.empty:
            st.info("Belum ada persediaan.")
        else:
            pasangan = sorted(
                (sm["kategori"].fillna("") + " | " + sm["item"].fillna("")).unique()
            )
            pasangan = [x for x in pasangan if x.strip(" |")]
            if pasangan:
                with st.form("pakai", clear_on_submit=True):
                    tgl_pakai = tgl_input("Tanggal pakai")
                    pilih = st.selectbox("Item", pasangan)
                    jml = input_angka("Jumlah dipakai (Kg untuk pakan, Ekor untuk bibit)", "in_pakai_jml")
                    catat_pakai = st.form_submit_button("Catat pemakaian")
                if catat_pakai:
                    kat_pakai, item_pakai = pilih.split(" | ", 1)
                    try:
                        pakai_stok(tgl_pakai, kat_pakai, item_pakai, jml)
                        catat("pemakaian_stok", item_pakai, fmt_qty(jml, SATUAN_KAT.get(kat_pakai, "")))
                        st.success("Pemakaian stok berhasil dicatat.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal mencatat pemakaian: {e}")

    pb = tbl("purchases")
    if not pb.empty:
        belum = belum_lunas(pb[pb.metode == "Utang"])
        if not belum.empty:
            with st.expander("Bayar utang"):
                opsi = {
                    f"#{r.id} {r.supplier} - {r['item']} - {rp(r.total)}": r
                    for _, r in belum.iterrows()
                }
                pilihan = st.selectbox("Pilih utang", list(opsi))
                if st.button("Tandai lunas (bayar tunai)"):
                    r = opsi[pilihan]
                    try:
                        jurnal(tgl_default(), f"Bayar utang {r.supplier}",
                               [(UTANG, r.total, 0), (KAS, 0, r.total)], "bayar_utang")
                        tabel("purchases").update({"lunas": True}).eq("id", int(r.id)).execute()
                        catat("bayar_utang", f"purchases#{int(r.id)}", rp(r.total))
                        st.success("Pembayaran utang berhasil dicatat.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal membayar utang: {e}")

    ui_kelola("purchases", pb,
             lambda r: f"#{r.id} {r.tanggal} {r.supplier} - {r['item']} - {rp(r.total)}")

    st.subheader("Transaksi terbaru")
    semua = beri_status(tbl("purchases", semua=True))
    terbaru = semua.sort_values("id", ascending=False).head(15) if not semua.empty else semua
    if terbaru is not None and not terbaru.empty:
        terbaru = terbaru.drop(columns=["batal", "journal_id"], errors="ignore")
    st.dataframe(
        fmt_rp(fmt_qty_df(terbaru, ["qty"], sat_kolom="satuan", buang_sat=True), ["harga", "total"]),
        hide_index=True, use_container_width=True
    )


# ------------------------- Penjualan -------------------------
def penjualan():
    st.title("💰 Penjualan")
    tk = tabel_kolam()
    berjalan = tk[tk.status != "Kosong"].kolam.tolist()

    with st.expander("Terima DP pelanggan"):
        with st.form("dp", clear_on_submit=True):
            c = st.columns(3)
            tgl_dp = tgl_input("Tanggal DP")
            pemb_dp = input_nama("Nama pelanggan", "customers", "pdp", c[1])
            jml_dp = input_angka("Jumlah DP (Rp)", "in_dp_jml", kolom=c[2])
            simpan_dp = st.form_submit_button("Simpan DP")
        if simpan_dp:
            if not pemb_dp.strip() or jml_dp <= 0:
                st.error("Isi nama pelanggan dan jumlah DP.")
            else:
                jid = None
                try:
                    jid = jurnal(tgl_dp, f"DP pelanggan {pemb_dp.strip()}",
                                 [(KAS, jml_dp, 0), (DP_AKUN, 0, jml_dp)], "dp")
                    tabel("down_payments").insert({
                        "tanggal": str(tgl_dp), "pembeli": pemb_dp.strip(),
                        "jumlah": jml_dp, "sisa": jml_dp, "journal_id": jid
                    }).execute()
                    catat("dp_pelanggan", pemb_dp.strip(), rp(jml_dp))
                    st.success("DP berhasil dicatat.")
                    st.rerun()
                except Exception as e:
                    if jid:
                        rollback(jid)
                    st.error(f"Gagal menyimpan DP: {e}")
        dpt = tbl("down_payments")
        if not dpt.empty:
            dpt["sisa"] = pd.to_numeric(dpt["sisa"], errors="coerce").fillna(0)
            sisa_dp = dpt[dpt.sisa > 0]
            if not sisa_dp.empty:
                st.caption("DP yang belum terpakai")
                st.dataframe(fmt_rp(sisa_dp[["tanggal", "pembeli", "jumlah", "sisa"]],
                                    ["jumlah", "sisa"]),
                             hide_index=True, use_container_width=True)

    with st.form("jual", clear_on_submit=True):
        c = st.columns(3)
        tgl = tgl_input("Tanggal")
        pembeli = input_nama("Pembeli", "customers", "pel", c[1])
        metode = c[2].selectbox("Pembayaran", ["Tunai", "Piutang"])
        c = st.columns(2)
        kg = input_angka("Berat panen (Kg)", "in_jual_kg", kolom=c[0])
        hkg = input_angka("Harga per Kg (Rp)", "in_jual_hkg", kolom=c[1])
        c = st.columns(2)
        kolam_sel = c[0].selectbox("Kolam dipanen (opsional)", ["-"] + berjalan)
        pakai_dp = c[1].checkbox("Pakai DP pembeli ini jika ada", value=True)
        submit = st.form_submit_button("Simpan penjualan")
    if submit:
        if kg <= 0 or hkg <= 0:
            st.error("Berat panen dan harga per Kg harus lebih dari nol.")
        else:
            total = kg * hkg
            nama = pembeli.strip()
            dp = 0.0
            dp_rows = pd.DataFrame()
            if pakai_dp and nama:
                dpt = tbl("down_payments")
                if not dpt.empty:
                    dpt["sisa"] = pd.to_numeric(dpt["sisa"], errors="coerce").fillna(0)
                    dp_rows = dpt[(dpt.pembeli.str.lower() == nama.lower()) & (dpt.sisa > 0)]
                    dp = min(float(dp_rows.sisa.sum()), total)
            bayar = total - dp
            baris = []
            if bayar > 0:
                baris.append((KAS if metode == "Tunai" else PIUTANG, bayar, 0))
            if dp > 0:
                baris.append((DP_AKUN, dp, 0))
            baris.append((JUAL, 0, total))
            jid = None
            try:
                jid = jurnal(tgl, f"Jual lele ke {nama}", baris, "jual")
                tabel("sales").insert({
                    "tanggal": str(tgl), "pembeli": nama, "kg": kg,
                    "harga_kg": hkg, "total": total, "metode": metode,
                    "lunas": metode == "Tunai" or bayar <= 0,
                    "journal_id": jid, "dp_dipakai": dp,
                    "kolam": None if kolam_sel == "-" else kolam_sel
                }).execute()
                sisa_pakai = dp
                for _, d in dp_rows.sort_values("id").iterrows():
                    if sisa_pakai <= 0:
                        break
                    pakai = min(float(d.sisa), sisa_pakai)
                    tabel("down_payments").update(
                        {"sisa": float(d.sisa) - pakai}).eq("id", int(d.id)).execute()
                    sisa_pakai -= pakai
                if kolam_sel != "-":
                    x = lines()
                    kk = x[(x.kolam == kolam_sel) & (x.kode_akun == KOLAM_AKUN)]
                    biaya = round(float(kk.debit.sum() - kk.kredit.sum()), 2)
                    if biaya > 0:
                        jurnal(tgl, f"HPP panen {kolam_sel}",
                               [(HPP, biaya, 0, kolam_sel), (KOLAM_AKUN, 0, biaya, kolam_sel)], "hpp")
                    cyc = tbl("cycles")
                    run = cyc[(cyc.kolam == kolam_sel) & (cyc.status == "berjalan")]
                    if not run.empty:
                        tabel("cycles").update({
                            "status": "selesai", "tgl_panen": str(tgl), "kg_panen": kg,
                            "penjualan": total, "hpp": biaya
                        }).eq("id", int(run.iloc[0].id)).execute()
                catat("penjualan", nama, f"{rp(total)} {metode}" + (f" panen {kolam_sel}" if kolam_sel != "-" else ""))
                st.success("Penjualan berhasil disimpan.")
                st.rerun()
            except Exception as e:
                if jid and kolam_sel == "-":
                    rollback(jid, "sales")
                st.error(f"Gagal menyimpan penjualan: {e}")

    sl = tbl("sales")
    if not sl.empty:
        belum = belum_lunas(sl)
        belum = belum[belum.piutang > 0] if not belum.empty else belum
        if not belum.empty:
            with st.expander("Pelunasan piutang"):
                opsi = {
                    f"#{r.id} {r.pembeli} - {rp(r.piutang)}": r
                    for _, r in belum.iterrows()
                }
                pilihan = st.selectbox("Pilih piutang", list(opsi))
                if st.button("Terima pembayaran"):
                    r = opsi[pilihan]
                    try:
                        jurnal(tgl_default(), f"Terima piutang {r.pembeli}",
                               [(KAS, r.piutang, 0), (PIUTANG, 0, r.piutang)], "terima_piutang")
                        tabel("sales").update({"lunas": True}).eq("id", int(r.id)).execute()
                        catat("terima_piutang", f"sales#{int(r.id)}", rp(r.piutang))
                        st.success("Piutang berhasil dilunasi.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal mencatat pelunasan: {e}")

    ui_kelola("sales", sl,
             lambda r: f"#{r.id} {r.tanggal} {r.pembeli} - {rp(r.total)}")

    st.subheader("Transaksi terbaru")
    semua = beri_status(tbl("sales", semua=True))
    terbaru = semua.sort_values("id", ascending=False).head(15) if not semua.empty else semua
    if terbaru is not None and not terbaru.empty:
        terbaru = terbaru.drop(columns=["batal", "journal_id"], errors="ignore")
    st.dataframe(
        fmt_rp(fmt_qty_df(terbaru, ["kg"], "Kg"), ["harga_kg", "total", "dp_dipakai", "piutang"]),
        hide_index=True, use_container_width=True
    )


# ------------------------- Koreksi bibit / pakan (Budidaya) -------------------------
def daftar_pemakaian():
    """Catatan bibit (tebar) dan pakan yang masuk ke biaya kolam, dibaca dari jurnal
    lalu dicocokkan dengan kartu stok (urutan input sama)."""
    x = lines()
    if x.empty:
        return []
    x = x.copy()
    x["tgl"] = x["tanggal"].astype(str).str[:10]
    kat_akun = {"1210": "Pakan", "1220": "Bibit"}
    hasil = []
    pakai = x[(x.ref_type == "pakai") & (x.kode_akun == KOLAM_AKUN) & (x.debit > 0)]
    for _, r in pakai.sort_values("journal_id").iterrows():
        jid = int(r.journal_id)
        kr = x[(x.journal_id == jid) & (x.kredit > 0)]
        kat = kat_akun.get(str(kr.iloc[0].kode_akun)) if not kr.empty else None
        ket = teks(r.keterangan)
        i = ket.find("Pakai ")
        if kat is None or i < 0:
            continue
        awalan, sisa = ket[:i], ket[i + 6:]
        akhir = f" ({r.kolam})"
        item = sisa[:-len(akhir)] if sisa.endswith(akhir) else sisa
        hasil.append({"jid": jid, "tanggal": r.tgl, "kategori": kat, "item": item,
                      "kolam": teks(r.kolam), "hpp": float(r.debit), "awalan": awalan,
                      "stock_id": None, "qty": None})
    sm = tbl("stock_moves")
    if not sm.empty and hasil:
        sm = sm.copy()
        sm["qty"] = pd.to_numeric(sm["qty"], errors="coerce")
        sm = sm[sm.qty < 0].sort_values("id")
        sm["tgl"] = sm["tanggal"].astype(str).str[:10]
        grup = {}
        for _, t in sm.iterrows():
            grup.setdefault((t.tgl, t.kategori, t["item"]), []).append(t)
        per = {}
        for d in hasil:
            per.setdefault((d["tanggal"], d["kategori"], d["item"]), []).append(d)
        for k, ds in per.items():
            ss = grup.get(k, [])
            if len(ss) == len(ds):
                for d, t in zip(ds, ss):
                    d["stock_id"] = int(t["id"])
                    d["qty"] = -float(t["qty"])
    return hasil


def siklus_untuk(kolam, tgl):
    """Siklus terbaru di kolam itu yang dimulai pada atau sebelum tanggal tsb."""
    c = tbl("cycles")
    if c.empty:
        return None
    c = c[(c.kolam == kolam) & (c.tgl_tebar.astype(str).str[:10] <= str(tgl)[:10])]
    return None if c.empty else c.sort_values("tgl_tebar").iloc[-1]


def cek_boleh_ubah_budidaya(d):
    cek_terbuka(d["tanggal"])
    if d["stock_id"] is None:
        raise ValueError("catatan stoknya tidak bisa dicocokkan otomatis.")
    sik = siklus_untuk(d["kolam"], d["tanggal"])
    if sik is not None and sik["status"] == "selesai":
        raise ValueError("kolam ini sudah dipanen, HPP-nya sudah ditutup.")
    return sik


def edit_pemakaian(d, tgl, item, jml, kolam, musim=None):
    sik = cek_boleh_ubah_budidaya(d)
    kat = d["kategori"]
    if jml <= 0:
        raise ValueError("Jumlah harus lebih dari nol.")
    tersedia = stok_tersedia(kat, item) + (d["qty"] if item == d["item"] else 0)
    if jml > tersedia:
        raise ValueError(f"Stok hanya tersedia {fmt_qty(tersedia, SATUAN_KAT.get(kat, ''))}.")
    pb = tbl("purchases")
    pb = pb[(pb.kategori == kat) & (pb["item"] == item)]
    if pb.empty or float(pb.qty.sum() or 0) <= 0:
        raise ValueError("Data pembelian untuk barang ini tidak ditemukan.")
    if kat == "Pakan":
        sik_b = siklus_untuk(kolam, tgl)
        if sik_b is None or sik_b["status"] != "berjalan":
            raise ValueError("Kolam tujuan tidak sedang berjalan pada tanggal itu.")
        if str(tgl) < str(sik_b["tgl_tebar"])[:10]:
            raise ValueError("Tanggal pakan tidak boleh sebelum tanggal tebar.")
    else:
        x = lines()
        lebih_awal = x[(x.kolam == kolam) & (x.kode_akun == KOLAM_AKUN)
                       & (x.journal_id != d["jid"])
                       & (x.tanggal.astype(str).str[:10] < str(tgl))]
        if not lebih_awal.empty:
            raise ValueError("Ada catatan biaya kolam ini sebelum tanggal tebar yang baru.")
    hpp = round(float(pb.total.sum()) / float(pb.qty.sum()) * jml, 0)
    persed = AKUN_PAKAI[kat][1]
    ket = f"{d['awalan']}Pakai {item} ({kolam})"
    tulis_ulang_jurnal(d["jid"], tgl, ket, [(KOLAM_AKUN, hpp, 0, kolam), (persed, 0, hpp)])
    tabel("stock_moves").update(
        {"tanggal": str(tgl), "item": item, "qty": -jml}).eq("id", int(d["stock_id"])).execute()
    if kat == "Bibit" and sik is not None:
        musim = musim or sik.get("musim") or "Kemarau"
        tabel("cycles").update({
            "tgl_tebar": str(tgl), "jumlah_bibit": jml, "musim": musim,
            "perkiraan_panen": str(tgl + timedelta(days=LAMA_PELIHARA[musim]))
        }).eq("id", int(sik["id"])).execute()
    catat("edit_budidaya", f"{kat} {kolam}", f"{item} {fmt_qty(jml, SATUAN_KAT.get(kat, ''))}")


def hapus_pemakaian(d):
    sik = cek_boleh_ubah_budidaya(d)
    if d["kategori"] == "Bibit":
        x = lines()
        awal = str(sik["tgl_tebar"])[:10] if sik is not None else d["tanggal"]
        lain = x[(x.kolam == d["kolam"]) & (x.kode_akun == KOLAM_AKUN)
                 & (x.journal_id != d["jid"]) & (x.tanggal.astype(str).str[:10] >= awal)]
        if not lain.empty:
            raise ValueError("Masih ada catatan pakan atau biaya lain di kolam ini. "
                             "Hapus atau koreksi catatan itu lebih dulu.")
        if sik is not None:
            tabel("cycles").delete().eq("id", int(sik["id"])).execute()
    hapus_jurnal(int(d["jid"]))
    tabel("stock_moves").delete().eq("id", int(d["stock_id"])).execute()
    catat("hapus_budidaya", f"{d['kategori']} {d['kolam']}",
          f"{d['item']} {fmt_qty(d['qty'], SATUAN_KAT.get(d['kategori'], ''))}")


def ui_koreksi_budidaya(berjalan):
    daftar = daftar_pemakaian()
    if not daftar:
        st.info("Belum ada catatan bibit atau pakan.")
        return
    daftar = sorted(daftar, key=lambda z: z["jid"], reverse=True)[:100]

    def label(z):
        q = angka(z["qty"]) if z["qty"] is not None else "?"
        sat = "Ekor" if z["kategori"] == "Bibit" else "Kg"
        return f"#{z['jid']} · {z['kategori']} · {z['tanggal']} · {z['item']} · {z['kolam']} · {q} {sat}"

    opsi = {label(z): z for z in daftar}
    d = opsi[st.selectbox("Pilih catatan", list(opsi), key="kb_pilih")]
    try:
        sik = cek_boleh_ubah_budidaya(d)
    except ValueError as e:
        st.warning(f"Catatan ini tidak bisa diubah: {e}")
        return
    kat = d["kategori"]
    t_edit, t_hapus = st.tabs(["Edit", "Hapus"])

    with t_edit:
        a, b = batas_tanggal()
        tgl_lama = date.fromisoformat(d["tanggal"])
        musim = None
        with st.form(f"kbe_{d['jid']}"):
            c = st.columns(3)
            tgl = c[0].date_input("Tanggal", min(max(tgl_lama, a), b), min_value=a, max_value=b)
            pilihan_item = item_stok(kat)
            if d["item"] not in pilihan_item:
                pilihan_item = [d["item"]] + pilihan_item
            item = c[1].selectbox("Bibit" if kat == "Bibit" else "Pakan", pilihan_item,
                                  index=pilihan_item.index(d["item"]))
            jml = input_angka("Jumlah (Ekor)" if kat == "Bibit" else "Jumlah (Kg)",
                              f"in_kb_jml_{d['jid']}", nilai=d["qty"], kolom=c[2])
            c = st.columns(2)
            if kat == "Pakan":
                pilihan_kolam = list(berjalan)
                if d["kolam"] not in pilihan_kolam:
                    pilihan_kolam = [d["kolam"]] + pilihan_kolam
                kolam = c[0].selectbox("Kolam", pilihan_kolam, index=pilihan_kolam.index(d["kolam"]))
            else:
                kolam = d["kolam"]
                c[0].text_input("Kolam", d["kolam"], disabled=True)
                daftar_musim = list(LAMA_PELIHARA)
                m0 = sik.get("musim") if sik is not None else None
                musim = c[1].selectbox("Musim", daftar_musim,
                                       index=daftar_musim.index(m0) if m0 in daftar_musim else 0)
            ok = st.form_submit_button("Simpan perubahan")
        if ok:
            try:
                edit_pemakaian(d, tgl, item, jml, kolam, musim)
                st.session_state["_pesan"] = "Catatan budidaya berhasil diperbarui."
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menyimpan perubahan: {e}")

    with t_hapus:
        st.warning("Catatan dihapus permanen dan stok dikembalikan.")
        ya = st.checkbox("Saya yakin ingin menghapus catatan ini", key=f"kbh_ya_{d['jid']}")
        if st.button("Hapus catatan", key=f"kbh_{d['jid']}"):
            if not ya:
                st.error("Centang konfirmasi terlebih dahulu.")
            else:
                try:
                    hapus_pemakaian(d)
                    st.session_state["_pesan"] = "Catatan budidaya dihapus."
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menghapus: {e}")


# ------------------------- Budidaya -------------------------
def budidaya():
    st.title("Budidaya 🐠")
    tk = tabel_kolam()
    st.subheader("Status Kolam")
    st.dataframe(fmt_rp(fmt_qty_df(tk, ["bibit", "mati"], "Ekor"), ["biaya_berjalan"]),
                 hide_index=True, use_container_width=True)
    kosong = tk[tk.status == "Kosong"].kolam.tolist()
    berjalan = tk[tk.status != "Kosong"].kolam.tolist()

    t1, t2, t3, t4, t5, t6 = st.tabs(
        ["Tebar Bibit", "Pakan Harian", "Kematian", "Listrik & Air", "Upah Panen",
         "Koreksi Bibit/Pakan"])

    with t1:
        bibit = item_stok("Bibit")
        if not kosong:
            st.info("Semua kolam sedang berjalan.")
        elif not bibit:
            st.info("Belum ada stok bibit. Catat pembelian bibit di menu Pembelian dulu.")
        else:
            with st.form("tebar", clear_on_submit=True):
                c = st.columns(3)
                kolam = c[0].selectbox("Kolam kosong", kosong)
                tgl = tgl_input("Tanggal tebar")
                item = c[2].selectbox("Bibit", bibit)
                c = st.columns(2)
                jml = input_angka("Jumlah (Ekor)", "in_tebar_jml", nilai=5000, kolom=c[0])
                musim = c[1].selectbox("Musim", list(LAMA_PELIHARA))
                ok = st.form_submit_button("Tebar bibit")
            if ok:
                try:
                    pakai_stok(tgl, "Bibit", item, jml, kolam)
                    tabel("cycles").insert({
                        "kolam": kolam, "tgl_tebar": str(tgl), "jumlah_bibit": jml,
                        "musim": musim, "status": "berjalan",
                        "perkiraan_panen": str(tgl + timedelta(days=LAMA_PELIHARA[musim]))
                    }).execute()
                    catat("tebar_bibit", kolam, fmt_qty(jml, "Ekor"))
                    st.success(f"Siklus baru di {kolam} dimulai.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal tebar bibit: {e}")

    with t2:
        pakan = item_stok("Pakan")
        if not berjalan:
            st.info("Belum ada kolam yang berjalan.")
        elif not pakan:
            st.info("Belum ada stok pakan.")
        else:
            with st.form("pakan", clear_on_submit=True):
                c = st.columns(4)
                kolam = c[0].selectbox("Kolam", berjalan)
                tgl = tgl_input("Tanggal")
                item = c[2].selectbox("Pakan", pakan)
                jml = input_angka("Jumlah (Kg)", "in_pakan_jml", kolom=c[3])
                ok = st.form_submit_button("Catat pakan")
            if ok:
                try:
                    pakai_stok(tgl, "Pakan", item, jml, kolam)
                    catat("pakan_harian", kolam, fmt_qty(jml, "Kg"))
                    st.success("Pakan dicatat ke biaya kolam.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal mencatat pakan: {e}")

    with t3:
        if not berjalan:
            st.info("Belum ada kolam yang berjalan.")
        else:
            with st.form("mati", clear_on_submit=True):
                c = st.columns(3)
                kolam = c[0].selectbox("Kolam", berjalan, key="mati_kolam")
                tgl = tgl_input("Tanggal", key="mati_tgl")
                jml = input_angka("Jumlah mati (Ekor)", "in_mati_jml", kolom=c[2])
                rugi = st.checkbox("Catat sebagai kerugian (di luar batas wajar)")
                ok = st.form_submit_button("Simpan kematian")
            if ok:
                if jml <= 0:
                    st.error("Jumlah harus lebih dari nol.")
                else:
                    try:
                        tabel("fish_deaths").insert({
                            "tanggal": str(tgl), "kolam": kolam, "jumlah": jml}).execute()
                        if rugi:
                            pb = tbl("purchases")
                            pb = pb[pb.kategori == "Bibit"]
                            per_ekor = float(pb.total.sum()) / float(pb.qty.sum()) if not pb.empty and pb.qty.sum() else 0
                            nilai = round(per_ekor * jml, 0)
                            if nilai > 0:
                                jurnal(tgl, f"Kerugian ikan mati {kolam}",
                                       [(RUGI_MATI, nilai, 0, kolam), (KOLAM_AKUN, 0, nilai, kolam)], "mati")
                        catat("kematian_ikan", kolam, fmt_qty(jml, "Ekor"))
                        st.success("Kematian dicatat.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal menyimpan: {e}")

    with t4:
        with st.form("listrik", clear_on_submit=True):
            c = st.columns(2)
            tgl = tgl_input("Tanggal", key="lis_tgl")
            jml = input_angka("Biaya listrik & air, tunai (Rp)", "in_lis_jml", kolom=c[1])
            ok = st.form_submit_button("Simpan dan bagi ke kolam")
        if ok:
            if jml <= 0:
                st.error("Jumlah harus lebih dari nol.")
            else:
                try:
                    if berjalan:
                        bagi = int(round(jml / len(berjalan), 0))
                        baris = [(KOLAM_AKUN, bagi, 0, k) for k in berjalan[:-1]]
                        baris.append((KOLAM_AKUN, jml - bagi * (len(berjalan) - 1), 0, berjalan[-1]))
                    else:
                        baris = [(LISTRIK, jml, 0)]
                    baris.append((KAS, 0, jml))
                    jurnal(tgl, "Biaya listrik dan air", baris, "listrik")
                    catat("listrik_air", detail=rp(jml))
                    st.success("Biaya listrik dan air dicatat.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menyimpan: {e}")

    with t5:
        if not berjalan:
            st.info("Belum ada kolam yang berjalan.")
        else:
            with st.form("upah", clear_on_submit=True):
                c = st.columns(3)
                kolam = c[0].selectbox("Kolam dipanen", berjalan, key="upah_kolam")
                tgl = tgl_input("Tanggal", key="upah_tgl")
                jml = input_angka("Upah panen, tunai (Rp)", "in_upah_jml", kolom=c[2])
                ok = st.form_submit_button("Simpan upah panen")
            if ok:
                if jml <= 0:
                    st.error("Jumlah harus lebih dari nol.")
                else:
                    try:
                        jurnal(tgl, f"Upah panen {kolam}",
                               [(UPAH, jml, 0, kolam), (KAS, 0, jml)], "upah")
                        catat("upah_panen", kolam, rp(jml))
                        st.success("Upah panen dicatat.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal menyimpan: {e}")


    with t6:
        ui_koreksi_budidaya(berjalan)


# ------------------------- Peralatan -------------------------
def peralatan():
    st.title("🛠️ Peralatan")
    with st.form("alat", clear_on_submit=True):
        c = st.columns(2)
        nama = c[0].text_input("Nama (kolam terpal, pompa, aerator, dll)")
        tgl = tgl_input("Tanggal beli")
        c = st.columns(3)
        harga = input_angka("Harga, dibayar tunai (Rp)", "in_alat_harga", kolom=c[0])
        umur = input_angka("Umur ekonomis (Bulan)", "in_alat_umur", nilai=24, kolom=c[1])
        residu = input_angka("Nilai residu (Rp)", "in_alat_residu", kolom=c[2])
        submit = st.form_submit_button("Simpan peralatan")
    if submit:
        if not nama.strip() or harga <= 0:
            st.error("Nama dan harga peralatan wajib diisi.")
        elif umur < 1:
            st.error("Umur ekonomis minimal 1 bulan.")
        elif residu > harga:
            st.error("Nilai residu tidak boleh lebih besar dari harga peralatan.")
        else:
            try:
                jurnal(tgl, f"Beli peralatan {nama.strip()}",
                       [("1310", harga, 0), (KAS, 0, harga)], "alat")
                tabel("equipment").insert({
                    "nama": nama.strip(), "tanggal": str(tgl), "harga": harga,
                    "umur_bulan": int(umur), "residu": residu
                }).execute()
                catat("beli_peralatan", nama.strip(), rp(harga))
                st.success("Peralatan berhasil disimpan.")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menyimpan peralatan: {e}")

    e = tbl("equipment")
    if e.empty:
        st.info("Belum ada peralatan yang dicatat.")
        return
    e["susut_per_bulan"] = ((e.harga - e.residu) / e.umur_bulan).round(0)
    tampil_e = e.copy()
    tampil_e["umur_bulan"] = tampil_e["umur_bulan"].map(lambda v: fmt_qty(v, "Bulan"))
    st.dataframe(
        fmt_rp(tampil_e, ["harga", "residu", "susut_per_bulan"]),
        hide_index=True, use_container_width=True
    )
    key = periode_aktif()
    total_susut = float(e.susut_per_bulan.sum())
    if sudah_susut(key):
        st.success(f"Penyusutan {key} sudah dijalankan.")
    elif st.button(f"Jalankan penyusutan {key} ({rp(total_susut)})"):
        try:
            jurnal(rentang(key)[1], f"Penyusutan peralatan {key}",
                   [("5140", total_susut, 0), ("1311", 0, total_susut)],
                   f"susut:{key}")
            catat("penyusutan", key, rp(total_susut))
            st.success("Penyusutan berhasil dicatat.")
            st.rerun()
        except Exception as ex:
            st.error(f"Gagal menjalankan penyusutan: {ex}")


# ------------------------- Laporan -------------------------
def jurnal_umum():
    l = lines()
    a = tbl("accounts")[["kode", "nama"]]
    if l.empty:
        st.info("Belum ada jurnal.")
        return
    dari, sampai = pilih_bulan("pj_ju")
    l = filter_tgl(l, dari, sampai)
    l = l.merge(a, left_on="kode_akun", right_on="kode", how="left")
    tampil = l.sort_values(["tanggal", "journal_id"])[
        ["tanggal", "journal_id", "keterangan", "kode_akun",
         "nama", "kolam", "debit", "kredit"]
    ]
    st.dataframe(
        fmt_rp(tampil, ["debit", "kredit"]),
        hide_index=True, use_container_width=True
    )
    ui_edit_jurnal(l)


def buku_besar():
    a = tbl("accounts")
    if a.empty:
        st.info("Daftar akun belum tersedia.")
        return
    pilihan_akun = st.selectbox("Akun", (a.kode + " - " + a.nama).tolist())
    x = lines()
    if x.empty:
        st.info("Belum ada transaksi jurnal.")
        return
    dari, sampai = pilih_bulan("pj_bb")
    x = filter_tgl(x, dari, sampai)
    x = x[x.kode_akun == pilihan_akun.split(" - ")[0]].sort_values(
        ["tanggal", "id"]
    ).copy()
    x["saldo (D-K)"] = (x.debit - x.kredit).cumsum()
    st.dataframe(
        fmt_rp(
            x[["tanggal", "keterangan", "kolam", "debit", "kredit", "saldo (D-K)"]],
            ["debit", "kredit", "saldo (D-K)"]
        ),
        hide_index=True, use_container_width=True
    )


def buku_pembantu():
    jenis = st.radio(
        "Jenis", ["Utang Usaha", "Piutang Usaha", "Persediaan", "Biaya per Kolam"],
        horizontal=True
    )
    if jenis == "Utang Usaha":
        p = tbl("purchases")
        u = belum_lunas(p[p.metode == "Utang"]) if not p.empty else p
        ringkas = u.groupby("supplier").total.sum().reset_index() if not u.empty else u
        st.dataframe(fmt_rp(ringkas, ["total"]), hide_index=True)
        st.dataframe(fmt_rp(fmt_qty_df(u, ["qty"], sat_kolom="satuan", buang_sat=True),
                            ["harga", "total"]),
                     hide_index=True, use_container_width=True)
    elif jenis == "Piutang Usaha":
        s = tbl("sales")
        u = belum_lunas(s)
        u = u[u.piutang > 0] if not u.empty else u
        ringkas = u.groupby("pembeli").piutang.sum().reset_index() if not u.empty else u
        st.dataframe(fmt_rp(ringkas, ["piutang"]), hide_index=True)
        st.dataframe(fmt_rp(fmt_qty_df(u, ["kg"], "Kg"),
                            ["harga_kg", "total", "dp_dipakai", "piutang"]),
                     hide_index=True, use_container_width=True)
    elif jenis == "Persediaan":
        sm = tbl("stock_moves")
        if sm.empty:
            st.info("Belum ada pergerakan stok.")
        else:
            ringkas = sm.groupby(["kategori", "item"]).qty.sum().reset_index()
            st.dataframe(fmt_qty_kategori(ringkas), hide_index=True)
            st.caption("Kartu stok")
            st.dataframe(fmt_qty_kategori(sm), hide_index=True, use_container_width=True)
    else:
        x = lines()
        x = x[x.kolam.notna() & (x.kolam != "")] if not x.empty else x
        if x.empty:
            st.info("Belum ada biaya per kolam.")
        else:
            kolam = st.selectbox("Kolam", sorted(x.kolam.unique()))
            y = x[x.kolam == kolam].sort_values(["tanggal", "id"]).copy()
            y["saldo (D-K)"] = (y.debit - y.kredit).cumsum()
            st.dataframe(
                fmt_rp(y[["tanggal", "keterangan", "kode_akun", "debit", "kredit", "saldo (D-K)"]],
                       ["debit", "kredit", "saldo (D-K)"]),
                hide_index=True, use_container_width=True
            )


def neraca_saldo():
    _, sampai = pilih_bulan("pj_ns", "Per akhir periode")
    a = saldo(None, sampai).reset_index()
    net = a["debit"] - a["kredit"]
    a["Debit"] = net.clip(lower=0)
    a["Kredit"] = (-net).clip(lower=0)
    a = a[(a.Debit != 0) | (a.Kredit != 0)]
    st.dataframe(fmt_rp(a[["kode", "nama", "Debit", "Kredit"]], ["Debit", "Kredit"]),
                 hide_index=True, use_container_width=True)
    td, tk = a.Debit.sum(), a.Kredit.sum()
    c1, c2 = st.columns(2)
    c1.metric("Total Debit", rp(td), border=True)
    c2.metric("Total Kredit", rp(tk), border=True)
    if round(td - tk, 2) == 0:
        st.success("Neraca saldo seimbang.")
    else:
        st.error(f"Neraca saldo TIDAK seimbang, selisih {rp(td - tk)}.")


def neraca():
    _, sampai = pilih_bulan("pj_nr", "Per akhir periode")
    a = saldo(None, sampai).reset_index()
    laba = laba_bersih(a.set_index("kode"))
    aset = a[a.tipe == "Aset"][["kode", "nama", "saldo"]]
    pas = a[a.tipe.isin(["Kewajiban", "Ekuitas"])][["kode", "nama", "saldo"]]
    pas = pd.concat([
        pas,
        pd.DataFrame([{"kode": "-", "nama": "Laba Berjalan", "saldo": laba}])
    ], ignore_index=True)
    c1, c2 = st.columns(2)
    c1.subheader("Aset")
    c1.dataframe(fmt_rp(aset, ["saldo"]), hide_index=True, use_container_width=True)
    c1.metric("Total Aset", rp(aset.saldo.sum()), border=True)
    c2.subheader("Kewajiban & Ekuitas")
    c2.dataframe(fmt_rp(pas, ["saldo"]), hide_index=True, use_container_width=True)
    c2.metric("Total Kewajiban + Ekuitas", rp(pas.saldo.sum()), border=True)
    if round(aset.saldo.sum() - pas.saldo.sum(), 2) == 0:
        st.success("Neraca seimbang.")
    else:
        st.error("Neraca TIDAK seimbang.")


def laba_rugi():
    dari, sampai = pilih_bulan("pj_lr")
    a = saldo(dari, sampai).reset_index()
    for tipe in ["Pendapatan", "Beban"]:
        x = a[a.tipe == tipe][["kode", "nama", "saldo"]]
        st.subheader(tipe)
        st.dataframe(fmt_rp(x, ["saldo"]), hide_index=True, use_container_width=True)
        st.write(f"Total {tipe}: **{rp(x.saldo.sum())}**")
    st.metric("Laba / (Rugi) Bersih", rp(laba_bersih(a.set_index("kode"))), border=True)


def laba_kolam_laporan():
    st.subheader("Laba Rugi per Kolam (per siklus)")
    lk = laba_per_kolam()
    if lk.empty:
        st.info("Belum ada data panen.")
        return
    st.dataframe(
        fmt_rp(fmt_qty_df(lk, ["kg_panen"], "Kg"),
               ["penjualan", "hpp", "upah", "laba", "laba_per_kg"]),
        hide_index=True, use_container_width=True
    )
    st.metric("Total laba semua siklus", rp(lk.laba.sum()), border=True)


def arus_kas():
    dari, sampai = pilih_bulan("pj_ak")
    x = filter_tgl(lines(), dari, sampai)
    x = x[x.kode_akun == KAS] if not x.empty else x
    if x.empty:
        st.info("Belum ada pergerakan kas.")
        return
    peta = {"alat": "Investasi", "modal": "Pendanaan"}
    x = x.copy()
    x["Aktivitas"] = x.ref_type.fillna("").map(lambda r: peta.get(r, "Operasi"))
    g = x.groupby("Aktivitas")[["debit", "kredit"]].sum().reset_index()
    g["Masuk"], g["Keluar"] = g["debit"], g["kredit"]
    g["Neto"] = g["Masuk"] - g["Keluar"]
    st.dataframe(fmt_rp(g[["Aktivitas", "Masuk", "Keluar", "Neto"]], ["Masuk", "Keluar", "Neto"]),
                 hide_index=True, use_container_width=True)
    st.metric("Kenaikan / (penurunan) kas", rp(g.Neto.sum()), border=True)


def akses_cepat():
    st.title("⚡ Akses Cepat")
    pilih = st.radio("Laporan", AKSES, horizontal=True, key="ak")
    {
        "Jurnal Umum": jurnal_umum,
        "Buku Besar": buku_besar,
        "Buku Pembantu": buku_pembantu,
        "Neraca Saldo": neraca_saldo,
        "Neraca": neraca,
        "Laba Rugi": laba_rugi,
        "Laba per Kolam": laba_kolam_laporan,
        "Arus Kas": arus_kas,
    }[pilih]()


# ------------------------- Periode -------------------------
def cadangan():
    data = {}
    for nama in KOLOM_KOSONG:
        df = tbl(nama, semua=True) if nama in ("purchases", "sales") else tbl(nama)
        data[nama] = df.to_dict("records")
    return json.dumps(data, default=str, ensure_ascii=False, indent=1)



# ------------------------- Data master (template) -------------------------
TEMPLATE = {
    "suppliers": [
        {"nama": "Supplier Bibit Ungaran", "jenis_pasokan": "Bibit", "daerah": "Ungaran",
         "kontak": None, "termin": "Tunai", "catatan": "Isi catatan kualitas dan harga bibit", "aktif": True},
        {"nama": "Supplier Bibit Tugu Semarang", "jenis_pasokan": "Bibit", "daerah": "Tugu Semarang",
         "kontak": None, "termin": "Tunai", "catatan": "Isi catatan kualitas dan harga bibit", "aktif": True},
        {"nama": "Supplier Bibit Jawa Timur", "jenis_pasokan": "Bibit", "daerah": "Jawa Timur",
         "kontak": None, "termin": "Tunai", "catatan": "Isi catatan kualitas dan harga bibit", "aktif": True},
        {"nama": "Toko Pakan Langganan", "jenis_pasokan": "Pakan", "daerah": "Semarang",
         "kontak": None, "termin": None, "catatan": "Pakan Tongwe, beli sekitar 500 Kg per minggu", "aktif": True},
    ],
    "customers": [
        {"nama": "Pelanggan Tetap 1", "kategori": "Pelanggan tetap", "kontak": None,
         "aturan_dp": "DP dulu sebelum panen", "catatan": "Memakai nota", "aktif": True},
        {"nama": "SPPG", "kategori": "SPPG", "kontak": None, "aturan_dp": None,
         "catatan": "Biasanya kredit", "aktif": True},
        {"nama": "Pembeli Langsung", "kategori": "Pembeli langsung", "kontak": None,
         "aturan_dp": None, "catatan": None, "aktif": True},
    ],
    "items": [
        {"nama": "Bibit Lele Mutiara", "kategori": "Bibit", "satuan": "Ekor",
         "harga_ref": 185, "stok_min": 0, "aktif": True},
        {"nama": "Pakan Tongwe", "kategori": "Pakan", "satuan": "Kg",
         "harga_ref": 15500, "stok_min": 150, "aktif": True},
        {"nama": "Perlengkapan (jaring, serok, ember)", "kategori": "Operasional",
         "satuan": "Unit", "harga_ref": 0, "stok_min": 0, "aktif": True},
    ],
}
MANUAL = "(ketik manual)"


def pastikan_master():
    """Isi template supplier, pelanggan, dan barang satu kali saja."""
    tanda = os.path.join(DATA_LOKAL, "master_siap.json")
    if os.path.exists(tanda):
        return
    for nama, isi in TEMPLATE.items():
        if not _baca(nama, []):
            tabel(nama).insert([dict(x) for x in isi]).execute()
    _tulis("master_siap", True)


def reset_template():
    for nama, isi in TEMPLATE.items():
        _tulis(nama, [{**x, "id": i} for i, x in enumerate(isi, 1)])


def nama_master(nama_tbl):
    df = tbl(nama_tbl)
    if df.empty:
        return []
    return df[df["aktif"].fillna(True).astype(bool)]["nama"].dropna().tolist()


def input_nama(label, nama_tbl, key, kolom=None):
    """Pilih dari data master, atau ketik manual."""
    kolom = kolom or st
    p = kolom.selectbox(label, [MANUAL] + nama_master(nama_tbl), key=key + "_p")
    t = kolom.text_input(f"{label} (jika ketik manual)", key=key + "_t")
    return t.strip() if p == MANUAL else p


def batas_stok(kat):
    it = tbl("items")
    if not it.empty:
        x = it[(it.kategori == kat) & it["aktif"].fillna(True).astype(bool)]
        v = pd.to_numeric(x["stok_min"], errors="coerce").dropna()
        if not v.empty:
            return float(v.max())
    return float(STOK_MIN.get(kat, 0))


def _py(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v.item() if hasattr(v, "item") else v


def teks(v):
    v = _py(v)
    return "" if v is None else str(v)


def editor_master(nama, kolom, config=None):
    df = tbl(nama)
    tampil = df[kolom].copy() if not df.empty else pd.DataFrame(columns=kolom)
    if "satuan" in tampil.columns:
        tampil["satuan"] = tampil["satuan"].map(satuan_rapi)
    hasil = st.data_editor(tampil, num_rows="dynamic", hide_index=True,
                           use_container_width=True, key=f"ed_{nama}", column_config=config)
    if st.button("Simpan perubahan", key=f"sv_{nama}"):
        rows = []
        for rec in hasil.to_dict("records"):
            rec = {k: _py(v) for k, v in rec.items()}
            if not teks(rec.get("nama")).strip():
                continue
            if rec.get("aktif") is None:
                rec["aktif"] = True
            if "satuan" in rec:
                rec["satuan"] = satuan_rapi(rec["satuan"]) or None
            for k_angka in ("harga_ref", "stok_min"):   # tanpa desimal
                if rec.get(k_angka) is not None:
                    rec[k_angka] = int(round(float(rec[k_angka])))
            rec["id"] = len(rows) + 1
            rows.append(rec)
        _tulis(nama, rows)
        catat("ubah_master", nama, f"{len(rows)} baris")
        st.session_state["_pesan"] = "Data master tersimpan."
        st.rerun()


# ------------------------- Edit / hapus transaksi -------------------------
def stok_tersedia(kat, item):
    sm = tbl("stock_moves")
    if sm.empty:
        return 0.0
    return float(pd.to_numeric(
        sm.loc[(sm.kategori == kat) & (sm["item"] == item), "qty"], errors="coerce"
    ).fillna(0).sum())


def cari_stok_row(tgl, kat, item, qty):
    sm = tbl("stock_moves")
    if sm.empty:
        return None
    m = sm[(sm.tanggal.astype(str) == str(tgl)[:10]) & (sm.kategori == kat)
           & (sm["item"] == item) & (pd.to_numeric(sm.qty, errors="coerce") == float(qty))]
    return int(m.iloc[0]["id"]) if not m.empty else None


def akhir_terkunci():
    p = tbl("periods")
    return rentang(max(p["bulan"]))[1] if not p.empty else None


def cek_terbuka(tgl):
    """Tanggal boleh diubah selama tidak berada di periode yang sudah ditutup
    dan tidak melewati periode aktif."""
    d = tgl if isinstance(tgl, date) else date.fromisoformat(str(tgl)[:10])
    k = akhir_terkunci()
    if k and d <= k:
        raise ValueError(f"Tanggal {d} berada di periode yang sudah ditutup.")
    if d > rentang(periode_aktif())[1]:
        raise ValueError(f"Tanggal {d} melewati periode aktif ({nama_bulan(periode_aktif())}).")


def batas_tanggal():
    k = akhir_terkunci()
    return (k + timedelta(days=1) if k else date(2020, 1, 1)), rentang(periode_aktif())[1]


def tulis_ulang_jurnal(jid, tgl, ket, baris):
    """Ganti isi jurnal yang sudah ada (untuk edit). Jika gagal, isi lama dikembalikan.
    Baris: (akun, debit, kredit) atau (akun, debit, kredit, kolam)."""
    cek_terbuka(tgl)
    baris = [(str(x[0]), float(x[1]), float(x[2]), x[3] if len(x) > 3 else None) for x in baris]
    if round(sum(x[1] for x in baris), 2) != round(sum(x[2] for x in baris), 2):
        raise ValueError("Jurnal tidak seimbang: total debit harus sama dengan total kredit.")
    lama = ambil_semua(lambda: tabel("journal_lines").select("*").eq("journal_id", int(jid)))
    tabel("journal_lines").delete().eq("journal_id", int(jid)).execute()
    try:
        isi = []
        for a_, d_, k_, kol in baris:
            row = {"journal_id": int(jid), "kode_akun": a_, "debit": d_, "kredit": k_}
            if kol:
                row["kolam"] = kol
            isi.append(row)
        tabel("journal_lines").insert(isi).execute()
        tabel("journals").update({"tanggal": str(tgl), "keterangan": ket}).eq("id", int(jid)).execute()
    except Exception:
        tabel("journal_lines").insert([{k: v for k, v in l.items() if k != "id"} for l in lama]).execute()
        raise


def edit_pembelian(r, tgl, supplier, kat, item, qty, sat, harga, metode, tempo):
    jid = r["journal_id"]
    if pd.isna(jid):
        raise ValueError("Transaksi lama tanpa nomor jurnal tidak bisa diedit otomatis.")
    cek_terbuka(r["tanggal"])
    if not item.strip() or qty <= 0 or harga <= 0:
        raise ValueError("Nama barang, jumlah, dan harga harus diisi dengan nilai lebih dari nol.")
    if r["metode"] == "Utang" and bool(r["lunas"]):
        raise ValueError("Utang ini sudah dibayar. Batalkan lalu input ulang.")
    item = item.strip()
    kat_lama, item_lama, qty_lama = r["kategori"], r["item"], n(r["qty"])
    stok_lama, stok_baru = kat_lama != "Operasional", kat != "Operasional"
    if stok_lama:
        sama_item = stok_baru and kat == kat_lama and item == item_lama
        sisa = stok_tersedia(kat_lama, item_lama) - qty_lama + (qty if sama_item else 0)
        if sisa < 0:
            raise ValueError("Stok barang ini sudah terpakai, jumlah/barang tidak bisa diubah.")
    total = qty * harga
    kredit = KAS if metode == "Tunai" else UTANG
    tulis_ulang_jurnal(jid, tgl, f"Beli {item}", [(AKUN_BELI[kat], total, 0), (kredit, 0, total)])
    row = cari_stok_row(r["tanggal"], kat_lama, item_lama, qty_lama) if stok_lama else None
    if stok_lama and row is None:
        tabel("stock_moves").insert({"tanggal": str(tgl), "kategori": kat_lama,
                                     "item": item_lama, "qty": -qty_lama}).execute()
    if stok_lama and row is not None and not stok_baru:
        tabel("stock_moves").delete().eq("id", row).execute()
    if stok_baru:
        data = {"tanggal": str(tgl), "kategori": kat, "item": item, "qty": qty}
        if row is not None:
            tabel("stock_moves").update(data).eq("id", row).execute()
        else:
            tabel("stock_moves").insert(data).execute()
    tabel("purchases").update({
        "tanggal": str(tgl), "supplier": supplier.strip(), "kategori": kat, "item": item,
        "qty": qty, "satuan": sat, "harga": harga, "total": total, "metode": metode,
        "jatuh_tempo": str(tempo) if metode == "Utang" else None, "lunas": metode == "Tunai"
    }).eq("id", int(r["id"])).execute()
    catat("edit_transaksi", f"purchases#{int(r['id'])}", item)


def edit_penjualan(r, tgl, pembeli, kg, hkg, metode):
    jid = r["journal_id"]
    if pd.isna(jid):
        raise ValueError("Transaksi lama tanpa nomor jurnal tidak bisa diedit otomatis.")
    cek_terbuka(r["tanggal"])
    if kg <= 0 or hkg <= 0:
        raise ValueError("Berat dan harga per Kg harus lebih dari nol.")
    if n(r["dp_dipakai"]) > 0 or teks(r.get("kolam")):
        raise ValueError("Penjualan ini memakai DP atau panen kolam. Batalkan lalu input ulang.")
    if r["metode"] == "Piutang" and bool(r["lunas"]):
        raise ValueError("Piutang sudah dilunasi. Batalkan lalu input ulang.")
    total = kg * hkg
    tulis_ulang_jurnal(jid, tgl, f"Jual lele ke {pembeli.strip()}",
                       [(KAS if metode == "Tunai" else PIUTANG, total, 0), (JUAL, 0, total)])
    tabel("sales").update({
        "tanggal": str(tgl), "pembeli": pembeli.strip(), "kg": kg, "harga_kg": hkg,
        "total": total, "metode": metode, "lunas": metode == "Tunai"
    }).eq("id", int(r["id"])).execute()
    catat("edit_transaksi", f"sales#{int(r['id'])}", pembeli.strip())


def hapus_permanen(nama_tbl, r):
    """Hapus total transaksi beserta jurnalnya (tanpa jejak jurnal pembalik)."""
    jid = r["journal_id"]
    if pd.isna(jid):
        raise ValueError("Transaksi lama tanpa nomor jurnal tidak bisa dihapus otomatis.")
    cek_terbuka(r["tanggal"])
    if nama_tbl == "purchases":
        if r["metode"] == "Utang" and bool(r["lunas"]):
            raise ValueError("Utang ini sudah dibayar. Tidak bisa dihapus.")
        if r["kategori"] != "Operasional" and stok_tersedia(r["kategori"], r["item"]) < n(r["qty"]):
            raise ValueError("Stok barang ini sudah terpakai, pembelian tidak bisa dihapus.")
    else:
        if teks(r.get("kolam")):
            raise ValueError("Penjualan panen kolam tidak bisa dihapus (HPP dan siklus sudah ditutup).")
        if r["metode"] == "Piutang" and bool(r["lunas"]) and n(r["piutang"]) > 0:
            raise ValueError("Piutang sudah dilunasi. Tidak bisa dihapus.")
    hapus_jurnal(int(jid))
    if nama_tbl == "purchases" and r["kategori"] != "Operasional":
        row = cari_stok_row(r["tanggal"], r["kategori"], r["item"], n(r["qty"]))
        if row is not None:
            tabel("stock_moves").delete().eq("id", row).execute()
        else:
            tabel("stock_moves").insert({"tanggal": str(r["tanggal"]), "kategori": r["kategori"],
                                         "item": r["item"], "qty": -n(r["qty"])}).execute()
    if nama_tbl == "sales" and n(r["dp_dipakai"]) > 0:
        tabel("down_payments").insert({"tanggal": str(r["tanggal"]), "pembeli": r["pembeli"],
                                       "jumlah": n(r["dp_dipakai"]), "sisa": n(r["dp_dipakai"])}).execute()
    tabel(nama_tbl).delete().eq("id", int(r["id"])).execute()
    catat("hapus_permanen", f"{nama_tbl}#{int(r['id'])}", rp(n(r["total"])))


def ui_kelola(nama_tbl, df, label):
    with st.expander("Edit / batalkan / hapus transaksi"):
        aktif = df[df["journal_id"].notna()] if not df.empty else df
        if aktif.empty:
            st.info("Belum ada transaksi yang bisa diubah.")
            return
        opsi = {label(r): r for _, r in aktif.sort_values("id", ascending=False).head(50).iterrows()}
        pilih = st.selectbox("Pilih transaksi", list(opsi), key=f"kl_{nama_tbl}")
        r = opsi[pilih]
        rid = int(r["id"])
        try:
            cek_terbuka(r["tanggal"])
        except ValueError as e:
            st.warning(f"Transaksi ini tidak bisa diubah: {e}")
            return
        a, b = batas_tanggal()
        tab_edit, tab_batal, tab_hapus = st.tabs(["Edit", "Batalkan (ada jejak)", "Hapus permanen"])

        with tab_edit:
            tgl_lama = date.fromisoformat(str(r["tanggal"])[:10])
            ok = False
            with st.form(f"ef_{nama_tbl}_{rid}"):
                if nama_tbl == "purchases":
                    c = st.columns(3)
                    tgl = c[0].date_input("Tanggal", tgl_lama, min_value=a, max_value=b)
                    supplier = c[1].text_input("Supplier", teks(r["supplier"]))
                    kats = list(AKUN_BELI)
                    kat = c[2].selectbox("Kategori", kats,
                                         index=kats.index(r["kategori"]) if r["kategori"] in kats else 0)
                    c = st.columns(4)
                    item = c[0].text_input("Nama barang", teks(r["item"]))
                    qty = input_angka("Jumlah (sesuai satuan)", f"in_kl_qty_{rid}",
                                      nilai=n(r["qty"]), kolom=c[1])
                    opsi_sat = list(SATUAN_OPSI)
                    sat_lama = satuan_rapi(r["satuan"])
                    if sat_lama and sat_lama not in opsi_sat:
                        opsi_sat.append(sat_lama)
                    sat = c[2].selectbox("Satuan", opsi_sat,
                                         index=opsi_sat.index(sat_lama) if sat_lama in opsi_sat else 0)
                    harga = input_angka("Harga satuan (Rp)", f"in_kl_harga_{rid}",
                                        nilai=n(r["harga"]), kolom=c[3])
                    c = st.columns(2)
                    metode = c[0].selectbox("Pembayaran", ["Tunai", "Utang"],
                                            index=0 if r["metode"] == "Tunai" else 1)
                    tempo_lama = _py(r["jatuh_tempo"])
                    tempo = c[1].date_input("Jatuh tempo (jika utang)",
                                            pd.to_datetime(tempo_lama).date() if tempo_lama else date.today())
                else:
                    c = st.columns(3)
                    tgl = c[0].date_input("Tanggal", tgl_lama, min_value=a, max_value=b)
                    pembeli = c[1].text_input("Pembeli", teks(r["pembeli"]))
                    metode = c[2].selectbox("Pembayaran", ["Tunai", "Piutang"],
                                            index=0 if r["metode"] == "Tunai" else 1)
                    c = st.columns(2)
                    kg = input_angka("Berat (Kg)", f"in_kl_kg_{rid}", nilai=n(r["kg"]), kolom=c[0])
                    hkg = input_angka("Harga per Kg (Rp)", f"in_kl_hkg_{rid}",
                                      nilai=n(r["harga_kg"]), kolom=c[1])
                ok = st.form_submit_button("Simpan perubahan")
            if ok:
                try:
                    if nama_tbl == "purchases":
                        edit_pembelian(r, tgl, supplier, kat, item, qty, sat, harga, metode, tempo)
                    else:
                        edit_penjualan(r, tgl, pembeli, kg, hkg, metode)
                    st.session_state["_pesan"] = "Transaksi berhasil diperbarui."
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menyimpan perubahan: {e}")

        with tab_batal:
            alasan = st.text_input("Alasan pembatalan", key=f"as_{nama_tbl}_{rid}")
            ya = st.checkbox("Saya yakin ingin membatalkan transaksi ini", key=f"ya_{nama_tbl}_{rid}")
            if st.button("Batalkan transaksi", key=f"bb_{nama_tbl}_{rid}"):
                if not alasan.strip():
                    st.error("Alasan pembatalan wajib diisi.")
                elif not ya:
                    st.error("Centang konfirmasi terlebih dahulu.")
                else:
                    try:
                        batalkan(nama_tbl, r, alasan.strip())
                        st.session_state["_pesan"] = "Transaksi dibatalkan, jurnal pembalik dibuat."
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal membatalkan: {e}")

        with tab_hapus:
            st.warning("Transaksi dan jurnalnya dihapus total dan tidak tampil lagi. "
                       "Aksi ini hanya tercatat di Riwayat.")
            ya2 = st.checkbox("Saya yakin ingin menghapus permanen", key=f"yh_{nama_tbl}_{rid}")
            if st.button("Hapus permanen", key=f"bh_{nama_tbl}_{rid}"):
                if not ya2:
                    st.error("Centang konfirmasi terlebih dahulu.")
                else:
                    try:
                        hapus_permanen(nama_tbl, r)
                        st.session_state["_pesan"] = "Transaksi dihapus permanen."
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal menghapus: {e}")


def tautkan_transaksi_lama():
    """Transaksi dari versi lama belum punya nomor jurnal. Cocokkan dengan jurnalnya
    (tanggal + keterangan) supaya bisa diedit/dihapus. Dijalankan sekali."""
    tanda = os.path.join(DATA_LOKAL, "tautan_siap.json")
    if os.path.exists(tanda):
        return
    jr_all = ambil_semua(lambda: tabel("journals").select("*").order("id"))
    dipakai = set()
    for nama, kunci, awalan, ref in (("purchases", "item", "Beli ", "beli"),
                                     ("sales", "pembeli", "Jual lele ke ", "jual")):
        df = tbl(nama, semua=True)
        if df.empty:
            continue
        dipakai |= {int(x) for x in pd.to_numeric(df["journal_id"], errors="coerce").dropna()}
        for _, r in df[df["journal_id"].isna()].sort_values("id").iterrows():
            ket = awalan + teks(r[kunci]).strip()
            for jr in jr_all:
                if jr["id"] in dipakai or jr.get("ref_type") != ref:
                    continue
                if str(jr["tanggal"])[:10] == str(r["tanggal"])[:10] and teks(jr.get("keterangan")).strip() == ket:
                    tabel(nama).update({"journal_id": int(jr["id"])}).eq("id", int(r["id"])).execute()
                    dipakai.add(jr["id"])
                    break
    _tulis("tautan_siap", True)


BEBAS = {"modal", "listrik", "upah", "mati"}   # jurnal yang boleh diubah penuh dari Jurnal Umum


TAK_BISA_HAPUS = {"pakai", "hpp"}   # terkait stok / siklus kolam


def cari_induk(jid):
    """Cari transaksi pembelian/penjualan yang memiliki jurnal ini."""
    for nama in ("purchases", "sales"):
        df = tbl(nama, semua=True)
        if df.empty:
            continue
        m = df[pd.to_numeric(df["journal_id"], errors="coerce") == jid]
        if not m.empty:
            return nama, m.iloc[0]
    return None


def status_jurnal(jid, ref):
    """(boleh_ubah_penuh, penjelasan) untuk tab Edit."""
    ind = cari_induk(jid)
    if ind:
        menu = "Pembelian" if ind[0] == "purchases" else "Penjualan"
        return False, (f"Jurnal ini milik transaksi di menu {menu}. Di sini keterangan boleh diubah. "
                       f"Untuk mengubah nilai atau nama barang/pembeli, pakai menu {menu} (tab Edit).")
    if ref in BEBAS:
        return True, ""
    return False, "Jurnal ini dibuat otomatis oleh fitur lain. Di sini hanya keterangannya yang boleh diubah."


def info_hapus(jid, ref):
    """(boleh_hapus, penjelasan data terkait yang ikut terhapus)."""
    if ref.startswith("pembalik:"):
        return True, "Ini jurnal pembalik. Transaksi yang dibatalkan beserta jurnal aslinya ikut dihapus."
    ind = cari_induk(jid)
    if ind:
        nama, r = ind
        label = "Pembelian" if nama == "purchases" else "Penjualan"
        isi = teks(r["item"]) if nama == "purchases" else teks(r["pembeli"])
        return True, f"{label} #{int(r['id'])} ({isi}) di menu {label} juga ikut terhapus."
    if ref in TAK_BISA_HAPUS:
        return False, ("Jurnal ini terkait stok atau siklus kolam, jadi tidak bisa dihapus sendiri. "
                       "Hapus transaksi induknya terlebih dahulu.")
    if ref == "dp":
        return True, "Data DP pelanggan terkait juga ikut terhapus (hanya jika DP belum terpakai)."
    if ref == "alat":
        return True, "Data peralatan terkait juga ikut terhapus."
    if ref in ("bayar_utang", "terima_piutang"):
        return True, "Transaksi yang dilunasi kembali menjadi belum lunas."
    return True, ""


def simpan_edit_jurnal(jid, tgl, ket, records, penuh):
    if not ket.strip():
        raise ValueError("Keterangan tidak boleh kosong.")
    if not penuh:
        tabel("journals").update({"keterangan": ket.strip()}).eq("id", int(jid)).execute()
        catat("edit_jurnal", f"#{jid}", ket.strip())
        return
    kode_ada = set(tbl("accounts")["kode"].astype(str))
    baris = []
    for rec in records:
        kode = teks(rec.get("kode_akun")).strip()
        d, k = n(rec.get("debit")), n(rec.get("kredit"))
        if not kode and d == 0 and k == 0:
            continue
        if kode not in kode_ada:
            raise ValueError(f"Kode akun '{kode}' tidak ada di daftar akun.")
        if d < 0 or k < 0 or (d > 0 and k > 0) or (d == 0 and k == 0):
            raise ValueError("Tiap baris harus punya debit saja atau kredit saja, lebih dari nol.")
        baris.append((kode, round(d), round(k), teks(rec.get("kolam")).strip() or None))
    if len(baris) < 2:
        raise ValueError("Jurnal minimal terdiri dari 2 baris.")
    tulis_ulang_jurnal(jid, tgl, ket.strip(), baris)
    catat("edit_jurnal", f"#{jid}", ket.strip())


def hapus_transaksi_batal(nama_tbl, r):
    """Hapus transaksi yang sudah dibatalkan beserta jurnal asli dan jurnal pembaliknya."""
    jid = int(r["journal_id"])
    cek_terbuka(r["tanggal"])
    for pj in ambil_semua(lambda: tabel("journals").select("id").eq("ref_type", f"pembalik:{jid}")):
        hapus_jurnal(pj["id"])
    hapus_jurnal(jid)
    if nama_tbl == "purchases" and r["kategori"] != "Operasional":
        for q in (n(r["qty"]), -n(r["qty"])):
            row = cari_stok_row(r["tanggal"], r["kategori"], r["item"], q)
            if row is not None:
                tabel("stock_moves").delete().eq("id", row).execute()
    tabel(nama_tbl).delete().eq("id", int(r["id"])).execute()
    catat("hapus_permanen", f"{nama_tbl}#{int(r['id'])}", "transaksi dibatalkan")


def hapus_jurnal_umum(jid):
    """Hapus jurnal. Karena buku besar, neraca saldo, dan laporan dibaca dari jurnal, semuanya
    otomatis ikut berubah. Data terkait (pembelian/penjualan, DP, peralatan) ikut dibersihkan."""
    j = ambil_semua(lambda: tabel("journals").select("*").eq("id", int(jid)))
    if not j:
        raise ValueError("Jurnal tidak ditemukan.")
    j = j[0]
    ref = teks(j.get("ref_type"))
    cek_terbuka(j["tanggal"])
    boleh, info = info_hapus(jid, ref)
    if not boleh:
        raise ValueError(info)
    asli = int(ref.split(":")[1]) if ref.startswith("pembalik:") else int(jid)
    ind = cari_induk(asli)
    if ind:
        nama, r = ind
        if bool(r["batal"]):
            hapus_transaksi_batal(nama, r)
            return
        if not ref.startswith("pembalik:"):
            hapus_permanen(nama, r)
            return
    ket = teks(j.get("keterangan"))
    baris = ambil_semua(lambda: tabel("journal_lines").select("*").eq("journal_id", int(jid)))
    nilai = sum(n(x["debit"]) for x in baris)
    if ref == "dp":
        dpt = tbl("down_payments")
        m = dpt[pd.to_numeric(dpt["journal_id"], errors="coerce") == jid] if not dpt.empty else dpt
        if not m.empty:
            d = m.iloc[0]
            if n(d["sisa"]) < n(d["jumlah"]):
                raise ValueError("DP ini sudah terpakai pada penjualan, tidak bisa dihapus.")
            tabel("down_payments").delete().eq("id", int(d["id"])).execute()
    elif ref == "alat":
        e = tbl("equipment")
        nm = ket.replace("Beli peralatan", "").strip()
        m = e[(e.nama == nm) & (e.tanggal.astype(str) == str(j["tanggal"])[:10])] if not e.empty else e
        if not m.empty:
            tabel("equipment").delete().eq("id", int(m.iloc[0]["id"])).execute()
    elif ref == "bayar_utang":
        pb = tbl("purchases")
        if not pb.empty:
            m = pb[(pb.metode == "Utang") & pb["lunas"].fillna(False).astype(bool)
                   & (pd.to_numeric(pb.total, errors="coerce") == nilai)]
            m = m[[teks(x) in ket for x in m.supplier]]
            if not m.empty:
                tabel("purchases").update({"lunas": False}).eq("id", int(m.iloc[0]["id"])).execute()
    elif ref == "terima_piutang":
        sl = tbl("sales")
        if not sl.empty:
            m = sl[(sl.metode == "Piutang") & sl["lunas"].fillna(False).astype(bool) & (sl.piutang == nilai)]
            m = m[[teks(x) in ket for x in m.pembeli]]
            if not m.empty:
                tabel("sales").update({"lunas": False}).eq("id", int(m.iloc[0]["id"])).execute()
    hapus_jurnal(int(jid))
    catat("hapus_jurnal", f"#{jid}", ket)


def ui_edit_jurnal(l):
    with st.expander("Edit / hapus jurnal"):
        daftar = l.drop_duplicates("journal_id").sort_values("journal_id", ascending=False).head(100)
        opsi = {f"#{int(r.journal_id)} · {r.tanggal} · {r.keterangan}": int(r.journal_id)
                for r in daftar.itertuples()}
        if not opsi:
            st.info("Belum ada jurnal.")
            return
        pilih = st.selectbox("Pilih jurnal", list(opsi), key="ej_pilih")
        jid = opsi[pilih]
        j = l[l.journal_id == jid]
        tgl_j = str(j.iloc[0]["tanggal"])[:10]
        ref = teks(j.iloc[0]["ref_type"])
        try:
            cek_terbuka(tgl_j)
        except ValueError as e:
            st.warning(f"Jurnal ini tidak bisa diubah atau dihapus: {e}")
            return
        penuh, info = status_jurnal(jid, ref)
        t1, t2 = st.tabs(["Edit", "Hapus"])
        with t1:
            a, b = batas_tanggal()
            ok = False
            with st.form(f"ej_{jid}"):
                ket = st.text_input("Keterangan", teks(j.iloc[0]["keterangan"]))
                tgl = date.fromisoformat(tgl_j)
                records = []
                if penuh:
                    tgl = st.date_input("Tanggal", min(max(tgl, a), b), min_value=a, max_value=b)
                    ed = st.data_editor(
                        j[["kode_akun", "debit", "kredit", "kolam"]].reset_index(drop=True),
                        num_rows="dynamic", hide_index=True, use_container_width=True,
                        key=f"edl_{jid}",
                        column_config={
                            "debit": st.column_config.NumberColumn("debit (Rp)", min_value=0,
                                                                   step=1, format="Rp %d"),
                            "kredit": st.column_config.NumberColumn("kredit (Rp)", min_value=0,
                                                                    step=1, format="Rp %d"),
                        })
                    records = ed.to_dict("records")
                ok = st.form_submit_button("Simpan perubahan")
            if ok:
                try:
                    simpan_edit_jurnal(jid, tgl, ket, records, penuh)
                    st.session_state["_pesan"] = "Jurnal berhasil diperbarui."
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menyimpan: {e}")
        with t2:
            boleh, ihapus = info_hapus(jid, ref)
            if not boleh:
                st.info(ihapus)
            else:
                st.warning("Jurnal dihapus total dan otomatis hilang dari buku besar, neraca saldo, "
                           "neraca, laba rugi, dan semua laporan lain. " + ihapus)
                ya = st.checkbox("Saya yakin ingin menghapus jurnal ini", key=f"yj_{jid}")
                if st.button("Hapus jurnal", key=f"hj_{jid}"):
                    if not ya:
                        st.error("Centang konfirmasi terlebih dahulu.")
                    else:
                        try:
                            hapus_jurnal_umum(jid)
                            st.session_state["_pesan"] = "Jurnal dan data terkaitnya dihapus."
                            st.rerun()
                        except Exception as e:
                            st.error(f"Gagal menghapus: {e}")


def kosongkan_transaksi():
    """Hapus semua transaksi dan riwayat aktivitas; data master tetap ada.
    Cadangan otomatis disimpan lebih dulu di data_lokal/cadangan."""
    os.makedirs(os.path.join(DATA_LOKAL, "cadangan"), exist_ok=True)
    nama_file = f"sebelum_kosongkan_{datetime.now(ZONA).strftime('%Y%m%d_%H%M%S')}.json"
    with open(os.path.join(DATA_LOKAL, "cadangan", nama_file), "w", encoding="utf-8") as f:
        f.write(cadangan())
    for nama in ("journal_lines", "journals", "purchases", "sales", "stock_moves", "equipment"):
        db().table(nama).delete().gte("id", 0).execute()
    for nama in ("cycles", "fish_deaths", "down_payments", "periods", "activity_log"):
        _tulis(nama, [])
    for nama in EKSTRA:
        _tulis("ekstra_" + nama, {})
    st.session_state["_per"] = None
    return nama_file


SALDO_NORMAL = {"Aset": "Debit", "Beban": "Debit", "Kewajiban": "Kredit",
                "Ekuitas": "Kredit", "Pendapatan": "Kredit"}


def tambah_akun(kode, nama, tipe):
    kode, nama = kode.strip(), nama.strip()
    if not (kode.isdigit() and len(kode) == 4):
        raise ValueError("Kode akun harus 4 angka, misalnya 1130.")
    if not nama:
        raise ValueError("Nama akun wajib diisi.")
    if tipe not in SALDO_NORMAL:
        raise ValueError("Tipe akun tidak dikenal.")
    if kode in set(tbl("accounts")["kode"].astype(str)):
        raise ValueError(f"Kode {kode} sudah dipakai.")
    tabel("accounts").insert({"kode": kode, "nama": nama, "tipe": tipe}).execute()
    catat("tambah_akun", kode, f"{nama} ({tipe})")


def ubah_nama_akun(kode, nama):
    if not nama.strip():
        raise ValueError("Nama akun wajib diisi.")
    lama = tbl("accounts")
    lama = lama[lama["kode"].astype(str) == str(kode)]
    if lama.empty:
        raise ValueError("Akun tidak ditemukan.")
    tabel("accounts").update({"nama": nama.strip()}).eq("kode", str(kode)).execute()
    catat("ubah_akun", kode, f"{lama.iloc[0]['nama']} -> {nama.strip()}")


def halaman_master():
    st.title("📇 Data Master")
    t1, t2, t3, t4 = st.tabs(["Supplier", "Pelanggan", "Barang", "Daftar Akun"])
    with t1:
        editor_master("suppliers",
                      ["nama", "jenis_pasokan", "daerah", "kontak", "termin", "catatan", "aktif"],
                      {"jenis_pasokan": st.column_config.SelectboxColumn(options=["Bibit", "Pakan", "Lainnya"])})
    with t2:
        editor_master("customers",
                      ["nama", "kategori", "kontak", "aturan_dp", "catatan", "aktif"],
                      {"kategori": st.column_config.SelectboxColumn(
                          options=["Pelanggan tetap", "SPPG", "Pembeli langsung"])})
    with t3:
        editor_master("items",
                      ["nama", "kategori", "satuan", "harga_ref", "stok_min", "aktif"],
                      {"kategori": st.column_config.SelectboxColumn(options=["Bibit", "Pakan", "Operasional"]),
                       "satuan": st.column_config.SelectboxColumn(options=SATUAN_OPSI),
                       "harga_ref": st.column_config.NumberColumn("harga_ref (Rp)", min_value=0,
                                                                  step=1, format="Rp %d"),
                       "stok_min": st.column_config.NumberColumn("stok_min (batas peringatan, sesuai satuan)",
                                                                 min_value=0, step=1, format="%d")})

    with t4:
        akun = tbl("accounts")
        tampil = akun[["kode", "nama", "tipe"]].copy()
        tampil["saldo_normal"] = tampil["tipe"].map(SALDO_NORMAL)
        l = lines()
        pakai = l.groupby("kode_akun").size() if not l.empty else pd.Series(dtype=int)
        tampil["jumlah_baris_jurnal"] = tampil["kode"].map(pakai).fillna(0).astype(int)
        st.dataframe(tampil, hide_index=True, use_container_width=True)
        st.subheader("Tambah akun")
        with st.form("akun_baru", clear_on_submit=True):
            c = st.columns(3)
            kode = c[0].text_input("Kode akun (4 angka)")
            nama = c[1].text_input("Nama akun")
            tipe = c[2].selectbox("Tipe", list(SALDO_NORMAL))
            tambah = st.form_submit_button("Tambah akun")
        if tambah:
            try:
                tambah_akun(kode, nama, tipe)
                st.session_state["_pesan"] = f"Akun {kode.strip()} ditambahkan."
                st.rerun()
            except Exception as e:
                st.error(str(e))
        st.subheader("Ubah nama akun")
        with st.form("akun_ubah", clear_on_submit=True):
            c = st.columns(2)
            pilih = c[0].selectbox("Akun", (akun["kode"].astype(str) + " - " + akun["nama"]).tolist())
            baru = c[1].text_input("Nama baru")
            ubah = st.form_submit_button("Simpan nama")
        if ubah:
            try:
                ubah_nama_akun(pilih.split(" - ")[0], baru)
                st.session_state["_pesan"] = "Nama akun diperbarui."
                st.rerun()
            except Exception as e:
                st.error(str(e))




# ------------------------- 5 transaksi pengujian (Modul bagian 14) -------------------------
BIBIT_ITEM = "Bibit Lele Mutiara"
PAKAN_ITEM = "Pakan Tongwe"


def isi_lima_transaksi():
    """Lima transaksi pengujian sesuai Modul Rancangan bagian 14 (nominal simulasi
    yang konsisten dengan hasil wawancara). Semuanya diposting di periode aktif."""
    if ambil_semua(lambda: tabel("journals").select("id")):
        raise ValueError("Sudah ada transaksi, data pengujian hanya bisa diisi pada database yang masih kosong.")
    awal, akhir = rentang(periode_aktif())

    def T(k):
        return min(awal + timedelta(days=k), akhir)

    def beli(tgl, kat, item, qty, sat, harga, metode, supplier, awalan, tempo=None):
        total = qty * harga
        kredit = KAS if metode == "Tunai" else UTANG
        jid = jurnal(tgl, f"{awalan}Beli {item}",
                     [(AKUN_BELI[kat], total, 0), (kredit, 0, total)], "beli")
        pid = tabel("purchases").insert({
            "tanggal": str(tgl), "supplier": supplier, "kategori": kat, "item": item,
            "qty": qty, "satuan": sat, "harga": harga, "total": total, "metode": metode,
            "jatuh_tempo": str(tempo) if tempo else None,
            "lunas": metode == "Tunai", "journal_id": jid
        }).execute().data[0]["id"]
        tabel("stock_moves").insert({"tanggal": str(tgl), "kategori": kat,
                                     "item": item, "qty": qty}).execute()
        return pid

    # Transaksi 1: beli bibit 5.000 ekor untuk KLM-01, tunai, Rp185 per ekor
    beli(T(0), "Bibit", BIBIT_ITEM, 5000, "Ekor", 185, "Tunai",
         "Supplier Bibit Ungaran", "Transaksi 1: ")

    # Transaksi 2: beli pakan 17 sak (510 Kg) kredit dari langganan, Rp465.000 per sak (Rp15.500/Kg)
    pid_pakan = beli(T(1), "Pakan", PAKAN_ITEM, 510, "Kg", 15500, "Utang",
                     "Toko Pakan Langganan", "Transaksi 2: ", tempo=T(1) + timedelta(days=14))

    # Transaksi 3: tebar bibit, pakai pakan 510 Kg di KLM-01, bayar listrik dan air Rp2.000.000 tunai
    pakai_stok(T(2), "Bibit", BIBIT_ITEM, 5000, "KLM-01", awalan="Transaksi 3: ")
    tabel("cycles").insert({
        "kolam": "KLM-01", "tgl_tebar": str(T(2)), "jumlah_bibit": 5000, "musim": "Kemarau",
        "status": "berjalan", "perkiraan_panen": str(T(2) + timedelta(days=LAMA_PELIHARA["Kemarau"]))
    }).execute()
    pakai_stok(T(2), "Pakan", PAKAN_ITEM, 510, "KLM-01", awalan="Transaksi 3: ")
    jurnal(T(2), "Transaksi 3: Biaya listrik dan air KLM-01",
           [(KOLAM_AKUN, 2000000, 0, "KLM-01"), (KAS, 0, 2000000)], "listrik")

    # Transaksi 4: terima DP Rp3.000.000, panen KLM-01 dan jual 500 Kg x Rp25.000, sisanya tunai
    jid_dp = jurnal(T(3), "Transaksi 4: DP pelanggan Pelanggan Tetap 1",
                    [(KAS, 3000000, 0), (DP_AKUN, 0, 3000000)], "dp")
    dp_id = tabel("down_payments").insert({
        "tanggal": str(T(3)), "pembeli": "Pelanggan Tetap 1", "jumlah": 3000000,
        "sisa": 0, "journal_id": jid_dp
    }).execute().data[0]["id"]
    total = 500 * 25000
    jid_jual = jurnal(T(4), "Transaksi 4: Jual lele ke Pelanggan Tetap 1",
                      [(KAS, total - 3000000, 0), (DP_AKUN, 3000000, 0), (JUAL, 0, total)], "jual")
    tabel("sales").insert({
        "tanggal": str(T(4)), "pembeli": "Pelanggan Tetap 1", "kg": 500, "harga_kg": 25000,
        "total": total, "metode": "Tunai", "lunas": True, "journal_id": jid_jual,
        "dp_dipakai": 3000000, "kolam": "KLM-01"
    }).execute()
    x = lines()
    kk = x[(x.kolam == "KLM-01") & (x.kode_akun == KOLAM_AKUN)]
    biaya = round(float(kk.debit.sum() - kk.kredit.sum()), 2)
    jurnal(T(4), "Transaksi 4: HPP panen KLM-01",
           [(HPP, biaya, 0, "KLM-01"), (KOLAM_AKUN, 0, biaya, "KLM-01")], "hpp")
    run = tbl("cycles")
    run = run[(run.kolam == "KLM-01") & (run.status == "berjalan")]
    tabel("cycles").update({
        "status": "selesai", "tgl_panen": str(T(4)), "kg_panen": 500,
        "penjualan": total, "hpp": biaya
    }).eq("id", int(run.iloc[0].id)).execute()

    # Transaksi 5: bayar utang pakan Rp7.905.000 dan upah panen Rp300.000
    jurnal(T(5), "Transaksi 5: Bayar utang Toko Pakan Langganan",
           [(UTANG, 7905000, 0), (KAS, 0, 7905000)], "bayar_utang")
    tabel("purchases").update({"lunas": True}).eq("id", pid_pakan).execute()
    jurnal(T(5), "Transaksi 5: Upah panen KLM-01",
           [(UPAH, 300000, 0, "KLM-01"), (KAS, 0, 300000)], "upah")
    catat("isi_lima_transaksi")


def halaman_periode():
    st.title("📅 Periode")
    if st.session_state.get("_pesan"):
        st.success(st.session_state.pop("_pesan"))
    key = periode_aktif()
    awal, akhir = rentang(key)
    st.subheader(f"Tutup periode {nama_bulan(key)}")

    s = saldo(awal, akhir)
    s_kum = saldo(None, akhir)
    penjualan_bln = float(s["saldo"].get(JUAL, 0))
    laba_bln = laba_bersih(s)
    kas_akhir = float(s_kum["saldo"].get(KAS, 0))

    m = st.columns(4)
    m[0].metric("Penjualan", rp(penjualan_bln), border=True)
    m[1].metric("Laba bersih", rp(laba_bln), border=True)
    m[2].metric("Kas akhir", rp(kas_akhir), border=True)
    m[3].metric("Kolam berjalan", int((tabel_kolam().status != "Kosong").sum()), border=True)
    m = st.columns(2)
    m[0].metric("Piutang", rp(s_kum["saldo"].get(PIUTANG, 0)), border=True)
    m[1].metric("Utang usaha", rp(s_kum["saldo"].get(UTANG, 0)), border=True)

    st.markdown("**Cek kesiapan**")
    seimbang = round(float(s_kum["debit"].sum() - s_kum["kredit"].sum()), 2) == 0
    susut_ok = tbl("equipment").empty or sudah_susut(key)
    cek1 = st.checkbox("Stok pakan dan bibit sudah dicocokkan dengan stok di gudang",
                       key=f"cek_stok_{key}")
    st.write(("✅" if seimbang else "❌") + " Neraca saldo seimbang")
    st.write(("✅" if susut_ok else "❌") + " Penyusutan bulan ini sudah dijalankan (menu Peralatan)")
    st.write(("✅" if cek1 else "❌") + " Stok sudah dicek")
    siap = seimbang and susut_ok and cek1

    konfirmasi = st.text_input(f'Ketik "{nama_bulan(key)}" untuk konfirmasi')
    if st.button("Tutup Periode", disabled=not siap):
        if konfirmasi.strip() != nama_bulan(key):
            st.error("Nama bulan konfirmasi tidak sesuai.")
        else:
            try:
                tabel("periods").insert({
                    "bulan": key, "tgl_tutup": str(date.today()),
                    "penjualan": penjualan_bln, "laba": laba_bln, "kas": kas_akhir
                }).execute()
                catat("tutup_periode", key, f"laba {rp(laba_bln)}")
                st.session_state["_per"] = None
                st.success(f"{nama_bulan(key)} ditutup. Periode berikutnya terbuka.")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menutup periode: {e}")

    st.subheader("Cadangan data")
    if st.button("Siapkan cadangan (JSON)"):
        st.session_state["_backup"] = cadangan()
    if st.session_state.get("_backup"):
        st.download_button("⬇️ Unduh cadangan", st.session_state["_backup"],
                           file_name=f"cadangan_{key}.json", mime="application/json")

    st.subheader("Riwayat periode")
    p = tbl("periods")
    if p.empty:
        st.info("Belum ada data.")
    else:
        st.dataframe(fmt_rp(p.sort_values("bulan", ascending=False), ["penjualan", "laba", "kas"]),
                     hide_index=True, use_container_width=True)
        terakhir = max(p["bulan"])
        with st.expander(f"Buka kembali {nama_bulan(terakhir)}"):
            alasan = st.text_input("Alasan membuka kembali", key="alasan_buka")
            if st.button("Buka kembali periode terakhir"):
                x = filter_tgl(lines(), awal, akhir)
                if not x.empty:
                    st.error("Periode berikutnya sudah berisi transaksi. "
                             "Koreksi lewat transaksi di periode aktif.")
                elif not alasan.strip():
                    st.error("Alasan wajib diisi.")
                else:
                    try:
                        tabel("periods").delete().eq("bulan", terakhir).execute()
                        catat("buka_kembali_periode", terakhir, alasan.strip())
                        st.session_state["_per"] = None
                        st.success(f"{nama_bulan(terakhir)} dibuka kembali.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal membuka kembali: {e}")


# ------------------------- Riwayat -------------------------
def halaman_riwayat():
    st.title("🧾 Riwayat Aktivitas")
    lg = tbl("activity_log")
    if lg.empty:
        st.info("Belum ada data.")
        return
    lg = lg.sort_values("id", ascending=False)
    aksi = st.multiselect("Filter aksi", sorted(lg.aksi.dropna().unique()))
    if aksi:
        lg = lg[lg.aksi.isin(aksi)]
    st.dataframe(lg.head(500), hide_index=True, use_container_width=True)


# ------------------------- Router utama (REVISI TAMPILAN) -------------------------
def main():
    # Gerbang login: tidak ada halaman/data yang dimuat sebelum login berhasil.
    if not st.session_state.get("login"):
        halaman_login()
        return

    # Daftar halaman. Menu ditaruh di bagian atas halaman, bukan di sidebar.
    PAGE_OBJ.clear()
    PAGE_OBJ.update({
        "Dashboard": st.Page(dashboard, title="Beranda", icon=":material/home:",
                             url_path="beranda", default=True),
        "Pembelian": st.Page(pembelian, title="Pembelian", icon=":material/receipt_long:",
                             url_path="pembelian"),
        "Penjualan": st.Page(penjualan, title="Penjualan", icon=":material/storefront:",
                             url_path="penjualan"),
        "Budidaya": st.Page(budidaya, title="Budidaya", icon=":material/water:",
                            url_path="budidaya"),
        "Peralatan": st.Page(peralatan, title="Peralatan", icon=":material/handyman:",
                             url_path="peralatan"),
        "Akses Cepat": st.Page(akses_cepat, title="Laporan", icon=":material/query_stats:",
                               url_path="laporan"),
        "Data Master": st.Page(halaman_master, title="Data Master", icon=":material/database:",
                               url_path="data-master"),
        "Periode": st.Page(halaman_periode, title="Periode", icon=":material/lock_clock:",
                           url_path="periode"),
        "Riwayat": st.Page(halaman_riwayat, title="Riwayat", icon=":material/manage_history:",
                           url_path="riwayat"),
    })
    P = PAGE_OBJ
    pg = st.navigation({
        "Beranda": [P["Dashboard"]],
        "Transaksi": [P["Pembelian"], P["Penjualan"]],
        "Produksi": [P["Budidaya"], P["Peralatan"]],
        "Laporan": [P["Akses Cepat"]],
        "Pengaturan": [P["Data Master"], P["Periode"], P["Riwayat"]],
    }, position="top")

    # Baris kepala: nama aplikasi di kiri, akun pengguna di kanan.
    kepala, akun = st.columns([5, 1.3], vertical_alignment="center")
    kepala.markdown("#### Lele Segar 🐠")
    with akun.popover(st.session_state.get("user", "Akun"),
                      icon=":material/account_circle:", use_container_width=True):
        st.caption("Masuk sebagai")
        st.markdown(f"**{st.session_state.get('user', '')}**")
        st.button("Keluar", on_click=logout, use_container_width=True,
                  icon=":material/logout:")

    # Titik ribuan otomatis pada semua kolom isian angka (Rp, Kg, Ekor).
    pasang_pemformat()

    try:
        # Tes koneksi dan pastikan daftar akun ada sebelum menampilkan halaman.
        pastikan_akun()
        pastikan_master()
        tautkan_transaksi_lama()
        st.session_state["_per"] = None          # hitung ulang periode aktif tiap muat
        key = periode_aktif()
        kepala.caption(f"Periode aktif: **{nama_bulan(key)}** · :green[terbuka]")
        if st.session_state.get("_pesan"):
            st.success(st.session_state.pop("_pesan"))
        pg.run()
    except Exception as e:
        st.error("Aplikasi belum dapat memuat data dari Supabase.")
        st.exception(e)
        st.info(
            "Periksa file kunci.toml: SUPABASE_URL dan SUPABASE_KEY harus berasal "
            "dari project Supabase yang sama. Pastikan juga seluruh tabel pada SQL "
            "schema sudah dibuat (termasuk migrasi.sql) dan API Data pada project "
            "tersebut aktif."
        )


if __name__ == "__main__":
    main()