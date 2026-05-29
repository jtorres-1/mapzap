from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import csv
import os
import io
import stripe
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY

def search_places(query, city, max_results=500):
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.id"
    }
    
    all_places = []
    next_page_token = None
    
    while len(all_places) < max_results:
        body = {
            "textQuery": f"{query} in {city}",
            "maxResultCount": min(20, max_results - len(all_places))
        }
        if next_page_token:
            body["pageToken"] = next_page_token
            
        response = requests.post(url, headers=headers, json=body)
        data = response.json()
        places = data.get("places", [])
        
        if not places:
            break
            
        all_places.extend(places)
        next_page_token = data.get("nextPageToken")
        
        if not next_page_token:
            break
    
    return all_places[:max_results]

@app.route("/api/scrape", methods=["POST"])
def scrape():
    # Verify payment session
    data = request.json
    session_id = data.get("session_id")
    business_type = data.get("business_type", "").strip()
    city = data.get("city", "").strip()
    
    if not session_id or not business_type or not city:
        return jsonify({"error": "Missing required fields"}), 400
    
    # Verify Stripe session
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status != "paid":
            return jsonify({"error": "Payment not completed"}), 402
    except Exception as e:
        return jsonify({"error": "Invalid session"}), 400
    
    # Run scraper
    try:
        places = search_places(business_type, city)
        
        if not places:
            return jsonify({"error": "No results found for that search"}), 404
        
        # Build CSV in memory
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
        return jsonify({"error": str(e)}), 500

@app.route("/api/create-checkout", methods=["POST"])
def create_checkout():
    data = request.json
    business_type = data.get("business_type", "").strip()
    city = data.get("city", "").strip()
    
    if not business_type or not city:
        return jsonify({"error": "Missing business type or city"}), 400
    
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"MapZap: {business_type} leads in {city}",
                        "description": f"Up to 500 local business leads — {business_type} in {city}"
                    },
                    "unit_amount": 4900,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"https://mapzap.org/success?session_id={{CHECKOUT_SESSION_ID}}&type={business_type}&city={city}",
            cancel_url="https://mapzap.org/#pricing",
            metadata={
                "business_type": business_type,
                "city": city
            }
        )
        return jsonify({"url": session.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
