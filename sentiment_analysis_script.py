import pymysql
from dotenv import load_dotenv
import os
from pinecone import Pinecone, ServerlessSpec
from google import genai
from openai import OpenAI
import time
import generate_excel 
from dbutils.pooled_db import PooledDB

load_dotenv()
#GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "cursorclass": pymysql.cursors.DictCursor
}

'''pool = PooledDB(
    creator=pymysql,
    maxconnections=6,
    mincached=2,
    maxcached=4,
    blocking=True,

    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    cursorclass=pymysql.cursors.DictCursor
)'''

#client = genai.Client(api_key=GEMINI_API_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)


pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
index_name = "support-chat-analysis"
#index_name = "supportchat-analysis"

if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=1536,  
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region=os.getenv("PINECONE_ENV"))
    )
index = pc.Index(index_name)



def get_db_connection():
    #return pool.coonnection
    return pymysql.connect( **db_config, connect_timeout=5 ) 

def fetch_reviews(batch_size=1000):
    conn=None
    cursor= None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        print("cursor created")
        query = """
            SELECT
                id, message
            FROM messages where is_processed = FALSE and (Length(TRIM(message)) > 1 and is_skipped = 0)
            ORDER BY id LIMIT %s
        """
        cursor.execute(query,(batch_size,))
        batch_data = cursor.fetchall()
        return batch_data
    except Exception as e:
        print("Fetch error", e)
    finally:
        if conn:
            cursor.close()
            conn.close()


def clean_text(text):
    return text.strip().replace("\n", " ") if text else None

def fetch_embedding (batch_size = 1000):
    conn=None
    cursor= None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = """
            SELECT
                id, user_id, friend_id, message, is_welcome, is_sent, is_read,
                created, sentiment, hour, is_embedded
            FROM messages where is_processed = TRUE and is_skipped = FALSE and is_embedded = FALSE
            ORDER BY id LIMIT %s 
        """
        cursor.execute(query,(batch_size,))
        batch_data = cursor.fetchall()
        return batch_data
    except Exception as e:
        print("Fetch error", e)
    finally:
        if conn:
            cursor.close()
            conn.close()

def retry_logic(api_call, retries=1, i=0):
    for attempt in range (retries):
        try:
            return api_call()
        except Exception as e:
            code = getattr(
                e,
                "status_code",
                0
            )
            print(f"API Error: {e}")
            if attempt < retries - 1:
                if code == 429 :
                    time.sleep(60)
                elif 500 <= code <= 504:
                    wait_time = 2 ** (attempt+1)
                    print(f"Retrying in {wait_time} seconds")
                    time.sleep(wait_time)
                else:
                    raise e
                print(f"Retry {attempt}")
            else:
                print(f"Batch {i} permanently failed")
                raise e

def get_sentiment(text):
    try:
        is_skipped=0
        sentiment_prompt = f"""
            You are an advanced sentiment analysis engine for customer support chats.

            Your task:
            Classify the sentiment of the message into ONLY one of these categories:

            Positive
            Negative
            Neutral

            Classification Rules:

            Positive:
            - appreciation
            - happiness
            - praise
            - gratitude
            - satisfaction
            - compliments
            - positive financial intent
            - successful resolution

            Negative:
            - anger
            - frustration
            - complaints
            - disappointment
            - threats
            - dissatisfaction
            - refund issues
            - unresolved problems
            - delivery problems

            Neutral:
            - informational statements
            - questions without emotion
            - status updates
            - greetings
            - unclear emotional tone
            - Consider emojis while analyzing sentiment
            Examples:
                awesome service - Sentiment: Positive
                great experience - Sentiment: Positive
                love the support - Sentiment: Positive
                excellent work  - Sentiment: Positive

                Message: this is very disappointing
                Sentiment: Negative

                Message: refund has still not been processed
                Sentiment: Negative

                Message: I am frustrated with the delay
                Sentiment: Negative

                Message: check my order status
                Sentiment: Neutral

                Message: please update me on delivery
                Sentiment: Neutral

            Important:
                - Consider emojis as emotional indicators
                - Return ONLY ONE word
                - Do NOT explain
            """ 
        response = retry_logic(lambda: client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "developer", 
                "content" :sentiment_prompt},
                {"role": "user", "content": text}
            ],
            temperature=0
            ), 
            3 )
        if not response.output_text.strip():
            is_skipped = 1
            return " ", is_skipped
        return response.output_text.strip(), is_skipped

    except Exception as e:
        print(f"Sentiment Error: {e}")
#response = client.models.generate_content(
            #model="gemini-3.1-flash-lite",
           # config={
           # "temperature":0} )
        
        #return response.text.strip()
    
def get_embedding(texts):
    all_embeddings = []

    for i in range(0, len(texts), 100):
        try:
            extract_text = []
            batch = texts[i:i+100]
            for text in batch:
                extract_text.append(text["text"]) 
            response = retry_logic(
                lambda: client.embeddings.create(
                    model="text-embedding-3-small",
                    input=extract_text
                ), 3)
                    
            for item, embedding in zip(batch, response.data):
                    all_embeddings.append({
                        "text_data": item,
                        "embedding": embedding.embedding
                    })         
            time.sleep(.2)
        except Exception as e:
            print(f"Python Error: {e}")
    return all_embeddings


'''def update_sentiment(update_db):
    conn= None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = "UPDATE messages SET sentiment=%s, is_processed=TRUE WHERE id=%s"
        cursor.executemany(query, update_db)
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Error occured : {e}")
    finally:
        if conn:
            cursor.close()
            conn.close()'''
def update_records(query, values):
    conn= None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.executemany(query, values)
        conn.commit()
        print("Done Updating")
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Error occured : {e}")
    finally:
        if conn:
            cursor.close()
            conn.close()


def store_in_vector_db(embeddings):
        vectors = []
        successful_id = []
        for item in embeddings:
            text_data = item["text_data"]
            row = text_data["row"]
            embedding = item["embedding"]
            metadata = {
                    "message": text_data["text"][:40],
                    "sentiment": text_data["sentiment"],
                    "user_id": row["user_id"],
                    "friend_id": row["friend_id"],
                    "is_read": bool(row.get("is_read", 0)),
                    "is_welcome":bool(row.get("is_welcome", 0)),
                    "is_sent": bool(row.get("is_sent", 0)),
                }
            if row.get("created"):
                metadata["created"] = row["created"].isoformat()

            if row.get("hour"):
                metadata["hour"] = row["hour"]
            vectors.append({
                "id": str(row["id"]),
                "values": embedding,
                "metadata" : metadata   
            })     

        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            try:
                batch = vectors[i:i + batch_size]
                retry_logic(lambda :index.upsert(vectors=batch), 3)
                successful_id.extend([
                    (int(item['id']), )
                    for item in batch 
                ])
                print(f"Upserted batch {i} to {i + len(batch)}")
            except Exception as e:
                print(f"store_in_vector function Error: {e}")
        return successful_id    
    

        
def run_pipeline(batch_size):
    while True:
        print("Pipeline started")
        reviews = fetch_reviews(batch_size)
        print("reviews collected")
        if not reviews :
            return 
        update_db=[]
        for row in reviews:
            review_id = row["id"]
            text = clean_text(row["message"])
            if not text or len(text)<=1:
                continue
            print(f"Processing ID: {review_id}")

            sentiment, is_skipped = get_sentiment(text)
            if sentiment is None and is_skipped == 0:
                continue
            update_db.append((sentiment, is_skipped, review_id))
            
            time.sleep(.2) 
        query = """    
        UPDATE messages
            SET sentiment=%s,
                is_processed=TRUE, is_skipped =%s
            WHERE id=%s
            """
        update_records(query, update_db)
        run_embeddings_pipeline(batch_size)
        
        print("Pipeline completed successfully! for batch")
        print("Completed fully")

def run_embeddings_pipeline(batch_size):
    try:
        data= fetch_embedding(batch_size)
        texts=[]
        if not data :
            return
        #update_db=[]
        for row in data :
            #update_db.append((row['id'],))
            embed={"row": row,
                "text": clean_text(row.get('message')),
                "sentiment": row['sentiment']}
            texts.append(embed) 
        generate_excel.export_to_excel(batch_size, data[0]["id"])
        embeddings = get_embedding(texts)
        if not embeddings:
            print("No data")
            #continue
            return
        successful_ids = store_in_vector_db(embeddings)
        if len(successful_ids) <= 0 :
            return 
        query='''
                UPDATE messages set is_embedded =TRUE WHERE id =%s
                '''
        update_records(query, successful_ids)
    except Exception as e:
        print(f"Embedding pipeline error: {e}")

'''def search_messages(query, sentiment_filter=None):
    response = client.models.embed_content(
        model="gemini-embedding-001",

        contents=query,

        config={
            "output_dimensionality": 1536
        }
    )

    query_embedding = response.embeddings[0].values
    filter_dict = {}
    if sentiment_filter:
        filter_dict["sentiment"] = sentiment_filter

    results = index.query(
        vector=query_embedding,

        top_k=5,

        include_metadata=True,

        filter=filter_dict if filter_dict else None
    )

    return results'''


if __name__ == "__main__":
    run_pipeline(500)
    
    #results = search_messages("bad delivery experience", sentiment_filter="Negative")
    #print("\n🔍 Search Results:\n", results)
