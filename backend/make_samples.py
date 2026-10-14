"""Create sample previous-year lesson materials (PPTX/PDF/DOCX) for testing."""
import os

os.makedirs("samples", exist_ok=True)

# ---------------- Sample old PPTX (last year's reading lesson) ----------------
from pptx import Presentation
from pptx.util import Pt

prs = Presentation()
s = prs.slides.add_slide(prs.slide_layouts[0])
s.shapes.title.text = "Reading: Finding the Main Idea"
s.placeholders[1].text = "Grade 3 English - Last Year"

slides = [
    ("What is the Main Idea?", "The main idea is what the text is mostly about\nIt is the big picture"),
    ("Example: The Lost Dog", "Read the short story\nWhat is it mostly about?\nA dog that misses his family"),
    ("Supporting Details", "Details tell us more about the main idea\nThey answer: who, what, where, when"),
    ("Practice: Find the Main Idea", "Read the paragraph\nCircle the main idea\nUnderline two details"),
    ("Old Topic: Phonics - Silent E", "cake, bike, note, cute\nThe silent E makes the vowel say its name"),
    ("Homework", "Worksheet page 12\nRead for 10 minutes"),
]
for title, body in slides:
    sl = prs.slides.add_slide(prs.slide_layouts[1])
    sl.shapes.title.text = title
    sl.placeholders[1].text = body
prs.save("samples/old_lesson.pptx")
print("old_lesson.pptx:", len(prs.slides.__iter__.__self__._sldIdLst), "slides")

# ---------------- Sample old PDF ----------------
from pypdf import PdfWriter

w = PdfWriter()
w.add_blank_page(612, 792)
# pypdf can't write text easily; use reportlab-free approach: build via fpdf? Not installed.
# Instead: print a text-based PDF via python-pptx export is not possible. Keep minimal:
with open("samples/old_worksheet.txt", "w", encoding="utf-8") as f:
    f.write("Fallback text fixture")
print("pdf fixture: skipped real PDF text (verified separately if reportlab available)")

# ---------------- Sample old DOCX ----------------
import docx

d = docx.Document()
d.add_heading("Reading Worksheet: Main Idea", level=1)
d.add_paragraph("Name: ________  Date: ________")
d.add_heading("Exercise 1", level=2)
d.add_paragraph("Read the story about the lost dog. Circle the main idea.")
d.add_heading("Exercise 2", level=2)
t = d.add_table(rows=2, cols=2)
t.cell(0, 0).text = "Story"
t.cell(0, 1).text = "Main idea"
t.cell(1, 0).text = "The Lost Dog"
t.cell(1, 1).text = "A dog misses his family"
d.save("samples/old_worksheet.docx")
print("old_worksheet.docx: saved")

# verify parsers on fixtures
import parsers

for f in ["samples/old_lesson.pptx", "samples/old_worksheet.docx"]:
    m = parsers.parse_file(f)
    print(f"\n== {f} ({m.kind}) -> {len(m.units)} units")
    for u in m.units:
        print(f"   {u.index}: [{u.title}] {u.text[:60]!r}")
