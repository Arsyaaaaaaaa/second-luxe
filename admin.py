"""Panel admin Second Luxe - Tahap 6.

Tidak menggunakan AI/chatbot. Semua operasi admin memakai SQLite parameterized,
CSRF dari app.py, dan decorator admin_required dari utils.py.
"""
import csv
import io
import os
import re
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for, current_app

import db as database
from engage import make_sell_offer, resolve_offer
from utils import admin_required, clean, rupiah

bp = Blueprint("admin", __name__, url_prefix="/admin")


def parse_money(value):
    digits = re.sub(r"\D", "", value or "")
    return int(digits) if digits else 0


def slugify_admin(value):
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value or "item"


def unique_slug(db, base, table="products", current_id=None):
    slug = slugify_admin(base)
    candidate = slug
    n = 2
    while True:
        sql = f"SELECT id FROM {table} WHERE slug=?"
        args = [candidate]
        if current_id:
            sql += " AND id<>?"
            args.append(current_id)
        if db.execute(sql, args).fetchone() is None:
            return candidate
        candidate = f"{slug}-{n}"
        n += 1


def sniff_image(data):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_product_images(files, product_id):
    folder = os.path.join(current_app.static_folder, "uploads", "products")
    os.makedirs(folder, exist_ok=True)
    saved = []
    for f in files:
        if not f or not f.filename:
            continue
        data = f.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError("Ukuran setiap foto maksimal 5 MB.")
        ext = sniff_image(data)
        if not ext:
            raise ValueError("Foto produk hanya boleh JPG, PNG, atau WEBP.")
        name = secrets.token_hex(12) + ext
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(data)
        saved.append("/static/uploads/products/" + name)
    return saved


def product_form_data(form):
    return {
        "name": clean(form.get("name"), 140),
        "brand_id": form.get("brand_id", ""),
        "category_id": form.get("category_id", ""),
        "gender": form.get("gender", "unisex"),
        "size": clean(form.get("size"), 60),
        "color": clean(form.get("color"), 80),
        "material": clean(form.get("material"), 120),
        "condition_score": form.get("condition_score", "8"),
        "condition_notes": clean(form.get("condition_notes"), 500),
        "flaws": clean(form.get("flaws"), 500),
        "completeness": clean(form.get("completeness"), 180),
        "price": parse_money(form.get("price")),
        "original_price": parse_money(form.get("original_price")),
        "stock": max(0, int(form.get("stock", 1) or 0)),
        "status": form.get("status", "available"),
        "featured": 1 if form.get("is_featured") else 0,
        "description": clean(form.get("description"), 2000),
    }


def product_select_data(db):
    brands = db.execute("SELECT id,name FROM brands ORDER BY name").fetchall()
    categories = db.execute("SELECT id,name FROM categories ORDER BY name").fetchall()
    return brands, categories


@bp.before_request
@admin_required
def guard():
    pass


@bp.get("")
def dashboard():
    db = database.get_db()
    total_products = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    available = db.execute("SELECT COUNT(*) FROM products WHERE status='available'").fetchone()[0]
    orders = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    revenue = db.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE payment_status='paid'").fetchone()[0]
    visitors = db.execute("SELECT COUNT(DISTINCT session_id) FROM visits").fetchone()[0]
    pending_offers = db.execute("SELECT COUNT(*) FROM offers WHERE status='pending'").fetchone()[0]
    pending_sells = db.execute("SELECT COUNT(*) FROM sell_requests WHERE status='pending'").fetchone()[0]
    pending_reviews = db.execute("SELECT COUNT(*) FROM reviews WHERE is_approved=0").fetchone()[0]
    top = db.execute("""SELECT p.name,b.name brand,p.sold_count,p.views
                      FROM products p JOIN brands b ON b.id=p.brand_id
                      ORDER BY p.sold_count DESC,p.views DESC LIMIT 5""").fetchall()

    days = []
    for i in range(6, -1, -1):
        day = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        row = db.execute("SELECT COALESCE(SUM(total),0) total, COUNT(*) orders FROM orders WHERE payment_status='paid' AND substr(created_at,1,10)=?", (day,)).fetchone()
        days.append({"date": day[5:], "total": row["total"], "orders": row["orders"]})
    max_total = max([d["total"] for d in days] or [1])
    for d in days:
        d["height"] = round((d["total"] / max_total) * 100) if max_total else 0

    return render_template("admin/dashboard.html", active="dashboard", total_products=total_products,
                           available=available, orders=orders, revenue=revenue, visitors=visitors,
                           pending_offers=pending_offers, pending_sells=pending_sells,
                           pending_reviews=pending_reviews, top=top, days=days)


@bp.route("/produk", methods=["GET", "POST"])
def products():
    db = database.get_db()
    brands, categories = product_select_data(db)
    if request.method == "POST":
        d = product_form_data(request.form)
        errors = []
        if len(d["name"]) < 3: errors.append("Nama produk wajib diisi.")
        if not d["brand_id"] or not d["category_id"]: errors.append("Brand dan kategori wajib dipilih.")
        if d["price"] <= 0: errors.append("Harga harus lebih dari 0.")
        if d["status"] not in ("available", "held", "sold"): errors.append("Status produk tidak valid.")
        try: d["condition_score"] = max(1, min(10, int(d["condition_score"])))
        except ValueError: errors.append("Nilai kondisi harus 1-10.")
        if errors:
            for e in errors: flash(e, "error")
        else:
            slug = unique_slug(db, d["name"])
            if d["status"] == "sold": d["stock"] = 0
            cur = db.execute("""INSERT INTO products
              (name,slug,description,brand_id,category_id,gender,size,color,material,condition_score,
               condition_notes,flaws,completeness,price,original_price,stock,status,is_featured,certificate_no)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (d["name"], slug, d["description"], d["brand_id"], d["category_id"], d["gender"],
               d["size"], d["color"], d["material"], d["condition_score"], d["condition_notes"],
               d["flaws"], d["completeness"], d["price"], d["original_price"] or None, d["stock"],
               d["status"], d["featured"], f"SL-2026-{datetime.utcnow().strftime('%m%d')}-{secrets.token_hex(3).upper()}"))
            pid = cur.lastrowid
            try:
                paths = save_product_images(request.files.getlist("photos"), pid)
            except ValueError as exc:
                db.execute("DELETE FROM products WHERE id=?", (pid,))
                db.commit()
                flash(str(exc), "error")
                return redirect(url_for("admin.products"))
            if not paths:
                paths = [f"/placeholder/{pid}.svg?v=1"]
            for idx, path in enumerate(paths, 1):
                db.execute("INSERT INTO product_images (product_id,url,is_primary,sort_order) VALUES (?,?,?,?)",
                           (pid, path, 1 if idx == 1 else 0, idx))
            db.commit()
            flash("Produk berhasil ditambahkan.", "success")
            return redirect(url_for("admin.product_edit", pid=pid))

    q = clean(request.args.get("q"), 100)
    status = request.args.get("status", "")
    sql = """SELECT p.*,b.name brand_name,c.name category_name,
              (SELECT url FROM product_images i WHERE i.product_id=p.id ORDER BY i.is_primary DESC,i.sort_order LIMIT 1) image_url
              FROM products p JOIN brands b ON b.id=p.brand_id JOIN categories c ON c.id=p.category_id WHERE 1=1"""
    args = []
    if q:
        sql += " AND (p.name LIKE ? OR b.name LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    if status in ("available", "held", "sold"):
        sql += " AND p.status=?"; args.append(status)
    sql += " ORDER BY p.id DESC"
    rows = db.execute(sql, args).fetchall()
    return render_template("admin/products.html", active="products", rows=rows, brands=brands, categories=categories)


@bp.route("/produk/<int:pid>/edit", methods=["GET", "POST"])
def product_edit(pid):
    db = database.get_db()
    p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if p is None: abort(404)
    brands, categories = product_select_data(db)
    if request.method == "POST":
        d = product_form_data(request.form)
        if not d["name"] or d["price"] <= 0:
            flash("Nama dan harga produk wajib valid.", "error")
            return redirect(url_for("admin.product_edit", pid=pid))
        try: score = max(1, min(10, int(d["condition_score"])))
        except ValueError: score = p["condition_score"]
        status = d["status"] if d["status"] in ("available", "held", "sold") else p["status"]
        stock = 0 if status == "sold" else d["stock"]
        db.execute("""UPDATE products SET name=?,slug=?,description=?,brand_id=?,category_id=?,gender=?,size=?,color=?,material=?,
                    condition_score=?,condition_notes=?,flaws=?,completeness=?,price=?,original_price=?,stock=?,status=?,is_featured=? WHERE id=?""",
                   (d["name"], unique_slug(db, d["name"], "products", pid), d["description"], d["brand_id"], d["category_id"],
                    d["gender"], d["size"], d["color"], d["material"], score, d["condition_notes"], d["flaws"],
                    d["completeness"], d["price"], d["original_price"] or None, stock, status, d["featured"], pid))
        try:
            paths = save_product_images(request.files.getlist("photos"), pid)
        except ValueError as exc:
            flash(str(exc), "error")
            db.commit()
            return redirect(url_for("admin.product_edit", pid=pid))
        max_order = db.execute("SELECT COALESCE(MAX(sort_order),0) FROM product_images WHERE product_id=?", (pid,)).fetchone()[0]
        for idx, path in enumerate(paths, max_order + 1):
            db.execute("INSERT INTO product_images (product_id,url,is_primary,sort_order) VALUES (?,?,?,?)", (pid,path,0,idx))
        db.commit()
        flash("Produk berhasil diperbarui.", "success")
        return redirect(url_for("admin.product_edit", pid=pid))
    images = db.execute("SELECT * FROM product_images WHERE product_id=? ORDER BY is_primary DESC,sort_order", (pid,)).fetchall()
    return render_template("admin/product_edit.html", active="products", p=p, images=images, brands=brands, categories=categories)


@bp.post("/produk/<int:pid>/hapus")
def product_delete(pid):
    db = database.get_db()
    p = db.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
    if not p: abort(404)
    used = db.execute("SELECT COUNT(*) FROM order_items WHERE product_id=?", (pid,)).fetchone()[0]
    if used:
        flash("Produk yang sudah pernah masuk pesanan tidak boleh dihapus. Ubah status menjadi sold.", "error")
    else:
        db.execute("DELETE FROM products WHERE id=?", (pid,)); db.commit(); flash("Produk dihapus.", "success")
    return redirect(url_for("admin.products"))


@bp.post("/produk/<int:pid>/foto/<int:image_id>/utama")
def image_primary(pid, image_id):
    db = database.get_db()
    if not db.execute("SELECT 1 FROM product_images WHERE id=? AND product_id=?", (image_id,pid)).fetchone(): abort(404)
    db.execute("UPDATE product_images SET is_primary=0 WHERE product_id=?", (pid,))
    db.execute("UPDATE product_images SET is_primary=1 WHERE id=?", (image_id,)); db.commit()
    flash("Foto utama diperbarui.", "success")
    return redirect(url_for("admin.product_edit", pid=pid))


@bp.post("/produk/<int:pid>/foto/<int:image_id>/hapus")
def image_delete(pid, image_id):
    db = database.get_db(); img = db.execute("SELECT * FROM product_images WHERE id=? AND product_id=?", (image_id,pid)).fetchone()
    if not img: abort(404)
    if img["url"].startswith("/static/"):
        path = os.path.join(current_app.root_path, img["url"].lstrip("/"))
        if os.path.isfile(path):
            try: os.remove(path)
            except OSError: pass
    db.execute("DELETE FROM product_images WHERE id=?", (image_id,)); db.commit()
    flash("Foto dihapus.", "success")
    return redirect(url_for("admin.product_edit", pid=pid))


@bp.route("/pesanan", methods=["GET", "POST"])
def orders():
    db = database.get_db()
    if request.method == "POST":
        oid = request.form.get("order_id", type=int); status = request.form.get("status", "")
        tracking = clean(request.form.get("tracking_no"), 80)
        allowed = {"menunggu_pembayaran", "diproses", "dikirim", "selesai", "dibatalkan"}
        if status not in allowed: flash("Status pesanan tidak valid.", "error")
        else:
            order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            if not order: abort(404)
            db.execute("UPDATE orders SET status=?,tracking_no=? WHERE id=?", (status, tracking or None, oid))
            db.execute("INSERT INTO order_events (order_id,status,note) VALUES (?,?,?)", (oid,status,"Diperbarui dari panel admin."))
            db.commit(); flash("Status pesanan diperbarui.", "success")
    status = request.args.get("status", "")
    sql = """SELECT o.*,u.name user_name,u.email user_email,
              (SELECT COUNT(*) FROM order_items oi WHERE oi.order_id=o.id) item_count
              FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE 1=1"""
    args=[]
    if status:
        sql += " AND o.status=?"; args.append(status)
    sql += " ORDER BY o.id DESC"
    rows = db.execute(sql,args).fetchall()
    return render_template("admin/orders.html", active="orders", rows=rows)


@bp.get("/pesanan/<int:oid>")
def order_detail(oid):
    db=database.get_db(); order=db.execute("SELECT o.*,u.name user_name,u.email user_email FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?",(oid,)).fetchone()
    if not order: abort(404)
    items=db.execute("SELECT * FROM order_items WHERE order_id=?",(oid,)).fetchall()
    events=db.execute("SELECT * FROM order_events WHERE order_id=? ORDER BY id DESC",(oid,)).fetchall()
    return render_template("admin/order_detail.html",active="orders",order=order,items=items,events=events)


@bp.route("/pengguna", methods=["GET", "POST"])
def users():
    db=database.get_db()
    if request.method=="POST":
        uid=request.form.get("user_id",type=int); action=request.form.get("action")
        if uid == g.user["id"] and action == "toggle":
            flash("Akun admin yang sedang digunakan tidak dapat dinonaktifkan.","error")
        elif action == "toggle":
            db.execute("UPDATE users SET is_active=CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id=?",(uid,)); db.commit(); flash("Status pengguna diperbarui.","success")
        elif action == "role" and request.form.get("role") in ("admin","customer"):
            new_role = request.form["role"]
            if uid == g.user["id"] and new_role != "admin":
                flash("Role akun admin yang sedang digunakan tidak dapat diturunkan.", "error")
            else:
                db.execute("UPDATE users SET role=? WHERE id=?", (new_role, uid)); db.commit(); flash("Role pengguna diperbarui.", "success")
    rows=db.execute("SELECT id,name,email,phone,role,is_active,created_at FROM users ORDER BY id DESC").fetchall()
    return render_template("admin/users.html",active="users",rows=rows)


@bp.route("/ulasan", methods=["GET", "POST"])
def reviews():
    db=database.get_db()
    if request.method=="POST":
        rid=request.form.get("review_id",type=int); action=request.form.get("action")
        if action=="approve": db.execute("UPDATE reviews SET is_approved=1 WHERE id=?",(rid,)); flash("Ulasan disetujui.","success")
        elif action=="reject": db.execute("UPDATE reviews SET is_approved=0 WHERE id=?",(rid,)); flash("Ulasan disembunyikan.","info")
        elif action=="delete": db.execute("DELETE FROM reviews WHERE id=?",(rid,)); flash("Ulasan dihapus.","success")
        db.commit()
    rows=db.execute("""SELECT r.*,p.name product_name FROM reviews r JOIN products p ON p.id=r.product_id ORDER BY r.id DESC""").fetchall()
    return render_template("admin/reviews.html",active="reviews",rows=rows)


@bp.route("/tawaran", methods=["GET", "POST"])
def offers():
    db=database.get_db()
    if request.method=="POST":
        oid=request.form.get("offer_id",type=int); action=request.form.get("action")
        counter=parse_money(request.form.get("counter_amount")); note=clean(request.form.get("note"),300)
        ok,msg=resolve_offer(db,oid,action,counter,note)
        flash(msg,"success" if ok else "error")
        return redirect(url_for("admin.offers"))
    rows=db.execute("""SELECT o.*,p.name product_name,p.price product_price,b.name brand,u.name user_name,u.email user_email
                      FROM offers o JOIN products p ON p.id=o.product_id JOIN brands b ON b.id=p.brand_id
                      JOIN users u ON u.id=o.user_id ORDER BY o.id DESC""").fetchall()
    return render_template("admin/offers.html",active="offers",rows=rows)


@bp.route("/jual", methods=["GET", "POST"])
def sell_requests():
    db=database.get_db()
    if request.method=="POST":
        sid=request.form.get("sell_id",type=int); action=request.form.get("action")
        s=db.execute("SELECT * FROM sell_requests WHERE id=?",(sid,)).fetchone()
        if not s: abort(404)
        if action=="offer":
            amount=parse_money(request.form.get("final_offer")); ok,msg=make_sell_offer(db,sid,amount,clean(request.form.get("note"),300)); flash(msg,"success" if ok else "error")
        elif action=="reject":
            db.execute("UPDATE sell_requests SET status='rejected',admin_note=? WHERE id=?",(clean(request.form.get("note"),300) or "Ditolak admin",sid)); db.commit(); flash("Pengajuan ditolak.","info")
        return redirect(url_for("admin.sell_requests"))
    rows=db.execute("SELECT s.*,u.name user_name,u.email user_email FROM sell_requests s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC").fetchall()
    return render_template("admin/sell_requests.html",active="sell",rows=rows)


@bp.route("/master", methods=["GET", "POST"])
def master():
    db=database.get_db()
    entity=request.form.get("entity")
    if request.method=="POST":
        action=request.form.get("action"); eid=request.form.get("id",type=int)
        try:
            if entity=="brand":
                name=clean(request.form.get("name"),60); desc=clean(request.form.get("description"),300)
                if action=="create" and name: db.execute("INSERT INTO brands(name,slug,description) VALUES(?,?,?)",(name,unique_slug(db,name,"brands"),desc))
                elif action=="update": db.execute("UPDATE brands SET name=?,slug=?,description=? WHERE id=?",(name,unique_slug(db,name,"brands",eid),desc,eid))
                elif action=="delete": db.execute("DELETE FROM brands WHERE id=?",(eid,))
            elif entity=="category":
                name=clean(request.form.get("name"),60)
                if action=="create" and name: db.execute("INSERT INTO categories(name,slug) VALUES(?,?)",(name,unique_slug(db,name,"categories")))
                elif action=="update": db.execute("UPDATE categories SET name=?,slug=? WHERE id=?",(name,unique_slug(db,name,"categories",eid),eid))
                elif action=="delete": db.execute("DELETE FROM categories WHERE id=?",(eid,))
            elif entity=="voucher":
                code=clean(request.form.get("code"),40).upper(); typ=request.form.get("type","fixed"); val=parse_money(request.form.get("value")); minp=parse_money(request.form.get("min_purchase")); maxd=parse_money(request.form.get("max_discount")); quota=max(0,int(request.form.get("quota",0) or 0)); active=1 if request.form.get("is_active") else 0
                if action=="create": db.execute("INSERT INTO vouchers(code,description,type,value,min_purchase,max_discount,quota,valid_from,valid_until,is_active) VALUES(?,?,?,?,?,?,?,?,?,?)",(code,clean(request.form.get("description"),200),typ,val,minp,maxd or None,quota,request.form.get("valid_from") or None,request.form.get("valid_until") or None,active))
                elif action=="update": db.execute("UPDATE vouchers SET code=?,description=?,type=?,value=?,min_purchase=?,max_discount=?,quota=?,valid_from=?,valid_until=?,is_active=? WHERE id=?",(code,clean(request.form.get("description"),200),typ,val,minp,maxd or None,quota,request.form.get("valid_from") or None,request.form.get("valid_until") or None,active,eid))
                elif action=="delete": db.execute("DELETE FROM vouchers WHERE id=?",(eid,))
            elif entity=="banner":
                vals=(clean(request.form.get("title"),150),clean(request.form.get("subtitle"),300),clean(request.form.get("cta_text"),60),clean(request.form.get("cta_url"),200),request.form.get("tone","cream"),int(request.form.get("sort_order",0) or 0),1 if request.form.get("is_active") else 0)
                if action=="create": db.execute("INSERT INTO banners(title,subtitle,cta_text,cta_url,tone,sort_order,is_active) VALUES(?,?,?,?,?,?,?)",vals)
                elif action=="update": db.execute("UPDATE banners SET title=?,subtitle=?,cta_text=?,cta_url=?,tone=?,sort_order=?,is_active=? WHERE id=?",vals+(eid,))
                elif action=="delete": db.execute("DELETE FROM banners WHERE id=?",(eid,))
            elif entity=="blog":
                title=clean(request.form.get("title"),180); slug=unique_slug(db,title,"blog_posts",eid if action=="update" else None); vals=(title,slug,clean(request.form.get("excerpt"),300),clean(request.form.get("content"),5000),clean(request.form.get("category"),80),1 if request.form.get("is_published") else 0)
                if action=="create": db.execute("INSERT INTO blog_posts(title,slug,excerpt,content,category,is_published) VALUES(?,?,?,?,?,?)",vals)
                elif action=="update": db.execute("UPDATE blog_posts SET title=?,slug=?,excerpt=?,content=?,category=?,is_published=? WHERE id=?",vals+(eid,))
                elif action=="delete": db.execute("DELETE FROM blog_posts WHERE id=?",(eid,))
            db.commit(); flash("Data berhasil disimpan.","success")
        except Exception as exc:
            db.rollback(); flash("Data tidak dapat disimpan. Periksa duplikasi atau relasi yang masih digunakan.","error")
    return render_template("admin/master.html",active="master",
                           brands=db.execute("SELECT * FROM brands ORDER BY name").fetchall(),
                           categories=db.execute("SELECT * FROM categories ORDER BY name").fetchall(),
                           vouchers=db.execute("SELECT * FROM vouchers ORDER BY id DESC").fetchall(),
                           banners=db.execute("SELECT * FROM banners ORDER BY sort_order,id").fetchall(),
                           blogs=db.execute("SELECT * FROM blog_posts ORDER BY id DESC").fetchall())


@bp.get("/laporan.csv")
def report_csv():
    db=database.get_db(); start=request.args.get("mulai",""); end=request.args.get("akhir","")
    sql="""SELECT o.order_no,o.created_at,u.email,o.status,o.payment_status,o.courier,o.total,
                   GROUP_CONCAT(oi.product_name,' | ') products
            FROM orders o LEFT JOIN users u ON u.id=o.user_id JOIN order_items oi ON oi.order_id=o.id WHERE 1=1"""
    args=[]
    if start: sql += " AND substr(o.created_at,1,10)>=?"; args.append(start)
    if end: sql += " AND substr(o.created_at,1,10)<=?"; args.append(end)
    sql += " GROUP BY o.id ORDER BY o.created_at DESC"
    rows=db.execute(sql,args).fetchall()
    out=io.StringIO(); writer=csv.writer(out); writer.writerow(["Nomor Pesanan","Tanggal","Email","Status","Pembayaran","Kurir","Total","Produk"])
    for r in rows: writer.writerow([r["order_no"],r["created_at"],r["email"] or "Tamu",r["status"],r["payment_status"],r["courier"],r["total"],r["products"]])
    resp=Response("\ufeff"+out.getvalue(),mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"]="attachment; filename=laporan-second-luxe.csv"
    return resp


@bp.get("/panduan")
def guide():
    return render_template("admin/guide.html", active="guide")
