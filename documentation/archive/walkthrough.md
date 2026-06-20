# AI Chat Backend & Frontend Implementation

I have successfully built the complete architecture based on your requirements.

## 🏗️ Backend Structure (`backend/`)
The backend is a highly modular FastAPI application ready to integrate with AWS Bedrock.

- **`backend/clients/bedrock_client.py`**: A dedicated class using `boto3` that easily switches between your provided models (`nvidia`, `openai`, `moonshotai`, `google`). It expects AWS credentials in the `.env` file.
- **`backend/parsers/`**:
  - `pdf_parser.py`: Adapted from your reference code, cleanly integrated.
  - `vcf_parser.py`: **[NEW]** Parses VCF genomic data and contains a mock function `trigger_external_pipeline(data)` designed to hit your future AI Agents pipeline.
  - `csv_txt_parser.py`: Handles generic text/csv uploads.
- **`backend/store/chat_store.py`**: A file-based local JSON store for saving, retrieving, and deleting chat histories.
- **`backend/api/`**: Separated routes for `/chat` and `/upload` for clean scaling.

## 🎨 Frontend Structure (`frontend/`)
The frontend is a modern React application built with Vite, ensuring a snappy user experience.

- **`frontend/src/index.css`**: Defines the premium **green/white aesthetic** requested, with CSS variables defining colors like Emerald Green (`#10b981`), clean white surfaces, and dark text.
- **`frontend/src/App.jsx`**:
  - **Dashboard/Sidebar**: Allows creating new chats and deleting old ones.
  - **Chat Window**: Features a beautiful interface with a clean chat bubble layout. 
  - **Markdown Parser**: Uses `react-markdown` so the AI's output is formatted properly with bullet points, bold text, code blocks, and tables.
  - **File Uploader**: A paperclip icon lets you upload `.pdf`, `.vcf`, `.txt`, and `.csv`. It automatically sends the file to the backend, retrieves the parsed summary, and injects that context into the active chat so the AI knows about it.

> [!TIP]
> **To start the backend:**
> ```bash
> cd backend
> uvicorn main:app --reload --port 8000
> ```
>
> **To start the frontend:**
> ```bash
> cd frontend
> npm run dev
> ```

> [!IMPORTANT]
> The AWS Credentials you provided (`AKIA2...`) have been placed into `backend/.env`. Keep this file out of version control. The VCF parser successfully intercepts uploads and calls a mock trigger for the external AI pipeline.
