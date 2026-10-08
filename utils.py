"""Utilitas bersama: validasi, dekorator akses, rate limit, redirect aman, email simulasi."""
import os
import re
import time
from datetime import datetime
from functools import wraps
from urllib.parse import quote, urlparse

from flask import abort, current_app, flash, g, redirect, request

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^(?:\+62|62|0)8\d{8,12}$")
CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# ------------------------------------------------------------- Input & format
def clean(text, maxlen=200):
    """Buang karakter kontrol, rapikan spasi tepi, potong sesuai batas."""
    return CTRL_RE.sub("", text or "").strip()[:maxlen]


def valid_phone(raw):
    """Kembalikan nomor HP bersih (tanpa spasi/strip) atau None jika tidak valid."""
    digits = re.sub(r"[\s\-]", "", raw or "")
    return digits if PHONE_RE.match(digits) else None


def rupiah(n):
    """12500000 -> Rp 12.500.000 (untuk pesan flash dan email)."""
    return "Rp " + f"{int(n):,}".replace(",", ".")


def client_ip():
    return request.remote_addr or "unknown"


# --------------------------------------------------------------- Redirect aman
def safe_next(target, default="/"):
    """Hanya izinkan path internal (cegah open redirect)."""
    target = (target or "").strip()
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    if CTRL_RE.search(target):
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return default
    return target


def redirect_back(default="/"):
    """Kembali ke field 'next' (jika aman), lalu ke halaman asal, lalu ke default."""
    nxt = request.form.get("next", "")
    if nxt.startswith("/"):
        return redirect(safe_next(nxt, default))
    ref = request.referrer
    if ref:
        p = urlparse(ref)
        if p.netloc == request.host:
            path = p.path + ("?" + p.query if p.query else "")
            return redirect(safe_next(path, default))
    return redirect(default)


# ------------------------------------------------------------- Rate limit
_hits = {}


def _recent(key, window):
    now = time.time()
    lst = [t for t in _hits.get(key, []) if now - t < window]
    if lst:
        _hits[key] = lst
    else:
        _hits.pop(key, None)
    return lst


def rate_hit(key, window=600):
    """Catat satu percobaan; kembalikan jumlah percobaan dalam jendela waktu."""
    if len(_hits) > 5000:           # cegah memori membengkak
        for k in list(_hits):
            _recent(k, window)
    lst = _recent(key, window)
    lst.append(time.time())
    _hits[key] = lst
    return len(lst)


def rate_exceeded(key, limit, window=600):
    return len(_recent(key, window)) >= limit


def rate_reset(key):
    _hits.pop(key, None)


# -------------------------------------------------------------- Dekorator akses
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Silakan masuk terlebih dahulu.", "info")
            nxt = request.full_path.rstrip("?") if request.method == "GET" else "/akun"
            return redirect("/masuk?next=" + quote(nxt, safe=""))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Silakan masuk sebagai admin.", "info")
            return redirect("/masuk?next=" + quote(request.path, safe=""))
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# ------------------------------------------------------------- Email simulasi
def send_email_simulated(to, subject, body):
    """Email tidak dikirim sungguhan: ditulis ke instance/emails.log."""
    path = os.path.join(current_app.instance_path, "emails.log")
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"=== {stamp} UTC ===\nKepada : {clean(to, 254)}\n"
                    f"Subjek : {clean(subject, 150)}\n\n{body}\n\n")
    except OSError:
        current_app.logger.warning("Gagal menulis emails.log")
