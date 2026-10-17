#!/usr/bin/env python3
"""Collect new bike / bike-part launches from RSS and build a static PWA into ./site/."""
import calendar, difflib, hashlib, html, json, math, os, re, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import escape

# ============================ CONFIG (edit here) ============================
SITE_NAME = "New Bike Releases"
SHORT_NAME = "Bike Releases"
DESCRIPTION = "New bike and bike part launches from the cycling press, refreshed every few hours."
BRAND = "#0B6E4F"   # brand colour (icons, buttons, badges). Keep it dark enough for white text.
KEEP_DAYS = 60      # history length
EMBED_LIMIT = 220   # newest items embedded in index.html (data.json holds everything)
FEED_LIMIT = 50     # items in feed.xml
TIMEOUT = 20        # seconds per feed

# (name, feed URL, default discipline).  "CHECK" = I could not verify this URL; the run log shows if it fails.
SOURCES = [
    ("Bikerumor", "https://bikerumor.com/feed/", "General"),
    ("Pinkbike", "https://www.pinkbike.com/pinkbike_xml_feed.php", "MTB"),
    ("Singletracks", "https://www.singletracks.com/feed/", "MTB"),
    ("Bikepacking.com", "https://bikepacking.com/feed/", "Gravel"),
    ("Cycling Weekly", "https://www.cyclingweekly.com/tech/feed", "Road"),            # CHECK
    ("Cyclingnews", "https://www.cyclingnews.com/tech/feed", "Road"),                 # CHECK
    ("Escape Collective", "https://escapecollective.com/feed/", "Road"),              # CHECK
    ("road.cc", "https://road.cc/all/feed", "Road"),                                  # CHECK
    ("BikeRadar", "https://www.bikeradar.com/feed/", "General"),                      # CHECK
    ("Bike Magazine", "https://www.bikemag.com/feed/", "MTB"),                        # CHECK
    ("Electrek e-bikes", "https://electrek.co/guides/electric-bikes/feed/", "E-bike"),  # CHECK
    ("Cycling Industry News", "https://www.cyclingindustry.news/feed/", "General"),   # CHECK
]

# Scoring: +1 per distinct positive word (max 3), -2 per distinct negative word. Title only. Keep if score >= THRESHOLD.
POSITIVE = ["launch", "launches", "launched", "unveil", "unveils", "unveiled", "introduce", "introduces",
            "introduced", "announce", "announces", "announced", "debut", "debuts", "debuted", "first look",
            "new", "all-new", "reveal", "reveals", "revealed", "drops", "dropped", "updated", "release",
            "releases", "released"]
NEGATIVE = ["review", "reviews", "reviewed", "deal", "deals", "sale", "sales", "best", "vs", "versus",
            "how to", "podcast", "interview", "race report", "results", "preview", "roundup", "round-up",
            "tested", "giveaway", "buyer's guide", "buying guide"]
THRESHOLD = 1

DISCIPLINES = ["Road", "Gravel", "MTB", "E-bike", "General"]
# First match wins (title only); otherwise the source's default discipline is used.
DISCIPLINE_RULES = [
    ("E-bike", ["e-bike", "e-bikes", "ebike", "ebikes", "e-mtb", "emtb", "electric", "e-gravel", "e-road"]),
    ("Gravel", ["gravel", "bikepacking", "adventure"]),
    ("MTB", ["mtb", "mountain bike", "mountain", "enduro", "downhill", "xc", "trail", "hardtail",
             "full-suspension", "full suspension", "dropper", "suspension", "fork", "shock"]),
    ("Road", ["road", "aero", "time trial", "triathlon", "criterium", "climbing"]),
]
# First match wins (title only); otherwise "Other".
CATEGORY_RULES = [
    ("Electronics and GPS", ["gps", "bike computer", "cycling computer", "computer", "head unit", "power meter",
                             "radar", "sensor", "garmin", "wahoo", "hammerhead", "karoo", "light", "lights",
                             "headlight", "taillight", "smart trainer", "trainer", "app", "smartwatch"]),
    ("Helmets and apparel", ["helmet", "helmets", "jersey", "jerseys", "bib", "bibs", "shorts", "jacket", "vest",
                             "gilet", "gloves", "shoes", "shoe", "apparel", "clothing", "kit", "sunglasses",
                             "glasses", "goggles", "socks", "tights", "knee pads", "elbow pads", "armor"]),
    ("Wheels", ["wheel", "wheels", "wheelset", "wheelsets", "rim", "rims", "hub", "hubs", "spokes"]),
    ("Tyres", ["tyre", "tyres", "tire", "tires", "tubeless", "inner tube", "sealant"]),
    ("Brakes", ["brake", "brakes", "rotor", "rotors", "caliper", "calipers", "brake pads", "disc brake"]),
    ("Drivetrain", ["groupset", "groupsets", "derailleur", "derailleurs", "cassette", "cassettes", "crankset",
                    "crank", "cranks", "chainring", "chainrings", "chain", "chains", "shifter", "shifters",
                    "drivetrain", "pedal", "pedals", "bottom bracket", "di2", "axs", "etap"]),
    ("Cockpit and saddles", ["handlebar", "handlebars", "bars", "stem", "saddle", "saddles", "seatpost",
                             "seatposts", "grips", "bar tape", "cockpit", "dropper post", "aerobars"]),
    ("Tools and nutrition", ["tool", "tools", "multi-tool", "pump", "pumps", "gel", "gels", "nutrition", "bottle",
                             "bottles", "bottle cage", "lube", "wax", "repair stand", "wrench", "hydration"]),
    ("Complete bikes", ["bike", "bikes", "e-bike", "e-bikes", "ebike", "ebikes", "e-mtb", "emtb", "frameset",
                        "frame", "hardtail", "trike", "full-suspension"]),
]
# ============================================================================

FMT = "%Y-%m-%dT%H:%M:%SZ"
FIELDS = dict(title="", link="", source="", published="", category="Other", discipline="General", snippet="")
USER_AGENT = f"BikeReleasesBot/1.0 (+https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'your/repo')})"
rx = lambda ws: re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w) for w in sorted(ws, key=len, reverse=True)) + r")(?![\w-])")
POS_RE, NEG_RE = rx(POSITIVE), rx(NEGATIVE)
DISC_RE = [(n, rx(w)) for n, w in DISCIPLINE_RULES]
CAT_RE = [(n, rx(w)) for n, w in CATEGORY_RULES]
TRACK = re.compile(r"^(utm_.*|fbclid|gclid|mc_.*|cmpid|ocid|igshid|ref|ref_.*|source|campaign)$", re.I)
TAGS = re.compile(r"<[^>]*>")


def norm(t):
    return t.lower().replace("\u2019", "'").replace("\u2018", "'")


def score(title):
    t = norm(title)
    pos, neg = set(POS_RE.findall(t)), set(NEG_RE.findall(t))
    return min(len(pos), 3) - 2 * len(neg), sorted(pos), sorted(neg)


def first(rules, title, default):
    t = norm(title)
    return next((n for n, r in rules if r.search(t)), default)


def clean(s, n=0):
    s = html.unescape(TAGS.sub(" ", str(s or "")))
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", TAGS.sub(" ", s))
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*The post .{0,200}? appeared first on .*$", "", s)
    return s[: n - 1].rstrip() + "\u2026" if n and len(s) > n else s


def clean_url(u):
    p = urlsplit(u.strip())
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if not TRACK.match(k)])
    return urlunsplit((p.scheme, p.netloc, p.path, q, ""))


def ukey(u):
    p = urlsplit(clean_url(u))
    return p.netloc.lower().removeprefix("www.") + p.path.rstrip("/") + ("?" + p.query if p.query else "")


def tkey(t):
    return re.sub(r"[^a-z0-9]+", " ", norm(t)).strip()


def is_dup(title, source, seen):
    k = tkey(title)
    for k2, s2 in seen:
        if k == k2:
            return True
        if s2 != source:
            m = difflib.SequenceMatcher(None, k, k2)
            if m.real_quick_ratio() >= 0.9 and m.quick_ratio() >= 0.9 and m.ratio() >= 0.9:
                return True
    return False


def parse_iso(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def to_item(e, name, disc, now):
    title, link = clean(e.get("title")), clean_url(str(e.get("link") or ""))
    if not title or not link.startswith(("http://", "https://")) or score(title)[0] < THRESHOLD:
        return None
    ts = e.get("published_parsed") or e.get("updated_parsed")
    pub = min(datetime.fromtimestamp(calendar.timegm(ts), timezone.utc), now) if ts else now
    body = e.get("summary") or e.get("description") or (e.get("content") or [{}])[0].get("value")
    snip = clean(body, 200)
    if tkey(snip) == tkey(title):
        snip = ""
    return dict(title=title, link=link, source=name, published=pub.strftime(FMT),
                category=first(CAT_RE, title, "Other"), discipline=first(DISC_RE, title, disc), snippet=snip)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def load_history(now):
    """Existing items from ./data.json (missing/corrupt file -> empty), pruned to KEEP_DAYS."""
    items, cutoff = {}, now - timedelta(days=KEEP_DAYS)
    try:
        raw = json.loads(Path("data.json").read_text("utf-8")).get("items", [])
    except Exception:
        raw = []
    for i in raw:
        try:
            i = {k: str(i.get(k) or d) for k, d in FIELDS.items()}
            if i["title"] and i["link"] and parse_iso(i["published"]) >= cutoff:
                items[ukey(i["link"])] = i
        except Exception:
            continue
    return items


def collect(items, now):
    import feedparser  # imported here so the classifier can be tested without it
    cutoff, seen = now - timedelta(days=KEEP_DAYS), [(tkey(i["title"]), i["source"]) for i in items.values()]
    for name, url, disc in SOURCES:
        try:
            feed = feedparser.parse(fetch(url))
            if not feed.entries:
                raise ValueError(f"no entries ({getattr(feed, 'bozo_exception', 'empty feed')})")
            kept = 0
            for e in feed.entries:
                try:
                    it = to_item(e, name, disc, now)
                    if not it or parse_iso(it["published"]) < cutoff or ukey(it["link"]) in items or is_dup(it["title"], name, seen):
                        continue
                except Exception:
                    continue  # malformed entry
                items[ukey(it["link"])] = it
                seen.append((tkey(it["title"]), name))
                kept += 1
            print(f"OK    {name}: {len(feed.entries)} entries, {kept} new launches kept")
        except Exception as ex:
            print(f"FAIL  {name}: {type(ex).__name__}: {str(ex)[:120]}")
    return sorted(items.values(), key=lambda i: (i["published"], i["link"]), reverse=True)


def save_history(items):
    body = ",\n".join(json.dumps(i, ensure_ascii=False, sort_keys=True) for i in items)
    Path("data.json").write_text('{"items": [\n' + body + "\n]}\n", "utf-8")  # no timestamp -> no empty commits


def site_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return "https://USER.github.io/REPO/"
    owner, name = repo.split("/", 1)
    host = f"{owner.lower()}.github.io"
    return f"https://{host}/" if name.lower() == host else f"https://{host}/{name}/"


def make_icons(out):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return print("WARN  Pillow missing, icons skipped")

    def wheel(px, r):
        s = px * 4; c = s / 2; R = s * r
        im = Image.new("RGB", (s, s), BRAND); d = ImageDraw.Draw(im)
        d.ellipse([c - R, c - R, c + R, c + R], outline="white", width=int(R * 0.13))
        for k in range(12):
            a = math.pi * k / 6
            d.line([c, c, c + math.cos(a) * R * 0.9, c + math.sin(a) * R * 0.9], fill="white", width=max(2, int(R * 0.035)))
        d.ellipse([c - R * .17, c - R * .17, c + R * .17, c + R * .17], fill="white")
        return im.resize((px, px), Image.LANCZOS)

    for name, px, r in (("icon-180", 180, .40), ("icon-192", 192, .40), ("icon-512", 512, .40), ("icon-512-maskable", 512, .27)):
        wheel(px, r).save(out / f"{name}.png", optimize=True)


SW = """const V="__BUILD__",C="bike-"+V;
const SHELL=["./index.html","./manifest.webmanifest","./icon-180.png","./icon-192.png","./icon-512.png","./icon-512-maskable.png","./data.json"];
self.addEventListener("install",e=>e.waitUntil(caches.open(C).then(c=>c.addAll(SHELL.map(u=>new Request(u,{cache:"reload"}))))));
self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(n=>n!==C).map(n=>caches.delete(n)))).then(()=>self.clients.claim())));
self.addEventListener("message",e=>{if(e.data==="SKIP_WAITING")self.skipWaiting()});
async function data(e,u){
  const c=await caches.open(C),k=u.origin+u.pathname,hit=await c.match(k);
  const net=fetch(k,{cache:"no-cache"}).then(x=>{if(x.ok)c.put(k,x.clone());return x});
  if(u.searchParams.has("fresh"))return net.catch(()=>hit||Response.error());
  if(hit){e.waitUntil(net.catch(()=>{}));return hit}
  return net;
}
self.addEventListener("fetch",e=>{
  const r=e.request,u=new URL(r.url),p=u.pathname;
  if(r.method!=="GET"||u.origin!==location.origin)return;
  if(r.mode==="navigate"){
    if(/\\/(index\\.html)?$/.test(p))e.respondWith(caches.open(C).then(c=>c.match("./index.html")).then(h=>h||fetch(r)));
    return;
  }
  if(p.endsWith("/data.json"))return e.respondWith(data(e,u));
  if(/\\.(png|webmanifest)$/.test(p))e.respondWith(caches.open(C).then(async c=>(await c.match(r,{ignoreSearch:true}))||fetch(r).then(x=>{if(x.ok)c.put(r,x.clone());return x})));
});
"""


def atom(items, base, stamp):
    q = lambda s: escape(str(s), {'"': "&quot;"})
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
           f"<title>{q(SITE_NAME)}</title><id>{q(base)}</id><updated>{stamp}</updated>",
           f'<link rel="self" href="{q(base)}feed.xml"/><link rel="alternate" href="{q(base)}"/>',
           f"<author><name>{q(SITE_NAME)}</name></author>"]
    for i in items[:FEED_LIMIT]:
        out.append(f'<entry><title>{q(i["title"])}</title><id>{q(i["link"])}</id><link href="{q(i["link"])}"/>'
                   f'<updated>{i["published"]}</updated><author><name>{q(i["source"])}</name></author>'
                   f'<category term="{q(i["category"])}"/><summary>{q(i["snippet"])}</summary></entry>')
    return "\n".join(out + ["</feed>"]) + "\n"


def build(items, now):
    out = Path("site"); out.mkdir(exist_ok=True)
    tpl, base, stamp = Path("template.html").read_text("utf-8"), site_url(), now.strftime(FMT)
    dump = lambda d: json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    head = {"updated": stamp, "disciplines": DISCIPLINES, "categories": [n for n, _ in CATEGORY_RULES] + ["Other"]}
    embed = items[:EMBED_LIMIT]
    version = hashlib.sha1((tpl + SW + BRAND + base + dump(embed)).encode()).hexdigest()[:10]  # same items -> same version -> no needless update prompts
    page = tpl.replace("https://USER.github.io/REPO/", base).replace("%BRAND%", BRAND)
    page = page.replace("__DATA__", dump({**head, "items": embed}).replace("<", "\\u003c"), 1)
    (out / "index.html").write_text(page, "utf-8")
    (out / "data.json").write_text(dump({**head, "items": items}), "utf-8")
    (out / "feed.xml").write_text(atom(items, base, stamp), "utf-8")
    (out / "sw.js").write_text(SW.replace("__BUILD__", version), "utf-8")
    icons = [{"src": f"icon-{s}.png", "sizes": f"{s}x{s}", "type": "image/png", "purpose": p}
             for s, p in ((192, "any"), (512, "any"))] + [{"src": "icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
    (out / "manifest.webmanifest").write_text(json.dumps({
        "name": SITE_NAME, "short_name": SHORT_NAME, "description": DESCRIPTION, "id": "./", "start_url": "./",
        "scope": "./", "display": "standalone", "theme_color": BRAND, "background_color": "#ffffff", "icons": icons}, indent=1), "utf-8")
    make_icons(out)
    print(f"BUILD version {version}, index.html {(out / 'index.html').stat().st_size // 1024} KB, base {base}")


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    items = collect(load_history(now), now)
    save_history(items)
    build(items, now)
    print(f"Done: {len(items)} items in history")


if __name__ == "__main__":
    main()
