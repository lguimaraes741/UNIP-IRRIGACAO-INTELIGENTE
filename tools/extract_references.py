from pathlib import Path
from pypdf import PdfReader
from docx import Document
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

root = Path(__file__).resolve().parents[1]
output = root / 'tmp' / 'extractions'
output.mkdir(parents=True, exist_ok=True)
for source in (root / 'docs/referencias').iterdir():
    if source.suffix == '.pdf':
        reader = PdfReader(source)
        text = '\n\n'.join(f'--- Página {i+1} ---\n{p.extract_text()}' for i, p in enumerate(reader.pages))
    elif source.suffix == '.docx':
        doc = Document(source)
        text = '\n'.join(p.text for p in doc.paragraphs)
        text += '\n' + '\n'.join(' | '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows)
    else:
        continue
    (output / (source.stem + '.txt')).write_text(text, encoding='utf-8')
    print(source.name)
    print(text)
