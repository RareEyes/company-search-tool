import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from tavily import TavilyClient
from google import genai

st.set_page_config(
    page_title="Company Search Tool",
    page_icon="🔎",
    layout="wide"
)

st.title("Company Search & Profile")
st.write(
    "Find official company websites and extract company "
    "information directly from those websites."
)

# API keys will be added through Streamlit Secrets.
try:
    tavily = TavilyClient(
        api_key=st.secrets["TAVILY_API_KEY"]
    )
    gemini = genai.Client(
        api_key=st.secrets["GEMINI_API_KEY"]
    )
except Exception:
    st.error(
        "API keys are not configured. Please check "
        "your Streamlit Secrets settings."
    )
    st.stop()


# Exclude obvious non-company websites.
BLOCKED_DOMAINS = (
    "wikipedia.org",
    "opencorporates.com",
    "bloomberg.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "crunchbase.com",
    "zoominfo.com",
    "dnb.com",
    "rocketreach.co",
    "yellowpages.",
    "yelp.com",
    "zaubacorp.com",
    "tofler.in",
    "companieshouse.gov.uk",
    "find-and-update.company-information.service.gov.uk",
)

BLOCKED_SUFFIXES = (
    ".gov", ".gov.in", ".gov.uk", ".gov.ae",
    ".gov.sa", ".gouv.fr", ".gc.ca",
)

def domain_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")

def is_candidate(url):
    domain = domain_of(url)

    if not domain or urlparse(url).scheme not in ("http", "https"):
        return False

    if any(x in domain for x in BLOCKED_DOMAINS):
        return False

    if any(domain.endswith(x) for x in BLOCKED_SUFFIXES):
        return False

    return True


def search_companies(company, country):
    query = (
        f'"{company}" company official website '
        f'{country} about contact'
    )

    response = tavily.search(
        query=query,
        search_depth="advanced",
        max_results=10
    )

    matches = []
    seen = set()

    for item in response.get("results", []):
        url = item.get("url", "")
        if not is_candidate(url):
            continue

        domain = domain_of(url)
        if domain in seen:
            continue

        seen.add(domain)
        matches.append({
            "Company / page": item.get("title", "Unknown"),
            "Website": url,
            "Country searched": country,
            "Search excerpt": item.get("content", "")
        })

    return matches


def read_website(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; CompanyResearchTool/1.0)"
        )
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=15,
        allow_redirects=True
    )
    response.raise_for_status()

    final_url = response.url

    # Do not follow redirects to a different domain.
    if domain_of(final_url) != domain_of(url):
        raise ValueError(
            "The selected website redirected to another domain."
        )

    soup = BeautifulSoup(response.text, "html.parser")

    for element in soup(
        ["script", "style", "nav", "footer", "header", "noscript"]
    ):
        element.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = soup.get_text(" ", strip=True)

    # Collect useful links on the same website.
    useful_links = []
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True)
        href = urljoin(final_url, link["href"])

        if (
            domain_of(href) == domain_of(final_url)
            and any(
                word in (label + " " + href).lower()
                for word in [
                    "about", "contact", "location",
                    "global", "office", "company"
                ]
            )
        ):
            useful_links.append((label, href))

    pages = [
        f"PAGE: {final_url}\nTITLE: {title}\nCONTENT: {text[:12000]}"
    ]
    visited = {final_url}

    for label, link_url in useful_links[:3]:
        if link_url in visited:
            continue

        visited.add(link_url)
        try:
            page = requests.get(
                link_url,
                headers=headers,
                timeout=10,
                allow_redirects=True
            )
            if (
                page.ok
                and domain_of(page.url) == domain_of(final_url)
                and "text/html" in page.headers.get(
                    "Content-Type", ""
                ).lower()
            ):
                page_soup = BeautifulSoup(page.text, "html.parser")
                for element in page_soup(
                    ["script", "style", "nav", "footer", "header"]
                ):
                    element.decompose()

                page_text = page_soup.get_text(" ", strip=True)
                pages.append(
                    f"PAGE: {page.url}\n"
                    f"TITLE: {label}\n"
                    f"CONTENT: {page_text[:6000]}"
                )
        except Exception:
            continue

    return "\n\n".join(pages)


def extract_profile(company, country, url, website_text):
    prompt = f"""
You are a company research assistant for KYC research.

Use ONLY the supplied content from the selected company's own website.
Do not use outside knowledge or invent facts.

Requested company: {company}
Country supplied by user: {country}
Selected website: {url}

Return these sections:
1. Company name
2. Address
3. Countries of operation
4. Business description (2-3 lines)

Rules:
- Only report information supported by the supplied website content.
- If an address is not found, say "Not found on the reviewed pages".
- If countries of operation are unclear, say so.
- Do not treat a website's country domain alone as proof of operations.
- Distinguish the requested company from similarly named companies.
- Cite the specific page URL beside each detail where possible.
- If the content does not clearly match the requested company,
  state that the match could not be verified.

WEBSITE CONTENT:
{website_text[:25000]}
"""

    response = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text


with st.form("company_search"):
    company_name = st.text_input(
        "Company name",
        placeholder="Enter the company name"
    )
    country = st.text_input(
        "Country",
        placeholder="Enter the country"
    )
    submitted = st.form_submit_button("Search websites")

if submitted:
    if not company_name.strip() or not country.strip():
        st.warning("Please enter both the company name and country.")
    else:
        with st.spinner("Searching for potential official websites..."):
            try:
                st.session_state["matches"] = search_companies(
                    company_name.strip(),
                    country.strip()
                )
                st.session_state["searched_company"] = company_name.strip()
                st.session_state["searched_country"] = country.strip()
            except Exception as error:
                st.error(f"Search failed: {error}")
                st.session_state.pop("matches", None)

matches = st.session_state.get("matches", [])

if matches:
    st.subheader("Potential website matches")
    st.caption(
        "Review the results carefully. A search result is not "
        "automatically proof that a website belongs to the company."
    )

    for index, item in enumerate(matches, start=1):
        st.markdown(
            f"**{index}. {item['Company / page']}**  \n"
            f"Website: {item['Website']}  \n"
            f"Country searched: {item['Country searched']}"
        )

    choices = [
        f"{item['Company / page']} — {item['Website']}"
        for item in matches
    ]

    selected_index = st.selectbox(
        "Select the correct company website",
        range(len(choices)),
        format_func=lambda i: choices[i]
    )

    if st.button("Confirm website and extract profile"):
        selected = matches[selected_index]

        with st.spinner("Reading the selected official website..."):
            try:
                website_text = read_website(selected["Website"])

                profile = extract_profile(
                    st.session_state["searched_company"],
                    st.session_state["searched_country"],
                    selected["Website"],
                    website_text
                )

                st.subheader("Company profile")
                st.markdown(profile)
                st.markdown(
                    f"**Selected website:** "
                    f"[{selected['Website']}]({selected['Website']})"
                )

            except Exception as error:
                st.error(
                    "Could not complete the extraction. "
                    "The website may block automated access, "
                    "or the page may be unavailable."
                )
                st.caption(str(error))

elif "matches" in st.session_state:
    st.info(
        "No suitable website matches were found. "
        "Try the company's full name or a different country spelling."
    )
