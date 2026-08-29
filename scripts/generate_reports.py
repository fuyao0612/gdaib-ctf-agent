from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Mm, Pt
from win32com.client import DispatchEx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "submission"


def set_run_font(run, size: float | None = None, bold: bool | None = None) -> None:
    run.font.name = "Aptos"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold


def add_markdown(document: Document, source: Path) -> None:
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line:
            document.add_paragraph()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            paragraph = document.add_heading(heading.group(2), level=len(heading.group(1)))
        elif re.match(r"^[-*]\s+", line):
            paragraph = document.add_paragraph(re.sub(r"^[-*]\s+", "", line), style="List Bullet")
        else:
            paragraph = document.add_paragraph(line.replace("`", ""))

        for run in paragraph.runs:
            set_run_font(run)


def build_report(title: str, subtitle: str, sources: list[str], stem: str) -> Path:
    document = Document()
    section = document.sections[0]
    section.top_margin = Mm(22)
    section.bottom_margin = Mm(22)
    section.left_margin = Mm(25)
    section.right_margin = Mm(25)

    normal = document.styles["Normal"]
    normal.font.name = "Aptos"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)

    title_paragraph = document.add_paragraph()
    title_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_paragraph.paragraph_format.space_before = Pt(150)
    title_run = title_paragraph.add_run(title)
    set_run_font(title_run, 28, True)

    subtitle_paragraph = document.add_paragraph()
    subtitle_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_run = subtitle_paragraph.add_run(f"{subtitle}\n版本日期：{date(2026, 8, 29).isoformat()}")
    set_run_font(subtitle_run, 14)

    document.add_page_break()
    for source in sources:
        add_markdown(document, ROOT / source)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run("御网智元 | 初审提交材料")
    set_run_font(footer_run, 8)

    path = OUTPUT / f"{stem}.docx"
    document.save(path)
    return path


def export_pdfs(paths: list[Path]) -> None:
    word = DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        for path in paths:
            document = word.Documents.Open(str(path.resolve()), ReadOnly=True)
            try:
                document.ExportAsFixedFormat(str(path.with_suffix(".pdf").resolve()), 17)
            finally:
                document.Close(False)
    finally:
        word.Quit()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    reports = [
        build_report(
            "御网智元技术报告",
            "具备自主决策能力的通用网络安全智能体",
            [
                "docs/submission/01-项目摘要.md",
                "docs/submission/02-技术方案.md",
                "docs/submission/03-系统架构与模块说明.md",
                "docs/submission/04-自主决策与可解释性设计.md",
                "docs/submission/05-工具协同与扩展机制.md",
                "docs/submission/06-安全设计与威胁模型.md",
                "docs/submission/11-创新点与同类方案对比.md",
            ],
            "御网智元-技术报告",
        ),
        build_report(
            "御网智元测试与评测报告",
            "质量门禁、通用评测与可复现证据",
            ["docs/submission/07-测试与评测报告.md"],
            "御网智元-测试与评测报告",
        ),
    ]
    export_pdfs(reports)


if __name__ == "__main__":
    main()
