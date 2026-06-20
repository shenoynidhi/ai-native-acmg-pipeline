import os
import csv

class CSVTxtParser:
    def parse_txt(self, filepath: str) -> str:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"TXT file not found: {filepath}")
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()

    def parse_csv(self, filepath: str) -> str:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"CSV file not found: {filepath}")
        
        lines = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                lines.append(", ".join(row))
        return "\n".join(lines)
