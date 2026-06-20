# Molsys AI - Project Report

## Overview
Molsys AI is a comprehensive web application featuring a highly modular backend built with FastAPI and a modern frontend built with React. The project aims to provide an intelligent Chat Interface, a User Dashboard, and seamless File Processing capabilities, explicitly designed to handle medical and genomic data (like VCFs, PDFs, CSVs, and TXTs).

The application integrates deeply with LLM endpoints (AWS Bedrock and Lightning AI) to analyze user uploads, summarize findings, and provide a conversational AI experience.

## Repository Tree

```text
Molsys agents/
├── backend/
│   ├── api/
│   │   ├── chat_routes.py
│   │   ├── dashboard_routes.py
│   │   └── upload_routes.py
│   ├── clients/
│   │   └── bedrock_client.py
│   ├── parsers/
│   │   ├── csv_txt_parser.py
│   │   ├── pdf_parser.py
│   │   └── vcf_parser.py
│   ├── store/
│   │   └── chat_store.py
│   ├── .env
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   ├── public/
│   │   ├── favicon.svg
│   │   └── icons.svg
│   ├── src/
│   │   ├── components/
│   │   │   └── Dashboard.jsx
│   │   ├── App.jsx
│   │   ├── index.css
│   │   └── main.jsx
│   ├── package.json
│   └── vite.config.js
├── dump_refrence/
│   ├── auth.py
│   ├── chat_store.py
│   ├── jobs.py
│   ├── main.py
│   ├── medical_pipeline.py
│   ├── nvidia_client.py
│   ├── patient_store.py
│   ├── pdf_processor.py
│   └── risk_engine.py
├── NEXT_PHASE_REQUIREMENTS.md
└── project_report.md
```

## Detailed File Analysis

### Backend (`/backend`)
The backend is a highly modular Python API built with FastAPI. It handles routing, LLM integrations, and file parsing.

*   **`main.py`**: The main entry point for the FastAPI application. It loads environment variables, configures CORS middleware, and mounts routers for chats, uploads, and the dashboard. It also provides a `/api/models` endpoint.
*   **`api/chat_routes.py`**: Manages all chat-related endpoints (`GET /`, `POST /new`, `DELETE /{id}`, `PUT /{id}`, `POST /send`). It interfaces with the `ChatStore` to save history and the `BedrockClient` to fetch AI responses.
*   **`api/upload_routes.py`**: Handles file uploads (`.pdf`, `.vcf`, `.csv`, `.txt`). Once a file is uploaded, it runs the appropriate parser, dynamically invokes the AI model to generate a rich markdown summary of the data, and automatically injects this summary into the active chat session.
*   **`api/dashboard_routes.py`**: Provides mock API endpoints returning analytical data, mock patient analyses, clinical notes, and variant classifications for the frontend dashboard.
*   **`clients/bedrock_client.py`**: A unified AI client wrapper. While named `BedrockClient`, it has been upgraded to explicitly support custom endpoints like `lightning-ai/gpt-oss-20b` alongside AWS Bedrock Converse APIs. 
*   **`parsers/vcf_parser.py`**: Parses genomic Variant Call Format (VCF) files. It includes a mock pipeline that simulates a multi-agent debate system, outputting structured variant findings (e.g., pathogenic BRCA2 variants).
*   **`parsers/csv_txt_parser.py` & `pdf_parser.py`**: Utility classes to extract text from unstructured and structured datasets to feed into the AI summarization pipeline.
*   **`store/chat_store.py`**: A lightweight file-based storage system that persists all chat messages and sessions in a `data/chats.json` file.
*   **`.env`**: Stores sensitive credentials, specifically AWS keys required for `boto3`.
*   **`requirements.txt`**: Declares backend dependencies (FastAPI, uvicorn, boto3, PyMuPDF, etc.).

### Frontend (`/frontend`)
The frontend is built using React and Vite, featuring a responsive, polished UI with an "Emerald Green and Cream" theme.

*   **`src/App.jsx`**: The core React application. It handles the entire chat interface, sidebar navigation, model selection, file uploads with dynamic progress toasts, and AI message rendering (utilizing `react-markdown` and `remark-gfm` for beautiful tables).
*   **`src/components/Dashboard.jsx`**: A dedicated UI view displaying user analyses, genome build notes, and variant tables based on data retrieved from `dashboard_routes.py`.
*   **`src/index.css`**: The main stylesheet. It implements a modern aesthetic featuring glassmorphism, responsive flex layouts, and custom CSS for markdown elements (like tables and code blocks) to prevent overflow and maintain visual excellence.
*   **`src/main.jsx`**: Bootstraps the React application into the DOM.
*   **`package.json`**: Lists frontend dependencies, notably `axios`, `lucide-react` for icons, `react-markdown`, and `remark-gfm`.
*   **`vite.config.js`**: Tooling configuration for the Vite development server.

### Miscellaneous / Reference
*   **`dump_refrence/`**: Contains legacy and reference scripts from earlier iterations of the pipeline (like old risk engines, nvidia clients, and medical pipelines) that are kept for structural context.
*   **`NEXT_PHASE_REQUIREMENTS.md`**: The initial requirement specification that guided this phase of development.

## Important Features & Integrations

1.  **Real-Time File Summarization**: When users upload files, the backend parses the file and immediately generates an AI summary. The UI displays an elegant, auto-dismissing toast and dynamically injects the formatted summary as an Assistant message directly into the chat flow.
2.  **Premium UI/UX**: The interface avoids generic aesthetics, employing a tailored cream (`#FFF9F0`) and emerald green (`#10b981`) color scheme. It uses glassmorphic overlays for the chat input and custom markdown table styling to handle extensive AI outputs without breaking layout constraints.
3.  **Modular AI Connectivity**: The system defaults to the `lightning-ai/gpt-oss-20b` model, but its modular routing allows it to easily switch to AWS Bedrock models (like Nemotron or Claude) using the unified `BedrockClient`.
4.  **Robust Persistence**: Conversations are saved reliably on the backend, ensuring that user histories, uploaded file summaries, and contextual AI responses survive page reloads and server restarts.
