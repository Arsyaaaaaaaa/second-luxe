"""Ongkos kirim simulasi: tarif per zona provinsi dan per kurir."""

# Zona 1 = dekat gudang (Jakarta), 4 = paling jauh
PROVINCES = {
    "DKI Jakarta": 1, "Banten": 1, "Jawa Barat": 1,
    "Jawa Tengah": 2, "DI Yogyakarta": 2, "Jawa Timur": 2, "Lampung": 2, "Sumatera Selatan": 2,
    "Bengkulu": 2, "Kepulauan Bangka Belitung": 2, "Bali": 2,
    "Aceh": 3, "Sumatera Utara": 3, "Sumatera Barat": 3, "Riau": 3, "Kepulauan Riau": 3, "Jambi": 3,
    "Kalimantan Barat": 3, "Kalimantan Tengah": 3, "Kalimantan Selatan": 3, "Kalimantan Timur": 3,
    "Kalimantan Utara": 3, "Sulawesi Utara": 3, "Sulawesi Tengah": 3, "Sulawesi Selatan": 3,
    "Sulawesi Tenggara": 3, "Sulawesi Barat": 3, "Gorontalo": 3, "Nusa Tenggara Barat": 3,
    "Nusa Tenggara Timur": 3,
    "Maluku": 4, "Maluku Utara": 4, "Papua": 4, "Papua Barat": 4, "Papua Barat Daya": 4,
    "Papua Selatan": 4, "Papua Tengah": 4, "Papua Pegunungan": 4,
}

COURIERS = {
    "JNE": {"label": "JNE Reguler",
            "cost": {1: 18000, 2: 25000, 3: 42000, 4: 75000},
            "eta": {1: "1-2 hari kerja", 2: "2-3 hari kerja", 3: "4-6 hari kerja", 4: "6-9 hari kerja"}},
    "J&T": {"label": "J&T Express",
            "cost": {1: 17000, 2: 24000, 3: 40000, 4: 72000},
            "eta": {1: "1-2 hari kerja", 2: "2-4 hari kerja", 3: "4-7 hari kerja", 4: "7-10 hari kerja"}},
    "SiCepat": {"label": "SiCepat Halu",
                "cost": {1: 16000, 2: 23000, 3: 41000, 4: 70000},
                "eta": {1: "1-2 hari kerja", 2: "2-3 hari kerja", 3: "4-6 hari kerja", 4: "6-9 hari kerja"}},
    # Gojek (instan) hanya untuk zona 1
    "Gojek": {"label": "Gojek Instant",
              "cost": {1: 35000},
              "eta": {1: "Hari yang sama"}},
}


def quote(province, courier):
    """Tarif satu kurir untuk satu provinsi, atau None jika tidak tersedia."""
    zone = PROVINCES.get(province)
    c = COURIERS.get(courier)
    if zone is None or c is None or zone not in c["cost"]:
        return None
    return {"courier": courier, "label": c["label"], "cost": c["cost"][zone], "eta": c["eta"][zone]}


def all_quotes(province):
    """Tarif semua kurir untuk satu provinsi: {kode: quote atau None}."""
    return {code: quote(province, code) for code in COURIERS}
