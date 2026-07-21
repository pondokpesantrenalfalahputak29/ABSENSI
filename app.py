"""
app.py
Aplikasi Absensi Pesantren - entry point Flask.

Alur inti:
  1. Halaman "Scan" punya 1 input field tersembunyi yang selalu fokus.
  2. Mesin scanner (USB HID) "mengetik" kode + Enter ke field itu.
  3. JS mengirim kode itu ke endpoint /scan lewat fetch().
  4. Server cocokkan kode ke tabel anggota, tentukan masuk/pulang, simpan.
"""

import base64
import csv
import io
from datetime import date, datetime, timedelta
from functools import wraps

import qrcode
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, Response
)
from werkzeug.security import generate_password_hash, check_password_hash

import database as db

app = Flask(__name__)
app.secret_key = "GANTI_DENGAN_SECRET_KEY_ACAK_SEBELUM_PRODUCTION"  # TODO: pindah ke env var


def buat_qr_base64(data: str) -> str:
    """Generate QR code untuk sebuah string (kode_kartu) dan kembalikan
    sebagai gambar PNG base64, siap ditempel langsung ke <img src="data:...">.
    Dibuat sepenuhnya di server (offline), tidak butuh koneksi internet."""
    img = qrcode.make(data, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------- Helper: proteksi login ----------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("peran") != "admin":
            flash("Halaman ini khusus admin.", "error")
            return redirect(url_for("beranda"))
        return view(*args, **kwargs)
    return wrapped


# ---------- Setup awal ----------

@app.before_request
def pastikan_ada_admin():
    """Kalau belum ada akun sama sekali, arahkan ke halaman setup akun admin pertama."""
    if request.endpoint in ("setup_admin", "static"):
        return
    if db.count_pengguna() == 0:
        return redirect(url_for("setup_admin"))


@app.route("/setup", methods=["GET", "POST"])
def setup_admin():
    if db.count_pengguna() > 0:
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        nama_lengkap = request.form["nama_lengkap"].strip()

        if not username or not password or not nama_lengkap:
            flash("Semua kolom wajib diisi.", "error")
            return render_template("setup_admin.html")

        db.create_pengguna(username, generate_password_hash(password), nama_lengkap, peran="admin")
        flash("Akun admin berhasil dibuat. Silakan login.", "success")
        return redirect(url_for("login"))

    return render_template("setup_admin.html")


# ---------- Auth ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = db.get_pengguna_by_username(username)

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Username atau password salah.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["nama_lengkap"] = user["nama_lengkap"]
        session["peran"] = user["peran"]
        return redirect(url_for("beranda"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Halaman utama: layar scan ----------

@app.route("/")
@login_required
def beranda():
    feed = db.get_absensi_terbaru(limit=10)
    return render_template("beranda.html", feed=feed, hari_ini=date.today().strftime("%A, %d %B %Y"))


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    """Dipanggil via fetch() dari JS setiap kali scanner mengirim kode."""
    kode = (request.json or {}).get("kode", "").strip()
    if not kode:
        return jsonify({"ok": False, "pesan": "Kode kosong."}), 400

    anggota = db.get_anggota_by_kode(kode)
    if anggota is None:
        return jsonify({"ok": False, "pesan": f"Kode '{kode}' tidak terdaftar."}), 404

    if db.cegah_scan_ganda(anggota["id"], jeda_detik=60):
        return jsonify({
            "ok": False,
            "pesan": f"{anggota['nama']} baru saja tercatat. Tunggu sebentar sebelum scan ulang."
        }), 429

    absensi_terakhir = db.get_absensi_terakhir_hari_ini(anggota["id"])
    tipe = "pulang" if (absensi_terakhir and absensi_terakhir["tipe"] == "masuk") else "masuk"

    db.create_absensi(anggota["id"], tipe)

    return jsonify({
        "ok": True,
        "nama": anggota["nama"],
        "kelompok": anggota["kelompok"],
        "tipe": tipe,
        "waktu": datetime.now().strftime("%H:%M:%S"),
    })


# ---------- Kelola anggota (santri) ----------

@app.route("/anggota")
@login_required
def daftar_anggota():
    return render_template("anggota.html", daftar=db.get_all_anggota())


@app.route("/anggota/tambah", methods=["GET", "POST"])
@login_required
def tambah_anggota():
    if request.method == "POST":
        try:
            db.create_anggota(
                kode_kartu=request.form["kode_kartu"].strip(),
                nama=request.form["nama"].strip(),
                kelompok=request.form["kelompok"].strip(),
                nis=request.form.get("nis", "").strip(),
                alamat=request.form.get("alamat", "").strip(),
            )
            flash("Data anggota berhasil ditambahkan.", "success")
            return redirect(url_for("daftar_anggota"))
        except Exception as e:
            flash(f"Gagal menyimpan: kode kartu mungkin sudah dipakai. ({e})", "error")

    return render_template("form_anggota.html", anggota=None)


@app.route("/anggota/<int:anggota_id>/ubah", methods=["GET", "POST"])
@login_required
def ubah_anggota(anggota_id):
    if request.method == "POST":
        db.update_anggota(
            anggota_id=anggota_id,
            kode_kartu=request.form["kode_kartu"].strip(),
            nama=request.form["nama"].strip(),
            kelompok=request.form["kelompok"].strip(),
            status=request.form["status"],
            nis=request.form.get("nis", "").strip(),
            alamat=request.form.get("alamat", "").strip(),
        )
        flash("Data anggota berhasil diperbarui.", "success")
        return redirect(url_for("daftar_anggota"))

    anggota = db.get_anggota_by_id(anggota_id)
    return render_template("form_anggota.html", anggota=anggota)


@app.route("/anggota/<int:anggota_id>/hapus", methods=["POST"])
@admin_required
def hapus_anggota(anggota_id):
    db.delete_anggota(anggota_id)
    flash("Data anggota dihapus.", "success")
    return redirect(url_for("daftar_anggota"))


# ---------- Cetak kartu identitas + QR ----------

@app.route("/anggota/<int:anggota_id>/kartu")
@login_required
def kartu_anggota(anggota_id):
    """Halaman cetak 1 kartu identitas (NIS, Nama, Kelas, Alamat) + QR kode_kartu."""
    anggota = db.get_anggota_by_id(anggota_id)
    if anggota is None:
        flash("Data anggota tidak ditemukan.", "error")
        return redirect(url_for("daftar_anggota"))

    qr_b64 = buat_qr_base64(anggota["kode_kartu"])
    return render_template("kartu_anggota.html", anggota=anggota, qr_b64=qr_b64)


@app.route("/anggota/cetak-semua")
@login_required
def cetak_semua_kartu():
    """Halaman cetak massal: semua kartu anggota (bisa difilter kelompok) sekaligus,
    disusun jadi grid siap print/PDF."""
    kelompok = request.args.get("kelompok") or None
    daftar = db.get_all_anggota()
    if kelompok:
        daftar = [a for a in daftar if a["kelompok"] == kelompok]

    kartu_list = [{"anggota": a, "qr_b64": buat_qr_base64(a["kode_kartu"])} for a in daftar]
    return render_template(
        "kartu_semua.html",
        kartu_list=kartu_list,
        kelompok_terpilih=kelompok,
        semua_kelompok=db.get_semua_kelompok(),
    )


# ---------- Laporan ----------

@app.route("/laporan")
@login_required
def laporan():
    tanggal_akhir = request.args.get("sampai", date.today().isoformat())
    tanggal_mulai = request.args.get("dari", (date.today() - timedelta(days=6)).isoformat())
    kelompok = request.args.get("kelompok") or None

    data = db.get_laporan(tanggal_mulai, tanggal_akhir, kelompok)
    return render_template(
        "laporan.html",
        data=data,
        dari=tanggal_mulai,
        sampai=tanggal_akhir,
        kelompok_terpilih=kelompok,
        semua_kelompok=db.get_semua_kelompok(),
    )


@app.route("/laporan/export")
@login_required
def export_laporan():
    tanggal_akhir = request.args.get("sampai", date.today().isoformat())
    tanggal_mulai = request.args.get("dari", (date.today() - timedelta(days=6)).isoformat())
    kelompok = request.args.get("kelompok") or None

    data = db.get_laporan(tanggal_mulai, tanggal_akhir, kelompok)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Tanggal", "Waktu", "Nama", "Kelompok", "Tipe"])
    for row in data:
        writer.writerow([row["tanggal"], row["waktu"], row["nama"], row["kelompok"], row["tipe"]])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=laporan_absensi_{tanggal_mulai}_sd_{tanggal_akhir}.csv"},
    )


if __name__ == "__main__":
    db.init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)