from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    Stealth().apply_stealth_sync(page)
    
    # Go to presentations page directly
    page.goto("https://investor.uber.com/news-events/events-and-presentations/default.aspx", timeout=60000)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(5000)
    
    # Extract all links that are PDFs
    links = page.eval_on_selector_all("a", "els => els.map(el => ({text: el.innerText, href: el.href}))")
    
    pdf_links = [l for l in links if ".pdf" in l["href"].lower() or "presentation" in l["text"].lower()]
    
    for link in pdf_links:
        print(link)
    
    browser.close()