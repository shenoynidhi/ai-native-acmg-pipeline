import os
import re
import fitz
from PyPDF2 import PdfReader

class PDFParser:
    def __init__(self):
        pass

    def parse(self, filepath: str) -> str:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"PDF file not found: {filepath}")
        try:
            return self._extract_with_pymupdf(filepath)
        except Exception as e:
            print(f"PyMuPDF failed: {e}. Falling back to PyPDF2.")
            return self._extract_with_pypdf2(filepath)

    def _extract_with_pymupdf(self, filepath: str) -> str:
        doc = fitz.open(filepath)
        texts = []
        for index in range(doc.page_count):
            page = doc.load_page(index)
            blocks = page.get_text("blocks", sort=True)
            block_texts = [block[4].strip() for block in blocks if block[4] and block[4].strip()]
            if block_texts:
                texts.append("\n\n".join(block_texts))
        if not texts:
            raise ValueError("No text extracted.")
        return "\n\n---PAGE BREAK---\n\n".join(texts)

    def _extract_with_pypdf2(self, filepath: str) -> str:
        texts = []
        reader = PdfReader(filepath)
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                texts.append(text)
        if not texts:
            raise ValueError("No text extracted.")
        return "\n\n---PAGE BREAK---\n\n".join(texts)
