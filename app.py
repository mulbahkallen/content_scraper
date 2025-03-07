import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
from collections import deque
import pandas as pd
import json
import io
from docx import Document
from docx.shared import Pt

def is_internal_link(base_domain, target_url):
    base_netloc = urlparse(base_domain).netloc
    target_netloc = urlparse(target_url).netloc
    return base_netloc == target_netloc

def sanitize_url(url):
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    return url

def parse_html_content(html):
    soup = BeautifulSoup(html, "html.parser")
    elements = []

    for level in range(1, 7):
        for tag in soup.find_all(f"h{level}"):
            text = tag.get_text(strip=True)
            if text:
                elements.append({"type": f"Heading {level}", "content": text})

    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if text:
            elements.append({"type": "Paragraph", "content": text})

    for ul in soup.find_all("ul"):
        for li in ul.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                elements.append({"type": "List item", "content": text})
    
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            row_data = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
            if row_data:
                rows.append(row_data)
        if rows:
            elements.append({"type": "Table", "content": rows})

    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        if src:
            elements.append({"type": "Image", "content": src, "alt": alt})

    return elements

def crawl_website(start_url, max_pages=20):
    start_url = sanitize_url(start_url)
    visited = set()
    data_map = {}
    queue = deque([start_url])

    while queue and len(visited) < max_pages:
        current_url = queue.popleft()
        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            resp = requests.get(current_url, timeout=10)
            resp.raise_for_status()
        except:
            continue

        content_elements = parse_html_content(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.find("title")
        page_title = title_tag.get_text(strip=True) if title_tag else current_url

        data_map[current_url] = {"title": page_title, "content": content_elements}

        for link_tag in soup.find_all("a", href=True):
            link = urljoin(current_url, link_tag["href"])
            if is_internal_link(start_url, link) and link not in visited:
                queue.append(link)

        time.sleep(1)
    
    return data_map

def run_app():
    st.title("Website Crawler & Content Extractor")
    domain_input = st.text_input("Enter a website URL:", "")
    crawl_button = st.button("Start Crawl")

    if "crawled_data" not in st.session_state:
        st.session_state["crawled_data"] = {}

    if crawl_button and domain_input:
        with st.spinner("Crawling website..."):
            data_map = crawl_website(domain_input)
            st.session_state["crawled_data"] = data_map
        st.success("Crawling complete!")

    data_map = st.session_state.get("crawled_data", {})

    if data_map:
        pages = list(data_map.keys())
        selected_page = st.selectbox("Select a page to view content", pages)

        if selected_page:
            page_info = data_map[selected_page]
            st.markdown(f"# {page_info['title']}")
            st.write(f"**URL:** {selected_page}")
            st.write("---")

            for element in page_info["content"]:
                etype = element["type"]
                content = element.get("content", "")
                
                if etype.startswith("Heading"):
                    level = int(etype.split()[1])
                    st.markdown(f"{'#' * level} {content}")
                elif etype == "Paragraph":
                    st.write(content)
                elif etype == "List item":
                    st.write(f"- {content}")
                elif etype == "Table":
                    df = pd.DataFrame(content)
                    st.table(df)
                elif etype == "Image":
                    with st.expander("Show Image"):
                        st.image(content, caption=element.get("alt", ""))
                else:
                    st.write(f"**{etype}:** {content}")
    else:
        if not crawl_button:
            st.info("Enter a domain and click 'Start Crawl' to begin.")

if __name__ == "__main__":
    run_app()
