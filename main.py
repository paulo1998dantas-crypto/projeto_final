import sys
import os
import pandas as pd
import io
from fastapi import FastAPI, Request, Depends, Body, UploadFile, File
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, cast, String
import uvicorn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

import database, models

database.Base.metadata.create_all(bind=database.engine)

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

ETAPAS_PRODUCAO = [
    "VIDROS",
    "A/C",
    "PREP",
    "SERRA",
    "EXPE.",
    "DESMONT",
    "ELETRICA",
    "REVEST",
    "BCO",
    "ACESSÓ.",
    "PLOTA.",
    "LIBERA"
]

# Define regras de filtragem por etapa
ETAPA_REGRAS = {
    "VIDROS": lambda s: s.get("VIDROS") == "N",
    "A/C": lambda s: s.get("A/C") == "N",
    "PREP": lambda s: s.get("PREP") == "N",
    "SERRA": lambda s: s.get("SERRA") == "N",
    "EXPE.": lambda s: s.get("EXPE.") == "N",
    "DESMONT": lambda s: s.get("VIDROS") in ["S", "N/A"] and s.get("A/C") in ["S", "N/A"] and s.get("DESMONT") == "N",
    "ELETRICA": lambda s: s.get("DESMONT") in ["S", "N/A"] and s.get("ELETRICA") == "N",
    "REVEST": lambda s: s.get("DESMONT") in ["S", "N/A"] and s.get("REVEST") == "N",
    "BCO": lambda s: s.get("REVEST") in ["S", "N/A"] and s.get("BCO") == "N",
    "ACESSÓ.": lambda s: s.get("ACESSÓ.") == "N",
    "PLOTA.": lambda s: s.get("PLOTA.") == "N",
    "LIBERA": lambda s: s.get("BANCO") in ["S", "N/A"] and s.get("LIBERA") == "N"
}

@app.get("/")
async def home(request: Request, db: Session = Depends(database.get_db), modelo: str = None, etapa: str = None):
    query = db.query(models.Veiculo)

    if modelo and modelo.strip():
        termo = f"%{modelo.strip().upper()}%"
        query = query.filter(
            or_(
                func.upper(cast(models.Veiculo.modelo, String)).like(termo),
                func.upper(cast(models.Veiculo.chassi, String)).like(termo)
            )
        )

    veiculos_db = query.all()
    veiculos_exibicao = []

    for v in veiculos_db:
        chassi_key = str(v.chassi).strip()
        apontamentos = db.query(models.Apontamento).filter(
            func.trim(cast(models.Apontamento.chassi, String)) == chassi_key
        ).all()

        status_map = {
            str(a.etapa).strip().upper(): str(a.status).strip().upper()
            for a in apontamentos
        }

        # Calculo progresso
        concluidos = sum(
            1 for e in ETAPAS_PRODUCAO
            if status_map.get(e.upper()) in ["SIM", "S", "OK", "N/A"]
        )
        v.progresso = int((concluidos / len(ETAPAS_PRODUCAO)) * 100) if ETAPAS_PRODUCAO else 0

        # Determina etapa atual
        v.etapa_atual = "FINALIZADO"
        for e in ETAPAS_PRODUCAO:
            if status_map.get(e.upper()) not in ["SIM", "S", "OK", "N/A"]:
                v.etapa_atual = e
                break

        # FILTRAGEM AJUSTADA POR REGRAS
        if etapa and etapa.strip():
            filtro = etapa.strip().upper()
            regra = ETAPA_REGRAS.get(filtro)
            if regra and regra(status_map):
                veiculos_exibicao.append(v)
        else:
            veiculos_exibicao.append(v)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "veiculos": veiculos_exibicao,
            "etapas": ETAPAS_PRODUCAO,
            "termo_busca": modelo or ""
        }
    )

@app.get("/veiculo/{chassi}")
async def detalhes(request: Request, chassi: str, db: Session = Depends(database.get_db)):
    c_limpo = chassi.strip()

    veiculo = db.query(models.Veiculo).filter(
        func.trim(cast(models.Veiculo.chassi, String)) == c_limpo
    ).first()

    feitos = db.query(models.Apontamento).filter(
        func.trim(cast(models.Apontamento.chassi, String)) == c_limpo
    ).all()

    status_map = {
        str(f.etapa).strip().upper(): str(f.status).strip().upper()
        for f in feitos
    }

    return templates.TemplateResponse(
        "detalhes.html",
        {
            "request": request,
            "veiculo": veiculo,
            "etapas": ETAPAS_PRODUCAO,
            "status_map": status_map
        }
    )

@app.post("/upload")
async def upload_base(file: UploadFile = File(...), db: Session = Depends(database.get_db)):
    try:
        content = await file.read()

        df = (
            pd.read_excel(io.BytesIO(content))
            if file.filename.endswith(".xlsx")
            else pd.read_csv(io.BytesIO(content))
        )

        df.columns = [str(c).upper().strip() for c in df.columns]

        db.query(models.Apontamento).delete()
        db.query(models.Veiculo).delete()
        db.commit()

        for _, row in df.iterrows():
            ch_raw = str(row.get("CHASSI", "")).strip().split(".")[0]
            if not ch_raw or ch_raw.lower() == "nan":
                continue

            modelo = str(row.get("MMMV", "")).strip().upper()
            db.add(models.Veiculo(chassi=ch_raw, modelo=modelo))

            for etapa in ETAPAS_PRODUCAO:
                if etapa in df.columns:
                    val = str(row[etapa]).strip().upper()
                    status = (
                        "SIM" if val in ["S", "SIM", "OK"]
                        else "NÃO" if val in ["N", "NÃO", "X"]
                        else "N/A"
                    )
                    db.add(models.Apontamento(
                        chassi=ch_raw,
                        etapa=etapa,
                        status=status
                    ))

        db.commit()
        return {"status": "sucesso"}

    except Exception as e:
        db.rollback()
        return {"status": "erro", "detail": str(e)}

@app.post("/apontar")
async def salvar(data: dict = Body(...), db: Session = Depends(database.get_db)):
    ch = str(data["chassi"]).strip()
    et = str(data["etapa"]).strip().upper()
    st = str(data["status"]).strip().upper()

    db.query(models.Apontamento).filter(
        func.trim(cast(models.Apontamento.chassi, String)) == ch,
        func.trim(cast(models.Apontamento.etapa, String)) == et
    ).delete()

    db.add(models.Apontamento(chassi=ch, etapa=et, status=st))

    v = db.query(models.Veiculo).filter(
        func.trim(cast(models.Veiculo.chassi, String)) == ch
    ).first()

    db.add(models.Historico(
        chassi=ch,
        modelo=v.modelo if v else "N/A",
        etapa=et,
        status=st
    ))

    db.commit()
    return {"status": "ok"}

@app.get("/exportar_historico")
async def exportar(db: Session = Depends(database.get_db)):
    logs = db.query(models.Historico).all()
    if not logs:
        return {"message": "Sem dados"}

    df = pd.DataFrame([
        {
            "CHASSI": l.chassi,
            "MODELO": l.modelo,
            "ETAPA": l.etapa,
            "STATUS": l.status,
            "DATA": l.data_apontamento
        }
        for l in logs
    ])

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, index=False)

    out.seek(0)

    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=relatorio.xlsx"}
    )

@app.get("/limpar_historico")
async def limpar_logs(db: Session = Depends(database.get_db)):
    db.query(models.Historico).delete()
    db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.get("/importar")
async def pg_importar(request: Request):
    return templates.TemplateResponse("importar.html", {"request": request})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
