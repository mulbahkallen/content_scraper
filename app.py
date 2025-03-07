import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
from urllib.robotparser import RobotFileParser
import re
from collections import deque
import pandas as pd
import json
import io
from docx import Document
from docx.enum.text import WD_BREAK
from docx.shared import Pt

# ------------------------------------------------
# UTILITY FUNCTIONS
# ------------------------------------------------

def is_internal_link(base_domain, target_url):
    """
    Check if target_url belongs to the same domain (internal link)
    """
    # Parse both URLs
    base_netloc = urlparse(base_domain).netloc
    target_netloc = urlparse(target_url).netloc
    return base_netloc == target_netloc

def sanitize_url(url):
    """Ensure URL always has a scheme (http/https)."""
    # If user just types 'example.com', prepend http://
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    return url

def get_robot_parser(domain_url):
    """
    Load and parse the robots.txt for the domain.
    Return a RobotFileParser instance.
    """
    rp = RobotFileParser()
    parsed = urlparse(domain_url)
    # Construct robots.txt URL
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        rp.set_url(robots_url)
        rp.read()
    except:
        # If robots.txt is unreachable, we do nothing special,
        # but the parser won't disallow anything by default.
        pass
    return rp

def parse_html_content(html):
    """
    Parse a single page's HTML and return a list of labeled content elements.
    Each element is a dict: {"type": ..., "content": ...} 
    Types can be: "Heading 1-6", "Paragraph", "List", "Table", "Image", etc.
    """
    soup = BeautifulSoup(html, "html.parser")

    elements = []

    # HEADINGS (H1-H6)
    for level in range(1, 7):
        for tag in soup.find_all(f"h{level}"):
            text = tag.get_text(strip=True)
            if text:
                elements.append({
                    "type": f"Heading {level}",
                    "content": text
                })

    # PARAGRAPHS
    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if text:
            elements.append({
                "type": "Paragraph",
                "content": text
            })

    # LISTS (UL / OL)
    # We'll label each LI separately with "List item"
    # We do this so that the user sees each bullet or number as a distinct piece.
    for ul in soup.find_all("ul"):
        for li in ul.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                elements.append({
                    "type": "List item",
                    "content": text
                })
    for ol in soup.find_all("ol"):
        for li in ol.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                elements.append({
                    "type": "List item",
                    "content": text
                })

    # TABLES
    # We'll parse each table's rows, storing them as a small text block
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            row_data = []
            # Check for th or td
            ths = tr.find_all("th")
            tds = tr.find_all("td")
            if ths:
                # header row
                for th in ths:
                    row_data.append(th.get_text(strip=True))
            else:
                # data row
                for td in tds:
                    row_data.append(td.get_text(strip=True))
            if row_data:
                rows.append(row_data)
        if rows:
            # Convert rows to a text representation
            # For example, "Header1 | Header2\nData1 | Data2\n..."
            table_text_lines = []
            for row in rows:
                table_text_lines.append(" | ".join(row))
            table_text = "\n".join(table_text_lines)
            elements.append({
                "type": "Table",
                "content": table_text
            })

    # IMAGES
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        if src:
            elements.append({
                "type": "Image",
                "content": src,
                "alt": alt
            })

    return elements


# ------------------------------------------------
# CRAWLER
# ------------------------------------------------

def crawl_website(start_url, max_pages=50):
    """
    BFS crawl of a website starting from start_url,
    respecting robots.txt if possible, and return a dict:
       { 'url': { 'title': ..., 'content': [ {...}, ... ] }, ... }
    with up to max_pages internal pages.
    """

    # Ensure start_url is well-formed
    start_url = sanitize_url(start_url)

    # Prepare robot parser
    rp = get_robot_parser(start_url)
    # Attempt to read a default crawl_delay; if not specified, use 1 second
    default_delay = rp.crawl_delay("*")
    if default_delay is None:
        default_delay = 1.0

    visited = set()
    data_map = {}

    queue = deque([start_url])

    while queue and len(visited) < max_pages:
        current_url = queue.popleft()
        # If we've seen this or can't fetch it, skip
        if current_url in visited:
            continue

        # Check robots.txt
        if not rp.can_fetch("*", current_url):
            # skip if disallowed
            continue

        # Mark visited
        visited.add(current_url)

        # Fetch the page
        try:
            resp = requests.get(current_url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            continue  # skip on fetch error

        # Parse HTML
        content_elements = parse_html_content(resp.text)

        # Attempt to get the page title
        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else current_url

        # Store the data
        data_map[current_url] = {
            "title": page_title,
            "content": content_elements
        }

        # Extract internal links
        for link_tag in soup.find_all("a", href=True):
            link = link_tag["href"]
            full_link = urljoin(current_url, link)
            # Check if internal
            if is_internal_link(start_url, full_link):
                # Clean up anchor tags (#something) or ? param might be optional
                # We'll keep queries but skip fragments
                parsed_link = urlparse(full_link)
                no_frag = parsed_link._replace(fragment="")
                final_url = no_frag.geturl()
                if final_url not in visited:
                    queue.append(final_url)

        # Respect crawl delay
        time.sleep(default_delay)

    return data_map


# ------------------------------------------------
# EXPORT UTILITIES
# ------------------------------------------------

def export_to_csv(data_map):
    """
    data_map: { url: { 'title': ..., 'content': [ {type, content, ...}, ... ] }, ... }
    Return CSV file as bytes for download.
    Each row: url, page_title, element_type, element_content (or alt text)
    """
    rows = []
    for url, page_data in data_map.items():
        title = page_data["title"]
        for element in page_data["content"]:
            row = {}
            row["URL"] = url
            row["Page Title"] = title
            row["Element Type"] = element["type"]
            if element["type"] == "Image":
                row["Content"] = element.get("content", "")
                row["Alt Text"] = element.get("alt", "")
            else:
                row["Content"] = element.get("content", "")
                row["Alt Text"] = ""
            rows.append(row)
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8")


def export_to_json(data_map):
    """
    Convert the entire data_map to JSON string (UTF-8 bytes).
    """
    return json.dumps(data_map, ensure_ascii=False, indent=2).encode("utf-8")


def export_to_word(data_map):
    """
    Build a .docx file in-memory from data_map, labeling the elements.
    Return the bytes for download.
    """
    document = Document()

    # A simple style for normal text
    style = document.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)

    for url, page_data in data_map.items():
        page_title = page_data["title"]
        content_list = page_data["content"]

        # Write the page URL as a heading (Heading 1 in doc)
        document.add_heading(page_title, level=1)
        document.add_paragraph(f"URL: {url}")

        # Add some spacing
        document.add_paragraph("")

        for element in content_list:
            el_type = element["type"]
            el_content = element.get("content", "")
            if el_type.startswith("Heading"):
                # Map website heading to doc heading, but avoid overshadowing page H1
                # For example, site Heading 1 -> doc heading level=2
                level_num = 2
                # Extract the number from 'Heading 2'
                match = re.search(r"(\d+)$", el_type)
                if match:
                    heading_level = int(match.group(1))
                    level_num = min(6, heading_level + 1)  # shift all headings by +1
                document.add_heading(el_content, level=level_num)

            elif el_type == "Paragraph":
                document.add_paragraph(el_content)
            elif el_type == "List item":
                # bullet
                document.add_paragraph(f"• {el_content}", style='List Bullet')
            elif el_type == "Table":
                # Just dump the table text in a paragraph
                # (Or we could parse into a real docx table)
                document.add_paragraph("Table:\n" + el_content)
            elif el_type == "Image":
                img_src = el_content
                alt_text = element.get("alt", "")
                # Just put a mention of the image
                document.add_paragraph(f"[Image: {img_src} alt='{alt_text}']")
            else:
                # fallback
                document.add_paragraph(f"{el_type}: {el_content}")

        # Add a page break after each page
        document.add_page_break()

    # Save to in-memory buffer
    f = io.BytesIO()
    document.save(f)
    f.seek(0)
    return f.read()


# ------------------------------------------------
# STREAMLIT APP
# ------------------------------------------------

def run_app():
    st.title("Website Crawler & Content Extractor")
    st.write(
        """
        Enter a domain (e.g., **https://www.jameschenmd.com**) and click "Start Crawl".
        This tool will:
        - Respect robots.txt & crawl-delay
        - Follow internal links (up to the max pages specified)
        - Extract headings, paragraphs, lists, tables, and image references
        - Provide labeled content for each page
        - Allow exporting as CSV, JSON, or Word.
        """
    )

    # User inputs
    domain_input = st.text_input("Enter domain to crawl (e.g. https://example.com):", "")
    max_pages = st.number_input("Max pages to crawl", min_value=1, max_value=10000, value=50)
    crawl_button = st.button("Start Crawl")

    if "crawled_data" not in st.session_state:
        st.session_state["crawled_data"] = {}

    if crawl_button and domain_input:
        with st.spinner("Crawling in progress... Please wait."):
            data_map = crawl_website(domain_input, max_pages=max_pages)
            st.session_state["crawled_data"] = data_map
        st.success("Crawling complete!")

    data_map = st.session_state.get("crawled_data", {})

    if data_map:
        st.subheader("Crawled Pages")
        pages = list(data_map.keys())
        pages.sort()
        selected_page = st.selectbox("Select a page to view content", pages)

        if selected_page:
            page_info = data_map[selected_page]
            st.write(f"**Page Title:** {page_info['title']}")
            st.write(f"**URL:** {selected_page}")
            st.write("---")

            # Display content
            for element in page_info["content"]:
                etype = element["type"]
                content = element.get("content", "")
                if etype.startswith("Heading"):
                    st.markdown(f"**{etype}:** {content}")
                elif etype == "Paragraph":
                    st.write(f"**Paragraph:** {content}")
                elif etype == "List item":
                    # bullet style
                    st.write(f"- {content}")
                elif etype == "Table":
                    st.write("**Table:**")
                    st.text(content)  # show text block of table
                elif etype == "Image":
                    alt_text = element.get("alt", "")
                    st.write(f"**Image:** {content} (alt='{alt_text}')")
                else:
                    st.write(f"{etype}: {content}")

        # Export options
        st.write("---")
        st.subheader("Export All Crawled Data")

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("Download CSV"):
                csv_data = export_to_csv(data_map)
                st.download_button(
                    label="Save CSV",
                    data=csv_data,
                    file_name="crawl_data.csv",
                    mime="text/csv"
                )

        with col2:
            if st.button("Download JSON"):
                json_data = export_to_json(data_map)
                st.download_button(
                    label="Save JSON",
                    data=json_data,
                    file_name="crawl_data.json",
                    mime="application/json"
                )

        with col3:
            if st.button("Download Word (.docx)"):
                docx_data = export_to_word(data_map)
                st.download_button(
                    label="Save Word Document",
                    data=docx_data,
                    file_name="crawl_data.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
    else:
        if not crawl_button:
            st.info("Enter a domain and click 'Start Crawl' to begin.")


# ------------------------------------------------
# ENTRY POINT
# ------------------------------------------------

if __name__ == "__main__":
    run_app()
