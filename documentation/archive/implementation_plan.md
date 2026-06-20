# Implementation Plan: AI Chat application with AWS Bedrock

This plan covers building a modular chat application using AWS Bedrock as the AI provider, capable of processing PDFs, TXTs, CSVs, and VCF files. The codebase will be highly structured into distinct folders to ensure maintainability.

## Open Questions

> [!WARNING]  
> **Frontend Technology**: The requirements specify "dashboard viewer" and "chatwindow" folders with a "green white theme" and Markdown parsing. To deliver a premium, fast experience, I recommend using a modern web framework like **React (Vite)** for the frontend inside a `frontend/` directory, rather than building it entirely in vanilla JS inside the backend folder. Alternatively, if you want everything to be strictly backend-served HTML, we can use Jinja2 templates.  
> *Question: Shall we proceed with a React (Vite) frontend for the UI, keeping the FastAPI backend separate, or would you prefer a different stack?*

> [!IMPORTANT]  
> **AI Pipeline API Stub**: For the VCF genomic data, you mentioned it should integrate with an existing AI agents pipeline you don't have yet. I will create a stub/mock API client that accepts the parsed VCF data and demonstrates how you would invoke the external pipeline once it's available. Does this sound correct?

## Proposed Changes

### 1. Project Structure

We will create a structured directory tree separating concerns inside the workspace:
`c:\Users\yrohi\Documents\CODING\PROJECTS\Molsys\Molsys agents\`

- `backend/`: Contains the FastAPI application.
  - `api/`: API Routers for chats, uploads, and system models.
  - `clients/`: Modular AI clients. Here we will place the `aws_bedrock.py` class that integrates `boto3` and the provided models.
  - `parsers/`: Document processing logic.
    - `pdf_parser.py`: Adapted from your reference `pdf_processor.py`.
    - `vcf_parser.py`: [NEW] Parser for genomic VCF files.
    - `csv_txt_parser.py`: [NEW] Parser for generic text and CSV data.
  - `store/`: Local filesystem logic for saving/deleting chats and file metadata.
  - `pipeline/`: Stubs and logic for integrating with the external AI agents pipeline.
- `frontend/`: Contains the UI.
  - `chatwindow/`: Components for the chat interface, message history, file attachments, and a Markdown parser for nicely formatted AI responses.
  - `dashboard_viewer/`: Components for uploading files, viewing summaries, and managing data.
  - `theme/`: CSS/tokens for the required green/white aesthetic.

### 2. Backend Implementation (FastAPI)

#### [NEW] `backend/main.py`
Sets up the FastAPI application, CORS middleware, and includes API routers from `backend/api/`.

#### [NEW] `backend/clients/bedrock_client.py`
A modular class utilizing `boto3.client("bedrock-runtime", region_name="us-east-1")`. It will expose methods like `chat(...)` and easily accept different `modelId` parameters.

#### [NEW] `backend/api/chat_routes.py`
Endpoints for `/api/chat/new`, `/api/chat/{id}/send`, `/api/chat/{id}`, and `/api/chats` (list/delete).

#### [NEW] `backend/api/upload_routes.py`
Endpoint for `/api/upload`. It will detect the file extension (`.pdf`, `.vcf`, `.txt`, `.csv`) and route the file to the appropriate parser in `backend/parsers/`. 

#### [NEW] `backend/parsers/vcf_parser.py`
A simple VCF parser that reads the headers and variant data lines. It will include a function `trigger_external_pipeline(parsed_data)` that acts as a placeholder for the external AI agent pipeline integration.

#### [NEW] `backend/parsers/pdf_parser.py`
A refactored version of the `pdf_processor.py` from the `dump_refrence` folder, adapted to work cleanly in the new modular structure.

### 3. Frontend Implementation

#### [NEW] `frontend/...`
Assuming React (Vite) is approved:
- We will set up a fresh React app with `npx -y create-vite@latest frontend --template react`.
- `src/chatwindow/ChatWindow.jsx`: Will use a library like `react-markdown` to render the model's markdown outputs with proper syntax highlighting, bold text, lists, and tables.
- `src/theme/globals.css`: Will establish the vibrant green and clean white styling, ensuring a premium, polished user interface.

## Verification Plan

### Automated Tests
- Basic API tests for creating a chat, listing models, and deleting a chat to ensure the `backend/store/` logic functions properly.

### Manual Verification
- Start the FastAPI backend and test Bedrock connectivity using the provided credentials.
- Start the frontend, verify the green/white theme.
- Upload a dummy PDF and verify summarization/parsing.
- Upload a dummy VCF and verify it hits the parser and mock pipeline stub.
