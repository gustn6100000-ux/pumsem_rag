import json

path = r"G:\My Drive\Antigravity\pjt\pumsem\pipeline\phase2_output\llm_entities_master.json"
d = json.load(open(path, "r", encoding="utf-8"))

wts = [
    e for ext in d["extractions"]
    for e in ext.get("entities", [])
    if e["type"] == "WorkType" and ext.get("section_id", "") == "13-2-4"
]

print(f"Total WorkTypes: {len(wts)}")
filled = [w for w in wts if w.get("sub_section")]
real_text = [w for w in filled if not w.get("sub_section","").startswith("소제목 #")]
fallback = [w for w in filled if w.get("sub_section","").startswith("소제목 #")]
print(f"With sub_section: {len(filled)}")
print(f"  Real text: {len(real_text)}")
print(f"  Fallback:  {len(fallback)}")
print(f"Fill rate: {len(filled)/len(wts)*100:.1f}%" if wts else "N/A")
print()

for w in wts:
    name = w["name"][:45].ljust(45)
    sub = w.get("sub_section", "NULL")
    print(f"  {name} | {sub}")
