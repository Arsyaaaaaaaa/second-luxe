"""Estimasi harga "Jual ke Kami" berbasis aturan (tanpa AI).

Dipakai sebagai cadangan: dipanggil langsung jika ai.py belum ada (Tahap 5) atau jika API AI
gagal / key kosong. Hasilnya selalu berupa dict {"min", "max", "notes", "source"}.
"""
import statistics

# Pilihan kondisi di form: kunci -> (label tampil, faktor harga)
CONDITIONS = {
    "seperti_baru": ("Seperti baru (9-10 dari 10)", 1.00),
    "sangat_baik": ("Sangat baik (8 dari 10)", 0.90),
    "baik": ("Baik, ada tanda pemakaian (6-7 dari 10)", 0.78),
    "cukup": ("Cukup, ada minus yang terlihat (4-5 dari 10)", 0.62),
}
PREMIUM = {"hermes", "chanel", "louis vuitton", "dior", "gucci"}
MIDRANGE = {"prada", "balenciaga"}


def round_50k(n):
    """Bulatkan ke kelipatan Rp 50.000 terdekat (minimal Rp 50.000)."""
    return max(int(round(n / 50000.0)) * 50000, 50000)


def rupiah(n):
    return "Rp " + f"{int(n):,}".replace(",", ".")


def market_median(db, brand):
    """Median harga produk brand yang sama di katalog kita (None jika belum ada)."""
    rows = db.execute("""SELECT p.price FROM products p JOIN brands b ON b.id = p.brand_id
                         WHERE lower(b.name) = lower(?)""", (brand,)).fetchall()
    prices = [r[0] for r in rows]
    return statistics.median(prices) if prices else None


def rule_estimate(db, brand, item_name, condition_key, expected_price, description=""):
    """Hitung rentang harga beli oleh Second Luxe dari harga harapan, kondisi, dan pasar internal."""
    label, factor = CONDITIONS.get(condition_key, CONDITIONS["baik"])
    notes = []
    anchor = float(expected_price)

    median = market_median(db, brand)
    if median:
        notes.append(f"Median harga {brand} di katalog kami sekitar {rupiah(median)}.")
        if expected_price > median * 1.5:
            anchor = (expected_price + median) / 2     # harapan jauh di atas pasar: tarik ke tengah
            notes.append("Harga harapan Anda jauh di atas pasar kami, jadi estimasi disesuaikan turun.")
    else:
        notes.append(f"Belum ada data {brand} di katalog kami, estimasi bertumpu pada harga harapan.")

    notes.append(f"Kondisi \"{label}\" memakai faktor {int(factor * 100)}% dari harga acuan.")
    mid = anchor * factor

    # Margin beli: brand premium lebih mudah terjual kembali, jadi tawaran lebih tinggi
    key = brand.strip().lower()
    if key in PREMIUM:
        lo, hi = 0.65, 0.85
        notes.append("Brand premium: perputaran cepat, rentang tawaran lebih tinggi.")
    elif key in MIDRANGE:
        lo, hi = 0.58, 0.78
    else:
        lo, hi = 0.50, 0.70

    low, high = round_50k(mid * lo), round_50k(mid * hi)
    if high <= low:
        high = low + 50000
    notes.append("Ini estimasi otomatis. Penawaran final menunggu pemeriksaan keaslian dan kondisi fisik oleh tim kami.")
    return {"min": low, "max": high, "notes": " ".join(notes), "source": "aturan"}
