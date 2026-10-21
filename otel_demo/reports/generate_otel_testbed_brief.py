from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


OUT = Path(__file__).with_name("otel-astronomy-shop-testbed-brief.pdf")


def p(text, style):
    return Paragraph(text, style)


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="TitleCustom", parent=styles["Title"], alignment=TA_CENTER,
    fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=colors.HexColor("#123B5D"),
    spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="Subtitle", parent=styles["Normal"], alignment=TA_CENTER,
    fontSize=10, leading=14, textColor=colors.HexColor("#456273"), spaceAfter=14,
))
styles.add(ParagraphStyle(
    name="H1Custom", parent=styles["Heading1"], fontSize=15, leading=18,
    textColor=colors.HexColor("#123B5D"), spaceBefore=8, spaceAfter=6,
))
styles.add(ParagraphStyle(
    name="BodyCustom", parent=styles["BodyText"], fontSize=9.2, leading=13,
    spaceAfter=5,
))
styles.add(ParagraphStyle(
    name="Small", parent=styles["BodyText"], fontSize=8, leading=10,
    textColor=colors.HexColor("#456273"),
))
styles.add(ParagraphStyle(
    name="TableCell", parent=styles["BodyText"], fontSize=8, leading=10,
))
styles.add(ParagraphStyle(
    name="TableHeader", parent=styles["BodyText"], fontName="Helvetica-Bold",
    fontSize=8, leading=10, textColor=colors.white,
))


def bullet(items):
    return ListFlowable(
        [ListItem(p(item, styles["BodyCustom"]), leftIndent=10) for item in items],
        bulletType="bullet", start="circle", leftIndent=16, bulletFontSize=7,
    )


story = [
    p("Recommended Test Application", styles["TitleCustom"]),
    p("OpenTelemetry Astronomy Shop Demo", styles["Subtitle"]),
    p("<b>Repository:</b> github.com/open-telemetry/opentelemetry-demo &nbsp; | &nbsp; <b>Docs:</b> opentelemetry.io/docs/demo/", styles["Small"]),
    Spacer(1, 5 * mm),
    p("Decision", styles["H1Custom"]),
    p("Use the <b>OpenTelemetry Astronomy Shop Demo</b> as the primary testbed for the AI SRE Copilot. Start with its native feature-flag scenarios, then add Grafana LGTM and Jaeger through deployment configuration.", styles["BodyCustom"]),
    p("Why this is the right choice", styles["H1Custom"]),
    bullet([
        "Real distributed behavior across HTTP, gRPC, Kafka, PostgreSQL, and Valkey.",
        "First-class OpenTelemetry traces, metrics, logs, exemplars, dashboards, and telemetry tests.",
        "Official architecture, service, telemetry, feature-flag, and debugging documentation for RAG.",
        "Native, reversible fault injection through OpenFeature and flagd.",
        "Faults cover downstream failures, latency, database contention, queues, resource leaks, and AI loops.",
        "Most integration work is configuration and a Copilot telemetry adapter, not application rewrites.",
    ]),
    p("Observability model", styles["H1Custom"]),
    p("<font name='Courier'>Astronomy Shop services → OpenTelemetry Collector → Prometheus / Tempo / Jaeger / Loki → Grafana</font>", styles["BodyCustom"]),
    p("The upstream deployment already includes the application, Collector, Prometheus, Grafana, and Jaeger/OpenSearch paths. Add Loki and Tempo with a small Collector overlay or Grafana's <font name='Courier'>otel-lgtm</font> backend. Retain Jaeger as a second trace backend if required.", styles["BodyCustom"]),
    p("Native fault scenarios", styles["H1Custom"]),
]

data = [
    [p("Scenario", styles["TableHeader"]), p("What it tests", styles["TableHeader"])],
    [p("paymentFailure", styles["TableCell"]), p("Repeatable known incident and memory recall", styles["TableCell"])],
    [p("paymentUnreachable", styles["TableCell"]), p("Same symptom, different downstream cause", styles["TableCell"])],
    [p("intlShippingSlowdown", styles["TableCell"]), p("Cohort-specific latency", styles["TableCell"])],
    [p("productCatalogLockContention", styles["TableCell"]), p("Database contention and cascading latency", styles["TableCell"])],
    [p("kafkaQueueProblems", styles["TableCell"]), p("Async backpressure and consumer lag", styles["TableCell"])],
    [p("recommendationCacheFailure", styles["TableCell"]), p("Gradual memory and latency degradation", styles["TableCell"])],
    [p("aiRunawayAgent", styles["TableCell"]), p("AI/tool-loop failure diagnosis", styles["TableCell"])],
]
table = Table(data, colWidths=[58 * mm, 112 * mm], repeatRows=1)
table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#176B87")),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F1F7FA"), colors.white]),
    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B7CBD5")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
]))
story.append(table)
story.extend([
    p("Fit for the AI SRE Copilot", styles["H1Custom"]),
    bullet([
        "Prometheus threshold breach starts proactive analysis.",
        "Trace and log correlation identifies the failing service or dependency.",
        "Official documentation explains expected behavior and diagnostic signals.",
        "Hindsight recalls prior incidents and successful fixes.",
        "A repeated fault can use fast memory verification; a changed context can reject stale memory.",
    ]),
    p("Recommended rollout", styles["H1Custom"]),
    p("1. Start with <font name='Courier'>paymentFailure</font>, <font name='Courier'>paymentUnreachable</font>, and <font name='Courier'>intlShippingSlowdown</font>.  2. Connect read-only Prometheus, Loki, Tempo, and Jaeger tools.  3. Index official demo documentation.  4. Add Kafka and database-lock scenarios.  5. Use Chaos Mesh or Istio later for infrastructure-level experiments.", styles["BodyCustom"]),
    p("Conclusion", styles["H1Custom"]),
    p("Astronomy Shop is the strongest practical choice because it combines realistic distributed behavior, maintained OpenTelemetry support, rich documentation, deterministic native faults, and a low-risk integration path. It is more capable than a small Petclinic-style app without the operational burden of a large SaaS platform such as PostHog.", styles["BodyCustom"]),
    Spacer(1, 5 * mm),
    p("Prepared as a concise architecture decision brief for the AI SRE Copilot testbed.", styles["Small"]),
])


doc = SimpleDocTemplate(
    str(OUT), pagesize=A4,
    rightMargin=17 * mm, leftMargin=17 * mm,
    topMargin=15 * mm, bottomMargin=15 * mm,
    title="OpenTelemetry Astronomy Shop Testbed Brief",
    author="AI SRE Copilot",
)
doc.build(story)
print(OUT)
