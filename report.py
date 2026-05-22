# report.py

import os
import re
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
st.write("Upload the Loading Manifest and Shipping Manifest PDFs or ZIPs. The app will return one printable matched PDF packet.")


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
        return datetime.strptime(
            m.group(1) + " " + m.group(2),
            "%m/%d/%Y %H:%M"
        )
    except:
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
                groups[current_load] = {
                    "pages": [],
                    "text": ""
                }

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

        loading_groups = group_pages_by_load(loading_pdf)
        shipping_groups = group_pages_by_load(shipping_pdf)

        all_loads = sorted(
            set(loading_groups.keys()) |
            set(shipping_groups.keys())
        )

        records = []

        for load in all_loads:
            lt = loading_groups.get(load, {}).get("text", "")
            st = shipping_groups.get(load, {}).get("text", "")

            dt = parse_pu_appt(lt or st)

            records.append({
                "load": load,
                "datetime": dt,
                "loading_pages": loading_groups.get(load, {}).get("pages", []),
                "shipping_pages": shipping_groups.get(load, {}).get("pages", [])
            })

        records = sorted(
            records,
            key=lambda r: (
                r["datetime"] is None,
                r["datetime"] or datetime.max,
                r["load"]
            )
        )

        output_pdf = os.path.join(workdir, "Matched_Manifest_Packet.pdf")

        writer = PdfWriter()
        loading_reader = PdfReader(loading_pdf)
        shipping_reader = PdfReader(shipping_pdf)

        for r in records:
            for page_num in r["loading_pages"]:
                writer.add_page(loading_reader.pages[page_num])

            for page_num in r["shipping_pages"]:
                writer.add_page(shipping_reader.pages[page_num])

        with open(output_pdf, "wb") as f:
            writer.write(f)

        with open(output_pdf, "rb") as f:
            pdf_bytes = f.read()

        return pdf_bytes, len(loading_groups), len(shipping_groups), len(records)


uploaded_files = st.file_uploader(
    "Upload Loading Manifest and Shipping Manifest files",
    type=["pdf", "zip"],
    accept_multiple_files=True
)

if uploaded_files:
    st.write(f"Files uploaded: **{len(uploaded_files)}**")

    if st.button("Build Matched PDF Packet"):
        try:
            with st.spinner("Matching manifests and building PDF..."):
                pdf_bytes, loading_count, shipping_count, load_count = build_matched_packet(uploaded_files)

            st.success("Matched PDF packet created successfully!")

            st.write(f"Loading loads found: **{loading_count}**")
            st.write(f"Shipping loads found: **{shipping_count}**")
            st.write(f"Unique loads matched: **{load_count}**")

            st.download_button(
                label="Download Matched Manifest Packet",
                data=pdf_bytes,
                file_name="Matched_Manifest_Packet.pdf",
                mime="application/pdf"
            )

        except Exception as e:
            st.error(str(e))
