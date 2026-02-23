from bs4 import BeautifulSoup
soup = BeautifulSoup(open(r'g:\My Drive\Antigravity\pipeline\download_file\20260208_747-878 OKOK.md', encoding='utf-8'), 'html.parser')
for i, t in enumerate(soup.find_all('table')[:30]):
    trs = t.find_all('tr')
    cols = len(trs[1].find_all('td')) if len(trs) > 1 else 0
    print(f"Table {i}: rows={len(trs)} cols={cols}")
