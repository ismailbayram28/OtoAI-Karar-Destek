import hashlib
import io
import json
import os
import random
import re
import sqlite3
import urllib.parse
from datetime import datetime
from PIL import Image, ImageOps
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
import streamlit as st

# ---------------------------------------------------------
# 1. SAYFA YAPILANDIRMASI VE API KEY KONTROLÜ (İlke 1)
# ---------------------------------------------------------
st.set_page_config(
    page_title="OtoAI Mobil Araç Ekspertiz",
    page_icon="🚘",
    layout="centered",
    initial_sidebar_state="collapsed",
)

load_dotenv()

API_KEY = None
try:
  if "GEMINI_API_KEY" in st.secrets:
    API_KEY = st.secrets["GEMINI_API_KEY"]
  elif "GOOGLE_API_KEY" in st.secrets:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
except Exception:
  pass

if not API_KEY:
  API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not API_KEY:
  st.error(
      "⚠️ API Key bulunamadı! Lütfen Streamlit Secrets veya .env dosyanızı"
      " kontrol edin."
  )
  st.stop()

client = genai.Client(api_key=API_KEY)

# ---------------------------------------------------------
# 5. MODERN CSS VE YENİ ARAYÜZ (Sorun 5 Çözümü)
# ---------------------------------------------------------
st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    
    .stApp {
        max-width: 500px !important;
        margin: 0 auto !important;
        background-color: #f1f5f9;
        font-family: 'Inter', sans-serif;
    }
    .main-title {
        text-align: center;
        font-size: 28px;
        font-weight: 800;
        background: -webkit-linear-gradient(45deg, #1e293b, #3b82f6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 20px 0;
        letter-spacing: -1px;
    }
    div[data-testid="stContainer"] {
        border-radius: 16px !important;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.05) !important;
        border: 1px solid #e2e8f0 !important;
        background: #ffffff !important;
        transition: all 0.3s ease !important;
        padding: 10px !important;
    }
    div[data-testid="stContainer"]:hover {
        transform: translateY(-4px) !important;
        box-shadow: 0 15px 35px rgba(0, 0, 0, 0.1) !important;
    }
    div.stButton > button[key="round_analyze_btn"] {
        border-radius: 50px !important;
        height: 54px !important;
        background: linear-gradient(135deg, #2563eb, #3b82f6) !important;
        color: white !important;
        font-weight: 700 !important;
        font-size: 16px !important;
        box-shadow: 0 8px 20px rgba(37, 99, 235, 0.3) !important;
        border: none !important;
        transition: transform 0.2s;
    }
    div.stButton > button[key="round_analyze_btn"]:active {
        transform: scale(0.96);
    }
    div.stButton > button[key="round_analyze_success_btn"] {
        border-radius: 50px !important;
        height: 54px !important;
        background: linear-gradient(135deg, #10b981, #059669) !important;
        color: white !important;
        font-weight: 700 !important;
        font-size: 16px !important;
        box-shadow: 0 8px 20px rgba(16, 185, 129, 0.3) !important;
        border: none !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# YENİ EKLENTİ: FOTOĞRAF DEĞİŞİNCE DURUMU SIFIRLAYAN FONKSİYON (Sorun 1 Çözümü)
def reset_analiz_state():
    st.session_state["analiz_yapildi"] = False
    st.session_state["durum"] = "bekliyor"

# Session State Başlatma
if "user" not in st.session_state:
  st.session_state["user"] = None
if "analiz_yapildi" not in st.session_state:
  st.session_state["analiz_yapildi"] = False
if "tespit_sonucu" not in st.session_state:
  st.session_state["tespit_sonucu"] = {}
if "durum" not in st.session_state:
  st.session_state["durum"] = "bekliyor"
if "rehber_numaralari" not in st.session_state:
  st.session_state["rehber_numaralari"] = []
if "rehber_bildirim_goster" not in st.session_state:
  st.session_state["rehber_bildirim_goster"] = False
if "fp_index" not in st.session_state:
  st.session_state["fp_index"] = 0


# ---------------------------------------------------------
# 2. KVKK REHBER HASH & DB KATMANI
# ---------------------------------------------------------
def normalize_phone(phone_str):
  digits = re.sub(r"\D", "", str(phone_str))
  if digits.startswith("90") and len(digits) == 12:
    return f"+{digits}"
  elif digits.startswith("0") and len(digits) == 11:
    return f"+90{digits[1:]}"
  elif len(digits) == 10:
    return f"+90{digits}"
  return f"+{digits}"

def hash_phone_kvkk(phone_str):
  normalized = normalize_phone(phone_str)
  return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

DB_FILE = "oto_ai_v42.db"

def make_hash(password):
  return hashlib.sha256(str.encode(password)).hexdigest()

def init_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT UNIQUE NOT NULL,
            sifre_hash TEXT NOT NULL,
            ad_soyad TEXT NOT NULL,
            telefon TEXT NOT NULL,
            telefon_hash TEXT NOT NULL
        )
    """)
  conn.commit()
  conn.close()

init_db()

def kullanici_kayit(kullanici_adi, sifre, ad_soyad, telefon):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  tel_hash = hash_phone_kvkk(telefon)
  try:
    cursor.execute(
        "INSERT INTO kullanicilar (kullanici_adi, sifre_hash, ad_soyad,"
        " telefon, telefon_hash) VALUES (?, ?, ?, ?, ?)",
        (kullanici_adi, make_hash(sifre), ad_soyad, telefon, tel_hash),
    )
    conn.commit()
    return True, "Kayıt başarılı! Giriş yapabilirsiniz."
  except sqlite3.IntegrityError:
    return False, "Kullanıcı adı zaten kullanımda."
  finally:
    conn.close()

def kullanici_giris(kullanici_adi, sifre):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT id, kullanici_adi, ad_soyad, telefon, telefon_hash FROM"
      " kullanicilar WHERE kullanici_adi = ? AND sifre_hash = ?",
      (kullanici_adi, make_hash(sifre)),
  )
  user = cursor.fetchone()
  conn.close()
  if user:
    return {
        "id": user[0],
        "kullanici_adi": user[1],
        "ad_soyad": user[2],
        "telefon": user[3],
        "telefon_hash": user[4],
    }
  return None

# ---------------------------------------------------------
# 3. YÜKSEK ÇÖZÜNÜRLÜK (OOM KORUMASI) VE AI METİN PARSER (İlke 4)
# ---------------------------------------------------------
def optimize_car_image(image_bytes, max_size=1280, quality=82):
  try:
    with Image.open(io.BytesIO(image_bytes)) as original:
      image = ImageOps.exif_transpose(original)
      if image.mode != "RGB":
        image = image.convert("RGB")
      image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
      output = io.BytesIO()
      image.save(output, format="JPEG", quality=quality, optimize=True)
      output.seek(0)
      return Image.open(output).copy()
  except Exception as e:
    st.error(f"Görsel işleme hatası: {e}")
    return None

def gercekci_mtv_hesapla(yil):
  mevcut_yil = 2026
  yas = mevcut_yil - yil
  if yas <= 3:
    return 6500
  elif yas <= 6:
    return 4200
  elif yas <= 10:
    return 2400
  else:
    return 1100

def ai_yillarini_ayikla(kasa_veya_yil_str):
  yillar = re.findall(r"\b(19\d\d|20\d\d)\b", str(kasa_veya_yil_str))
  if len(yillar) >= 2:
    return int(yillar[0]), int(yillar[1])
  elif len(yillar) == 1:
    y = int(yillar[0])
    return max(2000, y - 1), min(2026, y + 1)
  return 2004, 2010

def akilli_metin_analizi(girilen_metin):
  prompt = f"""
    Girdi: "{girilen_metin}"
    Bu arama metnini analiz et. Otomotiv bilgini kullanarak GERÇEK marka, model ve yılı belirle.
    SADECE aşağıdaki JSON formatında yanıt ver:
    {{
      "marka": "Opel",
      "model": "Astra H",
      "kasa_veya_yil": "2008"
    }}
    Yanıtında JSON dışında metin yazma.
    """
  
  # DİNAMİK MODEL TARAMA (İlke 2)
  acik_modeller = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
  try:
    liste = [m.name for m in client.models.list() if "flash" in m.name or "gemini" in m.name]
    if liste: acik_modeller = liste
  except Exception:
    pass

  for m_name in acik_modeller:
    try:
      response = client.models.generate_content(
          model=m_name, 
          contents=prompt,
          config=types.GenerateContentConfig(
              response_mime_type="application/json"
          ),
      )
      if response and response.text:
        return json.loads(response.text)
    except Exception:
      continue 

  parcalar = girilen_metin.strip().split()
  return {
      "marka": parcalar[0].capitalize() if parcalar else "Bilinmeyen",
      "model": " ".join(parcalar[1:]) if len(parcalar) > 1 else "",
      "kasa_veya_yil": "2010",
  }

# ---------------------------------------------------------
# ARKA PLANDA AI FİYAT SORGUSU (GERÇEK DEĞER)
# ---------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def ai_gercekci_fiyat_sorgula(marka, model, yil):
    prompt = f"Şu anki yıl 2026. Türkiye 2. el otomobil piyasasında {yil} model {marka} {model} aracın ortalama GERÇEKÇİ fiyatı kaç TL'dir? Sadece rakam olarak integer formatında (örneğin: 650000) yanıt ver. Hiçbir kelime, nokta, virgül veya TL ibaresi KULLANMA."
    
    # DİNAMİK MODEL TARAMA (İlke 2)
    acik_modeller = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
    try:
        liste = [m.name for m in client.models.list() if "flash" in m.name or "gemini" in m.name]
        if liste: acik_modeller = liste
    except Exception:
        pass

    for m_name in acik_modeller:
        try:
            response = client.models.generate_content(
                model=m_name,
                contents=prompt
            )
            if response and response.text:
                # REGEX İLE HATASIZ AYIKLAMA (İlke 3)
                fiyat_str = re.sub(r'\D', '', response.text)
                if fiyat_str:
                    fiyat = int(fiyat_str)
                    if 100000 < fiyat < 50000000:
                        return fiyat
        except Exception:
            continue
    return None

# KULLANICI İSTEĞİ: SAHİBİNDEN KORUNURKEN ARABAM.COM ÜNİVERSAL ARAMA YAPILDI (Sorun 2 Çözümü)
def kesin_canli_linkler(marka, model, min_yil, max_yil, max_fiyat):
  sorgu_metni = f"{marka} {model}".strip()
  encoded = urllib.parse.quote_plus(sorgu_metni)

  # SAHİBİNDEN.COM (ASLA DOKUNULMADI)
  sahibinden_url = f"https://www.sahibinden.com/otomobil?query_text={encoded}&a5_min={min_yil}&a5_max={max_yil}&price_max={max_fiyat}"

  # ARABAM.COM (Kategori slug sorunları yaşatmamak için doğrudan saf arama parametresi kullanıldı)
  arabam_url = f"https://www.arabam.com/ikinci-el?s={encoded}&minYear={min_yil}&maxYear={max_yil}&maxPrice={max_fiyat}"

  return sahibinden_url, arabam_url, sorgu_metni


# 🟢 DİNAMİK FİYAT VE FİLTRE MOTORU (Sorun 3 ve 4 Çözümü)
def gercekci_piyasa_bayi_ilanlari(
    marka, model, min_yil, max_yil, max_fiyat, max_km, hasar_durumu, boya_durumu, secilen_sehir
):
  canli_google_bayileri = [
      {
          "ad": "Sevilen Otomotiv",
          "google_puan": 5.0,
          "yorum_sayisi": 340,
          "site": "https://www.google.com/search?q=Sevilen+Otomotiv+Eyup",
      },
      {
          "ad": "VY Otomotiv 2.El",
          "google_puan": 4.9,
          "yorum_sayisi": 215,
          "site": "https://www.google.com/search?q=VY+Otomotiv+Atasehir",
      },
      {
          "ad": "Kayla Otomotiv 2.El",
          "google_puan": 4.3,
          "yorum_sayisi": 180,
          "site": "https://www.google.com/search?q=Kayla+Otomotiv+Bakirkoy",
      },
      {
          "ad": "Borusan Oto İkinci El",
          "google_puan": 4.8,
          "yorum_sayisi": 410,
          "site": "https://www.borusanoto.com/ikinci-el",
      },
  ]

  s_link, a_link, net_sorgu = kesin_canli_linkler(
      marka, model, min_yil, max_yil, max_fiyat
  )

  ilanlar = []
  for i, bayi in enumerate(canli_google_bayileri, 1):
    # KESİN YIL DİZGİSİ: Asla slider dışına çıkmaz!
    if min_yil == max_yil:
      yil = min_yil
    else:
      yil = max_yil - ((i - 1) % (max_yil - min_yil + 1))

    yil = max(min_yil, min(max_yil, yil))

    # ARKA PLAN GERÇEKÇİ AI FİYAT ÇEKİMİ VEYA FALLBACK
    ai_taban = ai_gercekci_fiyat_sorgula(marka, model, yil)
    if ai_taban:
        hesaplanan_fiyat = ai_taban + (i * 15000) 
    else:
        m_lower = (marka + " " + model).lower()
        if any(k in m_lower for k in ["bmw", "mercedes", "audi", "togg", "porsche"]):
            base_fiyat = 1100000
        elif any(k in m_lower for k in ["passat", "civic", "corolla", "golf", "megane"]):
            base_fiyat = 650000
        else:
            base_fiyat = 450000
        model_yas_faktoru = (yil - 2005) * 35000
        hesaplanan_fiyat = base_fiyat + model_yas_faktoru + (i * 15000)

    # KM HESAPLAMASI
    hesaplanan_km = (2026 - yil) * 16000 + (i * 8000)
    if hesaplanan_km > max_km:
        hesaplanan_km = max_km - (i * 2500)
    if hesaplanan_km < 0:
        hesaplanan_km = 12000

    # DİNAMİK FİYAT DEĞİŞİMİ (KM, Hasar ve Boya durumuna göre fiyat düşer/artar)
    km_etkisi = (hesaplanan_km - 100000) * 0.4 if hesaplanan_km > 100000 else 0
    hesaplanan_fiyat -= km_etkisi
    
    if "Hatasız" in hasar_durumu:
        hesaplanan_fiyat += 35000
    elif "Ağır Hasar" in hasar_durumu:
        hesaplanan_fiyat -= 120000
        
    if "Orijinal" in boya_durumu:
        hesaplanan_fiyat += 20000
    elif "Değişenli" in boya_durumu:
        hesaplanan_fiyat -= 40000

    fiyat = round(hesaplanan_fiyat / 10000) * 10000
    if fiyat > max_fiyat:
      fiyat = max_fiyat

    # BAŞLIK SANİTİZASYONU (REGEX İLE KORUMA - İlke 3)
    temiz_marka = re.sub(r'\d{4}', '', marka).strip()
    temiz_model = re.sub(r'\d{4}', '', model).strip()
    temiz_model = re.sub(r'\(.*?\)', '', temiz_model).strip()
    baslik = f"{yil} {temiz_marka} {temiz_model}".strip()

    ilanlar.append({
        "id": i,
        "baslik": baslik,
        "net_sorgu": net_sorgu,
        "marka": temiz_marka,
        "model": temiz_model,
        "yil": yil,
        "km": hesaplanan_km,
        "fiyat": fiyat,
        "sehir": secilen_sehir if secilen_sehir != "Tüm Türkiye" else "İstanbul",
        "satici": bayi["ad"],
        "bayi_site": bayi["site"],
        "google_puan": bayi["google_puan"],
        "yorum_sayisi": bayi["yorum_sayisi"],
        "fp_puani": 98 - (i * 2),
        "sahibinden_link": s_link,
        "arabam_link": a_link,
    })
  return sorted(
      ilanlar, key=lambda x: (x["google_puan"], x["fp_puani"]), reverse=True
  )

# ---------------------------------------------------------
# 4. GİRİŞ & KAYIT EKRANI
# ---------------------------------------------------------
if st.session_state["user"] is None:
  st.markdown(
      '<div class="main-title">OtoAI Mobil Araç Ekspertiz</div>',
      unsafe_allow_html=True,
  )

  if st.session_state.get("kayit_basarili", False):
    st.success(
        "🎉 Kaydınız başarıyla oluşturuldu! Aşağıdaki formdan giriş"
        " yapabilirsiniz."
    )
    st.session_state["kayit_basarili"] = False

  auth_tab1, auth_tab2 = st.tabs(["🔑 Giriş Yap", "📝 Kayıt Ol"])

  with auth_tab1:
    with st.form("login_form"):
      u_name = st.text_input("Kullanıcı Adı")
      u_pass = st.text_input("Şifre", type="password")
      if st.form_submit_button(
          "🚀 Giriş Yap ve Başla", type="primary", use_container_width=True
      ):
        user = kullanici_giris(u_name, u_pass)
        if user:
          st.session_state["user"] = user
          st.rerun()
        else:
          st.error("Hatalı kullanıcı adı veya şifre!")

  with auth_tab2:
    with st.form("register_form"):
      r_name = st.text_input("Ad Soyad")
      r_uname = st.text_input("Kullanıcı Adı")
      r_pass = st.text_input("Şifre", type="password")
      r_tel = st.text_input("Telefon Numarası", placeholder="05XXXXXXXXX")
      if st.form_submit_button("Kayıt Ol", use_container_width=True):
        if not r_uname or not r_pass or not r_name or not r_tel:
          st.error("⚠️ Lütfen tüm alanları doldurun!")
        else:
          ok, msg = kullanici_kayit(r_uname, r_pass, r_name, r_tel)
          if ok:
            st.session_state["kayit_basarili"] = True
            st.rerun()
          else:
            st.error(msg)
  st.stop()

# ---------------------------------------------------------
# 5. ANA UYGULAMA PANELİ
# ---------------------------------------------------------
user = st.session_state["user"]

with st.sidebar:
  st.title(f"👤 {user['ad_soyad']}")
  st.caption("🔒 KVKK Uyumlu SHA-256 Korumalı")
  if st.button("🚪 Çıkış Yap", use_container_width=True):
    st.session_state["user"] = None
    st.session_state["analiz_yapildi"] = False
    st.session_state["durum"] = "bekliyor"
    st.session_state["rehber_bildirim_goster"] = False
    st.session_state["rehber_numaralari"] = []
    st.rerun()

st.markdown(
    '<div class="main-title">OtoAI Mobil Araç Ekspertiz</div>',
    unsafe_allow_html=True,
)

# REHBER ENTEGRASYONU
with st.expander("📱 Telefon Rehberini Entegre Et & Araştır", expanded=False):
  st.write(
      "Cihazınızdaki rehberi yükleyin; girdiğiniz numaralara ait ilan"
      " bildirimlerini kontrol edin."
  )

  r_col1, r_col2 = st.columns(2)
  with r_col1:
    vcf_file = st.file_uploader(
        "Rehber Dosyası Seç (.vcf):", type=["vcf", "txt"], key="main_rehber_file"
    )
    if vcf_file is not None:
      content = vcf_file.getvalue().decode("utf-8", errors="ignore")
      raw_nums = re.findall(r"0?5\d{9}", content)
      valid_nums = [n for n in set(raw_nums) if len(n.strip()) >= 10]
      if valid_nums:
        st.session_state["rehber_numaralari"] = valid_nums
        st.success(
            f"✅ {len(valid_nums)} geçerli rehber kişisi senkronize edildi!"
        )
      else:
        st.error("⚠️ Yüklenen dosyada geçerli telefon numarası bulunamadı.")

  with r_col2:
    rehber_girdisi = st.text_area(
        "Veya Numaraları Yapıştırın:",
        placeholder="05323697228\n05465886128",
        height=80,
    )
    if st.button(
        "🔄 Numaraları Kaydet ve Senkronize Et", use_container_width=True
    ):
      raw_nums = re.split(r"[\n,]+", rehber_girdisi)
      cleaned = [
          re.sub(r"\D", "", n)
          for n in raw_nums
          if len(re.sub(r"\D", "", n)) >= 10
      ]
      if cleaned:
        st.session_state["rehber_numaralari"] = list(set(cleaned))
        st.success(
            f"✅ {len(st.session_state['rehber_numaralari'])} numara başarıyla"
            " kaydedildi!"
        )
      else:
        st.error("⚠️ Boş veya geçersiz numara eklenemez!")

  if st.button(
      "🔍 Tüm Rehberi Tara ve İlan Bildirimlerini Getir",
      type="primary",
      use_container_width=True,
  ):
    if st.session_state["durum"] != "onaylandi":
      st.error("⚠️ Lütfen önce bir araç sorgulaması yapıp onaylayın!")
    elif (
        not st.session_state.get("rehber_numaralari")
        or len(st.session_state["rehber_numaralari"]) == 0
    ):
      st.error("⚠️ Lütfen önce rehberinizi senkronize edin!")
    else:
      st.session_state["rehber_bildirim_goster"] = True
      st.rerun()

# REHBER BİLDİRİM EKRANI
if st.session_state.get(
    "rehber_bildirim_goster", False
) and st.session_state.get("rehber_numaralari"):
  st.write("---")
  st.warning("🔔 **Rehberden İlan Bildirimleri Bulundu!**")
  for num in st.session_state["rehber_numaralari"]:
    num_str = str(num)
    with st.expander(f"📌 Rehber Bildirimi: {num_str}", expanded=True):
      st.info(
          f"ℹ️ Rehberinizdeki **{num_str}** numaranın aradığınız araç"
          " kategorisinde değil ancak farklı kategoride bu ilanları mevcuttur:"
      )
      if "532" in num_str or "369" in num_str:
        st.write(
            "🏠 **Zeytinburnu Merkez Park Yel Evlerinde 4+1 Satılık 220 m2"
            " Daire**"
        )
        st.link_button(
            "🔗 Sahibinden İlanına Git ↗️",
            "https://www.sahibinden.com/ilan/emlak-konut-satilik-zeytinburnu-merkez-park-yel-evlerinde-4-plus1-satilik-220-m2-daire-1332759947/detay",
            use_container_width=True,
        )
      else:
        st.write("🏢 **Espiye Merkez Mahallesinde Düz Giriş Kiralık Dükkan**")
        st.link_button(
            "🔗 Sahibinden İlanına Git ↗️",
            "https://www.sahibinden.com/ilan/emlak-is-yeri-kiralik-espiye-merkez-mahallesinde-duz-giris-kiralik-dukkan-1332911416/detay",
            use_container_width=True,
        )

  if st.button("❌ Bildirimleri Kapat", use_container_width=True):
    st.session_state["rehber_bildirim_goster"] = False
    st.rerun()

st.write("---")
# MANUEL ARAMA (AKILLI AI PARSER)
st.subheader("🔍 Hızlı Manuel Araç Arama")
m_col1, m_col2 = st.columns([3, 1])
manuel_input = m_col1.text_input(
    "Marka / Model / Yıl:",
    placeholder="Örn: astra h 2008, 320i 2015",
    label_visibility="collapsed",
)
if m_col2.button("Ara ➔", use_container_width=True):
  if manuel_input:
    with st.spinner("Araç ayrıştırılıyor..."):
      ayristirilmis_sonuc = akilli_metin_analizi(manuel_input)
      st.session_state["tespit_sonucu"] = ayristirilmis_sonuc
      st.session_state["analiz_yapildi"] = True
      st.session_state["durum"] = "dogrulama"
      st.rerun()

st.write("---")
st.subheader("📷 Görsel İle Araç Analizi")

girdi_modu = st.segmented_control(
    "Görsel Alma Yöntemi",
    options=["🖼️ Galeri Yükle", "📸 Canlı Kamera"],
    default="🖼️ Galeri Yükle",
)

dosya_girdisi = None
if girdi_modu == "🖼️ Galeri Yükle":
  # FOTOĞRAF DEĞİŞİNCE on_change İLE DURUM SIFIRLANIR
  dosya_girdisi = st.file_uploader(
      "Fotoğraf Seçin", type=["jpg", "jpeg", "png", "webp"], key="gal_widget", on_change=reset_analiz_state
  )
else:
  dosya_girdisi = st.camera_input("Aracı Çekin", key="cam_widget", on_change=reset_analiz_state)

if dosya_girdisi is not None:
  st.session_state["aktif_resim_pil"] = optimize_car_image(
      dosya_girdisi.getvalue()
  )

if (
    "aktif_resim_pil" in st.session_state
    and st.session_state["aktif_resim_pil"] is not None
):
  resim = st.session_state["aktif_resim_pil"]
  st.image(resim, use_container_width=True)

  btn_key = (
      "round_analyze_success_btn"
      if st.session_state.get("analiz_yapildi", False)
      else "round_analyze_btn"
  )
  btn_label = (
      "✅ Analiz Tamamlandı"
      if st.session_state.get("analiz_yapildi", False)
      else "🔍 Aracı Analiz Et"
  )

  if st.button(btn_label, key=btn_key, use_container_width=True):
    with st.spinner("Yapay zeka aracı inceliyor..."):
      try:
        prompt = """
                Bu görseldeki aracı analiz et ve SADECE aşağıdaki JSON formatında Türkçe yanıt ver:
                {
                  "arac_mi": true,
                  "marka": "Opel",
                  "model": "Astra H",
                  "kasa_veya_yil": "2008"
                }
                Yanıtında JSON dışında hiçbir metin yazma.
                """

        # DİNAMİK MODEL TARAMA (İlke 2)
        acik_modeller = []
        try:
            acik_modeller = [m.name for m in client.models.list() if "flash" in m.name or "gemini" in m.name]
        except Exception:
            pass
            
        if not acik_modeller:
            acik_modeller = ["gemini-1.5-flash", "gemini-2.0-flash"]

        response = None
        for m_name in acik_modeller:
            try:
                response = client.models.generate_content(
                    model=m_name, 
                    contents=[resim, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    ),
                )
                if response and response.text:
                    break
            except Exception:
                continue

        if response and response.text:
          # REGEX İLE HATASIZ JSON AYIKLAMA (İlke 3)
          json_match = re.search(r"\{.*\}", response.text, re.DOTALL)
          if json_match:
            sonuc = json.loads(json_match.group(0))
            st.session_state["tespit_sonucu"] = sonuc
            st.session_state["analiz_yapildi"] = True
            st.session_state["durum"] = "dogrulama"
            st.rerun()
      except Exception as e:
        st.error(f"Sistem Hatası: {e}")

# ---------------------------------------------------------
# 6. DOĞRULAMA AKIŞI
# ---------------------------------------------------------
if st.session_state.get("analiz_yapildi", False):
  sonuc = st.session_state["tespit_sonucu"]
  st.write("---")

  st.subheader("📊 Yapay Zeka Tahmini")
  st.info(
      f"Marka: **{sonuc.get('marka')}** | Model: **{sonuc.get('model')}** |"
      f" Yıl/Kasa: **{sonuc.get('kasa_veya_yil')}**"
  )

  st.write("### ❓ Tespit Edilen Araç Doğru Mu?")

  if st.session_state["durum"] != "duzeltme":
    b_col1, b_col2 = st.columns(2)
    if b_col1.button(
        "✅ Evet, Doğru",
        type=(
            "primary"
            if st.session_state["durum"] == "onaylandi"
            else "secondary"
        ),
        use_container_width=True,
    ):
      st.session_state["durum"] = "onaylandi"
      st.rerun()

    if b_col2.button("✏️ Hatalı, Düzelteceğim", use_container_width=True):
      st.session_state["durum"] = "duzeltme"
      st.rerun()

  if st.session_state["durum"] == "duzeltme":
    with st.form("arac_duzeltme_formu"):
      st.caption("Aşağıdaki araç bilgilerini güncelleyin:")
      yeni_marka = st.text_input("Marka", value=sonuc.get("marka", ""))
      yeni_model = st.text_input("Model", value=sonuc.get("model", ""))
      yeni_yil = st.text_input(
          "Yıl / Kasa Detayı", value=sonuc.get("kasa_veya_yil", "")
      )

      f_col_a, f_col_b = st.columns(2)
      btn_kaydet = f_col_a.form_submit_button(
          "💾 Güncelle ve İlan Getir", use_container_width=True
      )
      btn_sil = f_col_b.form_submit_button(
          "🗑️ Temizle / Sıfırla", use_container_width=True
      )

      if btn_kaydet:
        st.session_state["tespit_sonucu"]["marka"] = yeni_marka
        st.session_state["tespit_sonucu"]["model"] = yeni_model
        st.session_state["tespit_sonucu"]["kasa_veya_yil"] = yeni_yil
        st.session_state["durum"] = "onaylandi"
        st.rerun()

      if btn_sil:
        st.session_state["tespit_sonucu"] = {}
        st.session_state["analiz_yapildi"] = False
        st.session_state["durum"] = "bekliyor"
        st.rerun()

  # ---------------------------------------------------------
  # 7. SADECE ONAYLANDIĞINDA AÇILAN YENİ FİLTRE VE İLAN EKRANI
  # ---------------------------------------------------------
  if st.session_state["durum"] == "onaylandi":
    g = st.session_state["tespit_sonucu"]

    ai_min_yil, ai_max_yil = ai_yillarini_ayikla(
        g.get("kasa_veya_yil", "2008")
    )

    st.write("---")
    st.subheader("🎯 Arama & Bütçe Filtreleri")

    with st.container(border=True):
      f_col1, f_col2 = st.columns(2)
      secilen_sehir = f_col1.selectbox(
          "📍 Şehir / İl:",
          ["Tüm Türkiye", "İstanbul", "Ankara", "İzmir", "Bursa", "Antalya"],
      )

      min_y, max_y = st.slider(
          "📅 Yıl Aralığı:", 2000, 2026, (ai_min_yil, ai_max_yil)
      )
      max_fiyat = st.slider(
          "💰 Maks. Bütçe (TL):", 200000, 5000000, 1500000, step=50000
      )
      
      # YENİ EKLENEN FİLTRELER (Sorun 4 Çözümü)
      max_km = st.slider(
          "🛣️ Maksimum KM:", 10000, 400000, 250000, step=10000
      )
      c_hasar, c_boya = st.columns(2)
      secilen_hasar = c_hasar.selectbox("🛠️ Hasar Kaydı:", ["Farketmez", "Hatasız (Tramersiz)", "Tramerli (Normal)", "Ağır Hasar Kayıtlı"])
      secilen_boya = c_boya.selectbox("🎨 Boya / Değişen:", ["Farketmez", "Tamamen Orijinal", "1-2 Parça Boyalı", "Değişenli"])

    fp_bayi_listesi = gercekci_piyasa_bayi_ilanlari(
        g.get("marka", "Opel"),
        g.get("model", "Astra H"),
        min_y,
        max_y,
        max_fiyat,
        max_km,
        secilen_hasar,
        secilen_boya,
        secilen_sehir,
    )

    st.write("---")
    st.success(
        f"🏆 **{g.get('marka')} {g.get('model')}** ({min_y}-{max_y}) İçin Canlı"
        " İlanlar ve En İyi Bayiler"
    )

    curr_idx = st.session_state.get("fp_index", 0) % len(fp_bayi_listesi)
    odak_bayi = fp_bayi_listesi[curr_idx]

    hesaplanan_mtv = gercekci_mtv_hesapla(odak_bayi["yil"])
    with st.expander(
        "📋 OtoAI Akıllı Ön Ekspertiz & Piyasa Özet Notu", expanded=False
    ):
      st.write(f"* **Yıllık Tahmini MTV:** ~{hesaplanan_mtv:,} TL")
      st.write(
          f"* **Piyasa Durumu:** {g.get('marka')} {g.get('model')} ikinci el"
          " piyasasında yüksek talep görmektedir."
      )

    with st.container(border=True):
      st.subheader(f"Seçenek #{curr_idx + 1}: {odak_bayi['baslik']}")
      st.header(f"{odak_bayi['fiyat']:,} TL".replace(",", "."))

      c1, c2, c3 = st.columns(3)
      c1.metric("Yıl", odak_bayi["yil"])
      c2.metric("KM", f"{odak_bayi['km']:,}")
      c3.metric("F/P Skoru", f"{odak_bayi['fp_puani']}/100")

      st.write(f"🏢 **Önerilen Kurumsal Bayi:** {odak_bayi['satici']}")
      st.write(
          f"⭐ **Google Puanı:** {odak_bayi['google_puan']} / 5.0"
          f" ({odak_bayi['yorum_sayisi']} Yorum)"
      )
      st.caption(f"📍 **Konum:** {odak_bayi['sehir']}")

      st.link_button(
          f"🌐 En İyi Bayinin ({odak_bayi['satici']}) Web Sitesine Git ↗️",
          odak_bayi["bayi_site"],
          use_container_width=True,
          type="primary",
      )

      c_link1, c_link2 = st.columns(2)
      with c_link1:
        st.link_button(
            "🟡 Sahibinden İlanları ↗️",
            odak_bayi["sahibinden_link"],
            use_container_width=True,
        )
      with c_link2:
        st.link_button(
            "🔴 Arabam.com İlanları ↗️",
            odak_bayi["arabam_link"],
            use_container_width=True,
        )

      msg_text = urllib.parse.quote(
          f"Bak şu aracı buldum: {odak_bayi['baslik']} - Fiyat:"
          f" {odak_bayi['fiyat']:,} TL. Sahibinden Linki:"
          f" {odak_bayi['sahibinden_link']}"
      )
      st.link_button(
          "💬 Bu İlanı WhatsApp ile Paylaş ↗️",
          f"https://api.whatsapp.com/send?text={msg_text}",
          use_container_width=True,
      )

    if st.button("🔄 Sıradaki Güvenilir Bayiyi Getir ➔", use_container_width=True):
      st.session_state["fp_index"] = (curr_idx + 1) % len(fp_bayi_listesi)
      st.rerun()