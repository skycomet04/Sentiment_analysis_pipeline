# Sentiment_analysis_pipeline

A scalable AI-powered sentiment analysis pipeline designed for processing customer support conversations using OpenAI models, MySQL, and Pinecone vector database.

Overview

This project automates the complete workflow of:

Fetching support chat records from MySQL
Cleaning and preprocessing messages
Generating sentiment labels using OpenAI GPT models
Creating semantic embeddings using OpenAI Embedding API
Storing embeddings in Pinecone for vector similarity search
Exporting processed data into Excel reports
Synchronizing database and vector database states
