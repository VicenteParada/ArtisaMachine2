from __future__ import annotations

import sys
from pathlib import Path

import pypdfium2 as pdfium


for source in sys.argv[1:]:
    pdf_path = Path(source)
    output_dir = pdf_path.with_suffix("")
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    for index in range(len(pdf)):
        page = pdf[index]
        image = page.render(scale=2.0).to_pil()
        output = output_dir / f"page-{index + 1}.png"
        image.save(output)
        print(output)
