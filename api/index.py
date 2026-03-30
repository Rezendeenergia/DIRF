import base64
import io
import re
import zipfile
from collections import defaultdict

import pdfplumber
from flask import Flask, jsonify, request
from pypdf import PdfReader, PdfWriter

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "public"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

# ──────────────────────────────────────────────
# Regex helpers
# ──────────────────────────────────────────────
CPF_RE   = re.compile(r'\b(\d{3}[\.\s]?\d{3}[\.\s]?\d{3}[-\.\s]?\d{2})\b')
CNPJ_RE  = re.compile(r'\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b')
IGNORE_CPFS = {"033.567.451-82", "03356745182"}


def normalize_cpf(cpf: str) -> str:
    return re.sub(r'\D', '', cpf)


def extract_collaborator(text: str) -> tuple:
    # Formato 1: Informe 829
    m = re.search(
        r'C\.P\.F\.\s+NOME COMPLETO\s*\n\s*(\d{3}[\.\s]?\d{3}[\.\s]?\d{3}[-\.\s]?\d{2})\s+'
        r'([\wÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙÇáéíóúâêîôûãõàèìòùç ]+)',
        text,
    )
    if m:
        return normalize_cpf(m.group(1)), m.group(2).strip()

    # Formato 2: DIRF Folha
    m = re.search(
        r'INFORMAÇÃO DO BENEFICIÁRIO DO DECLARANTE\s*\n\s*CPF:\s*'
        r'(\d{3}[\.\s]?\d{3}[\.\s]?\d{3}[-\.\s]?\d{2})\s+Nome:\s*\d+\s*-\s*'
        r'([\wÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙÇáéíóúâêîôûãõàèìòùç ]+)',
        text,
    )
    if m:
        return normalize_cpf(m.group(1)), m.group(2).strip()

    # Formato 3: Informe Janeiro
    m = re.search(
        r'CPF:\s+Nome Completo:\s*\n\s*(\d{3}[\.\s]?\d{3}[\.\s]?\d{3}[-\.\s]?\d{2})\s+'
        r'([\wÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙÇáéíóúâêîôûãõàèìòùç ]+)',
        text,
    )
    if m:
        return normalize_cpf(m.group(1)), m.group(2).strip()

    # Fallback: primeiro CPF que não seja CNPJ nem responsável
    all_cnpjs = {re.sub(r'\D', '', c) for c in CNPJ_RE.findall(text)}
    for cpf in CPF_RE.findall(text):
        norm = normalize_cpf(cpf)
        if norm not in all_cnpjs and norm not in {normalize_cpf(x) for x in IGNORE_CPFS}:
            return norm, "Desconhecido"

    return "", "Desconhecido"


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '', name).strip()


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")


# ── SEPARAR ──────────────────────────────────
@app.route("/api/process", methods=["POST"])
def process():
    files = request.files.getlist("pdfs")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    colaboradores: dict = defaultdict(lambda: {"nome": "", "pages": []})
    errors = []

    for uploaded in files:
        file_bytes = uploaded.read()
        buf = io.BytesIO(file_bytes)
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as plumber_pdf:
                reader = PdfReader(buf)
                for page_idx, plumber_page in enumerate(plumber_pdf.pages):
                    text = plumber_page.extract_text() or ""
                    cpf, nome = extract_collaborator(text)
                    if not cpf:
                        errors.append(
                            f"Pág. {page_idx + 1} de '{uploaded.filename}': CPF não identificado."
                        )
                        continue
                    colaboradores[cpf]["pages"].append((reader, page_idx))
                    if not colaboradores[cpf]["nome"] or colaboradores[cpf]["nome"] == "Desconhecido":
                        colaboradores[cpf]["nome"] = nome
        except Exception as e:
            errors.append(f"Erro ao processar '{uploaded.filename}': {e}")

    if not colaboradores:
        return jsonify({"error": "Nenhum colaborador identificado.", "warnings": errors}), 422

    # Build ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for cpf, data in colaboradores.items():
            nome = sanitize_filename(data["nome"])
            writer = PdfWriter()
            for reader, page_idx in data["pages"]:
                writer.add_page(reader.pages[page_idx])
            pdf_buf = io.BytesIO()
            writer.write(pdf_buf)
            zf.writestr(f"{nome}_{cpf}.pdf", pdf_buf.getvalue())

    summary = []
    for cpf, data in sorted(colaboradores.items(), key=lambda x: x[1]["nome"]):
        fmt_cpf = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else cpf
        summary.append({"nome": data["nome"], "cpf": fmt_cpf, "paginas": len(data["pages"])})

    return jsonify({
        "colaboradores": len(colaboradores),
        "total_paginas": sum(len(d["pages"]) for d in colaboradores.values()),
        "summary": summary,
        "warnings": errors,
        "zip_b64": base64.b64encode(zip_buf.getvalue()).decode(),
    })


# ── JUNTAR ───────────────────────────────────
@app.route("/api/merge", methods=["POST"])
def merge():
    files = request.files.getlist("pdfs")
    if len(files) < 2:
        return jsonify({"error": "Envie pelo menos 2 arquivos PDF para juntar."}), 400

    filename = request.form.get("filename", "documento_unificado.pdf")

    writer = PdfWriter()
    total_pages = 0

    try:
        for uploaded in files:
            reader = PdfReader(io.BytesIO(uploaded.read()))
            for page in reader.pages:
                writer.add_page(page)
                total_pages += 1
    except Exception as e:
        return jsonify({"error": f"Erro ao processar PDF: {e}"}), 422

    pdf_buf = io.BytesIO()
    writer.write(pdf_buf)

    return jsonify({
        "total_pages": total_pages,
        "filename": filename,
        "pdf_b64": base64.b64encode(pdf_buf.getvalue()).decode(),
    })


# ── RENOMEAR ─────────────────────────────────
@app.route("/api/rename", methods=["POST"])
def rename():
    files = request.files.getlist("pdfs")
    names = request.form.getlist("names")

    if not files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    if len(files) != len(names):
        return jsonify({"error": "Quantidade de arquivos e nomes não corresponde."}), 400

    zip_buf = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            seen = {}
            for uploaded, new_name in zip(files, names):
                safe = sanitize_filename(new_name) or sanitize_filename(uploaded.filename)
                if not safe.lower().endswith(".pdf"):
                    safe += ".pdf"
                # Deduplicate filenames
                if safe in seen:
                    seen[safe] += 1
                    base, ext = safe.rsplit(".", 1)
                    safe = f"{base}_{seen[safe]}.{ext}"
                else:
                    seen[safe] = 0
                zf.writestr(safe, uploaded.read())
    except Exception as e:
        return jsonify({"error": f"Erro ao renomear: {e}"}), 422

    return jsonify({
        "count": len(files),
        "zip_b64": base64.b64encode(zip_buf.getvalue()).decode(),
    })


if __name__ == "__main__":
    app.run(debug=True)
