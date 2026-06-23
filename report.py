# report.py

import os
import re
import io
import zipfile
import tempfile
from datetime import datetime

import streamlit as st
import fitz
from pypdf import PdfReader, PdfWriter


st.set_page_config(
    page_title="MG Manifest Matcher",
    layout="centered"
)

st.title("📦 Manifest Matcher")
st.write(
    "Upload the Loading Manifest and Shipping Manifest PDFs or ZIPs. "
    "The app will colour-code customer names to match the board, "
    "then return one printable matched PDF packet."
)


# ============================================================
# BOARD CONDITIONAL FORMATTING RULES
# ------------------------------------------------------------
# Extracted from board_5-29.xlsx column B (DESTINATION).
# Format: (keyword, font_color, bg_color)
#   font_color — (R,G,B) floats 0–1, or None to keep black
#   bg_color   — (R,G,B) floats 0–1, or None for no highlight
# Rules checked case-insensitively. First match wins.
# ============================================================

BOARD_CF_RULES = [
    #  keyword               font_color             bg_color
    ("Target",              None,                  (1.000, 1.000, 0.000)),  # yellow bg, black text
    ("Sobey",              (1.000, 0.000, 0.000),  (1.000, 1.000, 0.000)),  # red text + yellow bg
    ("Walmart Mississauga",(1.000, 0.000, 0.000),  (1.000, 1.000, 0.000)),  # red text + yellow bg
    ("Vaughan",            (1.000, 0.000, 0.000),  None),                   # red text only
    ("Costco StBruno",     (1.000, 0.000, 0.000),  None),
    ("Brampton",           (1.000, 0.000, 0.000),  None),
    ("Loblaws",            (1.000, 0.000, 0.000),  None),
    ("Moncton",            (1.000, 0.000, 0.000),  None),
    ("Regina",             (1.000, 0.000, 0.000),  None),
    ("Ontario",            (1.000, 0.000, 0.000),  None),
    ("Toronto",            (1.000, 0.000, 0.000),  None),
    ("Varennes",           (1.000, 0.000, 0.000),  None),
    ("Winnipeg",           (1.000, 0.000, 0.000),  None),
    ("Albertson",          None,                   (0.851, 0.886, 0.953)),  # black text + light blue bg
    ("Jewel",              None,                   (0.851, 0.886, 0.953)),
    ("Safeway",            None,                   (0.851, 0.886, 0.953)),
    ("Sysco",              None,                   (0.851, 0.886, 0.953)),
    ("United Supermarkets",None,                   (0.851, 0.886, 0.953)),
]


def match_board_cf(text: str):
    """Return (font_color, bg_color) for the first matching board CF rule.
    Either value may be None (no change for that property)."""
    upper = text.upper()
    for keyword, font_color, bg_color in BOARD_CF_RULES:
        if keyword.upper() in upper:
            return font_color, bg_color
    return None, None


def color_manifest_pdf(pdf_bytes: bytes) -> tuple[bytes, int]:
    """
    Apply board conditional-formatting colours to customer name spans.

    For each matching text span:
      1. Redact the original black text
      2. Draw background colour rectangle (if any)
      3. Re-insert text in the matching font colour

    Returns (coloured_pdf_bytes, total_spans_recoloured).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_recoloured = 0

    for page in doc:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        to_recolor = []
        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    font_color, bg_color = match_board_cf(span["text"])
                    if font_color is not None or bg_color is not None:
                        to_recolor.append({
                            "rect":       fitz.Rect(span["bbox"]),
                            "text":       span["text"],
                            "font_color": font_color,
                            "bg_color":   bg_color,
                            "size":       span["size"],
                            "origin":     span["origin"],
                        })

        if not to_recolor:
            continue

        # Step 1 — redact (erase) original black text
        for item in to_recolor:
            page.add_redact_annot(item["rect"])
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        # Step 2 — draw background highlight (if any), then re-draw text
        for item in to_recolor:
            if item["bg_color"]:
                page.draw_rect(item["rect"], color=None, fill=item["bg_color"])
            text_color = item["font_color"] if item["font_color"] else (0.0, 0.0, 0.0)
            page.insert_text(
                item["origin"],
                item["text"],
                fontsize=item["size"],
                color=text_color,
                fontname="Courier",  # monospace — matches LiberationMono spacing
            )

        total_recoloured += len(to_recolor)

    out = io.BytesIO()
    doc.save(out)
    doc.close()
    out.seek(0)
    return out.read(), total_recoloured


# ============================================================
# MANIFEST MATCHING
# ============================================================

def sample_text(pdf_path, pages=5):
    doc = fitz.open(pdf_path)
    text = ""
    for i in range(min(pages, len(doc))):
        text += doc[i].get_text("text") + "\n"
    doc.close()
    return text.upper()


def parse_load(text):
    m = re.search(r"\bLOAD\s*:\s*([A-Z]{1,5}\d+)", text, re.I)
    return m.group(1).upper().strip() if m else None


def parse_pu_appt(text):
    m = re.search(
        r"\bPU\s+APPT\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})",
        text,
        re.I
    )
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + " " + m.group(2), "%m/%d/%Y %H:%M")
    except Exception:
        return None


def group_pages_by_load(pdf_path):
    doc = fitz.open(pdf_path)
    groups = {}
    current_load = None

    for i in range(len(doc)):
        text = doc[i].get_text("text")
        found_load = parse_load(text)

        if found_load:
            current_load = found_load
            if current_load not in groups:
                groups[current_load] = {"pages": [], "text": ""}

        if current_load:
            groups[current_load]["pages"].append(i)
            groups[current_load]["text"] += "\n" + text

    doc.close()
    return groups


def extract_uploaded_files(uploaded_files, workdir):
    pdf_files = []

    for uploaded_file in uploaded_files:
        file_path = os.path.join(workdir, uploaded_file.name)

        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        if uploaded_file.name.lower().endswith(".zip"):
            folder = os.path.join(workdir, uploaded_file.name.replace(".zip", ""))
            os.makedirs(folder, exist_ok=True)

            with zipfile.ZipFile(file_path, "r") as z:
                z.extractall(folder)

            for root, dirs, files in os.walk(folder):
                for file in files:
                    if file.lower().endswith(".pdf"):
                        pdf_files.append(os.path.join(root, file))

        elif uploaded_file.name.lower().endswith(".pdf"):
            pdf_files.append(file_path)

    return pdf_files


def build_matched_packet(uploaded_files):
    with tempfile.TemporaryDirectory() as workdir:
        pdf_files = extract_uploaded_files(uploaded_files, workdir)

        if len(pdf_files) < 2:
            raise Exception("Need at least two PDFs or ZIPs containing PDFs.")

        loading_pdf = None
        shipping_pdf = None

        for pdf in pdf_files:
            text = sample_text(pdf)
            if "LOADING MANIFEST" in text and "SHIPPING MANIFEST" not in text:
                loading_pdf = pdf
            if "SHIPPING MANIFEST" in text or "PICKUP TOTAL" in text:
                shipping_pdf = pdf

        if not loading_pdf or not shipping_pdf:
            raise Exception("Could not identify loading and shipping PDFs.")

        # ── Colour-code both manifests before matching ──────────────────
        with open(loading_pdf, "rb") as f:
            loading_bytes = f.read()
        with open(shipping_pdf, "rb") as f:
            shipping_bytes = f.read()

        coloured_loading_bytes, loading_hits  = color_manifest_pdf(loading_bytes)
        coloured_shipping_bytes, shipping_hits = color_manifest_pdf(shipping_bytes)
        total_hits = loading_hits + shipping_hits

        # Write the coloured versions back to temp files for page grouping
        coloured_loading_path  = os.path.join(workdir, "coloured_loading.pdf")
        coloured_shipping_path = os.path.join(workdir, "coloured_shipping.pdf")

        with open(coloured_loading_path, "wb") as f:
            f.write(coloured_loading_bytes)
        with open(coloured_shipping_path, "wb") as f:
            f.write(coloured_shipping_bytes)

        # ── Group pages by load number (from coloured PDFs) ─────────────
        loading_groups  = group_pages_by_load(coloured_loading_path)
        shipping_groups = group_pages_by_load(coloured_shipping_path)

        all_loads = sorted(
            set(loading_groups.keys()) |
            set(shipping_groups.keys())
        )

        records = []
        for load in all_loads:
            lt = loading_groups.get(load, {}).get("text", "")
            st_text = shipping_groups.get(load, {}).get("text", "")
            dt = parse_pu_appt(lt or st_text)
            records.append({
                "load":           load,
                "datetime":       dt,
                "loading_pages":  loading_groups.get(load,  {}).get("pages", []),
                "shipping_pages": shipping_groups.get(load, {}).get("pages", []),
            })

        records = sorted(
            records,
            key=lambda r: (
                r["datetime"] is None,
                r["datetime"] or datetime.max,
                r["load"],
            )
        )

        # ── Build matched + coloured output PDF ─────────────────────────
        writer = PdfWriter()
        loading_reader  = PdfReader(coloured_loading_path)
        shipping_reader = PdfReader(coloured_shipping_path)

        for r in records:
            for page_num in r["loading_pages"]:
                writer.add_page(loading_reader.pages[page_num])
            for page_num in r["shipping_pages"]:
                writer.add_page(shipping_reader.pages[page_num])

        out = io.BytesIO()
        writer.write(out)
        pdf_bytes = out.getvalue()

        return pdf_bytes, len(loading_groups), len(shipping_groups), len(records), total_hits


# ============================================================
# STREAMLIT UI
# ============================================================

# Colour legend
with st.expander("Customer colour-coding rules"):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("**Red text only**")
        for kw, fc, bc in BOARD_CF_RULES:
            if fc and not bc:
                st.markdown(f"- {kw}")
    with col_b:
        st.markdown("** Yellow background**")
        for kw, fc, bc in BOARD_CF_RULES:
            if bc == (1.0, 1.0, 0.0) and not fc:
                st.markdown(f"- {kw} *(black text)*")
            elif bc == (1.0, 1.0, 0.0) and fc:
                st.markdown(f"- {kw} *(red text)*")
    with col_c:
        st.markdown("** Light blue background**")
        for kw, fc, bc in BOARD_CF_RULES:
            if bc and bc != (1.0, 1.0, 0.0):
                st.markdown(f"- {kw} *(black text)*")

st.divider()

uploaded_files = st.file_uploader(
    "Upload Loading Manifest and Shipping Manifest files",
    type=["pdf", "zip"],
    accept_multiple_files=True
)

if uploaded_files:
    st.write(f"Files uploaded: **{len(uploaded_files)}**")

    if st.button("Build Matched PDF Packet"):
        try:
            with st.spinner("Colour-coding and matching manifests…"):
                pdf_bytes, loading_count, shipping_count, load_count, colour_hits = build_matched_packet(uploaded_files)

            st.success("Matched PDF packet created successfully!")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Loading loads",  loading_count)
            c2.metric("Shipping loads", shipping_count)
            c3.metric("Matched loads",  load_count)
            c4.metric("Names coloured", colour_hits)

            if colour_hits == 0:
                st.info("No customer names matched the colour rules in these manifests.")

            st.download_button(
                label="Download Matched Manifest Packet",
                data=pdf_bytes,
                file_name="Matched_Manifest_Packet.pdf",
                mime="application/pdf"
            )

        except Exception as e:
            st.error(str(e))
