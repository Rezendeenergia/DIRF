# Separador de Informes — Rezende Energia

Separa PDFs de Informe de Rendimentos e DIRF por colaborador.

## Estrutura

```
separador-informes/
├── api/
│   └── index.py         # Backend Flask (serverless function)
├── public/
│   └── index.html       # Frontend SPA
├── requirements.txt     # Dependências Python
└── vercel.json          # Config do Vercel
```

## Deploy no Vercel

### 1. Instalar Vercel CLI
```bash
npm i -g vercel
```

### 2. Fazer login
```bash
vercel login
```

### 3. Deploy direto da pasta
```bash
cd separador-informes
vercel --prod
```

O Vercel detecta automaticamente o `vercel.json` e faz o build.

### Variáveis de ambiente (opcional)
Se quiser limitar o tamanho máximo do upload, adicione no Vercel Dashboard:
```
MAX_UPLOAD_MB=100
```

## Rodar localmente

```bash
pip install -r requirements.txt
cd api
flask --app index run --debug
```

Acesse: http://localhost:5000

## Formatos suportados

| Documento              | Campo identificador                          |
|------------------------|----------------------------------------------|
| Informe 829            | `C.P.F. / NOME COMPLETO`                     |
| DIRF Folha             | `INFORMAÇÃO DO BENEFICIÁRIO DO DECLARANTE`   |
| Informe Janeiro        | `CPF: / Nome Completo:`                      |
