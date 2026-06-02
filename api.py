from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import csv
import os
import io
import re
import time
import stripe
import dns.resolver
import json
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
stripe.api_key = STRIPE_SECRET_KEY

SESSIONS_FILE = "/root/mapzap/used_sessions.json"

def load_sessions():
    try:
        with open(SESSIONS_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_session(sid):
    sessions = load_sessions()
    sessions.add(sid)
    with open(SESSIONS_FILE, "w") as f:
        json.dump(list(sessions), f)

# =========================
# EMAIL FINDER
# =========================
BLOCKED_DOMAINS = {
    "your-domain.com", "domain.com", "address.com", "example.com",
    "example.org", "email.com", "yourdomain.com", "test.com",
    "yourcompany.com", "company.com", "site.com", "mysite.com",
    "website.com", "sentry.io", "wixpress.com", "squarespace.com",
    "godaddy.com", "wordpress.com", "shopify.com",
    "hilton.com", "hyatt.com", "marriott.com", "ihg.com",
    "wyndham.com", "choicehotels.com",
}
BLOCKED_LOCAL_PARTS = {
    "email", "name", "you", "your", "username", "user",
    "test", "demo", "sample", "admin", "webmaster",
    "noreply", "no-reply", "donotreply", "postmaster",
    "accessibility", "privacy", "legal", "press", "media",
    "careers", "jobs", "hr", "investor", "investors",
    "unsubscribe", "abuse",
}
BLOCKED_EMAILS = {
    "email@address.com", "info@your-domain.com", "user@domain.com",
    "name@email.com", "you@example.com", "hello@calldone.org",
}
PLACEHOLDER_PATTERNS = [
    r"your[-_]?domain", r"your[-_]?company", r"example\.",
    r"^email@", r"^name@", r"^you@",
    r"@.*\.(png|jpg|jpeg|gif|svg|webp)$",
]
FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com"}
PRIORITY_PREFIXES = ("info@", "contact@", "hello@", "service@", "office@", "manager@", "owner@", "dispatch@")

_mx_cache = {}

def get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except:
        return ""

def is_valid_email(email):
    email = (email or "").lower().strip()
    if not email or "@" not in email:
        return False
    if email in BLOCKED_EMAILS:
        return False
    local, _, domain = email.partition("@")
    if domain in BLOCKED_DOMAINS:
        return False
    if local in BLOCKED_LOCAL_PARTS:
        return False
    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, email):
            return False
    return True

def has_mx(domain):
    if not domain:
        return False
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=4)
        result = len(answers) > 0
    except Exception:
        result = False
    _mx_cache[domain] = result
    return result

def scrape_emails(url, business_domain=None):
    if not url:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        found = []
        for path in ["", "/contact", "/about", "/contact-us"]:
            try:
                r = requests.get(urljoin(url, path), headers=headers, timeout=6)
                emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', r.text)
                found += emails
            except:
                continue
        valid = []
        for e in set(found):
            e = e.lower().strip()
            if not is_valid_email(e):
                continue
            domain = e.split("@")[-1]
            if business_domain:
                if domain != business_domain and domain not in FREE_MAIL:
                    continue
            if not has_mx(domain):
                continue
            valid.append(e)
        def sort_key(x):
            x_domain = x.split("@")[-1]
            same_domain = 0 if (business_domain and x_domain == business_domain) else 1
            priority = 0 if x.startswith(PRIORITY_PREFIXES) else 1
            return (same_domain, priority)
        valid.sort(key=sort_key)
        return valid[0] if valid else ""
    except:
        return ""

# =========================
# PLACES SCRAPER
# =========================
def search_places(query, city, max_results=300):
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.id"
    }
    all_places = {}
    neighborhoods = [
        "", "downtown", "north", "south", "east", "west",
        "central", "northeast", "northwest", "southeast", "southwest",
        "midtown", "uptown", "old town", "historic district",
        "financial district", "waterfront", "suburbs", "metro", "city center"
    ]
    for area in neighborhoods:
        if len(all_places) >= max_results:
            break
        q = f"{query} in {area + ' ' if area else ''}{city}"
        body = {"textQuery": q, "maxResultCount": 20}
        try:
            response = requests.post(url, headers=headers, json=body, timeout=10)
            data = response.json()
            for p in data.get("places", []):
                pid = p.get("id", "")
                if pid and pid not in all_places:
                    all_places[pid] = p
            time.sleep(0.3)
        except Exception:
            continue
    return list(all_places.values())[:max_results]

# =========================
# ROUTES
# =========================
@app.route("/api/preview", methods=["POST"])
def preview():
    data = request.json
    business_type = data.get("business_type", "").strip()
    city = data.get("city", "").strip()
    if not business_type or not city:
        return jsonify({"error": "Missing required fields"}), 400
    try:
        places = search_places(business_type, city, max_results=5)
        if not places:
            return jsonify({"error": "No results found for that search"}), 404
        leads = []
        for p in places:
            leads.append({
                "name": p.get("displayName", {}).get("text", ""),
                "address": p.get("formattedAddress", ""),
                "phone": p.get("nationalPhoneNumber", ""),
                "website": p.get("websiteUri", "")
            })
        return jsonify({"leads": leads, "total": 300})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/scrape", methods=["POST"])
def scrape():
    data = request.json
    session_id = data.get("session_id")
    business_type = data.get("business_type", "").strip()
    city = data.get("city", "").strip()
    tier = data.get("tier", "basic").strip().lower()

    if not session_id or not business_type or not city:
        return jsonify({"error": "Missing required fields"}), 400

    if session_id in load_sessions():
        return jsonify({"error": "This session has already been used. Please purchase a new search."}), 403

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status != "paid":
            return jsonify({"error": "Payment not completed"}), 402
    except Exception:
        return jsonify({"error": "Invalid session"}), 400

    save_session(session_id)

    try:
        places = search_places(business_type, city)
        if not places:
            return jsonify({"error": "No results found for that search"}), 404

        output = io.StringIO()
        writer = csv.writer(output)

        if tier == "pro":
            writer.writerow(["Business Name", "Address", "Phone", "Website", "Email", "City", "Type"])
            for p in places:
                name = p.get("displayName", {}).get("text", "")
                address = p.get("formattedAddress", "")
                phone = p.get("nationalPhoneNumber", "")
                website = p.get("websiteUri", "")
                domain = get_domain(website)
                email = scrape_emails(website, business_domain=domain) or "N/A"
                writer.writerow([name, address, phone, website, email, city, business_type])
                time.sleep(0.3)
        else:
            writer.writerow(["Business Name", "Address", "Phone", "Website", "City", "Type"])
            for p in places:
                name = p.get("displayName", {}).get("text", "")
                address = p.get("formattedAddress", "")
                phone = p.get("nationalPhoneNumber", "")
                website = p.get("websiteUri", "")
                writer.writerow([name, address, phone, website, city, business_type])

        output.seek(0)
        filename = f"{business_type.replace(' ', '_')}_{city.replace(', ', '_').replace(' ', '_')}_leads.csv"
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype="text/csv",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
