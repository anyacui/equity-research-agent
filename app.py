import os

import streamlit as st
from dotenv import load_dotenv
import yfinance as yf
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import plotly.graph_objects as go
import markdown as md

try:
    from langchain.agents import AgentExecutor, create_openai_tools_agent
except ImportError:
    from langchain_classic.agents import AgentExecutor, create_openai_tools_agent

st.set_page_config(
    page_title="Equity Research Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

load_dotenv()

if not os.environ.get("OPENAI_API_KEY"):
    st.error(
        "Missing OpenAI key. Create a `.env` file in this folder with "
        "`OPENAI_API_KEY=sk-...`, or run `export OPENAI_API_KEY=sk-...` in the terminal first."
    )
    st.stop()

def plot_stock_chart(ticker: str):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist.index,
        y=hist['Close'],
        mode='lines',
        name=ticker,
        line=dict(color='#1f77b4', width=2)
    ))
    fig.update_layout(
        title=dict(text=f"{ticker} — 1 year price history", font=dict(size=16)),
        xaxis_title="Date",
        yaxis_title="Price (USD)",
        title_font_color="#f8fafc",
        hovermode="x unified",
        height=400,
        template="plotly_white",
        margin=dict(l=48, r=24, t=56, b=48),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
    )
    fig.update_traces(line=dict(color="#1e3a5f", width=2))
    return fig

def find_ir_url(sym: str) -> str:
    try:
        from ddgs import DDGS
        stock = yf.Ticker(sym)
        company_name = stock.info.get('longName', sym)
        # Use first word of company name to avoid overly specific matches
        short_name = company_name.split()[0].lower()

        with DDGS() as ddgs:
            results = list(ddgs.text(
                f'"{company_name}" investor relations earnings presentations',
                max_results=8
            ))

        ir_keywords = ["investor", "ir.", "/investors", "investorrelations"]
        
        for r in results:
            href = r.get('href', '').lower()
            # Must contain IR-like path AND company name/ticker in the domain
            has_ir = any(k in href for k in ir_keywords)
            has_company = short_name in href or sym.lower() in href
            if has_ir and has_company:
                return r.get('href', '')

        # Fallback: just find any IR-looking URL from results
        for r in results:
            href = r.get('href', '').lower()
            if any(k in href for k in ir_keywords):
                return r.get('href', '')

        return ""
    except:
        return ""

def get_ir_links(ir_url: str) -> str:
    if not ir_url:
        return "No IR page found."
    
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth
    from urllib.parse import urlparse

    relevant_keywords = ["transcript", "press release", "prepared remarks",
                        "presentation", "supplemental", "annual report"]

    def extract_pdfs(page):
        links = page.eval_on_selector_all(
            "a",
            "els => els.map(el => ({text: el.innerText.trim(), href: el.href}))"
        )
        return [
            l for l in links
            if any(k in l["text"].lower() for k in relevant_keywords)
            and l["href"].endswith(".pdf")
        ]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            Stealth().apply_stealth_sync(page)

            # Try the IR URL directly first
            page.goto(ir_url, timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(5000)
            relevant = extract_pdfs(page)

            # Only if nothing found, try one presentations subpage
            if not relevant:
                parsed = urlparse(ir_url)
                base = f"{parsed.scheme}://{parsed.netloc}"
                subpages = [
                    "/news-events/events-and-presentations/default.aspx",
                    "/events-and-presentations",
                    "/financial-information/quarterly-results",
                    "/events",
                ]
                for sub in subpages:
                    try:
                        page.goto(base + sub, timeout=30000)
                        page.wait_for_load_state("domcontentloaded")
                        page.wait_for_timeout(3000)
                        relevant = extract_pdfs(page)
                        if relevant:
                            break
                    except:
                        continue

            browser.close()

            if not relevant:
                return f"No PDF documents found on IR page. Search directly for {sym} earnings transcripts and press releases."

    except Exception as e:
        return f"Could not fetch IR documents: {e}"

@tool
def get_stock_fundamentals(ticker: str) -> str:
    """Get key financial fundamentals for a stock ticker."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return f"""
        Company: {info.get('longName')}
        Sector: {info.get('sector')}
        P/E Ratio: {info.get('trailingPE')}
        Revenue Growth: {info.get('revenueGrowth')}
        Profit Margins: {info.get('profitMargins')}
        Debt/Equity: {info.get('debtToEquity')}
        52w High/Low: {info.get('fiftyTwoWeekHigh')} / {info.get('fiftyTwoWeekLow')}
        Analyst Target: {info.get('targetMeanPrice')}
        """
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def web_search(query: str) -> str:
    """Search the web for recent news, headlines, and analyst commentary (use for ticker-related news)."""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No search results."
        lines = []
        for r in results:
            lines.append(
                f"- {r.get('title', '')}: {str(r.get('body', ''))[:300]} ({r.get('href', '')})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Search error: {e}"

@tool
def fetch_webpage(url: str) -> str:
    """Fetch and return the text content of a webpage."""
    import requests
    from bs4 import BeautifulSoup
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        # Remove noise
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:8000]
    except Exception as e:
        return f"Error fetching page: {e}"

@tool  
def fetch_pdf(url: str) -> str:
    """Fetch and extract text from a PDF at a given URL."""
    import requests
    import pdfplumber
    import io
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            text = ""
            for page in pdf.pages[:15]:  # First 15 pages
                text += page.extract_text() or ""
        return text[:8000]
    except Exception as e:
        return f"Error fetching PDF: {e}"

tools = [get_stock_fundamentals, web_search, fetch_webpage, fetch_pdf]

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

prompt = ChatPromptTemplate.from_messages([
    ("system", """You are a buy-side equity analyst producing an investment thesis note. 
    
    When given a stock ticker:
1. Use get_stock_fundamentals to retrieve key metrics
2. Call web_search multiple times:

   - Search for recent news on the company (e.g. "[company name] news 2026")
   - Search for analyst price targets and earnings estimates
   - Search for key competitors and market share
3. Call get_ir_documents with the company's IR presentations URL to get direct PDF links
4. Call fetch_pdf on the most recent:
   - Earnings call transcript
   - Press release
   - Prepared remarks
   Do NOT call fetch_pdf on a homepage URL like investor.apple.com — only call it on direct .pdf URLs.
5. . For any webpage links, call fetch_webpage


Then structure your output as follows:

**Company Snapshot**
Two sentence on what the business does, its current market position and any stock activity worth noting.

**What the Market Believes**
What is the current consensus view on this stock? What is priced in?

**Bull Case**
3 specific reasons the market may be underestimating the company. 
Ground each point in data or a specific recent development and no generic statements.
ite your source inline e.g. [Source](url)


**Bear Case**  
3 specific risks that could cause the thesis to fail.
Focus on company-specific risks, not generic sector risks like "competition" or "regulation" unless you can name a specific competitor or specific regulation.
Cite your source inline e.g. [Source](url)

**Where We Disagree with Consensus**
The single most important thing the market is missing or mispricing. This is your edge.

**View**
Buy / Hold / Sell. Price target if you can estimate one from analyst targets.
One paragraph explaining your conviction and what would change your view.

Be direct. Avoid generic statements. Every claim must be grounded in the data you retrieved."""),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad")
])

agent = create_openai_tools_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)


# ── UI ─────────────────────────────────────────────────────────────────────────
 
st.set_page_config(page_title="Research Analyst", layout="centered")
 
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Playfair+Display:wght@400;500&family=DM+Sans:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
.stApp { background: #060d1c !important; color: #e2e8f0; }
[data-testid="stHeader"] { background: #060d1c !important; border-bottom: 1px solid rgba(255,255,255,0.06); }
div.block-container { padding-top: 2rem; max-width: 900px; }
h1, h2, h3, h4 { color: #f8fafc !important; }
.stMarkdown p { color: #cbd5e1 !important; }
.stMarkdown li { color: #94a3b8 !important; }
.stMarkdown h2 { color: #f8fafc !important; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px; }
.stMarkdown h3 { color: #f1f5f9 !important; }
.stMarkdown strong { color: #f8fafc !important; }
.stMarkdown a { color: #d97706 !important; }
.stTextInput > label { font-family: 'IBM Plex Mono', monospace !important; font-size: 10px !important; letter-spacing: 0.14em !important; color: #64748b !important; text-transform: uppercase !important; }
.stTextInput input { background: #0d1629 !important; border: 1px solid rgba(255,255,255,0.1) !important; color: #f8fafc !important; font-family: 'IBM Plex Mono', monospace !important; border-radius: 6px !important; }
.stTextInput input:focus { border-color: #d97706 !important; box-shadow: none !important; }
.stButton > button[kind="primary"] { background: #d97706 !important; color: #060d1c !important; border: none !important; font-weight: 500 !important; border-radius: 6px !important; }
.stButton > button[kind="primary"]:hover { background: #b45309 !important; }
div[data-testid="stVerticalBlockBorderWrapper"] { background: #0d1629 !important; border: 1px solid rgba(255,255,255,0.08) !important; border-radius: 10px !important; box-shadow: none !important; }
.stSpinner > div { border-top-color: #d97706 !important; }
.stCaption { color: #475569 !important; }
.stMarkdown code {
    background: transparent !important;
    color: #cbd5e1 !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: inherit !important;
    padding: 0 !important;}
</style>
""", unsafe_allow_html=True)


 
st.markdown("""
<div style="margin-bottom:28px">
  <p style="font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:0.18em;color:#d97706;text-transform:uppercase;margin-bottom:8px">Equity research · AI-powered</p>
  <h1 style="font-family:'Playfair Display',Georgia,serif;font-size:36px;font-weight:400;color:#f8fafc;line-height:1.15;margin-bottom:8px">Research Analyst</h1>
  <p style="font-size:14px;color:#94a3b8;line-height:1.6;max-width:520px">Institutional-style notes drawing from earnings transcripts, press releases, and live market data. For research and education purposes, this is not investment advice.</p>
</div>
""", unsafe_allow_html=True)
 
with st.container(border=True):
    ticker = st.text_input(
        "Security",
        placeholder="e.g. AAPL, MSFT, UBER",
        label_visibility="visible",
    )
    run = st.button("Run analysis", type="primary", use_container_width=True)
    st.caption("Runtime: 1–3 min · Fetches IR docs, earnings transcripts and live data")
 
st.markdown("""
<div style="background:#0d1629;border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:16px 20px;margin-top:12px">
  <p style="font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:0.14em;color:#64748b;text-transform:uppercase;margin-bottom:10px">What this desk does</p>
  <ul style="color:#64748b;font-size:13px;line-height:1.9;padding-left:16px;margin:0">
    <li>Fundamentals and 1-year price history</li>
    <li>Web research: news, analyst targets, competitors</li>
    <li>Earnings transcripts and press releases via IR page PDFs</li>
    <li>Structured thesis: snapshot, consensus, bull/bear, view</li>
  </ul>
</div>
<div style="height:1px;background:rgba(255,255,255,0.06);margin:24px 0"></div>
""", unsafe_allow_html=True)
 
if run and ticker:
    sym = ticker.strip().upper()

    def is_valid_ticker(ticker: str) -> bool:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return info.get('regularMarketPrice') is not None or info.get('currentPrice') is not None or info.get('longName') is not None
        except:
            return False

    with st.spinner("Validating ticker..."):
        valid = is_valid_ticker(sym)

    if not valid:
        st.error(f"**{sym}** is not a recognised ticker. Please enter a valid US-listed symbol.")
        st.stop()

    st.markdown(
        f"<p style='font-family:IBM Plex Mono,monospace;font-size:11px;color:#64748b;"
        f"letter-spacing:0.1em;text-transform:uppercase;margin-bottom:8px'>Coverage · {sym}</p>",
        unsafe_allow_html=True
    )
 
    st.plotly_chart(plot_stock_chart(sym), use_container_width=True)
 
    with st.spinner("Fetching investor documents..."):
        ir_url = find_ir_url(sym)
        ir_links = get_ir_links(ir_url)
 
    with st.spinner("Researching..."):
        result = agent_executor.invoke({
            "input": (
                f"Analyse {sym}. Here are direct links to their investor documents, "
                f"use fetch_pdf on the most recent transcript, press release and prepared remarks:\n{ir_links}"
            )
        })
 
    st.markdown(
        f"""
        <div style='
            background:#0d1629;
            border:1px solid rgba(255,255,255,0.25);
            border-radius:12px;
            padding:28px 32px;
            margin-top:24px;
            line-height:1.8;
        '>
            <p style='font-family:IBM Plex Mono,monospace;font-size:10px;letter-spacing:0.14em;
            color:#d97706;text-transform:uppercase;margin:0 0 20px 0;
            padding-bottom:14px;border-bottom:1px solid rgba(255,255,255,0.08)'>
                Research note · {sym}
            </p>
            {"\n" + result['output']}
        </div>
        """,
        unsafe_allow_html=True
)
    # st.markdown(
    # "<p style='font-family:IBM Plex Mono,monospace;font-size:10px;letter-spacing:0.14em;"
    # "color:#d97706;text-transform:uppercase;margin:24px 0 12px'>Research note</p>",
    # unsafe_allow_html=True
    # )

    
    # with st.container(border=True):
    #     st.markdown(result['output'])
 
elif run and not ticker.strip():
    st.warning("Enter a ticker symbol to run the analysis.")