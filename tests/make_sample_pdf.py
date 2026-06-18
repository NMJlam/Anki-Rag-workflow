"""Generate a small test PDF for the offline smoke test.

The PDF has 5 pages:
  - Pages 1-2: front matter (page_offset=2 means these don't count)
  - Page 3 (printed p.1): intro text
  - Page 4 (printed p.2): scheduling text
  - Page 5 (printed p.3): TLB text  <-- the smoke test expects to find this

Run:  uv run python tests/make_sample_pdf.py
Then: uv run python -m rag.ingest tests/books.test.toml
"""
import os

try:
    from pypdf import PdfWriter
except ImportError:
    raise SystemExit("pip install pypdf  (or: uv add pypdf)")

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io


PAGES = [
    "Front Matter\n\nThis is the title page of the sample textbook.",
    "Table of Contents\n\nChapter 1: Introduction\nChapter 2: Scheduling\nChapter 3: TLBs",
    "Chapter 1: Introduction\n\nThis textbook covers operating system concepts "
    "including process management, memory management, and concurrency.",
    "Chapter 2: Scheduling\n\nThe Multi-Level Feedback Queue (MLFQ) scheduler uses "
    "multiple queues with different priority levels. A job in a higher-priority "
    "queue runs before jobs in lower-priority queues. Jobs that use up their time "
    "allotment have their priority reduced.",
    "Chapter 3: Translation Lookaside Buffers\n\n"
    "Who handles a TLB miss depends on the architecture. On CISC machines like x86, "
    "the hardware walks the page table and fills the TLB automatically (hardware-managed). "
    "On RISC machines like MIPS, a TLB miss traps to the OS, which looks up the page "
    "table entry and installs it in the TLB (software-managed). The key distinction is "
    "whether the OS or the hardware is responsible for the page-table walk.",
]


def make_pdf(out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    writer = PdfWriter()

    for text in PAGES:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        # write text as simple lines
        y = 720
        for line in text.split("\n"):
            c.drawString(72, y, line)
            y -= 16
        c.save()
        buf.seek(0)
        from pypdf import PdfReader
        reader = PdfReader(buf)
        writer.add_page(reader.pages[0])

    with open(out_path, "wb") as f:
        writer.write(f)
    print(f"Created {out_path} ({len(PAGES)} pages)")


if __name__ == "__main__":
    make_pdf("data/pdfs/sample.pdf")
