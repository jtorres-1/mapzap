from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import csv
import os
import io
import time
import stripe
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")

stripe.api_key = STRIPE_SECRET_KEY

used_sessions = set()

def search_places(query, city, max_results=100):
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.id"
    }

    all_places = {}
    neighborhoods = ["", "downtown", "north", "south", "east", "west", "central", "northeast", "northwest", "southeast", "southwest"]

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
        except Exception as e:
            continue

    return list(all_places.values())[:max_results]

@app.route("/api/scrape", methods=["POST"])
def scrape():
    data = request.json
    session_id = data.get("session_id")
    business_type = data.get("business_type", "").strip()
    city = data.get("city", "").strip()

    if not session_id or not business_type or not city:
        return jsonify({"error": "Missing required fields"}), 400

    if session_id in used_sessions:
        return jsonify({"error": "This session has already been used. Please purchase a new search."}), 403

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status != "paid":
            return jsonify({"error": "Payment not completed"}), 402
    except Exception as e:
        return jsonify({"error": "Invalid session"}), 400

    used_sessions.add(session_id)

    try:
        places = search_places(business_type, city)

        if not places:
            used_sessions.discard(session_id)
            return jsonify({"error": "No results found for that search"}), 404

        output = io.StringIO()
        writer = csv.writer(output)
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
        used_sessions.discard(session_id)
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
