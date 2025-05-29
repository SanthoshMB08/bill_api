from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient
from groq import Groq
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Optional
import os
from bson import ObjectId
from datetime import datetime
from fastapi.responses import FileResponse
import json
from pydantic import BaseModel, Field
# ========== Environment Variables ========== 
load_dotenv()
GROQ_API_KEY = os.getenv("api_key")
uri=os.getenv("mango_db")

# ========== MongoDB Setup ========== 
client = MongoClient(uri)
db = client["kaamkaz"]

# Collections
customers = db["business_directory"]
products = db["products"]
businwess_enities = db["business_entities"]

# ========== Groq LLaMA Client ========== 
groq_client = Groq(api_key=GROQ_API_KEY)

# ========== FastAPI Setup ========== 
app = FastAPI()

# ========== Models for Request/Response ========== 
class ChatRequest(BaseModel):
    user_input: str
    business_id: str
    biller_id:str
class ProductSelectionRequest(BaseModel):
    store:str
    customer_name: str
    product_names: str
    quantities: str
    business_id: str
    biller_id:str
    # e.g., "strip", "tablet", etc.
class businessEntity(BaseModel):
    businessName: str
    ownerName: str
    email: str
    phone: str
    address: str

# MongoDB ObjectId as string

class TaxDetail(BaseModel):
    rate: float
    amount: float

class Tax(BaseModel):
    sgst: TaxDetail
    cgst: TaxDetail

class Entry(BaseModel):
    productId: str
    productName: str
    productCost: float
    productQuantity: int
    taxIncluded: bool
    tax: Tax

class Discount(BaseModel):
    rate: float
    amount: float

class BillerDetails(BaseModel):
    businessName: str
    ownerName: str
    email: str
    phone: str
    address: str

class InvoiceResponseModel(BaseModel):
    userId: str # Optional user ID, can be None if not applicable
    businessName: str
    customerName: str
    customerPhone: str
    issueDate: datetime
    invoiceName: str
    entries: List[Entry]
    totalCost: float
    discount: Discount
    totalAmountPayable: float
    isDeleted: bool
    billerDetails: BillerDetails
    createdAt: datetime
    updatedAt: datetime
    _v: int
    class Config:
        allow_population_by_field_name = True
        json_encoders = {
            ObjectId: str
        }
# ========== AI Extraction ========== 
def extract_invoice_data(user_text: str):
    prompt = f"""
You are an AI assistant for generating invoices and assisting users.

Instructions:

1. If the user gives a billing request like:
"I bought 2 strips of Augmentin and 3 Crocin for Hrishita from Anand store", return:
{{
  "store": "Anand store",
  "customer_name": "Hrishita",
  "product_names": "Augmentin, Crocin",
  "quantities": "2, 3",
  "unit_type": "strip"
}}
Respond ONLY with JSON object .
\"\"\"{user_text}\"\"\""""

    response = groq_client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": prompt}],
        stream=False
    )

    reply_text = response.choices[0].message.content.strip()
    cleaned = reply_text.strip().strip("`").strip()
    try:
        
        data=json.loads(cleaned)  
        print(data)# Debugging line to see the AI response
        return data
    except json.JSONDecodeError:
        print(f"Failed to parse JSON: {cleaned}")
        return {"reply": cleaned}

# ========== Mongo Data Fetch ========== 
def fetch_data_from_mongo(customer_name, product_names,business_id):
    matched_customers = list(customers.find({
    "business_entity_id": ObjectId(business_id),
    "name": {"$regex": customer_name, "$options": "i"}
})) # Debugging line to see matched customers
    
    if len(matched_customers) == 1:
        customer_data = matched_customers[0]
    elif len(matched_customers) > 1:
        customer_data={"match_customer_names": [c['name'] for c in matched_customers]}
    else:
        customer_data = None

    product_list = [name.strip() for name in product_names.split(",")]
    
    product_data = [products.find_one({'productName': {'$regex': name, '$options': 'i'}}) for name in product_list]
   
    '''
    if len(product) == 1:
        product_data = product[0]
    elif len(product) > 1:
        product_data={"match_product_names": [c["Name"] for c in product]}
    else:
        product_data = None
''' 
    
    return customer_data, product_data

# ========== Invoice Generator ========== 
def create_invoice(customer_data, product_data_list, quantities_raw,store,biller_id):
    quantities = [int(q.strip()) for q in str(quantities_raw).split(",")]
    document = businwess_enities.find_one({'_id':ObjectId(biller_id)})
    print("document",document)
    billerDetails = BillerDetails(
        businessName=document["business_name"],
        ownerName="Anand Bora",
        email=document["email"],
        phone=document["phone_number"],
        address=document["business_address"])
   
        
    
    final_amount=0
    items = []
    for product, qty in zip(product_data_list, quantities):
        if not product:
            continue
        price = product['pricePerUnit']  # Adjust if 'is_strip' logic is needed
        cgst_percent = product['taxPercentages']['cgst'] / 100
        sgst_percent = product['taxPercentages']['sgst'] / 100

        subtotal = round(qty * price, 2)
        cgst = round(subtotal * cgst_percent, 2)
        sgst = round(subtotal * sgst_percent, 2)
        tax_total = round(cgst + sgst, 2)
        total = subtotal + cgst + sgst
        final_amount += total

        entry = Entry(
        productId=str(product['_id']),
        productName=product['productName'],
        productCost=price,
        productQuantity=qty,
        taxIncluded=False,
        tax=Tax(
            sgst=TaxDetail(rate=product['taxPercentages']['sgst'], amount=sgst),
            cgst=TaxDetail(rate=product['taxPercentages']['cgst'], amount=cgst)
            )
        )

        items.append(entry)
    collection = db["challans"]
    document_count = collection.count_documents({})
    challan_number = f"INV-{document_count + 1:06d}"
    discount_rate = 5
    discount_amount = round(final_amount * discount_rate / 100, 2)
    payable_amount = round(final_amount - discount_amount, 2)
    invoice = InvoiceResponseModel(
     # or str(ObjectId()) if using MongoDB
    userId=str(ObjectId("660f8bd71407f98fd9217723")),  # Optional user ID, can be None if not applicable
    businessName=store,
    customerName=customer_data['name'],
    customerPhone=customer_data['phone_number'],
    issueDate=datetime.now(),
    invoiceName=challan_number,
    entries=items,
    totalCost=round(final_amount, 2),
    discount=Discount(rate=discount_rate, amount=discount_amount),
    totalAmountPayable=payable_amount,
    isDeleted=True,
    billerDetails= billerDetails,
    createdAt=datetime.now(),
    updatedAt=datetime.now(),
    _v=4
    )
    try:
        collection.insert_one(invoice.dict())
        return {"message": f"Invoice generated successfully for {customer_data['name']} of Rs {payable_amount} bill no {challan_number}" }
    except Exception as e:
        return {"message": f"Error generating invoice: {str(e)}"}
@app.post("/selected_customer")
async def get_selected_customer(request:ProductSelectionRequest):
    store= request.store
    customer_name = request.customer_name
    product_names = request.product_names
    quantities = request.quantities
    business_id = request.business_id
    biller_id = request.biller_id
    try:
        # Step 2: Fetch customer and product data from MongoDB
        customer_data, product_data = fetch_data_from_mongo(customer_name, product_names,business_id)
        
        # Step 3: Handle multiple customer matches
        if customer_data is None:
            return {"message": "No products found matching the provided names.","customer_name": customer_name, "product_name": product_names, "quantities": quantities }
        elif isinstance(customer_data, dict) and "match_customer_names" in customer_data:
            return {"message": "Multiple products found. Please select one.","customer_name": customer_data["match_customer_names"], "product_name": product_names, "quantities": quantities}
        names=product_names.split(",")
        if None in product_data:
            i=product_data.index(None)
            return{"message": f"No products found matching the provided name {names[i]} .","customer_name": customer_name, "product_name": product_names, "quantities": quantities}
        print("product_data",product_data)
        # Step 4: If all matches are perfect, generate the invoice
        return create_invoice(customer_data, product_data, quantities ,store, biller_id)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# ========== Single Endpoint Function ========== 
@app.post("/generate_invoice")
async def generate_invoice(request: ChatRequest):
    user_input = request.user_input
    
    business_id = request.business_id
    biller_id = request.biller_id
    try:
        # Step 1: Extract the invoice data from the user input using the AI model
        extracted = extract_invoice_data(user_input)
        customer_name = extracted["customer_name"]
        product_names = extracted["product_names"]
        quantities = extracted["quantities"]
        store= extracted["store"]
        
        unit_type = extracted[ "unit_type"]

        # Step 2: Fetch customer and product data from MongoDB
        
        customer_data, product_data = fetch_data_from_mongo(customer_name, product_names,business_id)
        # Step 3: Handle multiple customer matches
        
        if customer_data is None:
            return {"message": "No products found matching the provided names.","customer_name": customer_name, "product_name": product_data, "quantities": quantities, "unit_type": unit_type}
        elif isinstance(customer_data, dict) and "match_customer_names" in customer_data:
            return { "message": "Multiple products found. Please select one.","customer_name": customer_data["match_customer_names"], "product_name": product_names, "quantities": quantities, "unit_type": unit_type}
        if product_data is None:
            return {"message": "No products found matching the provided names.","customer_name": customer_name, "product_name": product_names, "quantities": quantities, "unit_type": unit_type}
        # Step 5: If all matches are perfect, generate the invoice
        names=product_names.split(",")
        if None in product_data:
            i=product_data.index(None)
            return{"message": f"No products found matching the provided name {names[i]} .","customer_name": customer_name, "product_name": product_names, "quantities": quantities, "unit_type": unit_type}
        print(customer_data, product_data, quantities, store, business_id)
        return create_invoice(customer_data, product_data, quantities,store ,biller_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
