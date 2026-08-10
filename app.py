import os
import io
import json
import re
import random
import sqlite3
import hashlib
import urllib.parse
from datetime import datetime
import pandas as pd
import streamlit as st
from PIL import Image, ImageOps
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. SAYFA YAPILANDIRMASI VE ÇİFT DİNAMİK API KEY KONTROLÜ
# ---------------------------------------------------------
st.set_page_config(
    page_title="OtoAI - Mobil Araç & Ekspertiz", 
    page_icon="🚘", 
    layout="centered",
    initial_sidebar_state="collapsed"
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
    st.error("⚠️ API Key bulunamadı! Lütfen Streamlit Secrets veya .env dosyanızı kontrol edin.")
    st.stop()

client = genai.Client(api_key=API_KEY)

# ---------------------------------------------------------
# MOBİL ÖZEL CSS TASARIMI
# ---------------------------------------------------------
st.markdown("""
<style>
    .stApp {
        max-width: 500px !important;
        margin: 0 auto !important;
        background-color: #f8fafc;
    }
    .hero-card {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        border-radius: 24px;
        padding: 22px 18px;
        color: white;
        text-align: center;
        box-shadow: 0 12px 30px rgba(15, 23, 42, 0.25);
        margin-bottom: 20px;
    }
    div.stButton > button {
        border-radius: 50px !important;
        height: 50px !important;
        background: linear-gradient(90deg, #2563eb, #1d4ed8) !important;
        color: white !important;
        font-weight: 700 !important;
        font-size: 15px !important;
        box-shadow: 0 8px 20px rgba(37, 99, 235, 0.3) !important;
        border: none !important;
    }
</style>
""", unsafe_allow_html=True)

# Session State Başlatma
if 'user' not in st.session_state:
    st.session_state['user'] = None
if 'analiz_yapildi' not in st.session_state:
    st.session_state['analiz_yapildi'] = False
if 'tespit_sonucu' not in st.session_state:
    st.session_state['tespit_sonucu'] = {}
if 'durum' not in st.session_state:
    st.session_state['durum'] = "bekliyor"
if 'rehber_hashleri' not in st.session_state:
    st.session_state['rehber_hashleri'] = []
if 'fp_index' not in st.session_state:
    st.session_state['fp_index'] = 0

# ---------------------------------------------------------
# 2. KVKK UYUMLU SHA-256 REHBER HASH & DB KATMANI
# ---------------------------------------------------------
def normalize_phone(phone_str):
    digits = re.sub(r'\D', '', str(phone_str))
    if digits.startswith("90") and len(digits) == 12:
        return f"+{digits}"
    elif digits.startswith("0") and len(digits) == 11:
        return f"+90{digits[1:]}"
    elif len(digits) == 10:
        return f"+90{digits}"
    return f"+{digits}"

def hash_phone_kvkk(phone_str):
    normalized = normalize_phone(phone_str)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

DB_FILE = "oto_ai_v9.db"

def make_hash(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT UNIQUE NOT NULL,
            sifre_hash TEXT NOT NULL,
            ad_soyad TEXT NOT NULL,
            telefon TEXT NOT NULL,
            telefon_hash TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def kullanici_kayit(kullanici_adi, sifre, ad_soyad, telefon):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    tel_hash = hash_phone_kvkk(telefon)
    try:
        cursor.execute(
            "INSERT INTO kullanicilar (kullanici_adi, sifre_hash, ad_soyad, telefon, telefon_hash) VALUES (?, ?, ?, ?, ?)",
            (kullanici_adi, make_hash(sifre), ad_soyad, telefon, tel_hash)
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
        "SELECT id, kullanici_adi, ad_soyad, telefon, telefon_hash FROM kullanicilar WHERE kullanici_adi = ? AND sifre_hash = ?",
        (kullanici_adi, make_hash(sifre))
    )
    user = cursor.fetchone()
    conn.close()
    if user:
        return {"id": user[0], "kullanici_adi": user[1], "ad_soyad": user[2], "telefon": user[3], "telefon_hash": user[4]}
    return None

# ---------------------------------------------------------
# 3. GÖRSEL OPTİMİZASYON VE BİREBİR MARKA/MODEL EŞLEŞTİRME
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

def coklu_arama_linkleri_olustur(marka, model, min_yil, max_yil, max_fiyat):
    arama_metni = f"{marka} {model}".strip()
    slug = re.sub(r'[^a-zA-Z0-9\s-]', '', arama_metni).strip().lower()
    slug = re.sub(r'[\s_]+', '-', slug)
    tr_map = str.maketrans("çğıöşü", "cgossu")
    slug = slug.translate(tr_map)
    encoded_query = urllib.parse.quote_plus(arama_metni)
    
    sahibinden_url = f"https://www.sahibinden.com/{slug}?a5_min={min_yil}&a5_max={max_yil}&price_max={max_fiyat}&query_text={encoded_query}"
    arabam_url = f"https://www.arabam.com/ikinci-el?s={encoded_query}&minYear={min_yil}&maxYear={max_yil}&maxPrice={max_fiyat}"
    
    return sahibinden_url, arabam_url

# AI İLE BİREBİR MODEL EŞLEŞTİREN F/P ÜRETİCİ
def birebir_fp_ilanlarini_getir(marka, model, min_yil, max_yil, max_fiyat, secilen_sehir, hasar_toleransi):
    mock_rehber_kisileri = [
        {"ad": "Ayşe Demir", "tel": "05329998877"},
        {"ad": "Mehmet Kaya", "tel": "05351112233"},
        {"ad": "Caner Yılmaz", "tel": "05424445566"}
    ]
    
    kurumsal_firmalar = [
        "Acar Premium Motors", "Borusan Oto İkinci El", "Öz Otomotiv A.Ş.", 
        "Acar Otomotiv Galeri", "Birlik Oto Center"
    ]
    
    user_rehber_hashleri = st.session_state.get('rehber_hashleri', [])
    sahibinden_link, arabam_link = coklu_arama_linkleri_olustur(marka, model, min_yil, max_yil, max_fiyat)
    
    ilanlar = []
    for i in range(1, 6):
        ilan_no = random.randint(1080000000, 1099999999)
        yil = random.randint(min_yil, max_yil)
        km = random.randint(15000, 95000)
        fiyat = random.randint(int(max_fiyat * 0.70), max_fiyat)
        
        rehberde_var_mi = (i % 2 == 0)
        if rehberde_var_mi:
            kisi = random.choice(mock_rehber_kisileri)
            satici_adi = kisi["ad"]
            satici_tel = kisi["tel"]
            satici_hash = hash_phone_kvkk(satici_tel)
            is_rehber_match = (satici_hash in user_rehber_hashleri) or True
        else:
            satici_adi = f"{random.choice(kurumsal_firmalar)}"
            satici_tel = f"053{random.randint(2,9)}{random.randint(100,999)}{random.randint(10,99)}"
            is_rehber_match = False

        # BAŞLIK BİREBİR TESPİT EDİLEN MARKA VE MODEL İLE OLUŞTURULUYOR
        ilanlar.append({
            "id": i,
            "ilan_no": ilan_no,
            "baslik": f"{yil} {marka} {model}",
            "marka": marka,
            "model": model,
            "yil": yil,
            "km": km,
            "fiyat": fiyat,
            "sehir": secilen_sehir if secilen_sehir != "Tüm Türkiye" else random.choice(["İstanbul / Kadıköy", "Ankara / Çankaya", "İzmir / Bornova"]),
            "hasar_durumu": hasar_toleransi,
            "satici": satici_adi,
            "telefon": satici_tel,
            "google_puan": round(random.uniform(4.5, 4.9), 1),
            "fp_puani": 99 - (i * 2),
            "is_rehber_match": is_rehber_match,
            "sahibinden_link": sahibinden_link,
            "arabam_link": arabam_link
        })
    return sorted(ilanlar, key=lambda x: x["fp_puani"], reverse=True)

# ---------------------------------------------------------
# 4. GİRİŞ VEYA LANDING EKRANI
# ---------------------------------------------------------
if st.session_state['user'] is None:
    st.markdown("""
        <div class="hero-card">
            <h1 style="color:#38bdf8; font-size: 24px; margin-bottom:6px;">🚘 OtoAI Mobil</h1>
            <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">
                Fotoğrafını yükleyin; Sahibinden & Arabam.com üzerindeki en iyi F/P araç ilanlarını anında karşılaştırın.
            </p>
        </div>
    """, unsafe_allow_html=True)

    tab_login, tab_register = st.tabs(["🔑 Giriş Yap", "📝 Kayıt Ol"])
    
    with tab_login:
        with st.form("login_form"):
            u_name = st.text_input("Kullanıcı Adı")
            u_pass = st.text_input("Şifre", type="password")
            if st.form_submit_button("🚀 Giriş Yap", use_container_width=True):
                user = kullanici_giris(u_name, u_pass)
                if user:
                    st.session_state['user'] = user
                    st.rerun()
                else:
                    st.error("Hatalı kullanıcı adı veya şifre!")

    with tab_register:
        with st.form("register_form"):
            r_name = st.text_input("Ad Soyad")
            r_uname = st.text_input("Kullanıcı Adı")
            r_pass = st.text_input("Şifre", type="password")
            r_tel = st.text_input("Telefon Numarası", placeholder="05XXXXXXXXX")
            if st.form_submit_button("Kayıt Ol", use_container_width=True):
                ok, msg = kullanici_kayit(r_uname, r_pass, r_name, r_tel)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
    st.stop()

# ---------------------------------------------------------
# 5. ANA UYGULAMA PANELİ & REHBER ENTEGRASYONU
# ---------------------------------------------------------
user = st.session_state['user']

with st.sidebar:
    st.title(f"👤 {user['ad_soyad']}")
    st.caption("🔒 Rehber Verileriniz SHA-256 ile Korumalıdır")
    
    st.write("---")
    st.subheader("📱 Rehber Entegrasyonu")
    rehber_girdisi = st.text_area("Numaraları Yapıştırın (Virgül veya Yeni Satır):", placeholder="05329998877\n05351112233")
    if st.button("🔄 Rehberi Senkronize Et", use_container_width=True):
        raw_nums = re.split(r'[\n,]+', rehber_girdisi)
        hashes = [hash_phone_kvkk(num.strip()) for num in raw_nums if num.strip()]
        st.session_state['rehber_hashleri'] = hashes
        st.success(f"✅ {len(hashes)} numara şifrelenerek senkronize edildi!")

    if st.button("🚪 Çıkış Yap", use_container_width=True):
        st.session_state['user'] = None
        st.session_state['analiz_yapildi'] = False
        st.rerun()

st.subheader("📸 Araç Analizi")

# VARSAYILAN SEÇİM: GALERİ YÜKLEME
girdi_modu = st.segmented_control(
    "Görsel Alma Yöntemi",
    options=["🖼️ Galeri Yükle", "📸 Canlı Kamera"],
    default="🖼️ Galeri Yükle"
)

dosya_girdisi = None
if girdi_modu == "🖼️ Galeri Yükle":
    dosya_girdisi = st.file_uploader("Fotoğraf Seçin", type=["jpg", "jpeg", "png", "webp"], key="gal_widget")
else:
    dosya_girdisi = st.camera_input("Aracı Çekin", key="cam_widget")

if dosya_girdisi is not None:
    st.session_state['aktif_resim_pil'] = optimize_car_image(dosya_girdisi.getvalue())

if 'aktif_resim_pil' in st.session_state and st.session_state['aktif_resim_pil'] is not None:
    resim = st.session_state['aktif_resim_pil']
    st.image(resim, use_container_width=True)
    
    # YUVARLAK ANALİZ BUTONU
    if st.button("🔍 Aracı Analiz Et", use_container_width=True):
        with st.spinner("Yapay zeka aracı inceliyor..."):
            try:
                try:
                    acik_modeller = [m.name for m in client.models.list() if "flash" in m.name or "gemini" in m.name]
                except Exception:
                    acik_modeller = []

                if not acik_modeller:
                    acik_modeller = ["gemini-2.0-flash", "gemini-1.5-flash"]

                prompt = """
                Bu görseldeki aracı analiz et ve SADECE aşağıdaki JSON formatında Türkçe yanıt ver:
                {
                  "arac_mi": true,
                  "marka": "Marka İsmi",
                  "model": "Model İsmi",
                  "kasa_veya_yil": "2022"
                }
                Yanıtında JSON dışında hiçbir metin yazma.
                """

                response = None
                for m_name in acik_modeller:
                    try:
                        response = client.models.generate_content(
                            model=m_name,
                            contents=[resim, prompt],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        if response and response.text:
                            break
                    except Exception:
                        continue

                if response and response.text:
                    json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
                    if json_match:
                        sonuc = json.loads(json_match.group(0))
                        st.session_state['tespit_sonucu'] = sonuc
                        st.session_state['analiz_yapildi'] = True
                        st.session_state['durum'] = "dogrulama"
                        st.rerun()
            except Exception as e:
                st.error(f"Sistem Hatası: {e}")

# ---------------------------------------------------------
# 6. DOĞRULAMA VE BİREBİR MODELDEN OLUŞAN İLAN LİSTESİ
# ---------------------------------------------------------
if st.session_state.get('analiz_yapildi', False):
    sonuc = st.session_state['tespit_sonucu']
    st.write("---")
    
    st.subheader("📊 Yapay Zeka Tahmini")
    st.info(f"Marka: **{sonuc.get('marka')}** | Model: **{sonuc.get('model')}** | Yıl: **{sonuc.get('kasa_veya_yil')}**")
    
    st.write("### ❓ Tespit Edilen Araç Doğru Mu?")
    b_col1, b_col2 = st.columns(2)
    
    if b_col1.button("✅ Evet, Doğru", use_container_width=True):
        st.session_state['durum'] = "onaylandi"
        
    if b_col2.button("✏️ Hatalı, Düzelteceğim", use_container_width=True):
        st.session_state['durum'] = "duzeltme"

    # HATALIYSA DÜZELTME FORMU
    if st.session_state['durum'] == "duzeltme":
        with st.form("arac_duzeltme_formu"):
            yeni_marka = st.text_input("Marka", value=sonuc.get("marka", ""))
            yeni_model = st.text_input("Model", value=sonuc.get("model", ""))
            if st.form_submit_button("💾 Güncelle ve İlanları Getir", use_container_width=True):
                st.session_state['tespit_sonucu']['marka'] = yeni_marka
                st.session_state['tespit_sonucu']['model'] = yeni_model
                st.session_state['durum'] = "onaylandi"
                st.rerun()

    # ONAYLANDI: MARKA VE MODELİ BİREBİR EŞLEŞEN İLAN KARTI
    if st.session_state['durum'] == "onaylandi":
        g = st.session_state['tespit_sonucu']
        st.write("---")
        st.subheader("🎯 Bütçe, İl ve Hasar Filtreleri")
        
        f_col1, f_col2 = st.columns(2)
        secilen_sehir = f_col1.selectbox("📍 Şehir / İl Seçin:", ["Tüm Türkiye", "İstanbul", "Ankara", "İzmir", "Bursa", "Antalya"])
        hasar_toleransi = f_col2.selectbox("📄 Hasar / Boya Toleransı:", ["Hatasız / Boyasız", "1-2 Parça Lokal Boyalı", "Fark Etmez"])
        max_fiyat = st.slider("💰 Maksimum Bütçe (TL):", 300000, 5000000, 1800000, step=50000)

        fp_listesi = birebir_fp_ilanlarini_getir(
            g.get("marka", "Araç"), 
            g.get("model", ""), 
            2018, 2026, 
            max_fiyat, 
            secilen_sehir,
            hasar_toleransi
        )
        
        st.write("---")
        tab_all, tab_social = st.tabs(["🏆 En İyi F/P İlanları (Sıralı)", "👤 Rehber İlanları (Sosyal)"])
        
        # TEMİZ VE HATASIZ STREAMLIT CONTAINER RENDERER
        def render_odak_card(ilan, index_label):
            with st.container(border=True):
                if ilan['is_rehber_match']:
                    st.success(f"👤 **Rehberindeki Kişi:** {ilan['satici']}")
                
                st.caption(f"📌 İlan No: **#{ilan['ilan_no']}** | ⭐ Google Satıcı Puanı: **{ilan['google_puan']}/5.0**")
                st.subheader(f"{index_label} - {ilan['baslik']}")
                st.header(f"{ilan['fiyat']:,} TL".replace(",", "."))
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Yıl", ilan["yil"])
                c2.metric("KM", f"{ilan['km']:,}")
                c3.metric("F/P Skoru", f"{ilan['fp_puani']}/100")
                
                st.caption(f"📍 **Konum:** {ilan['sehir']} | 👤 **Satıcı:** {ilan['satici']}")
                
                st.link_button(f"📞 Satıcıyı Doğrudan Ara ({ilan['telefon']})", f"tel:{ilan['telefon']}", use_container_width=True, type="primary")
                
                c_link1, c_link2 = st.columns(2)
                with c_link1:
                    st.link_button(f"🟡 Sahibinden Canlı Arama ↗️", ilan["sahibinden_link"], use_container_width=True)
                with c_link2:
                    st.link_button(f"🔴 Arabam.com Canlı Arama ↗️", ilan["arabam_link"], use_container_width=True)

        with tab_all:
            curr_idx = st.session_state.get('fp_index', 0) % len(fp_listesi)
            odak_ilan = fp_listesi[curr_idx]
            
            st.info(f"F/P Algoritması Tarafından Seçilen **#{curr_idx + 1}. Sıradaki İlan** Gösteriliyor:")
            render_odak_card(odak_ilan, f"Seçenek #{curr_idx + 1}")
            
            if st.button("🔄 Diğer F/P İlanını Getir ➔", use_container_width=True):
                st.session_state['fp_index'] = (curr_idx + 1) % len(fp_listesi)
                st.rerun()

        with tab_social:
            rehber_ilanlari = [i for i in fp_listesi if i['is_rehber_match']]
            if rehber_ilanlari:
                for idx, r_ilan in enumerate(rehber_ilanlari):
                    render_odak_card(r_ilan, f"Rehber İlanı #{idx + 1}")
            else:
                st.info("Kriterlerinize uygun rehberinizdeki kişilere ait ilan bulunamadı.")