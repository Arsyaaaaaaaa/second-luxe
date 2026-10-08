"""Blueprint autentikasi: daftar, masuk, keluar, dan login Google (simulasi)."""
import re
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db as database
from account import sync_notifications
from cart import merge_guest_data
from utils import (EMAIL_RE, clean, client_ip, rate_exceeded, rate_hit, rate_reset, safe_next,
                   valid_phone)

bp = Blueprint("auth", __name__)

# Hash palsu agar waktu respons sama untuk email yang tidak terdaftar
_DUMMY_HASH = generate_password_hash("tidak-dipakai")

# Akun contoh untuk simulasi Google (tidak ada input email bebas, agar tidak bisa mengambil alih akun lain)
GOOGLE_DEMO_ACCOUNTS = [
    {"name": "Budi Santoso", "email": "budi.santoso@gmail.example"},
    {"name": "Citra Dewi", "email": "citra.dewi@gmail.example"},
]


def do_login(user_id):
    """Mulai session baru (cegah session fixation), lalu gabungkan keranjang dan wishlist tamu."""
    keep = {k: session[k] for k in ("sid", "recent", "viewed") if k in session}
    guest_cart = list(session.get("cart", []))
    guest_wish = list(session.get("wishlist", []))
    session.clear()
    session.update(keep)
    session["user_id"] = user_id
    merge_guest_data(user_id, guest_cart, guest_wish)
    sync_notifications(database.get_db(), user_id)


def password_error(pw):
    """Aturan sandi: minimal 8 karakter, ada huruf dan angka."""
    if len(pw) < 8 or len(pw) > 128:
        return "Kata sandi 8 sampai 128 karakter."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Kata sandi harus memuat huruf dan angka."
    return None


@bp.route("/masuk", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"), "/akun")
    if g.user:
        return redirect(nxt)
    email = ""
    if request.method == "POST":
        email = clean(request.form.get("email"), 254).lower()
        pw = (request.form.get("password") or "")[:128]
        key, ip_key = f"login:{client_ip()}:{email}", f"login-ip:{client_ip()}"
        if rate_exceeded(key, 5) or rate_exceeded(ip_key, 30):
            flash("Terlalu banyak percobaan masuk. Coba lagi sekitar 10 menit lagi.", "error")
            return render_template("auth/login.html", email=email, next=nxt), 429
        user = database.get_db().execute(
            "SELECT id,password_hash FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
        ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, pw) and user is not None
        if ok:
            rate_reset(key)
            do_login(user["id"])
            flash("Selamat datang kembali di Second Luxe.", "success")
            return redirect(nxt)
        rate_hit(key)
        rate_hit(ip_key)
        flash("Email atau kata sandi salah.", "error")
    return render_template("auth/login.html", email=email, next=nxt)


@bp.route("/daftar", methods=["GET", "POST"])
def register():
    nxt = safe_next(request.values.get("next"), "/akun")
    if g.user:
        return redirect(nxt)
    form = {"name": "", "email": "", "phone": ""}
    errors = {}
    if request.method == "POST":
        form["name"] = clean(request.form.get("name"), 80)
        form["email"] = clean(request.form.get("email"), 254).lower()
        form["phone"] = clean(request.form.get("phone"), 20)
        pw = (request.form.get("password") or "")[:128]
        pw2 = (request.form.get("password2") or "")[:128]

        if rate_exceeded(f"register:{client_ip()}", 10, 3600):
            flash("Terlalu banyak pendaftaran dari jaringan ini. Coba lagi nanti.", "error")
            return render_template("auth/register.html", form=form, errors=errors, next=nxt), 429
        rate_hit(f"register:{client_ip()}", 3600)

        if len(form["name"]) < 2:
            errors["name"] = "Nama minimal 2 karakter."
        if not EMAIL_RE.match(form["email"]):
            errors["email"] = "Format email tidak valid."
        phone = valid_phone(form["phone"])
        if form["phone"] and not phone:
            errors["phone"] = "Nomor HP tidak valid (contoh 081234567890)."
        err = password_error(pw)
        if err:
            errors["password"] = err
        elif pw != pw2:
            errors["password2"] = "Konfirmasi kata sandi tidak sama."

        db = database.get_db()
        if "email" not in errors and db.execute("SELECT 1 FROM users WHERE email=?", (form["email"],)).fetchone():
            errors["email"] = "Email sudah terdaftar. Silakan masuk."
        if not errors:
            cur = db.execute("INSERT INTO users (name,email,password_hash,phone,role) VALUES (?,?,?,?,'customer')",
                             (form["name"], form["email"], generate_password_hash(pw), phone))
            db.commit()
            do_login(cur.lastrowid)
            flash("Akun berhasil dibuat. Selamat berbelanja!", "success")
            return redirect(nxt)
        flash("Periksa kembali data yang ditandai.", "error")
    return render_template("auth/register.html", form=form, errors=errors, next=nxt)


@bp.post("/keluar")
def logout():
    session.clear()
    flash("Kamu sudah keluar.", "info")
    return redirect("/")


# ---------------------------------------------------------- Google (simulasi)
@bp.route("/masuk/google", methods=["GET", "POST"])
def google_login():
    """Simulasi alur OAuth Google: pilih akun lalu masuk. Cara mengaktifkan OAuth asli ada di README."""
    nxt = safe_next(request.values.get("next"), "/akun")
    if g.user:
        return redirect(nxt)
    if request.method == "POST":
        idx = request.form.get("akun", type=int)
        if idx is None or not 0 <= idx < len(GOOGLE_DEMO_ACCOUNTS):
            abort(400, description="Akun Google tidak valid.")
        acc = GOOGLE_DEMO_ACCOUNTS[idx]
        db = database.get_db()
        user = db.execute("SELECT id,role FROM users WHERE email=?", (acc["email"],)).fetchone()
        if user is None:
            cur = db.execute("INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,'customer')",
                             (acc["name"], acc["email"], generate_password_hash(secrets.token_hex(24))))
            db.commit()
            uid = cur.lastrowid
        else:
            uid = user["id"]
        do_login(uid)
        flash(f"Masuk dengan Google sebagai {acc['name']} (simulasi).", "success")
        return redirect(nxt)
    return render_template("auth/google.html", accounts=GOOGLE_DEMO_ACCOUNTS, next=nxt)
