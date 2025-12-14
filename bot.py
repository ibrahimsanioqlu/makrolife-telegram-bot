def fetch_listings_playwright(limit=50):
    """
    Sayfayı Playwright ile açar ve ilanları çeker.
    """
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(URL, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)

        # JavaScript ile tüm ilanları çek
        listings = page.evaluate('''() => {
            const results = [];
            const links = document.querySelectorAll('a[href*="ilandetay?ilan_kodu="]');
            const seen = new Set();
            
            links.forEach(link => {
                const href = link.getAttribute("href");
                if (!href) return;
                
                const match = href.match(/ilan_kodu=([A-Z0-9-]+)/);
                if (!match) return;
                
                const kod = match[1];
                if (seen.has(kod)) return;
                seen.add(kod);
                
                // Üst kartı bul
                let card = link;
                for (let i = 0; i < 6; i++) {
                    if (card.parentElement) card = card.parentElement;
                }
                
                // Fiyatı bul
                const text = card.innerText || "";
                const fiyatMatch = text.match(/([\\d.,]+)\\s*₺/);
                const fiyat = fiyatMatch ? fiyatMatch[0] : "Fiyat yok";
                
                results.push({
                    kod: kod,
                    fiyat: fiyat,
                    link: "https://www.makrolife.com.tr/" + href
                });
            });
            
            return results;
        }''')

        browser.close()
        
        for item in listings[:limit]:
            results.append((item["kod"], item["fiyat"], item["link"]))

    return results
