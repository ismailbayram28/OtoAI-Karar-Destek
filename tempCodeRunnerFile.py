import os
import io
import json
import re
from typing import Optional
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from pydantic import BaseModel, Field
import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

# 1. Ortam Değişkenleri
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("⚠️ UYARI: GEMINI_API_KEY veya GOOGLE_API_KEY .env dosyasında bulunamadı!")
else:
    genai.configure(api_key=api_key)

app = FastAPI(
    title="OtoAI - Arac Tanima Servisi",
    version="1.0.0",
    docs_url="/docs"
)

# 2. Pydantic Modelleri
class AracTahminSonucu(BaseModel):
    arac_mi: bool = Field(description="Gorselde arac var mi?")
    marka: Optional[str] = Field(default=None, description="Marka")
    model: Optional[str] = Field(default=None, description="Model")
    kasa_veya_yil: Optional[str] = Field(default=None, description="Kasa veya Yil")
    kasa_tipi: Optional[str] = Field(default=None, description="Kasa Tipi")
    renk: Optional[str] = Field(default=None, description="Renk")
    guven_orani: float = Field(description="Tahmin Guven Orani")

class AracAramaFiltresi(BaseModel):
    dogrulandi_mi: bool = Field(..., description="Kullanici onayladi mi?")
    marka: str = Field(..., description="Marka")
    model: str = Field(..., description="Model")
    min_yil: Optional[int] = Field(default=2010)
    max_yil: Optional[int] = Field(default=2026)
    max_km: Optional[int] = Field(default=150000)

@app.get("/", tags=["1. Sistem Kontrolu"])
def ana_sayfa():
    return {"durum": "Aktif", "mesaj": "OtoAI Sunucusu Calisiyor"}

@app.post(
    "/api/v1/arac-tespit", 
    response_model=AracTahminSonucu, 
    summary="Fotograftan Arac Tanima Yap",
    tags=["2. Yapay Zeka Islemleri"]
)
async def arac_tespit(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Lutfen gecerli bir resim yukleyin.")

    try:
        gorsel_baytlari = await file.read()
        gorsel = Image.open(io.BytesIO(gorsel_baytlari))

        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        
        prompt = """
        Bu gorseldeki araci analiz et ve SADECE asagidaki JSON formatinda Turkce yanit ver:
        {
          "arac_mi": true,
          "marka": "Marka İsmi",
          "model": "Model İsmi",
          "kasa_veya_yil": "Kasa Kodu veya Tahmini Yıl Aralığı",
          "kasa_tipi": "Kasa Tipi",
          "renk": "Araç Rengi",
          "guven_orani": 0.95
        }
        Eger gorselde bir arac yoksa "arac_mi": false yap ve diger alanlari null birak.
        Yanitinda JSON disinda hicbir metin veya markdown kodu yazma.
        """

        response = model.generate_content([prompt, gorsel])
        
        ham_metin = response.text
        json_match = re.search(r'\{.*\}', ham_metin, re.DOTALL)
        
        if json_match:
            temiz_json_str = json_match.group(0)
            return json.loads(temiz_json_str)
        else:
            raise ValueError("Gecerli bir JSON formati donmedi.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Islem hatasi: {str(e)}")

@app.post(
    "/api/v1/arac-dogrula-ve-ara", 
    summary="Arac Bilgisini Onayla ve İlan Ara",
    tags=["3. Ilan Arama"]
)
async def arac_dogrula_ve_ara(filtre: AracAramaFiltresi):
    return {
        "basarili": True,
        "arama_kriterleri": filtre
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)