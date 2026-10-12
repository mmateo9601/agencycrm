"""Local PDF rendering and conservative validation; no URL fetching or HTML execution."""

import hashlib
import io
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from django.core.exceptions import ValidationError
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

MAX_ASSET_BYTES = 1_500_000
MAX_DOCUMENT_BYTES = 2_000_000


def validate_pdf(data: bytes) -> None:
    """Reject encrypted, malformed or active PDFs before private storage."""
    if not data.startswith(b"%PDF-") or not 0 < len(data) <= MAX_DOCUMENT_BYTES:
        raise ValidationError("PDF no válido o demasiado grande.")
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
            raise ValueError
        stack, visited = [reader.trailer], set()
        forbidden = {
            "/JS",
            "/JavaScript",
            "/AA",
            "/OpenAction",
            "/EmbeddedFiles",
            "/A",
            "/RichMedia",
            "/AcroForm",
            "/XFA",
            "/Launch",
            "/EmbeddedFile",
            "/Filespec",
            "/FileAttachment",
        }
        while stack:
            value = stack.pop()
            if hasattr(value, "get_object"):
                value = value.get_object()
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            if len(visited) > 5000:
                raise ValueError
            if isinstance(value, dict):
                if forbidden.intersection(value):
                    raise ValueError
                if (
                    str(value.get("/Type", "")) in forbidden
                    or str(value.get("/Subtype", "")) in forbidden
                ):
                    raise ValueError
                stack.extend(value.values())
            elif isinstance(value, (list, tuple)):
                stack.extend(value)
    except (
        PyPdfError,
        ValueError,
        TypeError,
        RecursionError,
        KeyError,
        IndexError,
        OSError,
    ) as error:
        raise ValidationError(
            "El PDF está dañado, cifrado o contiene funciones no admitidas."
        ) from error


def upload_bytes(upload) -> bytes:
    """Validate extension, declared MIME, bounded bytes and PDF structure independently."""
    if Path(upload.name).suffix.lower() != ".pdf" or upload.content_type != "application/pdf":
        raise ValidationError("Los activos comerciales admiten únicamente PDF.")
    data = upload.read(MAX_ASSET_BYTES + 1)
    if len(data) > MAX_ASSET_BYTES:
        raise ValidationError("El activo supera 1,5 MB.")
    validate_pdf(data)
    return data


def document_pdf(proposal, snapshot: dict) -> bytes:
    """Render only plain text and bundled fonts; internal notes never enter the document."""
    fonts = Path(reportlab.__file__).parent / "fonts"
    if "AgencyCRM" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("AgencyCRM", str(fonts / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("AgencyCRM-Bold", str(fonts / "VeraBd.ttf")))
    styles = {
        "title": ParagraphStyle(
            "title",
            fontName="AgencyCRM-Bold",
            fontSize=18,
            leading=24,
            spaceAfter=14,
            textColor=colors.HexColor("#17324d"),
        ),
        "heading": ParagraphStyle(
            "heading",
            fontName="AgencyCRM-Bold",
            fontSize=11,
            leading=16,
            spaceBefore=12,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "body", fontName="AgencyCRM", fontSize=9, leading=14, spaceAfter=9, alignment=TA_LEFT
        ),
    }

    def text(value: str) -> str:
        # Missing glyphs get a visible replacement instead of invisible or broken squares.
        widths = pdfmetrics.getFont("AgencyCRM").face.charWidths
        value = "".join(char if char == "\n" or ord(char) in widths else "?" for char in value)
        return escape(value).replace("\n", "<br/>")

    story = [
        Paragraph(text(proposal.title), styles["title"]),
        Paragraph(
            text(f"{snapshot['agency']['trading_name']} | Versión {proposal.version}"),
            styles["body"],
        ),
    ]
    for title, field in (
        ("Resumen ejecutivo", "executive_summary"),
        ("Observaciones y contexto", "problem_statement"),
        ("Solución recomendada", "recommended_solution"),
        ("Próximos pasos", "next_steps"),
    ):
        story.extend(
            [
                Paragraph(title, styles["heading"]),
                Paragraph(text(getattr(proposal, field)), styles["body"]),
            ]
        )
    footer = snapshot["agency"].get("proposal_footer", "")
    if footer:
        story.extend([Spacer(1, 5 * mm), Paragraph(text(footer), styles["body"])])
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        topMargin=23 * mm,
        bottomMargin=23 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        title=proposal.title,
        author=snapshot["agency"]["trading_name"],
    )

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("AgencyCRM", 8)
        canvas.setFillColor(colors.HexColor("#536171"))
        canvas.drawString(20 * mm, 14 * mm, f"AgencyCRM | Propuesta v{proposal.version}")
        canvas.drawRightString(A4[0] - 20 * mm, 14 * mm, f"Página {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=page, onLaterPages=page)
    data = output.getvalue()
    validate_pdf(data)
    return data


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
