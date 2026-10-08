"""Second Luxe - aplikasi utama (Flask).

Konfigurasi, CSRF, filter template, dan route placeholder gambar.
Route fitur dipisah ke Blueprint (shop.py, dst.).
"""
import os
import secrets
import textwrap
from xml.sax.saxutils import escape

from dotenv import load_dotenv
from flask import (Flask, Response, abort, g, render_template, request, session)

import account
import auth
import cart
import checkout
import db as database
import engage
import pages
import shop
import admin

load_dotenv()


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        # Hanya untuk pengembangan: kunci acak baru tiap restart (sesi akan reset)
        secret = secrets.token_hex(32)
        print("[PERINGATAN] SECRET_KEY belum diatur di .env, memakai kunci sementara.")

    app.config.update(
        SECRET_KEY=secret,
        DATABASE=os.path.join(app.instance_path, "secondluxe.sqlite"),
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,        # batas upload 8 MB
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        WHATSAPP_NUMBER=os.environ.get("WHATSAPP_NUMBER", "6281234567890"),
        SITE_NAME="Second Luxe",
    )

    database.init_app(app)
    register_filters(app)
    register_hooks(app)
    register_routes(app)
    for module in (shop, auth, cart, checkout, account, engage, pages, admin):
        app.register_blueprint(module.bp)
    return app


# ------------------------------------------------------------ Filter Jinja
def register_filters(app):
    @app.template_filter("rupiah")
    def rupiah(value):
        """12500000 -> Rp 12.500.000"""
        try:
            return "Rp " + f"{int(value):,}".replace(",", ".")
        except (TypeError, ValueError):
            return "Rp 0"

    @app.template_filter("status_label")
    def status_label(value):
        """Kode status -> teks Indonesia."""
        return {"menunggu_pembayaran": "Menunggu pembayaran", "diproses": "Diproses", "dikirim": "Dikirim",
                "selesai": "Selesai", "dibatalkan": "Dibatalkan", "pending": "Menunggu", "paid": "Lunas",
                "failed": "Gagal"}.get(value, str(value or "").replace("_", " ").capitalize())

    @app.template_filter("tanggal")
    def tanggal(value):
        """'2026-10-08 10:00:00' -> 8 Okt 2026"""
        bulan = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
        try:
            y, m, d = str(value)[:10].split("-")
            return f"{int(d)} {bulan[int(m) - 1]} {y}"
        except (ValueError, IndexError):
            return str(value or "")


# ------------------------------------------------------- Hook per request
def count_cart_wishlist():
    """Hitung isi keranjang dan wishlist (database untuk user, session untuk tamu)."""
    db = database.get_db()
    if g.user:
        cart = [r[0] for r in db.execute("SELECT product_id FROM cart_items WHERE user_id=?", (g.user["id"],))]
        wish = [r[0] for r in db.execute("SELECT product_id FROM wishlist WHERE user_id=?", (g.user["id"],))]
    else:
        cart, wish = session.get("cart", []), session.get("wishlist", [])
    return cart, wish


def register_hooks(app):
    @app.before_request
    def load_user_and_guard():
        # Muat pengguna dari session
        g.user = None
        uid = session.get("user_id")
        if uid:
            g.user = database.get_db().execute(
                "SELECT id,name,email,role FROM users WHERE id=? AND is_active=1", (uid,)).fetchone()
            if g.user is None:
                session.pop("user_id", None)

        # Lepas booking 24 jam yang sudah kedaluwarsa
        if request.method == "GET" and not request.path.startswith(("/static", "/placeholder")):
            database.release_expired_holds()

        # Hitung pengunjung (sekali per session)
        if request.method == "GET" and not request.path.startswith(("/static", "/placeholder")):
            if "sid" not in session:
                session["sid"] = secrets.token_hex(8)
                database.get_db().execute("INSERT INTO visits (session_id,path) VALUES (?,?)",
                                          (session["sid"], request.path))
                database.get_db().commit()

        # CSRF: semua POST wajib membawa token (form atau header X-CSRF-Token)
        if request.method == "POST":
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            real = session.get("csrf_token", "")
            if not real or not secrets.compare_digest(sent, real):
                abort(400, description="Token keamanan tidak valid. Muat ulang halaman lalu coba lagi.")

    @app.context_processor
    def inject_globals():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        cart, wish = count_cart_wishlist()
        return {
            "csrf_token": session["csrf_token"],
            "current_user": g.get("user"),
            "cart_count": len(cart),
            "wishlist_count": len(wish),
            "wishlist_ids": set(wish),
            "whatsapp_number": app.config["WHATSAPP_NUMBER"],
        }

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp


# ------------------------------------------------------------------ Route
def register_routes(app):
    @app.route("/placeholder/<int:pid>.svg")
    def placeholder(pid):
        """Gambar placeholder SVG krem (rasio 4:5) berisi nama produk."""
        row = database.get_db().execute(
            "SELECT p.name, b.name AS brand FROM products p JOIN brands b ON b.id=p.brand_id WHERE p.id=?",
            (pid,)).fetchone()
        if row is None:
            abort(404)
        v = request.args.get("v", 1, type=int)
        svg = build_placeholder(row["brand"], row["name"], pid, v)
        resp = Response(svg, mimetype="image/svg+xml")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp


def build_placeholder(brand, name, pid, variant):
    """Susun SVG: gradien krem + bentuk lembut + teks brand dan nama."""
    palettes = [("#F3EBDD", "#E4D6BC"), ("#EFE6D6", "#D9C8A8"), ("#F6F0E6", "#E8DCC6"),
                ("#ECE3D3", "#D4C3A2"), ("#F1E9DA", "#E1D2B5")]
    c1, c2 = palettes[(pid + variant) % len(palettes)]
    shape = [
        '<circle cx="560" cy="330" r="230" fill="#B8975A" opacity="0.14"/>',
        '<rect x="170" y="230" width="460" height="520" rx="30" fill="#B8975A" opacity="0.12"/>',
        '<path d="M120 780 Q400 340 680 780 Z" fill="#B8975A" opacity="0.14"/>',
    ][variant % 3]
    lines = textwrap.wrap(name, 20)[:3]
    tspans = "".join(
        f'<tspan x="400" dy="{0 if i == 0 else 56}">{escape(t)}</tspan>' for i, t in enumerate(lines))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1000" role="img" aria-label="{escape(name)}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient></defs>
<rect width="800" height="1000" fill="url(#g)"/>{shape}
<rect x="40" y="40" width="720" height="920" fill="none" stroke="#B8975A" stroke-opacity="0.45" stroke-width="2"/>
<text x="400" y="400" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="30" letter-spacing="6" fill="#6B6560">{escape(brand.upper())}</text>
<text x="400" y="490" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="46" fill="#1A1A1A">{tspans}</text>
<text x="400" y="900" text-anchor="middle" font-family="Georgia, serif" font-size="22" letter-spacing="5" fill="#B8975A">SECOND LUXE</text>
</svg>"""


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
