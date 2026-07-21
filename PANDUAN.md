# Panduan Instalasi & Deployment — Absensi Pesantren

## 1. Menjalankan di komputer (mode development / uji coba)

```bash
cd absensi_app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Buka browser ke `http://localhost:5000`. Karena belum ada akun sama sekali,
kamu akan diarahkan otomatis ke halaman **Setup Awal** untuk bikin akun admin
pertama.

Colok mesin scanner MSC2 ke komputer, buka halaman **Scan Absensi**, lalu
scan barcode/kartu — sistem otomatis menangkapnya karena scanner terdeteksi
sebagai keyboard (HID).

---

## 2. Menjalankan sebagai server produksi (1 komputer, diakses banyak device via WiFi/LAN)

### a. Ganti secret key
Buka `app.py`, ganti baris ini dengan string acak panjang (jangan dipakai bersama):
```python
app.secret_key = "GANTI_DENGAN_SECRET_KEY_ACAK_SEBELUM_PRODUCTION"
```
Bisa generate acak dengan:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### b. Matikan debug mode
Di `app.py`, baris paling bawah, ubah:
```python
app.run(host="0.0.0.0", port=5000, debug=True)
```
menjadi:
```python
app.run(host="0.0.0.0", port=5000, debug=False)
```
(Nanti untuk production sebenarnya kita tidak pakai `app.run()` lagi,
tapi lewat Gunicorn — lihat langkah c.)

### c. Jalankan dengan Gunicorn (lebih stabil dari server bawaan Flask)
```bash
gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
```

### d. Bikin service systemd biar otomatis jalan terus
Buat file `/etc/systemd/system/absensi.service`:
```ini
[Unit]
Description=Aplikasi Absensi Pesantren
After=network.target

[Service]
User=axioo
WorkingDirectory=/home/axioo/absensi_app
Environment="PATH=/home/axioo/absensi_app/venv/bin"
ExecStart=/home/axioo/absensi_app/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```
Sesuaikan `User` dan path dengan username & lokasi folder kamu.

Aktifkan:
```bash
sudo systemctl daemon-reload
sudo systemctl enable absensi
sudo systemctl start absensi
sudo systemctl status absensi
```

Sekarang aplikasi otomatis jalan tiap komputer nyala, dan otomatis restart
kalau crash.

### e. Cek IP komputer server
```bash
ip addr show | grep "inet "
```
Device lain di WiFi/LAN yang sama bisa akses via `http://<IP-komputer>:5000`.
Sebaiknya set IP statis untuk komputer server ini di pengaturan router,
biar alamatnya gak berubah-ubah.

### f. (Opsional) Pasang Nginx sebagai reverse proxy
Supaya bisa akses tanpa nulis `:5000` dan lebih siap untuk HTTPS nantinya:
```bash
sudo apt install nginx
```
Buat file `/etc/nginx/sites-available/absensi`:
```nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/absensi /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## 3. Backup database otomatis

Database cuma 1 file: `instance/absensi.db`. Bikin script backup harian:

```bash
# backup.sh
#!/bin/bash
TANGGAL=$(date +%Y-%m-%d)
cp /home/axioo/absensi_app/instance/absensi.db /home/axioo/backup_absensi/absensi_$TANGGAL.db
```

Jadwalkan tiap malam jam 23:00 pakai cron:
```bash
crontab -e
```
Tambahkan baris:
```
0 23 * * * /home/axioo/absensi_app/backup.sh
```

---

## 4. Menambah akun staff/admin lain

Saat ini pembuatan akun baru (selain admin pertama) belum ada di UI.
Cara sementara lewat Python shell:
```bash
cd absensi_app
python3
>>> import database as db
>>> from werkzeug.security import generate_password_hash
>>> db.create_pengguna("staff1", generate_password_hash("passwordnya"), "Nama Staff", peran="staff")
>>> exit()
```
(Kalau perlu, aku bisa buatkan halaman UI untuk kelola akun staff juga.)

---

## 5. Checklist sebelum dipakai beneran

- [ ] Ganti `secret_key` di `app.py`
- [ ] Matikan `debug=True`
- [ ] Jalankan lewat Gunicorn + systemd, bukan `python3 app.py` manual
- [ ] Set IP statis untuk komputer server
- [ ] Coba scan mesin MSC2 di halaman Scan Absensi
- [ ] Tambahkan data anggota/santri secukupnya
- [ ] Setup backup otomatis
- [ ] Uji akses dari device lain di jaringan yang sama
