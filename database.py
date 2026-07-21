"""
database.py
Modul koneksi & skema database SQLite untuk Aplikasi Absensi Pesantren.
Semua akses database (query) dipusatkan di sini biar gampang dirawat.
"""

import sqlite3
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent / "instance" / "absensi.db"


def get_connection():
    """Buka koneksi baru ke database. row_factory diset biar hasil query
    bisa diakses seperti dictionary (row['nama']) bukan cuma index angka."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Membuat tabel-tabel yang dibutuhkan kalau belum ada.
    Aman dipanggil berkali-kali (idempotent)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    cur = conn.cursor()

    # Tabel akun admin/staff yang boleh login ke sistem
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pengguna (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nama_lengkap TEXT NOT NULL,
            peran TEXT NOT NULL DEFAULT 'staff',  -- 'admin' atau 'staff'
            dibuat_pada TEXT NOT NULL
        )
    """)

    # Tabel data santri/karyawan yang absen
    cur.execute("""
        CREATE TABLE IF NOT EXISTS anggota (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kode_kartu TEXT UNIQUE NOT NULL,   -- kode dari hasil scan barcode/QR
            nama TEXT NOT NULL,
            nis TEXT,                           -- nomor induk santri/siswa
            kelompok TEXT,                      -- kelas/asrama/divisi
            alamat TEXT,
            status TEXT NOT NULL DEFAULT 'aktif',  -- aktif / nonaktif
            dibuat_pada TEXT NOT NULL
        )
    """)

    # Migrasi: tambah kolom baru kalau database lama belum punya (aman dipanggil berkali-kali)
    kolom_anggota = [r["name"] for r in cur.execute("PRAGMA table_info(anggota)").fetchall()]
    if "nis" not in kolom_anggota:
        cur.execute("ALTER TABLE anggota ADD COLUMN nis TEXT")
    if "alamat" not in kolom_anggota:
        cur.execute("ALTER TABLE anggota ADD COLUMN alamat TEXT")

    # Tabel catatan absensi
    cur.execute("""
        CREATE TABLE IF NOT EXISTS absensi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anggota_id INTEGER NOT NULL,
            waktu TEXT NOT NULL,        -- timestamp lengkap ISO
            tanggal TEXT NOT NULL,      -- tanggal saja, buat gampang filter/laporan
            tipe TEXT NOT NULL,         -- 'masuk' atau 'pulang'
            FOREIGN KEY (anggota_id) REFERENCES anggota(id)
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_absensi_tanggal ON absensi(tanggal)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_absensi_anggota ON absensi(anggota_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_anggota_kode ON anggota(kode_kartu)")

    conn.commit()
    conn.close()


# ---------- Query: Pengguna (akun login) ----------

def get_pengguna_by_username(username: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM pengguna WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return row


def create_pengguna(username: str, password_hash: str, nama_lengkap: str, peran: str = "staff"):
    conn = get_connection()
    conn.execute(
        "INSERT INTO pengguna (username, password_hash, nama_lengkap, peran, dibuat_pada) "
        "VALUES (?, ?, ?, ?, ?)",
        (username, password_hash, nama_lengkap, peran, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def count_pengguna():
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) AS c FROM pengguna").fetchone()["c"]
    conn.close()
    return total


# ---------- Query: Anggota (santri/karyawan) ----------

def get_anggota_by_kode(kode_kartu: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM anggota WHERE kode_kartu = ? AND status = 'aktif'", (kode_kartu,)
    ).fetchone()
    conn.close()
    return row


def get_all_anggota():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM anggota ORDER BY nama ASC").fetchall()
    conn.close()
    return rows


def get_anggota_by_id(anggota_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM anggota WHERE id = ?", (anggota_id,)).fetchone()
    conn.close()
    return row


def create_anggota(kode_kartu: str, nama: str, kelompok: str, nis: str = None, alamat: str = None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO anggota (kode_kartu, nama, nis, kelompok, alamat, status, dibuat_pada) "
        "VALUES (?, ?, ?, ?, ?, 'aktif', ?)",
        (kode_kartu, nama, nis, kelompok, alamat, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def update_anggota(anggota_id: int, kode_kartu: str, nama: str, kelompok: str, status: str,
                    nis: str = None, alamat: str = None):
    conn = get_connection()
    conn.execute(
        "UPDATE anggota SET kode_kartu = ?, nama = ?, nis = ?, kelompok = ?, alamat = ?, status = ? "
        "WHERE id = ?",
        (kode_kartu, nama, nis, kelompok, alamat, status, anggota_id),
    )
    conn.commit()
    conn.close()


def delete_anggota(anggota_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM anggota WHERE id = ?", (anggota_id,))
    conn.commit()
    conn.close()


# ---------- Query: Absensi ----------

def get_absensi_terakhir_hari_ini(anggota_id: int):
    """Ambil record absensi paling akhir buat anggota ini hari ini,
    dipakai buat nentuin scan berikutnya itu 'masuk' atau 'pulang'."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM absensi WHERE anggota_id = ? AND tanggal = ? "
        "ORDER BY waktu DESC LIMIT 1",
        (anggota_id, date.today().isoformat()),
    ).fetchone()
    conn.close()
    return row


def cegah_scan_ganda(anggota_id: int, jeda_detik: int = 60):
    """Cek apakah anggota ini baru aja scan dalam beberapa detik terakhir,
    buat mencegah 1 orang kecatat 2x gara-gara scan gak sengaja berkali-kali."""
    conn = get_connection()
    row = conn.execute(
        "SELECT waktu FROM absensi WHERE anggota_id = ? ORDER BY waktu DESC LIMIT 1",
        (anggota_id,),
    ).fetchone()
    conn.close()
    if not row:
        return False
    waktu_terakhir = datetime.fromisoformat(row["waktu"])
    selisih = (datetime.now() - waktu_terakhir).total_seconds()
    return selisih < jeda_detik


def create_absensi(anggota_id: int, tipe: str):
    conn = get_connection()
    now = datetime.now()
    conn.execute(
        "INSERT INTO absensi (anggota_id, waktu, tanggal, tipe) VALUES (?, ?, ?, ?)",
        (anggota_id, now.isoformat(), now.date().isoformat(), tipe),
    )
    conn.commit()
    conn.close()


def get_absensi_terbaru(limit: int = 10):
    """Buat feed 'scan terakhir' di halaman utama."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT absensi.waktu, absensi.tipe, anggota.nama, anggota.kelompok
        FROM absensi
        JOIN anggota ON anggota.id = absensi.anggota_id
        ORDER BY absensi.waktu DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def get_laporan(tanggal_mulai: str, tanggal_akhir: str, kelompok: str = None):
    conn = get_connection()
    query = """
        SELECT absensi.tanggal, absensi.waktu, absensi.tipe, anggota.nama, anggota.kelompok
        FROM absensi
        JOIN anggota ON anggota.id = absensi.anggota_id
        WHERE absensi.tanggal BETWEEN ? AND ?
    """
    params = [tanggal_mulai, tanggal_akhir]
    if kelompok:
        query += " AND anggota.kelompok = ?"
        params.append(kelompok)
    query += " ORDER BY absensi.waktu DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_semua_kelompok():
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT kelompok FROM anggota WHERE kelompok IS NOT NULL ORDER BY kelompok"
    ).fetchall()
    conn.close()
    return [r["kelompok"] for r in rows]