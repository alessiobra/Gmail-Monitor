import asyncio
import os
import pickle
import base64
import discord
import time
import io
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from PIL import Image
from bs4 import BeautifulSoup

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
DISCORD_TOKEN = ''
DISCORD_CHANNEL_ID = 
CLIENT_SECRET_FILE = r""
SENT_SUBJECTS_FILE = 'sent_subjects.txt'  # File to store sent email subjects
TIMESTAMP_FILE = 'last_clear_timestamp.txt'  # File to store the last clearance time

intents = discord.Intents.default()
intents.message_content = True  # Enable the message content intent

client = discord.Client(intents=intents)

script_start_time = int(time.time())

def authenticate_gmail():
    creds = None
    token_path = 'token.pickle'

    if os.path.exists(token_path):
        try:
            with open(token_path, 'rb') as token:
                creds_data = pickle.load(token)
                if isinstance(creds_data, str):
                    creds_data = eval(creds_data)  # Convert string representation to dictionary
                creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
        except Exception as e:
            print(f"Error loading token: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'wb') as token:
            pickle.dump(creds.to_json(), token)

    service = build('gmail', 'v1', credentials=creds)
    return service

def extract_parts(parts, images, text_content, html_content):
    for part in parts:
        mimeType = part.get('mimeType')
        body = part.get('body', {})
        data = body.get('data')
        if mimeType == 'text/plain':
            text_content.append(base64.urlsafe_b64decode(data).decode('utf-8'))
        elif mimeType == 'text/html':
            html_content.append(base64.urlsafe_b64decode(data).decode('utf-8'))
        elif 'image' in mimeType:
            if data:
                image_data = base64.urlsafe_b64decode(data)
                images.append(image_data)
            elif 'attachmentId' in body:
                attachment_id = body['attachmentId']
                attachment = service.users().messages().attachments().get(
                    userId='me', messageId=part['id'], id=attachment_id).execute()
                image_data = base64.urlsafe_b64decode(attachment['data'])
                images.append(image_data)
        if 'parts' in part:
            extract_parts(part['parts'], images, text_content, html_content)

def check_for_new_emails(service):
    global script_start_time
    # Convert script_start_time to string format expected by Gmail API
    query = f'is:unread after:{script_start_time}'
    print(f"Query: {query}")  # Log the query to debug
    try:
        results = service.users().messages().list(userId='me', labelIds=['INBOX'], q=query).execute()
    except Exception as e:
        print(f"Error fetching emails: {e}")
        return []
    
    messages = results.get('messages', [])

    new_emails = []
    if messages:
        for message in messages:
            try:
                msg = service.users().messages().get(userId='me', id=message['id']).execute()
            except Exception as e:
                print(f"Error fetching message details: {e}")
                continue
            
            payload = msg['payload']
            headers = payload.get('headers', [])
            parts = payload.get('parts', [])
            subject, sender = '', ''
            images = []
            text_content = []
            html_content = []

            for header in headers:
                if header['name'] == 'Subject':
                    subject = header['value']
                if header['name'] == 'From':
                    sender = header['value']

            extract_parts(parts, images, text_content, html_content)

            full_text_content = '\n'.join(text_content)
            full_html_content = '\n'.join(html_content)
            new_emails.append({'id': message['id'], 'subject': subject, 'sender': sender, 'text': full_text_content, 'html': full_html_content, 'images': images})
    return new_emails

def mark_as_read(service, email_id):
    service.users().messages().modify(userId='me', id=email_id, body={'removeLabelIds': ['UNREAD']}).execute()

def save_html_to_file(html_content, file_path):
    # Clean up HTML content using BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    cleaned_html = soup.prettify()
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(cleaned_html)

def take_screenshot(html_file_path, screenshot_path):
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--window-size=1200,2000")  # Adjust window width and initial height
    driver = webdriver.Chrome(options=options)
    driver.get(f"file://{os.path.abspath(html_file_path)}")
    
    # Calculate total height
    total_height = driver.execute_script("return document.body.scrollHeight")
    driver.set_window_size(1200, total_height)  # Set the width and calculated height
    time.sleep(2)  # Give some time for the page to load completely
    driver.save_screenshot(screenshot_path)
    driver.quit()

def load_sent_subjects():
    if not os.path.exists(SENT_SUBJECTS_FILE):
        return set()
    with open(SENT_SUBJECTS_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f)

def save_sent_subject(subject):
    with open(SENT_SUBJECTS_FILE, 'a', encoding='utf-8') as f:
        f.write(subject + '\n')

def clear_sent_subjects_file():
    open(SENT_SUBJECTS_FILE, 'w').close()

def get_last_clear_time():
    if not os.path.exists(TIMESTAMP_FILE):
        return None
    with open(TIMESTAMP_FILE, 'r', encoding='utf-8') as f:
        timestamp_str = f.read().strip()
        return datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')

def update_last_clear_time():
    with open(TIMESTAMP_FILE, 'w', encoding='utf-8') as f:
        f.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

def check_and_clear_sent_subjects():
    last_clear_time = get_last_clear_time()
    if last_clear_time is None or datetime.now() - last_clear_time > timedelta(days=1):
        clear_sent_subjects_file()
        update_last_clear_time()

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    service = authenticate_gmail()
    if not service:
        print("Gmail service not authenticated.")
        return
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        print(f"Channel with ID {DISCORD_CHANNEL_ID} not found.")
        return
    print(f"Channel found: {channel.name}")
    
    check_and_clear_sent_subjects()  # Check and clear sent subjects if needed
    sent_subjects = load_sent_subjects()  # Load sent subjects from file

    while True:
        check_and_clear_sent_subjects()  # Check and clear sent subjects if needed
        new_emails = check_for_new_emails(service)
        for email in new_emails:
            if email['subject'] in sent_subjects:
                continue  # Skip if the subject was already sent
            print("New email found!")
            html_file_path = "email_content.html"
            screenshot_path = "email_screenshot.png"
            save_html_to_file(email['html'], html_file_path)
            take_screenshot(html_file_path, screenshot_path)
            content = f"New email from {email['sender']}: {email['subject']}"
            with open(screenshot_path, 'rb') as f:
                await channel.send(content, file=discord.File(f, "email_screenshot.png"))
            save_sent_subject(email['subject'])  # Mark the subject as sent
            sent_subjects.add(email['subject'])
            mark_as_read(service, email['id'])  # Mark the email as read after processing
        await asyncio.sleep(20)

client.run(DISCORD_TOKEN)
